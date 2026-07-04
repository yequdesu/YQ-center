"""Execution admission service.

This service deliberately decides from structured Center facts, not from prompt
text.  It is the first boundary of Center Execution Runtime v2.
"""

from __future__ import annotations

from yequ.application.schemas import ExecuteToolCommand
from yequ.runtime.admission.schemas import ExecutionPlan

INLINE_TOOLS = {
    "node.list",
    "node.status",
    "capability.search",
    "capability.describe",
    "capability.recommend",
    "context.inspect",
    "context.expand",
    "context.tail",
    "context.schema",
    "context.search",
    "context.status",
    "artifact.list",
    "artifact.get",
    "artifact.present",
    "artifact.deploy.preflight",
    "operation.status",
    "transfer.preflight",
    "transfer.status",
}


class ExecutionAdmissionService:
    """Classify a Center execution request before the execution path is chosen."""

    def plan(self, command: ExecuteToolCommand) -> ExecutionPlan:
        name = command.function_name
        if name in INLINE_TOOLS:
            return ExecutionPlan(
                function_name=name,
                decision="inline",
                reason="center_read_or_presentation_meta_tool",
            )
        if name in {"transfer.create", "transfer.resume"}:
            return ExecutionPlan(
                function_name=name,
                decision="workflow_operation",
                reason=f"{name}_fans_out_to_sender_and_receiver_jobs",
                operation_kind="transfer",
                waitable=True,
                metadata={"ref_type": "transfer_session"},
            )
        if name == "artifact.deploy":
            return ExecutionPlan(
                function_name=name,
                decision="waitable_operation",
                reason="artifact_deploy_delegates_to_target_node_download_job",
                operation_kind="job",
                waitable=True,
                metadata={"ref_type": "job"},
            )
        if name in {"transfer.cancel", "operation.cancel"}:
            return ExecutionPlan(
                function_name=name,
                decision="sync_wait",
                reason="cancel_request_must_apply_immediately_to_existing_runtime_state",
            )
        if name == "capability.invoke":
            return ExecutionPlan(
                function_name=name,
                decision="sync_wait",
                reason="capability_invoke_delegates_to_resolved_node_capability",
            )
        return ExecutionPlan(
            function_name=name,
            decision="sync_wait",
            reason="default_node_capability_execution_path",
        )
