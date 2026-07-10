"""Center Execution Runtime v2.

This module is the execution boundary after admission.  Agent, admin, CLI and
workflow code should enter here instead of calling application-layer legacy
tool execution services.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.application.errors import ToolInvocationError
from yequ.application.meta_tools import CENTER_META_TOOLS
from yequ.application.schemas import (
    ApprovalRequiredResult,
    ExecuteToolCommand,
    ExecuteToolResult,
    ToolExecutionEvent,
)
from yequ.config import get_settings
from yequ.models.invocation import Invocation
from yequ.models.job import Job
from yequ.models.timeline import TimelineEvent
from yequ.runtime.admission import ExecutionAdmissionService
from yequ.runtime.command import RuntimeCommand
from yequ.runtime.guards import ExecutionGuard, GuardDecision
from yequ.runtime.input_utils import (
    int_or_default,
    int_or_none,
    required_string,
    runtime_error,
    string_or_none,
)
from yequ.runtime.meta_tools import execute_inline_meta_tool
from yequ.services.approval_service import (
    consume_approval,
    create_approval,
    verify_approval,
)
from yequ.services.capability_resolver import ResolvedCapability, resolve_function
from yequ.services.invocation_service import create_invocation, start_invocation
from yequ.services.job_service import create_job
from yequ.services.policy import check_policy_l2
from yequ.services.timeline_writer import add_timeline_event


class CenterExecutionRuntime:
    """Unified execution boundary for Center capabilities and workflows."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def execute(
        self,
        command: RuntimeCommand | ExecuteToolCommand,
    ) -> ExecuteToolResult:
        runtime_command = _as_runtime_command(command)
        execute_command = runtime_command.to_execute_tool_command()
        guard_decision = ExecutionGuard().evaluate(runtime_command)
        if not guard_decision.allowed:
            return _guard_error(runtime_command, guard_decision)
        admission_plan = ExecutionAdmissionService().plan(execute_command)

        if runtime_command.function_name == "capability.invoke":
            result = await self._execute_capability_invoke(runtime_command)
        elif runtime_command.function_name == "transfer.create":
            result = await self._execute_transfer_create(runtime_command)
        elif runtime_command.function_name == "transfer.resume":
            result = await self._execute_transfer_resume(runtime_command)
        elif runtime_command.function_name == "artifact.deploy":
            result = await self._execute_artifact_deploy(runtime_command)
        elif runtime_command.function_name in CENTER_META_TOOLS:
            result = await execute_inline_meta_tool(self.db, runtime_command)
        else:
            result = await self._execute_node_job(runtime_command)

        result.execution_plan = admission_plan.to_dict()
        return result

    async def execute_stream(
        self,
        command: RuntimeCommand | ExecuteToolCommand,
    ) -> AsyncIterator[ToolExecutionEvent]:
        result = await self.execute(command)
        yield ToolExecutionEvent("tool.execution.result", result)

    async def _execute_capability_invoke(
        self,
        command: RuntimeCommand,
    ) -> ExecuteToolResult:
        from yequ.services.capability_registry import (
            resolve_capability_invoke_target,
            resolve_center_capability_name,
        )

        input_data = dict(command.input_data)
        capability_ref = string_or_none(input_data.get("capability_ref")) or string_or_none(
            input_data.get("capability_id")
        )
        source_id = string_or_none(input_data.get("source_id"))
        node_id = string_or_none(input_data.get("node_id")) or command.target_node_id
        tool_input = input_data.get("input")
        if tool_input is None:
            tool_input = input_data.get("arguments")
        if tool_input is None:
            tool_input = {}
        if not isinstance(tool_input, dict):
            return runtime_error(
                command,
                "invalid_input",
                "capability.invoke input must be an object",
            )

        center_function = await resolve_center_capability_name(
            self.db,
            capability_ref=capability_ref,
            source_id=source_id,
        )
        if center_function:
            if center_function == "capability.invoke":
                return runtime_error(
                    command,
                    "invalid_input",
                    "capability.invoke cannot invoke itself",
                )
            delegated = RuntimeCommand(
                function_name=center_function,
                input_data=dict(tool_input),
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                session_id=command.session_id,
                target_node_id=node_id,
                execution_mode=command.execution_mode,
                max_depth=command.max_depth,
                max_steps=command.max_steps,
                max_total_duration_sec=command.max_total_duration_sec,
                call_path=list(command.call_path or []) + ["capability.invoke"],
                approval_id=command.approval_id,
                dry_run=command.dry_run,
                wait_for_result=command.wait_for_result,
                deadline=command.deadline,
                timeout_sec=command.timeout_sec,
                lease_sec=command.lease_sec,
                suppress_operation=command.suppress_operation,
            )
            return await self.execute(delegated)

        try:
            target = await resolve_capability_invoke_target(
                self.db,
                capability_ref=capability_ref,
                source_id=source_id,
                node_id=node_id,
            )
        except ValueError as exc:
            return runtime_error(command, _capability_invoke_error_code(exc), str(exc))

        if target.canonical_name == "artifact.download_file" and not node_id:
            return runtime_error(
                command,
                "target_node_required",
                (
                    "node_id is required for artifact.download_file because output_path "
                    "is interpreted on the target Node filesystem. Use artifact.deploy "
                    "for user-facing artifact placement."
                ),
            )

        validation_error = _validate_invoked_tool_input(command, target, dict(tool_input))
        if validation_error is not None:
            return validation_error

        delegated = RuntimeCommand(
            function_name=target.registered_name,
            input_data=dict(tool_input),
            actor_type=command.actor_type,
            actor_id=command.actor_id,
            session_id=command.session_id,
            target_node_id=target.node_id,
            execution_mode=command.execution_mode,
            max_depth=command.max_depth,
            max_steps=command.max_steps,
            max_total_duration_sec=command.max_total_duration_sec,
            call_path=list(command.call_path or []) + ["capability.invoke"],
            approval_id=command.approval_id,
            dry_run=command.dry_run,
            wait_for_result=command.wait_for_result,
            deadline=command.deadline,
            resource_keys=command.resource_keys,
            timeout_sec=command.timeout_sec or target.timeout_sec,
            lease_sec=command.lease_sec,
            declared_risk=target.risk,
            declared_effect=target.effect,
            suppress_operation=command.suppress_operation,
        )
        return await self.execute(delegated)

    async def _execute_artifact_deploy(self, command: RuntimeCommand) -> ExecuteToolResult:
        from yequ.application.artifact_deploy import (
            ArtifactDeployApplicationService,
            ArtifactDeployCommand,
        )

        input_data = dict(command.input_data)
        try:
            artifact_id = required_string(input_data.get("artifact_id"), "artifact_id")
            target_node_id = required_string(input_data.get("target_node_id"), "target_node_id")
            output_path = required_string(input_data.get("output_path"), "output_path")
        except ValueError as exc:
            return runtime_error(command, "invalid_input", str(exc))
        mode = string_or_none(input_data.get("mode")) or "fail_if_exists"
        if mode not in {"fail_if_exists", "overwrite"}:
            return runtime_error(
                command,
                "invalid_input",
                "mode must be fail_if_exists or overwrite",
            )
        try:
            await ArtifactDeployApplicationService(self.db).validate_preflight(
                ArtifactDeployCommand(
                    artifact_id=artifact_id,
                    target_node_id=target_node_id,
                    output_path=output_path,
                    mode=mode,
                    preflight_id=string_or_none(input_data.get("preflight_id")),
                    skip_preflight=bool(input_data.get("skip_preflight", False)),
                    skip_reason=string_or_none(input_data.get("skip_reason")),
                )
            )
        except ValueError as exc:
            return runtime_error(command, "invalid_input", str(exc))

        delegated = RuntimeCommand(
            function_name="capability.invoke",
            input_data={
                "capability_ref": "artifact.download_file",
                "source_id": string_or_none(input_data.get("source_id")),
                "node_id": target_node_id,
                "input": {
                    "artifact_id": artifact_id,
                    "output_path": output_path,
                    "mode": mode,
                },
            },
            actor_type=command.actor_type,
            actor_id=command.actor_id,
            session_id=command.session_id,
            target_node_id=target_node_id,
            execution_mode=command.execution_mode,
            max_depth=command.max_depth,
            max_steps=command.max_steps,
            max_total_duration_sec=command.max_total_duration_sec,
            call_path=list(command.call_path or []) + ["artifact.deploy"],
            approval_id=command.approval_id,
            dry_run=command.dry_run,
            wait_for_result=command.wait_for_result,
            deadline=command.deadline,
            timeout_sec=command.timeout_sec or 300,
            lease_sec=command.lease_sec,
            declared_risk="maintenance",
            declared_effect="write",
            suppress_operation=command.suppress_operation,
        )
        result = await self._execute_capability_invoke(delegated)
        result.function_name = command.function_name
        result.output_data = dict(result.output_data or {})
        result.output_data["artifact_deploy"] = {
            "artifact_id": artifact_id,
            "target_node_id": target_node_id,
            "output_path": output_path,
            "mode": mode,
            "preflight_id": string_or_none(input_data.get("preflight_id")),
            "node_capability_ref": "artifact.download_file",
        }
        return result

    async def _execute_transfer_create(self, command: RuntimeCommand) -> ExecuteToolResult:
        from yequ.application.transfer import TransferApplicationService, TransferCreateCommand
        from yequ.services.operation_service import OperationService, wait_handle_for_operation

        input_data = dict(command.input_data)
        try:
            transfer = await TransferApplicationService(self.db).create(
                TransferCreateCommand(
                    source_node_id=required_string(
                        input_data.get("source_node_id"),
                        "source_node_id",
                    ),
                    target_node_id=required_string(
                        input_data.get("target_node_id"),
                        "target_node_id",
                    ),
                    source_path=required_string(input_data.get("source_path"), "source_path"),
                    target_output_dir=string_or_none(input_data.get("target_output_dir")),
                    target_path=string_or_none(input_data.get("target_path")),
                    code=string_or_none(input_data.get("code")),
                    relay_url=string_or_none(input_data.get("relay_url")),
                    route_policy=string_or_none(input_data.get("route_policy")),
                    direct_ip=string_or_none(input_data.get("direct_ip")),
                    multicast_address=string_or_none(input_data.get("multicast_address")),
                    resume_mode=string_or_none(input_data.get("resume_mode")),
                    timeout_sec=int_or_default(input_data.get("timeout_sec"), 3600),
                    expected_sha256=string_or_none(input_data.get("expected_sha256")),
                    preflight_id=string_or_none(input_data.get("preflight_id")),
                    skip_preflight=bool(input_data.get("skip_preflight", False)),
                    skip_reason=string_or_none(input_data.get("skip_reason")),
                    actor_type=command.actor_type,
                    actor_id=command.actor_id,
                    session_id=command.session_id,
                    execution_mode=command.execution_mode,
                )
            )
            operation = await OperationService(self.db).create_for_transfer(
                transfer,
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                session_id=command.session_id,
            )
        except ValueError as exc:
            return runtime_error(command, "invalid_input", str(exc))

        wait_handle = wait_handle_for_operation(operation)
        return ExecuteToolResult(
            status="waiting_operation",
            function_name=command.function_name,
            target_node_id=command.target_node_id,
            risk="maintenance",
            effect="external",
            output_data={
                "transfer": transfer,
                "operation": operation,
                "wait_handle": wait_handle,
            },
            operation_id=str(operation["operation_id"]),
            wait_handle=wait_handle,
        )

    async def _execute_transfer_resume(self, command: RuntimeCommand) -> ExecuteToolResult:
        from yequ.application.transfer import TransferApplicationService, TransferResumeCommand
        from yequ.services.operation_service import OperationService, wait_handle_for_operation

        input_data = dict(command.input_data)
        try:
            transfer = await TransferApplicationService(self.db).resume(
                TransferResumeCommand(
                    transfer_id=required_string(input_data.get("transfer_id"), "transfer_id"),
                    code=string_or_none(input_data.get("code")),
                    relay_url=string_or_none(input_data.get("relay_url")),
                    route_policy=string_or_none(input_data.get("route_policy")),
                    direct_ip=string_or_none(input_data.get("direct_ip")),
                    multicast_address=string_or_none(input_data.get("multicast_address")),
                    timeout_sec=int_or_none(input_data.get("timeout_sec")),
                    actor_type=command.actor_type,
                    actor_id=command.actor_id,
                    session_id=command.session_id,
                    execution_mode=command.execution_mode,
                )
            )
            operation = await OperationService(self.db).create_for_transfer(
                transfer,
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                session_id=command.session_id,
            )
        except ValueError as exc:
            return runtime_error(command, "invalid_input", str(exc))

        wait_handle = wait_handle_for_operation(operation)
        return ExecuteToolResult(
            status="waiting_operation",
            function_name=command.function_name,
            target_node_id=command.target_node_id,
            risk="maintenance",
            effect="external",
            output_data={
                "transfer": transfer,
                "operation": operation,
                "wait_handle": wait_handle,
            },
            operation_id=str(operation["operation_id"]),
            wait_handle=wait_handle,
        )

    async def _execute_node_job(self, command: RuntimeCommand) -> ExecuteToolResult:
        input_data = dict(command.input_data)
        approval_id = command.approval_id or string_or_none(input_data.get("approval_id"))
        approval_bypassed = False

        if command.declared_risk or command.declared_effect:
            declared_policy = check_policy_l2(
                execution_mode=command.execution_mode,
                risk_level=command.declared_risk or "safe",
                effect=command.declared_effect or "read",
            )
            if not declared_policy.allowed and declared_policy.decision != "ask":
                return ExecuteToolResult(
                    status="denied",
                    function_name=command.function_name,
                    target_node_id=command.target_node_id,
                    risk=command.declared_risk or "safe",
                    effect=command.declared_effect or "read",
                    error_code="policy_denied",
                    error_message=declared_policy.reason or "Policy denied",
                )

        resolved = await resolve_function(
            self.db,
            command.function_name,
            target_node_id=command.target_node_id,
            settings=get_settings(),
        )
        if resolved is None or not resolved.available:
            return ExecuteToolResult(
                status="unavailable",
                function_name=command.function_name,
                target_node_id=command.target_node_id or (resolved.node_id if resolved else None),
                error_code=resolved.unavailable_code if resolved else "function_not_available",
                error_message=(
                    resolved.unavailable_reason
                    if resolved and resolved.unavailable_reason
                    else f"No online node has capability {command.function_name!r}"
                ),
            )

        if command.dry_run and not approval_id:
            return await self._dry_run(command, resolved, input_data)

        approval = None
        if approval_id:
            try:
                approval = await verify_approval(
                    self.db,
                    approval_id,
                    actor_id=command.actor_id,
                    session_id=command.session_id,
                    function_name=command.function_name,
                    target_node_id=resolved.node_id,
                    input_data=input_data,
                )
            except ValueError as exc:
                return ExecuteToolResult(
                    status="denied",
                    function_name=command.function_name,
                    target_node_id=resolved.node_id,
                    risk=resolved.risk,
                    effect=resolved.effect,
                    error_code="approval_invalid",
                    error_message=str(exc),
                )
        else:
            policy = check_policy_l2(
                execution_mode=command.execution_mode,
                risk_level=resolved.risk,
                effect=resolved.effect,
            )
            if not policy.allowed:
                if policy.decision == "ask":
                    if get_settings().approval_bypass_enabled:
                        approval_bypassed = True
                    else:
                        return await self._create_approval_result(command, resolved, input_data)
                else:
                    return ExecuteToolResult(
                        status="denied",
                        function_name=command.function_name,
                        target_node_id=resolved.node_id,
                        risk=resolved.risk,
                        effect=resolved.effect,
                        error_code="policy_denied",
                        error_message=policy.reason or "Policy denied",
                    )

        inv = await create_invocation(
            self.db,
            actor_type=command.actor_type,
            actor_id=command.actor_id,
            session_id=command.session_id,
            function_name=command.function_name,
            input_payload=input_data,
            target_node_id=resolved.node_id,
            execution_mode=command.execution_mode,
            max_depth=command.max_depth,
            max_steps=command.max_steps,
            max_total_duration_sec=command.max_total_duration_sec,
            call_path=command.call_path or [command.function_name],
        )
        start_invocation(inv)

        if approval is not None:
            try:
                await consume_approval(self.db, approval, invocation_id=inv.invocation_id)
            except ValueError as exc:
                return ExecuteToolResult(
                    status="denied",
                    function_name=command.function_name,
                    target_node_id=resolved.node_id,
                    invocation_id=inv.invocation_id,
                    risk=resolved.risk,
                    effect=resolved.effect,
                    error_code="approval_invalid",
                    error_message=str(exc),
                )

        resource_keys = _resource_keys(command, resolved, approval)
        try:
            job = await create_job(
                self.db,
                invocation_id=inv.invocation_id,
                node_id=resolved.node_id,
                runtime_id=resolved.runtime_id,
                function_name=command.function_name,
                input_payload=input_data,
                execution_requirements_snapshot=resolved.execution_requirements,
                timeout_sec=command.timeout_sec or resolved.timeout_sec,
                lease_sec=command.lease_sec or resolved.lease_sec,
                resource_keys=resource_keys,
                dry_run=False if approval_id else command.dry_run,
                approval_id=approval_id,
                risk=resolved.risk,
                effect=resolved.effect,
                conflict_policy=resolved.conflict_policy,
            )
        except ValueError as exc:
            invocation_id = inv.invocation_id
            resolved_node_id = resolved.node_id
            resolved_risk = resolved.risk
            resolved_effect = resolved.effect
            await self.db.rollback()
            await self._write_resource_conflict(
                command,
                node_id=resolved_node_id,
                invocation_id=invocation_id,
                message=str(exc),
            )
            return ExecuteToolResult(
                status="failed",
                function_name=command.function_name,
                target_node_id=resolved_node_id,
                invocation_id=invocation_id,
                risk=resolved_risk,
                effect=resolved_effect,
                error_code="resource_lock_conflict",
                error_message=str(exc),
            )

        if approval_bypassed:
            await self._write_approval_bypassed(
                command,
                resolved=resolved,
                invocation_id=inv.invocation_id,
                job_id=job.job_id,
            )

        await self.db.commit()

        if _should_create_job_operation(command, resolved):
            from yequ.services.operation_service import OperationService, wait_handle_for_operation

            operation = await OperationService(self.db).create_for_job(
                job,
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                session_id=command.session_id,
            )
            wait_handle = wait_handle_for_operation(operation)
            return ExecuteToolResult(
                status="waiting_operation",
                function_name=command.function_name,
                target_node_id=resolved.node_id,
                invocation_id=inv.invocation_id,
                job_id=job.job_id,
                risk=resolved.risk,
                effect=resolved.effect,
                output_data={
                    **({"approval_bypassed": True} if approval_bypassed else {}),
                    "operation": operation,
                    "wait_handle": wait_handle,
                    "job": {
                        "job_id": job.job_id,
                        "invocation_id": inv.invocation_id,
                        "node_id": resolved.node_id,
                        "function_name": command.function_name,
                        "status": job.status,
                    },
                },
                operation_id=str(operation["operation_id"]),
                wait_handle=wait_handle,
            )

        result = ExecuteToolResult(
            status="running" if command.wait_for_result else "created",
            function_name=command.function_name,
            target_node_id=resolved.node_id,
            invocation_id=inv.invocation_id,
            job_id=job.job_id,
            risk=resolved.risk,
            effect=resolved.effect,
            output_data={"approval_bypassed": True} if approval_bypassed else {},
        )

        if not command.wait_for_result:
            return result

        final_status = await self._wait_for_node_invocation_terminal(
            inv.invocation_id,
            command.deadline,
        )
        return await self._collect_terminal_result(result, final_status)

    @staticmethod
    async def _wait_for_node_invocation_terminal(
        invocation_id: str,
        deadline: datetime | None,
        poll_interval: float = 0.5,
    ) -> str:
        if deadline is None:
            return "running"

        while datetime.now(UTC) < deadline:
            from yequ.db import async_session_factory

            async with async_session_factory() as db:
                result = await db.execute(
                    select(Invocation).where(Invocation.invocation_id == invocation_id)
                )
                inv = result.scalar_one_or_none()
                if inv and inv.status in {
                    "succeeded",
                    "failed",
                    "timeout",
                    "cancelled",
                    "partial",
                }:
                    return inv.status
            await asyncio.sleep(poll_interval)

        return "timeout"

    async def _create_approval_result(
        self,
        command: RuntimeCommand,
        resolved: ResolvedCapability,
        input_data: dict[str, object],
    ) -> ExecuteToolResult:
        inv = await create_invocation(
            self.db,
            actor_type=command.actor_type,
            actor_id=command.actor_id,
            session_id=command.session_id,
            function_name=command.function_name,
            input_payload=input_data,
            target_node_id=resolved.node_id,
            execution_mode=command.execution_mode,
            max_depth=command.max_depth,
            max_steps=command.max_steps,
            max_total_duration_sec=command.max_total_duration_sec,
            call_path=command.call_path or [command.function_name],
        )
        inv.status = "waiting_approval"
        await self.db.flush()

        resource_key_template = resolved.resource_keys[0] if resolved.resource_keys else None
        approval = await create_approval(
            self.db,
            actor_id=command.actor_id,
            session_id=command.session_id,
            function_name=command.function_name,
            target_node_id=resolved.node_id,
            input_data=input_data,
            risk=resolved.risk,
            effect=resolved.effect,
            resource_keys=None if resource_key_template else resolved.resource_keys,
            resource_key_template=resource_key_template,
            invocation_id=inv.invocation_id,
        )
        from yequ.services.operation_service import OperationService, wait_handle_for_operation

        operation = await OperationService(self.db).create_for_approval(
            approval,
            actor_type=command.actor_type,
            actor_id=command.actor_id,
            session_id=command.session_id,
        )
        wait_handle = wait_handle_for_operation(operation)

        details = ApprovalRequiredResult(
            approval_id=approval.approval_id,
            function_name=command.function_name,
            target_node_id=resolved.node_id,
            risk=resolved.risk,
            effect=resolved.effect,
            resource_keys=list(approval.resource_keys or []),
        )
        return ExecuteToolResult(
            status="approval_required",
            function_name=command.function_name,
            target_node_id=resolved.node_id,
            invocation_id=inv.invocation_id,
            approval=details,
            risk=resolved.risk,
            effect=resolved.effect,
            error_code="approval_required",
            error_message="Write operation requires approval",
            output_data={
                "approval_id": approval.approval_id,
                "operation": operation,
                "wait_handle": wait_handle,
            },
            operation_id=str(operation["operation_id"]),
            wait_handle=wait_handle,
        )

    async def _dry_run(
        self,
        command: RuntimeCommand,
        resolved: ResolvedCapability,
        input_data: dict[str, object],
    ) -> ExecuteToolResult:
        event = TimelineEvent(
            global_seq=0,
            event_type="l2.dry_run.completed",
            actor_type=command.actor_type,
            actor_id=command.actor_id,
            node_id=resolved.node_id,
            data={
                "input": input_data,
                "function_name": command.function_name,
                "target_node_id": resolved.node_id,
            },
            timestamp=datetime.now(UTC),
        )
        await add_timeline_event(self.db, event)
        await self.db.commit()
        return ExecuteToolResult(
            status="approval_required",
            function_name=command.function_name,
            target_node_id=resolved.node_id,
            risk=resolved.risk,
            effect=resolved.effect,
            approval=ApprovalRequiredResult(
                approval_id="",
                function_name=command.function_name,
                target_node_id=resolved.node_id,
                risk=resolved.risk,
                effect=resolved.effect,
                resource_keys=list(resolved.resource_keys),
            ),
            error_code="approval_required",
            error_message="Dry run completed; approval is required for execution",
        )

    async def _collect_terminal_result(
        self,
        base: ExecuteToolResult,
        final_status: str,
    ) -> ExecuteToolResult:
        if base.invocation_id is None:
            raise ToolInvocationError("Cannot collect result without invocation_id")

        from yequ.db import async_session_factory

        async with async_session_factory() as db:
            inv_result = await db.execute(
                select(Invocation).where(Invocation.invocation_id == base.invocation_id)
            )
            inv = inv_result.scalar_one_or_none()
            job = None
            if base.job_id:
                job_result = await db.execute(select(Job).where(Job.job_id == base.job_id))
                job = job_result.scalar_one_or_none()

        if final_status == "succeeded":
            base.status = "succeeded"
            base.output_data = inv.result if inv else {}
            return base

        base.status = "timeout" if final_status == "timeout" else "failed"
        base.error_code = (
            (job.error_code if job else None)
            or (inv.error_code if inv else None)
            or ("tool_timeout" if final_status == "timeout" else "tool_failed")
        )
        base.error_message = (
            (job.error_message if job else None)
            or (inv.error_message if inv else None)
            or f"Tool {base.function_name} ended with {final_status}"
        )
        base.error_details = (
            job.error_details
            if job and job.error_details
            else inv.error_details
            if inv and inv.error_details
            else None
        )
        return base

    async def _write_resource_conflict(
        self,
        command: RuntimeCommand,
        *,
        node_id: str,
        invocation_id: str,
        message: str,
    ) -> None:
        event = TimelineEvent(
            global_seq=0,
            event_type="resource.lock.conflict",
            actor_type="system",
            actor_id=command.actor_id,
            node_id=node_id,
            invocation_id=invocation_id,
            data={"error": message},
            timestamp=datetime.now(UTC),
        )
        await add_timeline_event(self.db, event)
        await self.db.commit()

    async def _write_approval_bypassed(
        self,
        command: RuntimeCommand,
        *,
        resolved: ResolvedCapability,
        invocation_id: str,
        job_id: str,
    ) -> None:
        settings = get_settings()
        event = TimelineEvent(
            global_seq=0,
            event_type="approval.bypassed",
            actor_type=command.actor_type,
            actor_id=command.actor_id,
            session_id=command.session_id,
            invocation_id=invocation_id,
            job_id=job_id,
            node_id=resolved.node_id,
            data={
                "function_name": command.function_name,
                "target_node_id": resolved.node_id,
                "risk": resolved.risk,
                "effect": resolved.effect,
                "reason": settings.approval_bypass_reason,
            },
            timestamp=datetime.now(UTC),
        )
        await add_timeline_event(self.db, event)


