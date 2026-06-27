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


class ToolInvocationApplicationService:
    """Use-case boundary for all Center function execution requests."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def execute(self, command: ExecuteToolCommand) -> ExecuteToolResult:
        """Execute or stage a function invocation through the Center pipeline."""
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
            await self.db.rollback()
            await self._write_resource_conflict(command, resolved, invocation_id, str(exc))
            return ExecuteToolResult(
                status="failed",
                function_name=command.function_name,
                target_node_id=resolved.node_id,
                invocation_id=invocation_id,
                risk=resolved.risk,
                effect=resolved.effect,
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
        return await self._collect_terminal_result(result, final_status)

    async def execute_stream(
        self,
        command: ExecuteToolCommand,
    ) -> AsyncIterator[ToolExecutionEvent]:
        """Execute a command and expose a minimal structured event stream."""
        initial = await self.execute(command)
        yield ToolExecutionEvent("tool.execution.result", initial)

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
        resolved: ResolvedCapability,
        invocation_id: str,
        message: str,
    ) -> None:
        event = TimelineEvent(
            global_seq=0,
            event_type="resource.lock.conflict",
            actor_type="system",
            actor_id=command.actor_id,
            node_id=resolved.node_id,
            invocation_id=invocation_id,
            data={"error": message},
            timestamp=datetime.now(UTC),
        )
        await add_timeline_event(self.db, event)
        await self.db.commit()


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


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
