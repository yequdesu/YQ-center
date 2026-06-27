"""Job state machine — unified transition function.

ALL job status changes MUST go through transition().
Illegal transitions are rejected and audit events written.
Terminal states are immutable — they can only be written once.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.job import Job
from yequ.protocol import JobStatus
from yequ.shared_types import JsonObject

# Valid transitions from each source state
VALID_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.CREATED: {JobStatus.QUEUED},
    JobStatus.QUEUED: {JobStatus.CLAIMED, JobStatus.CANCELLED},
    JobStatus.CLAIMED: {JobStatus.RUNNING, JobStatus.TIMEOUT},
    JobStatus.RUNNING: {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLING,
        JobStatus.TIMEOUT,
    },
    JobStatus.CANCELLING: {JobStatus.CANCELLED, JobStatus.FAILED, JobStatus.TIMEOUT},
    # Terminal states — no outgoing transitions
    JobStatus.SUCCEEDED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
    JobStatus.TIMEOUT: set(),
}

TERMINAL_STATUSES: set[JobStatus] = {
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
    JobStatus.TIMEOUT,
}

NON_TERMINAL_STATUSES: set[JobStatus] = {
    JobStatus.CREATED,
    JobStatus.QUEUED,
    JobStatus.CLAIMED,
    JobStatus.RUNNING,
    JobStatus.CANCELLING,
}


def is_valid_transition(current: str, target: str) -> bool:
    """Check if a state transition is valid."""
    cur = JobStatus(current)
    tgt = JobStatus(target)
    return tgt in VALID_TRANSITIONS.get(cur, set())


def is_terminal(status: str) -> bool:
    """Check if a status is a terminal (finished) state."""
    return JobStatus(status) in TERMINAL_STATUSES


async def transition(
    db: AsyncSession,
    job: Job,
    target_status: str,
    *,
    node_id: str | None = None,
    invocation_id: str | None = None,
    reason: str | None = None,
    output: JsonObject | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    error_details: JsonObject | None = None,
) -> None:
    """Execute a job state transition.

    Validates the transition, records an audit TimelineEvent,
    and updates the job object. Does NOT commit — caller must commit.

    Args:
        db: AsyncSession
        job: Job ORM object (modified in-place)
        target_status: Target JobStatus value
        node_id: Entity performing the transition (for audit)
        invocation_id: Associated invocation
        reason: Human-readable reason (for cancel/timeout)
        output: Job output data (for succeeded)
        error_code: Error code string (for failed)
        error_message: Error message string (for failed)

    Raises:
        ValueError: If the transition is not allowed or job is already terminal
    """
    from yequ.models.timeline import TimelineEvent
    from yequ.services.timeline_writer import add_timeline_event

    current = JobStatus(job.status)
    target = JobStatus(target_status)

    # Check if transition is valid
    if target not in VALID_TRANSITIONS.get(current, set()):
        # Write audit event for the illegal attempt before raising
        event = TimelineEvent(
            global_seq=0,
            event_type="job.invalid_transition",
            actor_type="system",
            actor_id=node_id or "unknown",
            node_id=node_id,
            job_id=job.job_id,
            invocation_id=invocation_id or job.invocation_id,
            data={
                "from_status": current.value,
                "to_status": target.value,
                "reason": reason,
            },
        )
        await add_timeline_event(db, event)
        raise ValueError(f"Invalid transition: {current.value} -> {target.value}")

    # Reject if already terminal — terminal states are immutable
    if is_terminal(job.status):
        raise ValueError(f"Job {job.job_id} already in terminal state {job.status}")

    now = datetime.now(UTC)
    old_status = job.status
    job.status = target.value

    # Update timestamp/metadata fields based on target state
    if target == JobStatus.RUNNING:
        job.started_at = now
    elif target == JobStatus.CANCELLING:
        if reason:
            job.cancel_reason = reason
    elif target == JobStatus.CANCELLED:
        job.finished_at = now
    elif target == JobStatus.FAILED:
        job.finished_at = now
        if error_code:
            job.error_code = error_code
        if error_message:
            job.error_message = error_message
        if error_details is not None:
            job.error_details = error_details
    elif target == JobStatus.SUCCEEDED:
        job.finished_at = now
        if output is not None:
            job.output = output
    elif target == JobStatus.TIMEOUT:
        job.finished_at = now
        if reason:
            job.error_message = reason

    # Write success audit event
    event_data: JsonObject = {
        "from_status": old_status,
        "to_status": target.value,
    }
    if reason:
        event_data["reason"] = reason
    if error_code:
        event_data["error_code"] = error_code
    if error_message:
        event_data["error_message"] = error_message
    if error_details is not None:
        event_data["error_details"] = error_details
    if output is not None:
        event_data["output"] = output

    event = TimelineEvent(
        global_seq=0,
        event_type=f"job.{target.value}",
        actor_type="system",
        actor_id=node_id or "unknown",
        node_id=node_id,
        job_id=job.job_id,
        invocation_id=invocation_id or job.invocation_id,
        data=event_data,
        timestamp=now,
    )
    await add_timeline_event(db, event)
