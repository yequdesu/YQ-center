"""Application service for executing registered Center functions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.application.errors import ToolInvocationError
from yequ.application.schemas import (
    ApprovalRequiredResult,
    ExecuteToolCommand,
    ExecuteToolResult,
    ToolExecutionEvent,
)
from yequ.config import get_settings
from yequ.models.invocation import Invocation
from yequ.models.job import Job
from yequ.models.node import Node
from yequ.models.timeline import TimelineEvent
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

CENTER_META_TOOLS = {
    "node.list",
    "node.status",
    "capability.search",
    "capability.describe",
    "artifact.list",
    "artifact.get",
    "artifact.present",
    "operation.status",
    "operation.cancel",
    "transfer.create",
    "transfer.status",
    "transfer.cancel",
}


class ToolInvocationApplicationService:
    """Use-case boundary for all Center function execution requests."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def execute(self, command: ExecuteToolCommand) -> ExecuteToolResult:
        """Execute or stage a function invocation through the Center pipeline."""
        from yequ.runtime.admission import ExecutionAdmissionService

        admission_plan = ExecutionAdmissionService().plan(command)
        if command.function_name in CENTER_META_TOOLS:
            result = await self._execute_center_meta_tool(command)
            result.execution_plan = admission_plan.to_dict()
            return result
        if command.function_name == "capability.invoke":
            result = await self._execute_capability_invoke(command)
            result.execution_plan = admission_plan.to_dict()
            return result

        input_data = dict(command.input_data)
        approval_id = command.approval_id or _string_or_none(input_data.get("approval_id"))

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
            if command.allow_unregistered_function and command.target_node_id:
                resolved = await self._resolve_unregistered_admin_function(command)
            if resolved is not None and resolved.available:
                pass
            else:
                return ExecuteToolResult(
                    status="unavailable",
                    function_name=command.function_name,
                    target_node_id=(
                        command.target_node_id or (resolved.node_id if resolved else None)
                    ),
                    error_code=(
                        resolved.unavailable_code if resolved else "FUNCTION_NOT_AVAILABLE"
                    ),
                    error_message=(
                        resolved.unavailable_reason
                        if resolved and resolved.unavailable_reason
                        else f"No online node has capability {command.function_name!r}"
                    ),
                )

        if resolved is None:
            return ExecuteToolResult(
                status="unavailable",
                function_name=command.function_name,
                target_node_id=command.target_node_id,
                error_code="FUNCTION_NOT_AVAILABLE",
                error_message=f"No online node has capability {command.function_name!r}",
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
                    return await self._create_approval_result(command, resolved, input_data)
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

        await self.db.commit()

        result = ExecuteToolResult(
            status="running" if command.wait_for_result else "created",
            function_name=command.function_name,
            target_node_id=resolved.node_id,
            invocation_id=inv.invocation_id,
            job_id=job.job_id,
            risk=resolved.risk,
            effect=resolved.effect,
        )

        if not command.wait_for_result:
            return result

        final_status = await self.wait_for_invocation(
            inv.invocation_id,
            command.deadline,
        )
        final_result = await self._collect_terminal_result(result, final_status)
        final_result.execution_plan = admission_plan.to_dict()
        return final_result

    async def execute_stream(
        self,
        command: ExecuteToolCommand,
    ) -> AsyncIterator[ToolExecutionEvent]:
        """Execute a command and expose a minimal structured event stream."""
        initial = await self.execute(command)
        yield ToolExecutionEvent("tool.execution.result", initial)

    async def _execute_capability_invoke(
        self,
        command: ExecuteToolCommand,
    ) -> ExecuteToolResult:
        from yequ.services.capability_registry import resolve_capability_invoke_target

        input_data = dict(command.input_data)
        capability_ref = _string_or_none(input_data.get("capability_ref")) or _string_or_none(
            input_data.get("capability_id")
        )
        source_id = _string_or_none(input_data.get("source_id"))
        node_id = _string_or_none(input_data.get("node_id")) or command.target_node_id
        tool_input = input_data.get("input")
        if tool_input is None:
            tool_input = input_data.get("arguments")
        if tool_input is None:
            tool_input = {}
        if not isinstance(tool_input, dict):
            return _meta_tool_error(
                command,
                "invalid_input",
                "capability.invoke input must be an object",
            )

        try:
            target = await resolve_capability_invoke_target(
                self.db,
                capability_ref=capability_ref,
                source_id=source_id,
                node_id=node_id,
            )
        except ValueError as exc:
            return _meta_tool_error(command, "capability_source_unresolved", str(exc))

        delegated = ExecuteToolCommand(
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
            allow_unregistered_function=False,
        )
        result = await self.execute(delegated)
        return result

    async def _execute_center_meta_tool(
        self,
        command: ExecuteToolCommand,
    ) -> ExecuteToolResult:
        from yequ.application.transfer import TransferApplicationService, TransferCreateCommand
        from yequ.services.artifact_service import (
            artifact_to_dict,
            get_artifact,
            list_artifacts,
        )
        from yequ.services.capability_registry import (
            capability_describe,
            capability_search,
            node_list,
            node_status,
        )
        from yequ.services.operation_service import OperationService, wait_handle_for_operation

        input_data = dict(command.input_data)
        try:
            if command.function_name == "node.list":
                output = {"nodes": await node_list(self.db)}
            elif command.function_name == "node.status":
                node_id = _string_or_none(input_data.get("node_id")) or command.target_node_id
                if not node_id:
                    return _meta_tool_error(command, "invalid_input", "node_id is required")
                output = {"node": await node_status(self.db, node_id)}
            elif command.function_name == "capability.search":
                output = {
                    "capabilities": await capability_search(
                        self.db,
                        query=_string_or_none(input_data.get("query"))
                        or _string_or_none(input_data.get("q")),
                        node_id=_string_or_none(input_data.get("node_id")),
                        platform_os=_string_or_none(input_data.get("platform_os")),
                        effect=_string_or_none(input_data.get("effect")),
                        risk=_string_or_none(input_data.get("risk")),
                        capability_type=(
                            _string_or_none(input_data.get("capability_type")) or "function"
                        ),
                        include_inactive=bool(input_data.get("include_inactive", False)),
                        limit=_int_or_default(input_data.get("limit"), 20),
                    )
                }
            elif command.function_name == "capability.describe":
                capability_ref = _string_or_none(
                    input_data.get("capability_ref")
                ) or _string_or_none(
                    input_data.get("capability_id"),
                )
                if not capability_ref:
                    return _meta_tool_error(
                        command,
                        "invalid_input",
                        "capability_ref is required",
                    )
                output = {
                    "capability": await capability_describe(
                        self.db,
                        capability_ref,
                        node_id=_string_or_none(input_data.get("node_id")),
                        include_inactive=bool(input_data.get("include_inactive", False)),
                    )
                }
            elif command.function_name == "artifact.list":
                artifacts = await list_artifacts(
                    self.db,
                    session_id=_string_or_none(input_data.get("session_id"))
                    or command.session_id,
                    invocation_id=_string_or_none(input_data.get("invocation_id")),
                    job_id=_string_or_none(input_data.get("job_id")),
                    node_id=_string_or_none(input_data.get("node_id")),
                    artifact_type=_string_or_none(input_data.get("artifact_type")),
                    limit=_int_or_default(input_data.get("limit"), 20),
                )
                output = {"artifacts": [artifact_to_dict(artifact) for artifact in artifacts]}
            elif command.function_name == "artifact.get":
                artifact_id = _string_or_none(input_data.get("artifact_id"))
                if not artifact_id:
                    return _meta_tool_error(
                        command,
                        "invalid_input",
                        "artifact_id is required",
                    )
                artifact = await get_artifact(self.db, artifact_id)
                output = {"artifact": artifact_to_dict(artifact)}
            elif command.function_name == "artifact.present":
                artifact_ids = _string_list(input_data.get("artifact_ids"))
                artifact_id = _string_or_none(input_data.get("artifact_id"))
                if artifact_id:
                    artifact_ids = [artifact_id, *artifact_ids]
                artifact_ids = _dedupe_strings(artifact_ids)
                if not artifact_ids:
                    return _meta_tool_error(
                        command,
                        "invalid_input",
                        "artifact_id or artifact_ids is required",
                    )
                if len(artifact_ids) > 10:
                    return _meta_tool_error(
                        command,
                        "invalid_input",
                        "artifact.present can show at most 10 artifacts",
                    )
                artifacts = [
                    artifact_to_dict(await get_artifact(self.db, artifact_id))
                    for artifact_id in artifact_ids
                ]
                output = {
                    "artifacts": artifacts,
                    "presentation": {
                        "kind": "artifact_gallery",
                        "count": len(artifacts),
                    },
                }
            elif command.function_name == "transfer.create":
                transfer = await TransferApplicationService(self.db).create(
                    TransferCreateCommand(
                        source_node_id=_required_string(
                            input_data.get("source_node_id"),
                            "source_node_id",
                        ),
                        target_node_id=_required_string(
                            input_data.get("target_node_id"),
                            "target_node_id",
                        ),
                        source_path=_required_string(
                            input_data.get("source_path"),
                            "source_path",
                        ),
                        target_output_dir=_string_or_none(input_data.get("target_output_dir")),
                        target_path=_string_or_none(input_data.get("target_path")),
                        code=_string_or_none(input_data.get("code")),
                        relay_url=_string_or_none(input_data.get("relay_url")),
                        resume_mode=_string_or_none(input_data.get("resume_mode")) or "resume",
                        timeout_sec=_int_or_default(
                            input_data.get("timeout_sec"),
                            3600,
                        ),
                        expected_sha256=_string_or_none(input_data.get("expected_sha256")),
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
            elif command.function_name == "operation.status":
                operation_id = _required_string(input_data.get("operation_id"), "operation_id")
                output = await OperationService(self.db).status(operation_id)
            elif command.function_name == "operation.cancel":
                operation_id = _required_string(input_data.get("operation_id"), "operation_id")
                output = await OperationService(self.db).cancel(
                    operation_id,
                    reason=_string_or_none(input_data.get("reason")) or "operation_cancelled",
                )
            elif command.function_name == "transfer.status":
                transfer_id = _required_string(input_data.get("transfer_id"), "transfer_id")
                output = {
                    "transfer": await TransferApplicationService(self.db).status(transfer_id)
                }
            elif command.function_name == "transfer.cancel":
                transfer_id = _required_string(input_data.get("transfer_id"), "transfer_id")
                output = {
                    "transfer": await TransferApplicationService(self.db).cancel(
                        transfer_id,
                        reason=_string_or_none(input_data.get("reason"))
                        or "transfer_cancelled",
                    )
                }
            else:
                return _meta_tool_error(command, "unknown_meta_tool", command.function_name)
        except ValueError as exc:
            return _meta_tool_error(command, "not_found", str(exc))

        return ExecuteToolResult(
            status="succeeded",
            function_name=command.function_name,
            target_node_id=command.target_node_id,
            risk="safe",
            effect="read",
            output_data=output,
        )

    async def _resolve_unregistered_admin_function(
        self,
        command: ExecuteToolCommand,
    ) -> ResolvedCapability:
        node_result = await self.db.execute(
            select(Node).where(Node.node_id == command.target_node_id)
        )
        node = node_result.scalar_one_or_none()
        if node is None:
            return ResolvedCapability(
                node_id=command.target_node_id or "",
                function_name=command.function_name,
                available=False,
                unavailable_code="node_not_found",
                unavailable_reason=f"Node '{command.target_node_id}' not found",
            )

        from yequ.services.node_liveness_service import is_node_schedulable

        schedulable, reason = is_node_schedulable(node, get_settings())
        if not schedulable:
            return ResolvedCapability(
                node_id=node.node_id,
                function_name=command.function_name,
                available=False,
                unavailable_code="NODE_UNAVAILABLE",
                unavailable_reason=f"Node {node.node_id} is not schedulable: {reason}",
            )

        return ResolvedCapability(
            node_id=node.node_id,
            function_name=command.function_name,
            risk="safe",
            effect="read",
            timeout_sec=command.timeout_sec or 30,
            available=True,
        )

    @staticmethod
    async def wait_for_invocation(
        invocation_id: str,
        deadline: datetime | None,
        poll_interval: float = 0.5,
    ) -> str:
        """Wait for an Invocation to reach a terminal status."""
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
        command: ExecuteToolCommand,
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
        )

    async def _dry_run(
        self,
        command: ExecuteToolCommand,
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
        command: ExecuteToolCommand,
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


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _required_string(value: object, field_name: str) -> str:
    text = _string_or_none(value)
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _int_or_default(value: object, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _meta_tool_error(
    command: ExecuteToolCommand,
    error_code: str,
    error_message: str,
) -> ExecuteToolResult:
    return ExecuteToolResult(
        status="failed",
        function_name=command.function_name,
        target_node_id=command.target_node_id,
        risk="safe",
        effect="read",
        error_code=error_code,
        error_message=error_message,
    )


def _resource_keys(
    command: ExecuteToolCommand,
    resolved: ResolvedCapability,
    approval: object,
) -> list[str]:
    approval_keys = getattr(approval, "resource_keys", None)
    if approval_keys:
        return list(approval_keys)
    if command.resource_keys is not None:
        return list(command.resource_keys)
    return list(resolved.resource_keys or [])
