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

from yequ.agent.limits import (
    DEFAULT_AGENT_MAX_DEPTH,
    DEFAULT_AGENT_MAX_STEPS,
    DEFAULT_AGENT_MAX_TOTAL_DURATION_SEC,
)
from yequ.ycr.client import YcrClient, YcrError, get_ycr_client

AgentRunStatus = Literal[
    "created",
    "building_context",
    "model_running",
    "validating_tools",
    "preflighting",
    "waiting_approval",
    "waiting_operation",
    "executing_tools",
    "observing",
    "synthesizing",
    "succeeded",
    "failed",
    "cancelled",
]

RuntimeDecisionKind = Literal[
    "continue",
    "final",
    "failure",
    "waiting_approval",
    "waiting_operation",
]
ToolObservationEventType = Literal[
    "agent.tool_call.completed",
    "agent.tool_call.failed",
    "agent.tool_call.waiting_approval",
    "agent.tool_call.waiting_operation",
]


@dataclass(frozen=True)
class AgentRuntimeLimits:
    max_depth: int = DEFAULT_AGENT_MAX_DEPTH
    max_steps: int = DEFAULT_AGENT_MAX_STEPS
    max_total_duration_sec: int = DEFAULT_AGENT_MAX_TOTAL_DURATION_SEC


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

    def waiting_operation(self) -> AgentRuntimeDecision:
        self.status = "waiting_operation"
        return AgentRuntimeDecision(kind="waiting_operation", status=self.status)

    def missing_final_answer(self) -> AgentRuntimeFailure:
        self.status = "failed"
        return AgentRuntimeFailure(
            error_code="agent_protocol_error",
            message="Agent loop ended without a provider final answer.",
        )


@dataclass
class AgentRunGraph:
    """Explicit state graph facade for one AgentRun.

    Provider IO and tool IO still live in the stream runner, but all decisions
    that advance the AgentRun status pass through this object. This keeps the
    loop from growing hidden side-channel state as resume/subagent support
    expands.
    """

    controller: AgentRuntimeController
    loop_state: AgentRunStatus = "created"

    def begin_iteration(
        self, now: datetime | None = None
    ) -> AgentRuntimeIteration | AgentRuntimeFailure:
        result = self.controller.begin_iteration(now)
        self.loop_state = self.controller.status
        return result

    def provider_failed(self, message: str) -> AgentRuntimeFailure:
        failure = self.controller.provider_failed(message)
        self.loop_state = failure.status
        return failure

    def decide_provider_output(
        self,
        *,
        assistant_text: str,
        tool_calls: Sequence[object],
    ) -> AgentRuntimeDecision:
        decision = self.controller.decide_provider_output(
            assistant_text=assistant_text,
            tool_calls=tool_calls,
        )
        self.loop_state = decision.status
        return decision

    def observe_tool_results(
        self,
        results: Sequence[dict[str, object]],
    ) -> AgentRuntimeDecision | None:
        statuses = {str(result.get("status") or "") for result in results}
        if "waiting_operation" in statuses:
            decision = self.controller.waiting_operation()
            self.loop_state = decision.status
            return decision
        if "waiting_approval" in statuses:
            decision = self.controller.waiting_approval()
            self.loop_state = decision.status
            return decision
        self.controller.status = "observing"
        self.loop_state = "observing"
        return None

    def missing_final_answer(self) -> AgentRuntimeFailure:
        failure = self.controller.missing_final_answer()
        self.loop_state = failure.status
        return failure


class AgentToolObservationCollector:
    def __init__(
        self,
        provider_call_order: dict[str, int],
        *,
        ycr_client: YcrClient | None = None,
    ) -> None:
        self._provider_call_order = dict(provider_call_order)
        self._ycr_client = ycr_client or get_ycr_client()
        self._results: list[dict[str, object]] = []
        self._latest_result: dict[str, object] | None = None
        self.has_waiting_approval = False
        self.has_waiting_operation = False

    @property
    def latest_result(self) -> dict[str, object] | None:
        return self._latest_result

    async def _project_observation(
        self,
        *,
        name: str,
        call_id: str,
        status: str,
        result: object,
        target_node_id: object = None,
    ) -> dict[str, object]:
        try:
            return await self._ycr_client.project_tool_observation(
                name=name,
                call_id=call_id,
                status=status,
                result=result,
                target_node_id=target_node_id,
            )
        except YcrError as exc:
            return {
                "name": name,
                "call_id": call_id,
                "status": "failed",
                "result": {
                    "kind": "tool_observation",
                    "summary": f"YCR projection failed: {exc.message}",
                    "facts": {
                        "error_code": exc.code,
                        "message": exc.message,
                    },
                    "refs": [],
                    "omitted": [],
                    "truncated": False,
                    "trust_level": "center_runtime_error",
                    "projection_policy": "tool_observation_projection_error_v1",
                },
                "target_node_id": target_node_id,
                "error": exc.message,
                "error_code": exc.code,
                "ycr": {
                    "projected": True,
                    "projection_policy": "tool_observation_projection_error_v1",
                    "projection_version": 1,
                },
            }

    async def record_event(self, event_type: str, data: dict[str, object]) -> bool:
        self._latest_result = None
        if event_type not in {
            "agent.tool_call.completed",
            "agent.tool_call.failed",
            "agent.tool_call.waiting_approval",
            "agent.tool_call.waiting_operation",
        }:
            return False

        call_id = str(data.get("call_id", ""))
        name = str(data.get("name", ""))
        if event_type == "agent.tool_call.completed":
            projected = await self._project_observation(
                name=name,
                call_id=call_id,
                status="succeeded",
                result=data.get("result"),
                target_node_id=data.get("target_node_id"),
            )
            self._results.append(projected)
            self._latest_result = projected
            return True
        if event_type == "agent.tool_call.failed":
            failed_result = {
                "message": data.get("message"),
                "error_code": data.get("error_code"),
                "error_details": data.get("details"),
                "status": data.get("status") or "failed",
            }
            projected = await self._project_observation(
                name=name,
                call_id=call_id,
                status="failed",
                result=failed_result,
                target_node_id=data.get("target_node_id"),
            )
            projected["error"] = data.get("message")
            projected["error_code"] = data.get("error_code")
            projected["error_details"] = data.get("details")
            self._results.append(projected)
            self._latest_result = projected
            return True

        if event_type == "agent.tool_call.waiting_operation":
            self.has_waiting_operation = True
            waiting_result = {
                "operation_id": data.get("operation_id"),
                "wait_handle": data.get("wait_handle"),
                "status": "waiting_operation",
            }
            projected = await self._project_observation(
                name=name,
                call_id=call_id,
                status="waiting_operation",
                result=waiting_result,
                target_node_id=data.get("target_node_id"),
            )
            projected["operation_id"] = data.get("operation_id")
            projected["wait_handle"] = data.get("wait_handle")
            self._results.append(projected)
            self._latest_result = projected
            return True

        self.has_waiting_approval = True
        waiting_result = {
            "approval_id": data.get("approval_id"),
            "message": data.get("message") or "Approval required",
            "status": "waiting_approval",
        }
        projected = await self._project_observation(
            name=name,
            call_id=call_id,
            status="waiting_approval",
            result=waiting_result,
            target_node_id=data.get("target_node_id"),
        )
        projected["approval_id"] = data.get("approval_id")
        self._results.append(projected)
        self._latest_result = projected
        return True

    def ordered_results(self) -> list[dict[str, object]]:
        return sorted(
            self._results,
            key=lambda result: self._provider_call_order.get(str(result["call_id"]), 999),
        )
