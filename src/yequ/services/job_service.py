"""Job service — creation, cancellation, timeout, lease management.

All job lifecycle operations use the centralized state machine
via job_state_machine.transition().
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.job import Job
from yequ.protocol import JobStatus
from yequ.services.job_state_machine import (
    TERMINAL_STATUSES,
    transition,
)


def _make_job_id() -> str:
    """Generate a unique job ID."""
    return f"job_{uuid.uuid4().hex[:16]}"


async def create_job(
    db: AsyncSession,
    *,
    invocation_id: str,
    node_id: str,
    function_name: str,
    input_payload: dict[str, object] | None = None,
    timeout_sec: int = 30,
    lease_sec: int = 30,
) -> Job:
    """Create a new Job and transition it to QUEUED.

    Creates the Job in CREATED status, then immediately transitions
    to QUEUED via the state machine (which writes audit events).

    Returns the Job object (already flushed but not committed --
    caller must commit).
    """
    job = Job(
        job_id=_make_job_id(),
        invocation_id=invocation_id,
        node_id=node_id,
        function_name=function_name,
        input_payload=input_payload or {},
        status=JobStatus.CREATED,
        timeout_sec=timeout_sec,
        lease_sec=lease_sec,
    )
    db.add(job)
    await db.flush()

    # Transition created -> queued
    await transition(
        db, job, JobStatus.QUEUED,
        node_id=node_id,
        invocation_id=invocation_id,
    )
    await db.flush()
    return job


async def cancel_job(
    db: AsyncSession,
    job: Job,
    *,
    reason: str = "user_requested",
    node_id: str | None = None,
) -> None:
    """Cancel a job.

    QUEUED -> CANCELLED (immediate, not yet picked up)
    CLAIMED/RUNNING -> CANCELLING (Daemon should stop and report)
    Other states raise ValueError.
    """
    if job.status == JobStatus.QUEUED:
        await transition(
            db, job, JobStatus.CANCELLED,
            node_id=node_id, reason=reason,
        )
    elif job.status in (JobStatus.CLAIMED, JobStatus.RUNNING):
        await transition(
            db, job, JobStatus.CANCELLING,
            node_id=node_id, reason=reason,
        )
    else:
        raise ValueError(f"Cannot cancel job in status {job.status}")


async def timeout_job(
    db: AsyncSession,
    job: Job,
    *,
    node_id: str | None = None,
) -> None:
    """Mark a job as TIMEOUT due to lease expiry.

    Only CLAIMED or RUNNING jobs can time out.
    """
    if job.status not in (JobStatus.CLAIMED, JobStatus.RUNNING):
        raise ValueError(f"Cannot timeout job in status {job.status}")

    await transition(
        db, job, JobStatus.TIMEOUT,
        node_id=node_id,
        reason="lease_expired",
    )


async def find_expired_jobs(db: AsyncSession) -> list[Job]:
    """Find all jobs with expired leases that should time out.

    Only checks CLAIMED and RUNNING jobs that have a non-null
    lease_expires_at in the past.
    """
    now = datetime.now(UTC)
    result = await db.execute(
        select(Job).where(
            Job.status.in_([JobStatus.CLAIMED, JobStatus.RUNNING]),
            Job.lease_expires_at.isnot(None),
            Job.lease_expires_at < now,
        )
    )
    return list(result.scalars().all())


async def find_incomplete_jobs(db: AsyncSession) -> list[Job]:
    """Find all jobs not in a terminal state (for recovery on restart).

    Returns jobs in CREATED, QUEUED, CLAIMED, RUNNING, CANCELLING.
    """
    terminal_values = [s.value for s in TERMINAL_STATUSES]
    result = await db.execute(
        select(Job).where(Job.status.notin_(terminal_values))
    )
    return list(result.scalars().all())
