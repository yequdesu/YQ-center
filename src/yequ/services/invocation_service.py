"""Invocation service — create Invocations, fan out to Jobs, aggregate status."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.invocation import Invocation
from yequ.models.job import Job
from yequ.protocol import InvocationStatus, JobStatus


def _make_invocation_id() -> str:
    """Generate a unique invocation ID."""
    return f"inv_{uuid.uuid4().hex[:16]}"


async def create_invocation(
    db: AsyncSession,
    *,
    actor_type: str = "user",
    actor_id: str = "admin",
    session_id: str | None = None,
    function_name: str,
    input_payload: dict[str, object] | None = None,
    target_node_id: str,
    execution_mode: str = "auto",
    max_depth: int | None = None,
    max_steps: int | None = None,
    max_total_duration_sec: int | None = None,
    call_path: list[str] | None = None,
) -> Invocation:
    """Create an Invocation and return it.

    The Invocation is created in PENDING status.
    Callers should then fan out to Jobs via create_job() and
    transition the Invocation to RUNNING.

    Returns the Invocation object (flushed, not committed).
    """
    inv = Invocation(
        invocation_id=_make_invocation_id(),
        actor_type=actor_type,
        actor_id=actor_id,
        session_id=session_id,
        function_name=function_name,
        input_payload=input_payload or {},
        status=InvocationStatus.PENDING,
        execution_mode=execution_mode,
        target_node_id=target_node_id,
        max_depth=max_depth,
        max_steps=max_steps,
        max_total_duration_sec=max_total_duration_sec,
        call_path=call_path or [function_name],
    )
    db.add(inv)
    await db.flush()
    return inv


def start_invocation(inv: Invocation) -> None:
    """Transition an Invocation from PENDING to RUNNING.

    This is a synchronous in-memory operation. The caller must
    commit the transaction.
    """
    inv.status = InvocationStatus.RUNNING
    inv.started_at = datetime.now(UTC)


def finish_invocation(
    inv: Invocation,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Transition an Invocation to a terminal status.

    Synchronous in-memory operation. Caller must commit.
    """
    inv.status = status
    inv.finished_at = datetime.now(UTC)
    if error_code:
        inv.error_code = error_code
    if error_message:
        inv.error_message = error_message


async def aggregate_invocation_status(
    db: AsyncSession,
    invocation_id: str,
) -> str:
    """Compute an Invocation's aggregate status from its Jobs.

    Logic (first match wins):
    - No jobs -> PENDING
    - Any FAILED -> FAILED
    - Any TIMEOUT -> TIMEOUT
    - Any CANCELLED -> CANCELLED
    - All SUCCEEDED -> SUCCEEDED
    - All terminal but mixed -> PARTIAL
    - Some non-terminal -> RUNNING

    Returns an InvocationStatus value.
    """
    result = await db.execute(
        select(Job).where(Job.invocation_id == invocation_id)
    )
    jobs = list(result.scalars().all())

    if not jobs:
        return InvocationStatus.PENDING

    statuses = {job.status for job in jobs}

    if JobStatus.FAILED in statuses:
        return InvocationStatus.FAILED
    if JobStatus.TIMEOUT in statuses:
        return InvocationStatus.TIMEOUT
    if JobStatus.CANCELLED in statuses:
        return InvocationStatus.CANCELLED

    terminal_values = {"succeeded", "failed", "cancelled", "timeout"}
    all_terminal = statuses.issubset(terminal_values)

    if all_terminal and statuses == {"succeeded"}:
        return InvocationStatus.SUCCEEDED
    if all_terminal:
        return InvocationStatus.PARTIAL

    return InvocationStatus.RUNNING
