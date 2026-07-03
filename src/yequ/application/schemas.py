"""Shared DTOs for application-layer tool execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

ToolExecutionStatus = Literal[
    "not_found",
    "unavailable",
    "denied",
    "approval_required",
    "waiting_operation",
    "created",
    "running",
    "succeeded",
    "failed",
    "timeout",
    "cancelled",
    "partial",
]


@dataclass(slots=True)
class ExecuteToolCommand:
    """Command to execute a registered Center function."""

    function_name: str
    input_data: dict[str, object] = field(default_factory=dict)
    actor_type: str = "user"
    actor_id: str = "admin"
    session_id: str | None = None
    target_node_id: str | None = None
    execution_mode: str = "auto"
    max_depth: int | None = None
    max_steps: int | None = None
    max_total_duration_sec: int | None = None
    call_path: list[str] = field(default_factory=list)
    approval_id: str | None = None
    dry_run: bool = False
    wait_for_result: bool = False
    deadline: datetime | None = None
    resource_keys: list[str] | None = None
    timeout_sec: int | None = None
    lease_sec: int | None = None
    declared_risk: str | None = None
    declared_effect: str | None = None


@dataclass(slots=True)
class ApprovalRequiredResult:
    """Details returned when execution must wait for approval."""

    approval_id: str
    function_name: str
    target_node_id: str
    risk: str
    effect: str
    resource_keys: list[str] = field(default_factory=list)
    reason: str = "approval_required"


@dataclass(slots=True)
class ExecuteToolResult:
    """Result of a Center function execution request."""

    status: ToolExecutionStatus
    function_name: str
    target_node_id: str | None = None
    invocation_id: str | None = None
    job_id: str | None = None
    approval: ApprovalRequiredResult | None = None
    risk: str = "safe"
    effect: str = "read"
    output_data: dict[str, object] | None = None
    operation_id: str | None = None
    wait_handle: dict[str, object] | None = None
    execution_plan: dict[str, object] | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_details: dict[str, object] | None = None

    @property
    def approval_id(self) -> str | None:
        return self.approval.approval_id if self.approval else None

    @property
    def terminal(self) -> bool:
        return self.status in {
            "succeeded",
            "failed",
            "timeout",
            "cancelled",
            "partial",
            "denied",
            "unavailable",
            "not_found",
            "approval_required",
            "waiting_operation",
        }


@dataclass(slots=True)
class ToolExecutionEvent:
    """Structured event emitted by application-level tool execution."""

    event_type: str
    result: ExecuteToolResult
    data: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class ToolPreflightCommand:
    """Read-only command used before scheduling tool execution."""

    function_name: str
    execution_mode: str = "auto"
    target_node_id: str | None = None
    declared_risk: str | None = None
    declared_effect: str | None = None


@dataclass(slots=True)
class ToolPreflightResult:
    """Result of checking a function before execution scheduling."""

    function_name: str
    status: Literal["ok", "unavailable", "denied"]
    target_node_id: str | None = None
    risk: str = "safe"
    effect: str = "read"
    resource_keys: list[str] = field(default_factory=list)
    conflict_policy: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    @property
    def is_concurrent_safe(self) -> bool:
        return (
            self.status == "ok"
            and self.risk == "safe"
            and self.effect == "read"
            and not (self.resource_keys and self.conflict_policy == "serialize")
        )