def _as_runtime_command(command: RuntimeCommand | ExecuteToolCommand) -> RuntimeCommand:
    if isinstance(command, RuntimeCommand):
        return command
    return RuntimeCommand.from_execute_tool_command(command)


def _capability_invoke_error_code(exc: ValueError) -> str:
    message = str(exc).lower()
    if "target_node_mismatch" in message:
        return "target_node_mismatch"
    if "ambiguous" in message:
        return "ambiguous_capability_source"
    if "no active capability source matches" in message:
        return "capability_source_unavailable"
    if "capability_ref or source_id is required" in message:
        return "invalid_input"
    return "capability_not_found"


def _validate_invoked_tool_input(
    command: RuntimeCommand,
    target,
    input_data: dict[str, object],
) -> ExecuteToolResult | None:
    schema = target.input_schema if isinstance(target.input_schema, dict) else {}
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    required = [
        str(item)
        for item in schema.get("required", [])
        if isinstance(item, str) and item
    ]
    if (
        target.canonical_name == "exec.run"
        and "profile" in required
        and not target.available_execution_profiles
    ):
        return ExecuteToolResult(
            status="failed",
            function_name=command.function_name,
            target_node_id=target.node_id,
            risk=target.risk,
            effect=target.effect,
            error_code="profile_unavailable",
            error_message=_schema_error_message(
                "target runtime did not report any execution profile",
                required=required,
                target=target,
                field="profile",
                allowed=[],
            ),
            error_details={
                "capability_ref": target.canonical_name,
                "source_id": target.source_id,
                "registered_name": target.registered_name,
                "field": "profile",
                "required": required,
                "available_execution_profiles": [],
            },
        )
    missing = [
        field
        for field in required
        if field not in input_data or _missing_required_value(input_data[field])
    ]
    if missing:
        return ExecuteToolResult(
            status="failed",
            function_name=command.function_name,
            target_node_id=target.node_id,
            risk=target.risk,
            effect=target.effect,
            error_code="missing_required_slot",
            error_message=_schema_error_message(
                f"{', '.join(missing)} is required",
                required=required,
                target=target,
            ),
            error_details={
                "capability_ref": target.canonical_name,
                "source_id": target.source_id,
                "registered_name": target.registered_name,
                "missing": missing,
                "required": required,
                "available_execution_profiles": list(target.available_execution_profiles),
            },
        )

    for field, value in input_data.items():
        prop = properties.get(field)
        if not isinstance(prop, dict):
            continue
        allowed = [item for item in prop.get("enum", []) if isinstance(item, str)]
        if field == "profile" and target.canonical_name == "exec.run":
            allowed = list(target.available_execution_profiles)
        if not allowed or value in allowed:
            continue
        return ExecuteToolResult(
            status="failed",
            function_name=command.function_name,
            target_node_id=target.node_id,
            risk=target.risk,
            effect=target.effect,
            error_code="unsupported_enum_value",
            error_message=_schema_error_message(
                f"unsupported {field}: {value}",
                required=required,
                target=target,
                field=field,
                allowed=allowed,
            ),
            error_details={
                "capability_ref": target.canonical_name,
                "source_id": target.source_id,
                "registered_name": target.registered_name,
                "field": field,
                "value": value,
                "allowed": allowed,
                "required": required,
                "available_execution_profiles": list(target.available_execution_profiles),
            },
        )
    return None


