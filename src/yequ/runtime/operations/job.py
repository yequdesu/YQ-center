"""Job Operation projection handler."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from yequ.models.artifact import Artifact
from yequ.models.job import Job
from yequ.models.operation import Operation
from yequ.services.artifact_service import artifact_to_dict
from yequ.services.job_service import cancel_job

TERMINAL_OPERATION_STATUSES = {"succeeded", "failed", "cancelled", "timeout"}


class JobOperationHandler:
    """Project one Node Job into an Operation shell."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def project(self, operation: Operation) -> dict[str, object]:
        job = await self._get_job(operation.ref_id)
        artifacts = await self._get_artifacts(job.job_id)
        self.sync_operation(operation, job, artifacts=artifacts)
        return {"job": job_dict(job), "artifacts": artifacts}

    async def cancel(self, operation: Operation, *, reason: str) -> dict[str, object]:
        job = await self._get_job(operation.ref_id)
        if job.status not in TERMINAL_OPERATION_STATUSES:
            await cancel_job(self.db, job, reason=reason, node_id=job.node_id)
        artifacts = await self._get_artifacts(job.job_id)
        self.sync_operation(operation, job, artifacts=artifacts)
        return {"job": job_dict(job), "artifacts": artifacts}

    async def _get_job(self, job_id: str) -> Job:
        result = await self.db.execute(select(Job).where(Job.job_id == job_id))
        job = result.scalar_one_or_none()
        if job is None:
            raise ValueError(f"Job {job_id!r} not found")
        return job

    async def _get_artifacts(self, job_id: str) -> list[dict[str, object]]:
        result = await self.db.execute(
            select(Artifact)
            .options(selectinload(Artifact.blobs))
            .where(Artifact.job_id == job_id)
            .order_by(Artifact.created_at.asc())
        )
        return [artifact_to_dict(artifact) for artifact in result.scalars().all()]

    @staticmethod
    def sync_operation(
        operation: Operation,
        job: Job,
        *,
        artifacts: list[dict[str, object]] | None = None,
    ) -> None:
        operation.status = operation_status_from_job(job.status)
        operation.output_data = {"job": job_dict(job), "artifacts": artifacts or []}
        if artifacts:
            operation.progress_message = f"{len(artifacts)} artifact(s) available"
        operation.error_code = job.error_code
        operation.error_message = job.error_message
        if operation.status in TERMINAL_OPERATION_STATUSES:
            operation.completed_at = operation.completed_at or datetime.now(UTC)


def operation_status_from_job(status: str) -> str:
    if status in {"created", "queued"}:
        return "queued"
    if status in {"claimed", "running"}:
        return "running"
    if status in TERMINAL_OPERATION_STATUSES:
        return status
    return "running"


def job_title(job: Job) -> str:
    return f"Job {job.function_name} @ {job.node_id}"


def job_dict(job: Job) -> dict[str, object]:
    return {
        "job_id": job.job_id,
        "invocation_id": job.invocation_id,
        "node_id": job.node_id,
        "runtime_id": job.runtime_id,
        "function_name": job.function_name,
        "status": job.status,
        "output": job.output,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "error_details": job.error_details,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
