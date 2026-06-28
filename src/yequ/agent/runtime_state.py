"""Explicit Agent runtime states.

The current stream runner still executes inside a generator, but durable turn
state should already speak in runtime-state-machine terms.  These constants are
the contract used by checkpoints, projections, diagnostics, and the future
loop extraction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

AgentRunStatus = Literal[
    "created",
    "building_context",
    "model_running",
    "validating_tools",
    "preflighting",
    "waiting_approval",
    "executing_tools",
    "observing",
    "synthesizing",
    "succeeded",
    "failed",
    "cancelled",
]

TERMINAL_AGENT_RUN_STATUSES = {"succeeded", "failed", "cancelled"}

RuntimeDecisionKind = Literal["continue", "final", "failure", "waiting_approval"]
ToolObservationEventType = Literal[
    "agent.tool_call.completed",
    "agent.tool_call.failed",
    "agent.tool_call.waiting_approval",
]


@dataclass(frozen=True)
class AgentRuntimeLimits:
    max_depth: int = 5
    max_steps: int = 20
    max_total_duration_sec: int = 300


@dataclass(frozen=True)
class AgentRuntimeFailure:
    error_code: str
    message: str
    status: AgentRunStatus = "failed"

    def as_event_data(self) -> dict[str, object]:
        return {"error_code": self.error_code, "message": self.message}


@dataclass(frozen=True)
class AgentRuntimeIteration:
    iteration: int
    max_steps: int

    def as_event_data(self) -> dict[str, object]:
        return {"iteration": self.iteration, "max_steps": self.max_steps}


@dataclass(frozen=True)
class AgentRuntimeDecision:
    kind: RuntimeDecisionKind
    status: AgentRunStatus
    final_message: str = ""
    failure: AgentRuntimeFailure | None = None


@dataclass
class AgentRuntimeController:
    """Small explicit controller for Agent loop state and guardrails.

    The ReAct runner still performs provider and tool IO, but this object owns
    runtime limits and terminal decision semantics.  It is the boundary that can
    later grow into a full graph/state-machine executor.
    """

    limits: AgentRuntimeLimits = field(default_factory=AgentRuntimeLimits)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    current_step: int = 0
    call_path: Sequence[str] = field(default_factory=list)
    status: AgentRunStatus = "created"

    def check_initial_constraints(self, now: datetime | None = None) -> AgentRuntimeFailure | None:
        now = now or datetime.now(UTC)
        elapsed = (now - self.started_at).total_seconds()
        if elapsed > self.limits.max_total_duration_sec:
            self.status = "failed"
            return AgentRuntimeFailure(
                error_code="max_duration_exceeded",
                message=f"Duration {elapsed:.1f}s exceeds max",
            )
        if self.current_step >= self.limits.max_steps:
            self.status = "failed"
            return AgentRuntimeFailure(
                error_code="max_steps_exceeded",
                message=f"Max steps {self.limits.max_steps} exceeded",
            )
        if len(self.call_path) >= self.limits.max_depth:
            self.status = "failed"
            return AgentRuntimeFailure(
                error_code="call_depth_exceeded",
                message=f"Depth {len(self.call_path)} exceeds max {self.limits.max_depth}",
            )
        return None

    def begin_iteration(
        self, now: datetime | None = None
    ) -> AgentRuntimeIteration | AgentRuntimeFailure:
        failure = self.check_initial_constraints(now)
        if failure:
            return failure
        self.current_step += 1
        self.status = "model_running"
        return AgentRuntimeIteration(
            iteration=self.current_step,
            max_steps=self.limits.max_steps,
        )

    def provider_failed(self, message: str) -> AgentRuntimeFailure:
        self.status = "failed"
        return AgentRuntimeFailure(error_code="llm_error", message=message)

    def decide_provider_output(
        self,
        *,
        assistant_text: str,
        tool_calls: Sequence[object],
    ) -> AgentRuntimeDecision:
        if tool_calls:
            self.status = "validating_tools"
            return AgentRuntimeDecision(kind="continue", status=self.status)
        if assistant_text:
            self.status = "succeeded"
            return AgentRuntimeDecision(
                kind="final",
                status=self.status,
                final_message=assistant_text,
            )
        failure = AgentRuntimeFailure(
            error_code="agent_protocol_error",
            message="Provider returned neither assistant text nor tool calls.",
        )
        self.status = "failed"
        return AgentRuntimeDecision(kind="failure", status=self.status, failure=failure)

    def waiting_approval(self) -> AgentRuntimeDecision:
        self.status = "waiting_approval"
        return AgentRuntimeDecision(kind="waiting_approval", status=self.status)

    def missing_final_answer(self) -> AgentRuntimeFailure:
        self.status = "failed"
        return AgentRuntimeFailure(
            error_code="agent_protocol_error",
            message="Agent loop ended without a provider final answer.",
        )


class AgentToolObservationCollector:
    def __init__(self, provider_call_order: dict[str, int]) -> None:
        self._provider_call_order = dict(provider_call_order)
        self._results: list[dict[str, object]] = []
        self.has_waiting_approval = False

    def record_event(self, event_type: str, data: dict[str, object]) -> bool:
        if event_type not in {
            "agent.tool_call.completed",
            "agent.tool_call.failed",
            "agent.tool_call.waiting_approval",
        }:
            return False

        call_id = str(data.get("call_id", ""))
        name = str(data.get("name", ""))
        if event_type == "agent.tool_call.completed":
            self._results.append(
                {
                    "name": name,
                    "call_id": call_id,
                    "status": "succeeded",
                    "result": data.get("result"),
                    "target_node_id": data.get("target_node_id"),
                }
            )
            return True
        if event_type == "agent.tool_call.failed":
            self._results.append(
                {
                    "name": name,
                    "call_id": call_id,
                    "status": "failed",
                    "error": data.get("message"),
                    "error_code": data.get("error_code"),
                    "error_details": data.get("details"),
                    "target_node_id": data.get("target_node_id"),
                }
            )
            return True

        self.has_waiting_approval = True
        self._results.append(
            {
                "name": name,
                "call_id": call_id,
                "status": "waiting_approval",
                "approval_id": data.get("approval_id"),
                "target_node_id": data.get("target_node_id"),
            }
        )
        return True

    def ordered_results(self) -> list[dict[str, object]]:
        return sorted(
            self._results,
            key=lambda result: self._provider_call_order.get(str(result["call_id"]), 999),
        )


def status_for_stream_event(event_type: str, error_code: str | None = None) -> str | None:
    if event_type == "stream.open":
        return "created"
    if event_type == "agent.prompt_context":
        return "building_context"
    if event_type == "agent.provider.started":
        return "model_running"
    if event_type == "agent.tool_call.created":
        return "validating_tools"
    if event_type in {"agent.invocation.created", "agent.job.queued", "agent.job.running"}:
        return "executing_tools"
    if event_type in {"agent.approval.required", "agent.tool_call.waiting_approval"}:
        return "waiting_approval"
    if event_type == "agent.observing":
        return "observing"
    if event_type in {"agent.output.delta", "agent.synthesizing"}:
        return "synthesizing"
    if event_type == "agent.completed":
        return "succeeded"
    if event_type in {"agent.failed", "agent.provider.failed"}:
        return "failed"
    if error_code == "cancelled":
        return "cancelled"
    return None
