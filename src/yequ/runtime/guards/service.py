"""Hard execution preconditions for Center runtime commands."""

from __future__ import annotations

from dataclasses import dataclass, field

from yequ.runtime.command import RuntimeCommand


@dataclass(frozen=True, slots=True)
class GuardDecision:
    """Structured decision before policy and admission.

    Guard decisions are factual and structural. They do not approve risk,
    schedule jobs, create operations, or generate assistant text.
    """

    decision: str
    reason: str
    missing_slots: list[str] = field(default_factory=list)
    required_facts: list[str] = field(default_factory=list)
    failed_preconditions: list[dict[str, object]] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    def to_dict(self) -> dict[str, object]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "missing_slots": list(self.missing_slots),
            "required_facts": list(self.required_facts),
            "failed_preconditions": [dict(item) for item in self.failed_preconditions],
        }


class ExecutionGuard:
    """Evaluate hard preconditions before a command enters execution."""

    def evaluate(self, command: RuntimeCommand) -> GuardDecision:
        if command.function_name == "transfer.create":
            return self._evaluate_transfer_create(command)
        if command.function_name == "transfer.resume":
            return self._evaluate_transfer_resume(command)
        if command.function_name == "artifact.deploy":
            return self._evaluate_artifact_deploy(command)
        return GuardDecision(decision="allow", reason="no_guard_rule")

    def _evaluate_transfer_create(self, command: RuntimeCommand) -> GuardDecision:
        input_data = command.input_data
        missing: list[str] = []
        for slot in ("source_node_id", "target_node_id", "source_path"):
            if not _has_string(input_data.get(slot)):
                missing.append(slot)
        if not _has_string(input_data.get("resume_mode")):
            missing.append("resume_mode")
        if not (
            _has_string(input_data.get("target_output_dir"))
            or _has_string(input_data.get("target_path"))
        ):
            missing.append("target_output_dir_or_target_path")

        if missing:
            return GuardDecision(
                decision="needs_input",
                reason="transfer.create requires explicit source, target, and landing path",
                missing_slots=missing,
            )

        if input_data.get("resume_mode") not in {
            "resume",
            "overwrite",
            "fail_if_exists",
        }:
            return GuardDecision(
                decision="blocked",
                reason="resume_mode must be resume, overwrite, or fail_if_exists",
                failed_preconditions=[
                    {
                        "fact": "transfer.resume_mode",
                        "code": "invalid_resume_mode",
                    }
                ],
            )

        if bool(input_data.get("skip_preflight")) and not _has_string(
            input_data.get("skip_reason")
        ):
            return GuardDecision(
                decision="blocked",
                reason="skip_preflight requires explicit skip_reason",
                failed_preconditions=[
                    {
                        "fact": "preflight.skip_reason",
                        "code": "missing_skip_reason",
                    }
                ],
            )

        if not bool(input_data.get("skip_preflight")) and not _has_string(
            input_data.get("preflight_id")
        ):
            return GuardDecision(
                decision="preflight_required",
                reason="transfer.create requires a successful transfer.preflight result",
                required_facts=["transfer.preflight"],
            )

        return GuardDecision(decision="allow", reason="transfer_intent_slots_present")

    def _evaluate_transfer_resume(self, command: RuntimeCommand) -> GuardDecision:
        if not _has_string(command.input_data.get("transfer_id")):
            return GuardDecision(
                decision="needs_input",
                reason="transfer.resume requires transfer_id",
                missing_slots=["transfer_id"],
            )
        return GuardDecision(decision="allow", reason="transfer_resume_intent_slots_present")

    def _evaluate_artifact_deploy(self, command: RuntimeCommand) -> GuardDecision:
        input_data = command.input_data
        missing: list[str] = []
        for slot in ("artifact_id", "target_node_id", "output_path"):
            if not _has_string(input_data.get(slot)):
                missing.append(slot)

        if missing:
            return GuardDecision(
                decision="needs_input",
                reason="artifact.deploy requires explicit artifact, target node, and output path",
                missing_slots=missing,
            )

        if bool(input_data.get("skip_preflight")) and not _has_string(
            input_data.get("skip_reason")
        ):
            return GuardDecision(
                decision="blocked",
                reason="skip_preflight requires explicit skip_reason",
                failed_preconditions=[
                    {
                        "fact": "preflight.skip_reason",
                        "code": "missing_skip_reason",
                    }
                ],
            )

        if not bool(input_data.get("skip_preflight")) and not _has_string(
            input_data.get("preflight_id")
        ):
            return GuardDecision(
                decision="preflight_required",
                reason="artifact.deploy requires a successful artifact.deploy.preflight result",
                required_facts=["artifact.deploy.preflight"],
            )

        return GuardDecision(decision="allow", reason="artifact_deploy_intent_slots_present")


def _has_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
