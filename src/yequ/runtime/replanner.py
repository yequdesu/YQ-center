"""Deterministic Agent runtime replanner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

JsonDict = dict[str, Any]

ReplannerAction = Literal[
    "continue_llm",
    "wait_approval",
    "wait_operation",
    "ask_user",
    "complete",
    "fail",
]


@dataclass(frozen=True)
class ReplannerDecision:
    action: ReplannerAction
    reason_code: str
    target_operation_id: str | None = None
    target_approval_id: str | None = None
    missing_slots: list[JsonDict] = field(default_factory=list)
    blocked_evidence: list[JsonDict] = field(default_factory=list)
    next_prompt: str | None = None

    def to_dict(self) -> JsonDict:
        return {
            "action": self.action,
            "reason_code": self.reason_code,
            "target_operation_id": self.target_operation_id,
            "target_approval_id": self.target_approval_id,
            "missing_slots": self.missing_slots,
            "blocked_evidence": self.blocked_evidence,
            "next_prompt": self.next_prompt,
        }


def decide_next_action(task_state: JsonDict) -> ReplannerDecision:
    pending_approvals = [
        item
        for item in task_state.get("pending_approvals", [])
        if isinstance(item, dict) and item.get("status") in {None, "pending", "waiting"}
    ]
    if pending_approvals:
        approval_id = _string_or_none(pending_approvals[0].get("approval_id"))
        return ReplannerDecision(
            action="wait_approval",
            reason_code="pending_approval",
            target_approval_id=approval_id,
        )

    pending_operations = [
        item
        for item in task_state.get("pending_operations", [])
        if isinstance(item, dict)
        and item.get("status") not in {"succeeded", "failed", "cancelled", "timeout"}
    ]
    if pending_operations:
        operation_id = _string_or_none(pending_operations[0].get("operation_id"))
        return ReplannerDecision(
            action="wait_operation",
            reason_code="pending_operation",
            target_operation_id=operation_id,
        )

    completion = task_state.get("completion")
    completion = completion if isinstance(completion, dict) else {}
    if completion.get("status") == "complete":
        return ReplannerDecision(action="complete", reason_code="completion_satisfied")
    if completion.get("status") == "failed":
        return ReplannerDecision(
            action="fail",
            reason_code="completion_failed",
            blocked_evidence=_dict_items(task_state.get("blockers")),
        )
    missing = _dict_items(completion.get("missing"))
    blockers = _dict_items(task_state.get("blockers"))
    if missing and not _has_viable_working_set(task_state):
        return ReplannerDecision(
            action="ask_user",
            reason_code="missing_user_information",
            missing_slots=missing,
            blocked_evidence=blockers,
        )
    if _all_paths_failed(task_state):
        return ReplannerDecision(
            action="fail",
            reason_code="all_paths_failed",
            blocked_evidence=blockers,
        )
    return ReplannerDecision(action="continue_llm", reason_code="ready_to_continue")


def apply_decision(task_state: JsonDict, decision: ReplannerDecision) -> JsonDict:
    completion = task_state.get("completion")
    if not isinstance(completion, dict):
        completion = {"status": "in_progress", "criteria": [], "satisfied": [], "missing": []}
        task_state["completion"] = completion
    if decision.action == "wait_approval":
        completion["status"] = "waiting_approval"
    elif decision.action == "wait_operation":
        completion["status"] = "waiting_operation"
    elif decision.action == "ask_user":
        completion["status"] = "ask_user"
    elif decision.action == "complete":
        completion["status"] = "complete"
    elif decision.action == "fail":
        completion["status"] = "failed"
    else:
        completion["status"] = "in_progress"
    task_state["last_decision"] = decision.to_dict()
    return task_state


def _has_viable_working_set(task_state: JsonDict) -> bool:
    working_set = task_state.get("working_set")
    if not isinstance(working_set, dict):
        return False
    capabilities = working_set.get("capabilities")
    return isinstance(capabilities, list) and bool(capabilities)


def _all_paths_failed(task_state: JsonDict) -> bool:
    blockers = _dict_items(task_state.get("blockers"))
    return bool(blockers) and all(item.get("terminal") is True for item in blockers)


def _dict_items(value: object) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_or_none(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
