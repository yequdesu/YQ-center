"""YCR source adapters for durable Center anchors."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.agent_message import AgentMessage
from yequ.models.agent_run import AgentRun, AgentRunStep
from yequ.models.artifact import Artifact
from yequ.models.capability_runtime import CapabilityDefinition, CapabilitySource
from yequ.models.job import Job
from yequ.models.transfer import TransferAttempt, TransferSession
from yequ.services.operation_service import OperationService


async def load_source_value(
    db: AsyncSession,
    *,
    source_type: str,
    source_id: str,
) -> object:
    if source_type == "job":
        result = await db.execute(select(Job).where(Job.job_id == source_id))
        job = result.scalar_one_or_none()
        if job is None:
            raise ValueError(f"Job source not found: {source_id}")
        return {
            "job_id": job.job_id,
            "invocation_id": job.invocation_id,
            "node_id": job.node_id,
            "function_name": job.function_name,
            "status": job.status,
            "output": job.output,
            "error_code": job.error_code,
            "error_message": job.error_message,
            "progress_pct": job.progress_pct,
            "progress_message": job.progress_message,
            "progress_detail": job.progress_detail,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        }
    if source_type == "operation":
        return await OperationService(db).status(source_id)
    if source_type == "transfer":
        result = await db.execute(
            select(TransferSession).where(TransferSession.transfer_id == source_id)
        )
        transfer = result.scalar_one_or_none()
        if transfer is None:
            raise ValueError(f"Transfer source not found: {source_id}")
        attempts = await db.execute(
            select(TransferAttempt)
            .where(TransferAttempt.transfer_id == source_id)
            .order_by(TransferAttempt.attempt)
        )
        return {
            "transfer_id": transfer.transfer_id,
            "transport": transfer.transport,
            "mode": transfer.mode,
            "status": transfer.status,
            "source_node_id": transfer.source_node_id,
            "target_node_id": transfer.target_node_id,
            "source_path": transfer.source_path,
            "target_path": transfer.target_path,
            "target_output_dir": transfer.target_output_dir,
            "size_bytes": transfer.size_bytes,
            "sha256": transfer.sha256,
            "relay_url": transfer.relay_url,
            "route_policy": transfer.route_policy,
            "resume_mode": transfer.resume_mode,
            "attempt": transfer.attempt,
            "error_code": transfer.error_code,
            "error_message": transfer.error_message,
            "metadata": transfer.metadata_json,
            "attempts": [
                {
                    "attempt": item.attempt,
                    "status": item.status,
                    "source_job_id": item.source_job_id,
                    "target_job_id": item.target_job_id,
                    "resumable": item.resumable,
                    "error_code": item.error_code,
                    "error_message": item.error_message,
                    "metadata": item.metadata_json,
                }
                for item in attempts.scalars().all()
            ],
        }
    if source_type == "artifact":
        result = await db.execute(select(Artifact).where(Artifact.artifact_id == source_id))
        artifact = result.scalar_one_or_none()
        if artifact is None:
            raise ValueError(f"Artifact source not found: {source_id}")
        return {
            "artifact_id": artifact.artifact_id,
            "artifact_type": artifact.artifact_type,
            "title": artifact.title,
            "summary": artifact.summary,
            "metadata": artifact.metadata_json,
            "session_id": artifact.session_id,
            "invocation_id": artifact.invocation_id,
            "job_id": artifact.job_id,
            "node_id": artifact.node_id,
            "content_type": artifact.content_type,
            "size_bytes": artifact.size_bytes,
            "sha256": artifact.sha256,
            "status": artifact.status,
            "expires_at": artifact.expires_at.isoformat() if artifact.expires_at else None,
        }
    if source_type == "agent_run":
        result = await db.execute(select(AgentRun).where(AgentRun.run_id == source_id))
        run = result.scalar_one_or_none()
        if run is None:
            raise ValueError(f"AgentRun source not found: {source_id}")
        steps = await db.execute(
            select(AgentRunStep)
            .where(AgentRunStep.run_record_id == run.id)
            .order_by(AgentRunStep.step_index)
        )
        return {
            "run_id": run.run_id,
            "session_id": run.session_id,
            "turn_id": run.turn_id,
            "trace_id": run.trace_id,
            "provider_name": run.provider_name,
            "status": run.status,
            "execution_mode": run.execution_mode,
            "target_node_id": run.target_node_id,
            "user_message": run.user_message,
            "final_message": run.final_message,
            "error_code": run.error_code,
            "error_message": run.error_message,
            "metadata": run.metadata_json,
            "steps": [
                {
                    "step_index": step.step_index,
                    "step_type": step.step_type,
                    "status": step.status,
                    "tool_call_id": step.tool_call_id,
                    "node_id": step.node_id,
                    "job_id": step.job_id,
                    "output_data": step.output_data,
                    "error_code": step.error_code,
                    "error_message": step.error_message,
                }
                for step in steps.scalars().all()
            ],
        }
    if source_type == "session_history":
        result = await db.execute(
            select(AgentMessage)
            .where(AgentMessage.session_id == source_id)
            .order_by(AgentMessage.created_at)
        )
        return {
            "session_id": source_id,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                    "tool_calls": message.tool_calls,
                    "created_at": message.created_at.isoformat(),
                }
                for message in result.scalars().all()
            ],
        }
    if source_type == "capability":
        definition = await db.execute(
            select(CapabilityDefinition).where(
                CapabilityDefinition.canonical_name == source_id
            )
        )
        capability = definition.scalar_one_or_none()
        if capability is None:
            raise ValueError(f"Capability source not found: {source_id}")
        sources = await db.execute(
            select(CapabilitySource).where(CapabilitySource.definition_id == capability.id)
        )
        return {
            "canonical_name": capability.canonical_name,
            "capability_type": capability.capability_type,
            "display_name": capability.display_name,
            "description": capability.description,
            "input_schema": capability.input_schema,
            "output_schema": capability.output_schema,
            "risk": capability.risk,
            "effect": capability.effect,
            "sources": [
                {
                    "source_id": source.source_id,
                    "node_record_id": source.node_record_id,
                    "registered_name": source.registered_name,
                    "is_active": source.is_active,
                    "status": source.status,
                    "runtime_kind": (
                        source.execution_requirements or {}
                    ).get("runtime_kind"),
                    "runtime_labels": (
                        source.execution_requirements or {}
                    ).get("labels"),
                }
                for source in sources.scalars().all()
            ],
        }
    raise ValueError(f"Unsupported YCR source type: {source_type}")