def _missing_required_value(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _schema_error_message(
    message: str,
    *,
    required: list[str],
    target,
    field: str | None = None,
    allowed: list[str] | None = None,
) -> str:
    parts = [
        message,
        f"capability={target.canonical_name}",
        f"node_id={target.node_id}",
    ]
    if required:
        parts.append(f"required={required}")
    if field:
        parts.append(f"field={field}")
    if allowed is not None:
        parts.append(f"allowed={allowed}")
    if target.available_execution_profiles:
        parts.append(f"available_execution_profiles={target.available_execution_profiles}")
    return "; ".join(parts)


def _guard_error(
    command: RuntimeCommand,
    decision: GuardDecision,
) -> ExecuteToolResult:
    return ExecuteToolResult(
        status="failed",
        function_name=command.function_name,
        target_node_id=command.target_node_id,
        risk=(
            "maintenance"
            if command.function_name in {"transfer.create", "artifact.deploy"}
            else "safe"
        ),
        effect=(
            "external"
            if command.function_name == "transfer.create"
            else "write"
            if command.function_name == "artifact.deploy"
            else "read"
        ),
        output_data={"guard": decision.to_dict()},
        error_code=decision.decision,
        error_message=decision.reason,
        error_details=decision.to_dict(),
    )


def _resource_keys(
    command: RuntimeCommand,
    resolved: ResolvedCapability,
    approval: object,
) -> list[str]:
    approval_keys = getattr(approval, "resource_keys", None)
    if approval_keys:
        return list(approval_keys)
    if command.resource_keys is not None:
        return list(command.resource_keys)
    return list(resolved.resource_keys or [])


def _should_create_job_operation(
    command: RuntimeCommand,
    resolved: ResolvedCapability,
) -> bool:
    if command.approval_id:
        return True
    if command.suppress_operation or command.wait_for_result:
        return False
    timeout_sec = command.timeout_sec or resolved.timeout_sec or 30
    if timeout_sec > 60:
        return True
    if resolved.effect in {"external", "write"} and timeout_sec > 30:
        return True
    return resolved.conflict_policy == "serialize" and timeout_sec > 30
