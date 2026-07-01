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
from yequ.runtime.admission import ExecutionAdmissionService
from yequ.runtime.command import RuntimeCommand
from yequ.runtime.guards import ExecutionGuard, GuardDecision
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
    "artifact.deploy.preflight",
    "artifact.deploy",
    "operation.status",
    "operation.cancel",
    "transfer.preflight",
    "transfer.create",
    "transfer.status",
    "transfer.cancel",
}


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
        elif runtime_command.function_name == "artifact.deploy":
            result = await self._execute_artifact_deploy(runtime_command)
        elif runtime_command.function_name in CENTER_META_TOOLS:
            result = await self._execute_inline_meta_tool(runtime_command)
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
            return _runtime_error(
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
            return _runtime_error(command, "capability_source_unresolved", str(exc))

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
            allow_unregistered_function=False,
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
            artifact_id = _required_string(input_data.get("artifact_id"), "artifact_id")
            target_node_id = _required_string(input_data.get("target_node_id"), "target_node_id")
            output_path = _required_string(input_data.get("output_path"), "output_path")
        except ValueError as exc:
            return _runtime_error(command, "invalid_input", str(exc))
        mode = _string_or_none(input_data.get("mode")) or "fail_if_exists"
        if mode not in {"fail_if_exists", "overwrite"}:
            return _runtime_error(
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
                    preflight_id=_string_or_none(input_data.get("preflight_id")),
                    skip_preflight=bool(input_data.get("skip_preflight", False)),
                    skip_reason=_string_or_none(input_data.get("skip_reason")),
                )
            )
        except ValueError as exc:
            return _runtime_error(command, "invalid_input", str(exc))

        delegated = RuntimeCommand(
            function_name="capability.invoke",
            input_data={
                "capability_ref": "artifact.download_file",
                "source_id": _string_or_none(input_data.get("source_id")),
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
            allow_unregistered_function=False,
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
            "preflight_id": _string_or_none(input_data.get("preflight_id")),
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
                    source_node_id=_required_string(
                        input_data.get("source_node_id"),
                        "source_node_id",
                    ),
                    target_node_id=_required_string(
                        input_data.get("target_node_id"),
                        "target_node_id",
                    ),
                    source_path=_required_string(input_data.get("source_path"), "source_path"),
                    target_output_dir=_string_or_none(input_data.get("target_output_dir")),
                    target_path=_string_or_none(input_data.get("target_path")),
                    conflict_mode=_string_or_none(input_data.get("conflict_mode")),
                    timeout_sec=_int_or_default(input_data.get("timeout_sec"), 3600),
                    expected_sha256=_string_or_none(input_data.get("expected_sha256")),
                    cleanup_on_failure=bool(input_data.get("cleanup_on_failure", False)),
                    preflight_id=_string_or_none(input_data.get("preflight_id")),
                    skip_preflight=bool(input_data.get("skip_preflight", False)),
                    skip_reason=_string_or_none(input_data.get("skip_reason")),
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
            return _runtime_error(command, "invalid_input", str(exc))

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

    async def _execute_inline_meta_tool(self, command: RuntimeCommand) -> ExecuteToolResult:
        from yequ.application.transfer import TransferApplicationService, TransferPreflightCommand
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
        from yequ.services.operation_service import OperationService

        input_data = dict(command.input_data)
        try:
            if command.function_name == "node.list":
                output = {"nodes": await node_list(self.db)}
            elif command.function_name == "node.status":
                node_id = _string_or_none(input_data.get("node_id")) or command.target_node_id
                if not node_id:
                    return _runtime_error(command, "invalid_input", "node_id is required")
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
                        runtime_kind=_string_or_none(input_data.get("runtime_kind")),
                        runtime_labels=_string_list(input_data.get("runtime_labels"))
                        or _string_list(input_data.get("labels")),
                        supports_progress=_bool_or_none(input_data.get("supports_progress")),
                        supports_cancel=_bool_or_none(input_data.get("supports_cancel")),
                        supports_resume=_bool_or_none(input_data.get("supports_resume")),
                        preflight_supported=_bool_or_none(
                            input_data.get("preflight_supported")
                        ),
                        artifact_input=_bool_or_none(input_data.get("artifact_input")),
                        artifact_output=_bool_or_none(input_data.get("artifact_output")),
                        projection=(
                            _string_or_none(input_data.get("projection")) or "summary"
                        ),
                        capability_type=(
                            _string_or_none(input_data.get("capability_type")) or "function"
                        ),
                        include_inactive=bool(input_data.get("include_inactive", False)),
                        limit=_int_or_default(input_data.get("limit"), 10),
                    )
                }
            elif command.function_name == "capability.describe":
                capability_ref = _string_or_none(
                    input_data.get("capability_ref")
                ) or _string_or_none(
                    input_data.get("capability_id"),
                )
                if not capability_ref:
                    return _runtime_error(command, "invalid_input", "capability_ref is required")
                output = {
                    "capability": await capability_describe(
                        self.db,
                        capability_ref,
                        node_id=_string_or_none(input_data.get("node_id")),
                        sections=_string_list(input_data.get("sections")),
                        projection=_string_or_none(input_data.get("projection")) or "detail",
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
                    return _runtime_error(command, "invalid_input", "artifact_id is required")
                artifact = await get_artifact(self.db, artifact_id)
                output = {"artifact": artifact_to_dict(artifact)}
            elif command.function_name == "artifact.present":
                artifact_ids = _string_list(input_data.get("artifact_ids"))
                artifact_id = _string_or_none(input_data.get("artifact_id"))
                if artifact_id:
                    artifact_ids = [artifact_id, *artifact_ids]
                artifact_ids = _dedupe_strings(artifact_ids)
                if not artifact_ids:
                    return _runtime_error(
                        command,
                        "invalid_input",
                        "artifact_id or artifact_ids is required",
                    )
                if len(artifact_ids) > 10:
                    return _runtime_error(
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
            elif command.function_name == "artifact.deploy.preflight":
                from yequ.application.artifact_deploy import (
                    ArtifactDeployApplicationService,
                    ArtifactDeployPreflightCommand,
                )

                output = {
                    "preflight": await ArtifactDeployApplicationService(self.db).preflight(
                        ArtifactDeployPreflightCommand(
                            artifact_id=_required_string(
                                input_data.get("artifact_id"),
                                "artifact_id",
                            ),
                            target_node_id=_required_string(
                                input_data.get("target_node_id"),
                                "target_node_id",
                            ),
                            output_path=_required_string(
                                input_data.get("output_path"),
                                "output_path",
                            ),
                            mode=_string_or_none(input_data.get("mode"))
                            or "fail_if_exists",
                            timeout_sec=_int_or_default(input_data.get("timeout_sec"), 20),
                            ttl_sec=_int_or_default(input_data.get("ttl_sec"), 120),
                            actor_type=command.actor_type,
                            actor_id=command.actor_id,
                            session_id=command.session_id,
                            execution_mode=command.execution_mode,
                        )
                    )
                }
            elif command.function_name == "operation.status":
                operation_id = _required_string(input_data.get("operation_id"), "operation_id")
                output = await OperationService(self.db).status(operation_id)
            elif command.function_name == "operation.cancel":
                operation_id = _required_string(input_data.get("operation_id"), "operation_id")
                output = await OperationService(self.db).cancel(
                    operation_id,
                    reason=_string_or_none(input_data.get("reason")) or "operation_cancelled",
                )
            elif command.function_name == "transfer.preflight":
                output = {
                    "preflight": await TransferApplicationService(self.db).preflight(
                        TransferPreflightCommand(
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
                            target_output_dir=_string_or_none(
                                input_data.get("target_output_dir")
                            ),
                            target_path=_string_or_none(input_data.get("target_path")),
                            conflict_mode=_string_or_none(input_data.get("conflict_mode")),
                            include_sha256=bool(input_data.get("include_sha256", False)),
                            timeout_sec=_int_or_default(input_data.get("timeout_sec"), 20),
                            ttl_sec=_int_or_default(input_data.get("ttl_sec"), 120),
                            actor_type=command.actor_type,
                            actor_id=command.actor_id,
                            session_id=command.session_id,
                            execution_mode=command.execution_mode,
                        )
                    )
                }
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
                return _runtime_error(command, "unknown_meta_tool", command.function_name)
        except ValueError as exc:
            return _runtime_error(command, "not_found", str(exc))

        return ExecuteToolResult(
            status="succeeded",
            function_name=command.function_name,
            target_node_id=command.target_node_id,
            risk="safe",
            effect="read",
            output_data=output,
        )

    async def _execute_node_job(self, command: RuntimeCommand) -> ExecuteToolResult:
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
            if resolved is None or not resolved.available:
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
        )

        if not command.wait_for_result:
            return result

        final_status = await self._wait_for_node_invocation_terminal(
            inv.invocation_id,
            command.deadline,
        )
        return await self._collect_terminal_result(result, final_status)

    async def _resolve_unregistered_admin_function(
        self,
        command: RuntimeCommand,
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


def _as_runtime_command(command: RuntimeCommand | ExecuteToolCommand) -> RuntimeCommand:
    if isinstance(command, RuntimeCommand):
        return command
    return RuntimeCommand.from_execute_tool_command(command)


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


def _bool_or_none(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _runtime_error(
    command: RuntimeCommand,
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
    if command.suppress_operation or command.wait_for_result:
        return False
    timeout_sec = command.timeout_sec or resolved.timeout_sec or 30
    if timeout_sec > 60:
        return True
    if resolved.effect in {"external", "write"} and timeout_sec > 30:
        return True
    return resolved.conflict_policy == "serialize" and timeout_sec > 30
