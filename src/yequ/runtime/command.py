"""Runtime command types for Center Execution Runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from yequ.application.schemas import ExecuteToolCommand


@dataclass(slots=True)
class RuntimeCommand:
    """Normalized execution command consumed by CenterExecutionRuntime."""

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
    suppress_operation: bool = False

    @classmethod
    def from_execute_tool_command(cls, command: ExecuteToolCommand) -> RuntimeCommand:
        return cls(
            function_name=command.function_name,
            input_data=dict(command.input_data),
            actor_type=command.actor_type,
            actor_id=command.actor_id,
            session_id=command.session_id,
            target_node_id=command.target_node_id,
            execution_mode=command.execution_mode,
            max_depth=command.max_depth,
            max_steps=command.max_steps,
            max_total_duration_sec=command.max_total_duration_sec,
            call_path=list(command.call_path),
            approval_id=command.approval_id,
            dry_run=command.dry_run,
            wait_for_result=command.wait_for_result,
            deadline=command.deadline,
            resource_keys=list(command.resource_keys) if command.resource_keys else None,
            timeout_sec=command.timeout_sec,
            lease_sec=command.lease_sec,
            declared_risk=command.declared_risk,
            declared_effect=command.declared_effect,
        )

    def to_execute_tool_command(self) -> ExecuteToolCommand:
        return ExecuteToolCommand(
            function_name=self.function_name,
            input_data=dict(self.input_data),
            actor_type=self.actor_type,
            actor_id=self.actor_id,
            session_id=self.session_id,
            target_node_id=self.target_node_id,
            execution_mode=self.execution_mode,
            max_depth=self.max_depth,
            max_steps=self.max_steps,
            max_total_duration_sec=self.max_total_duration_sec,
            call_path=list(self.call_path),
            approval_id=self.approval_id,
            dry_run=self.dry_run,
            wait_for_result=self.wait_for_result,
            deadline=self.deadline,
            resource_keys=list(self.resource_keys) if self.resource_keys else None,
            timeout_sec=self.timeout_sec,
            lease_sec=self.lease_sec,
            declared_risk=self.declared_risk,
            declared_effect=self.declared_effect,
        )
