"""Admin endpoints for execution activity: jobs, invocations, timeline, locks."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.api.deps import get_admin_token, get_db
from yequ.api.routes.admin_schemas import (
    InvocationDetail,
    JobSummary,
    TimelineSummary,
    _inv_detail,
    _job_summary,
    _tl_summary,
)
from yequ.models.approval import ApprovalRequest
from yequ.models.invocation import Invocation
from yequ.models.job import Job
from yequ.models.resource_lock import ResourceLock
from yequ.models.timeline import TimelineEvent

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/jobs", response_model=list[JobSummary])
async def list_jobs(
    node_id: str | None = None,
    status: str | None = None,
    invocation_id: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[JobSummary]:
    """List jobs with optional filters."""
    stmt = select(Job)
    if node_id:
        stmt = stmt.where(Job.node_id == node_id)
    if status:
        stmt = stmt.where(Job.status == status)
    if invocation_id:
        stmt = stmt.where(Job.invocation_id == invocation_id)
    stmt = stmt.order_by(Job.created_at.desc()).limit(min(limit, 200))
    result = await db.execute(stmt)
    jobs = result.scalars().all()
    return [_job_summary(j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=JobSummary)
async def get_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JobSummary:
    """Get a single job's details."""
    result = await db.execute(select(Job).where(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return _job_summary(job)


@router.get("/invocations/{invocation_id}", response_model=InvocationDetail)
async def get_invocation(
    invocation_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> InvocationDetail:
    """Get invocation details including associated jobs."""
    result = await db.execute(select(Invocation).where(Invocation.invocation_id == invocation_id))
    inv = result.scalar_one_or_none()
    if inv is None:
        raise HTTPException(status_code=404, detail=f"Invocation {invocation_id!r} not found")

    # Fetch associated jobs
    jresult = await db.execute(
        select(Job).where(Job.invocation_id == invocation_id).order_by(Job.created_at)
    )
    jobs = jresult.scalars().all()
    return _inv_detail(inv, jobs)


@router.get("/invocations", response_model=list[InvocationDetail])
async def list_invocations(
    status: str | None = None,
    actor_id: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[InvocationDetail]:
    """List invocations with optional filters."""
    stmt = select(Invocation)
    if status:
        stmt = stmt.where(Invocation.status == status)
    if actor_id:
        stmt = stmt.where(Invocation.actor_id == actor_id)
    if session_id:
        stmt = stmt.where(Invocation.session_id == session_id)
    stmt = stmt.order_by(Invocation.started_at.desc().nullslast()).limit(min(limit, 200))
    result = await db.execute(stmt)
    invs = result.scalars().all()
    out = []
    for inv in invs:
        jresult = await db.execute(select(Job).where(Job.invocation_id == inv.invocation_id))
        jobs = jresult.scalars().all()
        out.append(_inv_detail(inv, jobs))
    return out


@router.get("/timeline", response_model=list[TimelineSummary])
async def list_timeline(
    node_id: str | None = None,
    job_id: str | None = None,
    invocation_id: str | None = None,
    session_id: str | None = None,
    event_type: str | None = None,
    approval_id: str | None = None,
    trace_id: str | None = None,
    limit: int = 50,
    created_after: str | None = None,
    created_before: str | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[TimelineSummary]:
    """Query timeline events with filters and cursor support."""
    stmt = select(TimelineEvent)
    if trace_id:
        stmt = stmt.where(TimelineEvent.trace_id == trace_id)
    if node_id:
        stmt = stmt.where(TimelineEvent.node_id == node_id)
    if job_id:
        stmt = stmt.where(TimelineEvent.job_id == job_id)
    if invocation_id:
        stmt = stmt.where(TimelineEvent.invocation_id == invocation_id)
    if session_id:
        stmt = stmt.where(TimelineEvent.session_id == session_id)
    if event_type:
        stmt = stmt.where(TimelineEvent.event_type == event_type)
    if approval_id:
        # Resolve approval to get execution invocation, then include all related events
        apv_result = await db.execute(
            select(ApprovalRequest).where(ApprovalRequest.approval_id == approval_id)
        )
        approval = apv_result.scalar_one_or_none()
        if approval:
            # Find the execution invocation
            exec_inv_id = approval.consumed_invocation_id or approval.invocation_id
            # Get all job_ids for this invocation
            j_result = await db.execute(select(Job.job_id).where(Job.invocation_id == exec_inv_id))
            job_ids = [row[0] for row in j_result.fetchall()]
            # Build OR filter
            conditions = [TimelineEvent.data.op("->>")("approval_id") == approval_id]
            if exec_inv_id:
                conditions.append(TimelineEvent.invocation_id == exec_inv_id)
            if job_ids:
                conditions.append(TimelineEvent.job_id.in_(job_ids))
            stmt = stmt.where(or_(*conditions))
        else:
            # Fallback: just filter by approval_id in data
            stmt = stmt.where(TimelineEvent.data.op("->>")("approval_id") == approval_id)
    if created_after:
        stmt = stmt.where(TimelineEvent.timestamp > datetime.fromisoformat(created_after))
    if created_before:
        stmt = stmt.where(TimelineEvent.timestamp < datetime.fromisoformat(created_before))
    stmt = stmt.order_by(TimelineEvent.global_seq.desc()).limit(min(limit, 200))
    result = await db.execute(stmt)
    events = result.scalars().all()
    return [_tl_summary(e) for e in events]


@router.get("/locks")
async def list_locks(
    status: str | None = None,
    node_id: str | None = None,
    job_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict = Depends(get_admin_token),
) -> list[dict]:
    """List resource locks with optional filters."""
    stmt = select(ResourceLock)
    if status:
        stmt = stmt.where(ResourceLock.status == status)
    if node_id:
        stmt = stmt.where(ResourceLock.node_id == node_id)
    if job_id:
        stmt = stmt.where(ResourceLock.job_id == job_id)
    stmt = stmt.order_by(ResourceLock.created_at.desc()).limit(200)
    result = await db.execute(stmt)
    locks = result.scalars().all()
    return [
        {
            "lock_id": lock.lock_id,
            "resource_key": lock.resource_key,
            "job_id": lock.job_id,
            "invocation_id": lock.invocation_id,
            "node_id": lock.node_id,
            "status": lock.status,
            "expires_at": lock.expires_at.isoformat() if lock.expires_at else None,
            "created_at": lock.created_at.isoformat() if lock.created_at else None,
            "released_at": lock.released_at.isoformat() if lock.released_at else None,
        }
        for lock in locks
    ]
