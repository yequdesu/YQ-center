# YeQu Center Stage 3: Job Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement the full Job state machine with unified transition function, Invocation→Job creation, lease/cancel/timeout flows, background timeout scanner, recovery on restart, job.event/job.cancel endpoints, and comprehensive tests.

**Architecture:** State machine logic centralized in `job_state_machine.py` with a single `transition()` function. All state changes go through it — illegal transitions are rejected and audit events written. Background asyncio task scans for expired jobs. Lifespan hooks handle recovery and scanner lifecycle.

**Tech Stack:** Python 3.11, Pydantic v2, SQLAlchemy 2 (async), FastAPI, pytest

---

## File Map

| File | Responsibility |
|---|---|
| `src/yequ/services/job_state_machine.py` | Unified transition function, valid transition map |
| `src/yequ/services/job_service.py` | Job creation, cancel, timeout, timeline writing |
| `src/yequ/services/invocation_service.py` | Invocation → multi-Job creation, aggregation |
| `src/yequ/services/timeout_scanner.py` | Background asyncio task — scan & timeout expired jobs |
| `src/yequ/services/node_service.py` | **Modify:** add job.event, job.cancel; use state machine |
| `src/yequ/api/routes/yqp.py` | **Modify:** add job.cancel, job.event dispatch |
| `src/yequ/api/routes/admin.py` | **Modify:** add POST /admin/invocations |
| `src/yequ/api/app.py` | **Modify:** add timeout scanner + recovery to lifespan |
| `tests/test_job_state_machine.py` | State machine unit tests |
| `tests/test_job_runtime.py` | Integration tests: cancel, timeout, recovery, aggregation |

---

### Task 1: Job State Machine

**Files:**
- Create: `src/yequ/services/job_state_machine.py`
- Test: `tests/test_job_state_machine.py`

The state machine defines the ONLY valid transitions. All code changing job status MUST use `transition()`.

Valid transitions:
```
created → queued
queued → claimed
queued → cancelled      (cancel before pickup)
claimed → running
claimed → timeout       (lease expired before accept)
running → succeeded
running → failed
running → cancelling
running → timeout       (lease expired while running)
cancelling → cancelled
cancelling → failed     (cancel failed, job finished with error)
```

`transition(job, target_status, db, node_id)`:
- Checks if transition is valid
- If invalid: writes TimelineEvent (event_type="job.invalid_transition"), raises ValueError
- If valid: updates job.status, writes TimelineEvent for the transition
- Terminal states (succeeded/failed/cancelled/timeout) can only be written once

```python
# src/yequ/services/job_state_machine.py
"""Job state machine — unified transition function.

ALL job status changes MUST go through transition().
Illegal transitions are rejected and audit events written.
Terminal states are immutable.
"""

from yequ.protocol import JobStatus

# Valid transitions from each state
VALID_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.CREATED: {JobStatus.QUEUED},
    JobStatus.QUEUED: {JobStatus.CLAIMED, JobStatus.CANCELLED},
    JobStatus.CLAIMED: {JobStatus.RUNNING, JobStatus.TIMEOUT},
    JobStatus.RUNNING: {
        JobStatus.SUCCEEDED, JobStatus.FAILED,
        JobStatus.CANCELLING, JobStatus.TIMEOUT,
    },
    JobStatus.CANCELLING: {JobStatus.CANCELLED, JobStatus.FAILED},
    # Terminal states — no outgoing transitions
    JobStatus.SUCCEEDED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
    JobStatus.TIMEOUT: set(),
}

TERMINAL_STATUSES: set[JobStatus] = {
    JobStatus.SUCCEEDED, JobStatus.FAILED,
    JobStatus.CANCELLED, JobStatus.TIMEOUT,
}

NON_TERMINAL_STATUSES: set[JobStatus] = {
    JobStatus.CREATED, JobStatus.QUEUED, JobStatus.CLAIMED,
    JobStatus.RUNNING, JobStatus.CANCELLING,
}


def is_valid_transition(current: str, target: str) -> bool:
    """Check if a transition is valid."""
    cur = JobStatus(current)
    tgt = JobStatus(target)
    return tgt in VALID_TRANSITIONS.get(cur, set())


def is_terminal(status: str) -> bool:
    """Check if a status is a terminal state."""
    return JobStatus(status) in TERMINAL_STATUSES


async def transition(
    db,
    job,
    target_status: str,
    *,
    node_id: str | None = None,
    invocation_id: str | None = None,
    reason: str | None = None,
    output: dict | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Execute a job state transition.

    Validates the transition, writes an audit TimelineEvent,
    and updates the job record. Raises ValueError for illegal transitions.

    Args:
        db: AsyncSession
        job: Job ORM object
        target_status: The target JobStatus value
        node_id: Node performing the transition (for audit)
        invocation_id: Associated invocation (for audit)
        reason: Human-readable reason (for cancel/timeout)
        output: Job output data (for succeeded)
        error_code: Error code (for failed)
        error_message: Error message (for failed)
    """
    from datetime import datetime, timezone

    from yequ.models.timeline import TimelineEvent
    from yequ.protocol import JobStatus

    current = JobStatus(job.status)
    target = JobStatus(target_status)

    if target not in VALID_TRANSITIONS.get(current, set()):
        # Write audit event for illegal attempt
        event = TimelineEvent(
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
        db.add(event)
        raise ValueError(
            f"Invalid transition: {current.value} -> {target.value}"
        )

    # If already terminal, reject (terminal immutability)
    if is_terminal(job.status):
        raise ValueError(
            f"Job {job.job_id} already in terminal state {job.status}"
        )

    now = datetime.now(timezone.utc)
    old_status = job.status
    job.status = target.value

    # Update timestamp fields based on transition
    if target == JobStatus.RUNNING:
        job.started_at = now
    elif target in TERMINAL_STATUSES:
        job.finished_at = now
        if output is not None:
            job.output = output
        if error_code:
            job.error_code = error_code
        if error_message:
            job.error_message = error_message
    elif target == JobStatus.CANCELLING:
        if reason:
            job.cancel_reason = reason

    # Write success audit event
    event = TimelineEvent(
        event_type=f"job.{target.value}",
        actor_type="system",
        actor_id=node_id or "unknown",
        node_id=node_id,
        job_id=job.job_id,
        invocation_id=invocation_id or job.invocation_id,
        data={
            "from_status": old_status,
            "to_status": target.value,
            "reason": reason,
            "error_code": error_code,
        },
    )
    db.add(event)
    await db.flush()
```

---

### Task 2: Job Service — cancel, timeout, creation

**Files:**
- Create: `src/yequ/services/job_service.py`

```python
"""Job service — creation, cancellation, timeout, lease management."""

from datetime import datetime, timezone

from sqlalchemy import select, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.job import Job
from yequ.models.timeline import TimelineEvent
from yequ.protocol import JobStatus
from yequ.services.job_state_machine import (
    TERMINAL_STATUSES,
    NON_TERMINAL_STATUSES,
    transition,
)


async def create_job(
    db: AsyncSession,
    *,
    invocation_id: str,
    node_id: str,
    function_name: str,
    input_payload: dict | None = None,
    timeout_sec: int = 30,
    lease_sec: int = 30,
) -> Job:
    """Create a new Job in CREATED status, then transition to QUEUED."""
    from yequ.models.job import Job as JobModel

    job = JobModel(
        job_id=f"job_{invocation_id}_{function_name.replace('.', '_')}",
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
    await transition(db, job, JobStatus.QUEUED, node_id=node_id,
                     invocation_id=invocation_id)
    await db.flush()
    return job


async def cancel_job(
    db: AsyncSession,
    job: Job,
    *,
    reason: str = "user_requested",
    node_id: str | None = None,
) -> None:
    """Cancel a running job.

    running → cancelling (Center requests, Daemon should stop)
    queued → cancelled (immediate cancel, not yet picked up)
    """
    if job.status == JobStatus.QUEUED:
        await transition(db, job, JobStatus.CANCELLED,
                         node_id=node_id, reason=reason)
    elif job.status in (JobStatus.CLAIMED, JobStatus.RUNNING):
        await transition(db, job, JobStatus.CANCELLING,
                         node_id=node_id, reason=reason)
    else:
        raise ValueError(f"Cannot cancel job in status {job.status}")


async def timeout_job(
    db: AsyncSession,
    job: Job,
    *,
    node_id: str | None = None,
) -> None:
    """Mark a job as timeout due to lease expiry."""
    await transition(db, job, JobStatus.TIMEOUT,
                     node_id=node_id, reason="lease_expired")


async def find_expired_jobs(db: AsyncSession) -> list[Job]:
    """Find all jobs that have expired leases and should time out."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Job).where(
            Job.status.in_([JobStatus.CLAIMED, JobStatus.RUNNING]),
            Job.lease_expires_at.isnot(None),
            Job.lease_expires_at < now,
        )
    )
    return list(result.scalars().all())


async def find_incomplete_jobs(db: AsyncSession) -> list[Job]:
    """Find all jobs that are not in terminal state (for recovery)."""
    terminal = [s.value for s in TERMINAL_STATUSES]
    result = await db.execute(
        select(Job).where(Job.status.notin_(terminal))
    )
    return list(result.scalars().all())
```

---

### Task 3: Invocation Service

**Files:**
- Create: `src/yequ/services/invocation_service.py`

```python
"""Invocation service — create Invocations, fan out to Jobs, aggregate status."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.invocation import Invocation
from yequ.models.job import Job
from yequ.protocol import InvocationStatus, JobStatus
from yequ.services.job_state_machine import TERMINAL_STATUSES


async def create_invocation(
    db: AsyncSession,
    *,
    actor_type: str = "user",
    actor_id: str = "admin",
    session_id: str | None = None,
    function_name: str,
    input_payload: dict | None = None,
    target_node_id: str,
    execution_mode: str = "auto",
    max_depth: int | None = None,
    max_steps: int | None = None,
    max_total_duration_sec: int | None = None,
    call_path: list[str] | None = None,
) -> Invocation:
    """Create an Invocation and fan out to Jobs on the target node."""
    inv = Invocation(
        invocation_id=f"inv_{actor_id}_{function_name.replace('.', '_')}",
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

    # Transition to running
    inv.status = InvocationStatus.RUNNING
    inv.started_at = datetime.now(timezone.utc)
    await db.flush()

    return inv


async def aggregate_invocation_status(
    db: AsyncSession,
    invocation_id: str,
) -> str:
    """Compute Invocation aggregate status from its Jobs.

    - All terminal + all succeeded → succeeded
    - Any failed → failed
    - Any timeout → timeout
    - Any cancelled → cancelled
    - Otherwise → running
    """
    result = await db.execute(
        select(Job).where(Job.invocation_id == invocation_id)
    )
    jobs = result.scalars().all()

    if not jobs:
        return InvocationStatus.PENDING

    statuses = {job.status for job in jobs}
    terminal_set = {s.value for s in TERMINAL_STATUSES}

    all_terminal = statuses.issubset(terminal_set)

    if JobStatus.FAILED in statuses:
        return InvocationStatus.FAILED
    if JobStatus.TIMEOUT in statuses:
        return InvocationStatus.TIMEOUT
    if JobStatus.CANCELLED in statuses:
        return InvocationStatus.CANCELLED
    if all_terminal and statuses == {JobStatus.SUCCEEDED}:
        return InvocationStatus.SUCCEEDED
    if all_terminal:
        return InvocationStatus.PARTIAL

    return InvocationStatus.RUNNING
```

---

### Task 4: Timeout Scanner

**Files:**
- Create: `src/yequ/services/timeout_scanner.py`

```python
"""Background timeout scanner — periodically checks for expired jobs."""

import asyncio

from yequ.db import async_session_factory
from yequ.logconfig import get_logger
from yequ.services.job_service import find_expired_jobs, timeout_job

log = get_logger(__name__)


class TimeoutScanner:
    """Background task that scans for expired job leases and times them out."""

    def __init__(self, interval_sec: int = 5) -> None:
        self._interval_sec = interval_sec
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background scan loop."""
        self._task = asyncio.create_task(self._run())
        log.info("timeout scanner started", interval_sec=self._interval_sec)

    async def stop(self) -> None:
        """Stop the background scan loop."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("timeout scanner stopped")

    async def _run(self) -> None:
        """Main loop — scan for expired jobs on interval."""
        while True:
            try:
                await self._scan()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("timeout scanner error")
            await asyncio.sleep(self._interval_sec)

    async def _scan(self) -> None:
        """Scan for expired jobs and mark them timeout."""
        async with async_session_factory() as db:
            jobs = await find_expired_jobs(db)
            for job in jobs:
                await timeout_job(db, job, node_id="timeout_scanner")
                log.info(
                    "job timed out",
                    job_id=job.job_id,
                    node_id=job.node_id,
                    lease_expires_at=str(job.lease_expires_at),
                )
            if jobs:
                await db.commit()


# Singleton
_scanner: TimeoutScanner | None = None


def get_scanner(interval_sec: int = 5) -> TimeoutScanner:
    global _scanner
    if _scanner is None:
        _scanner = TimeoutScanner(interval_sec=interval_sec)
    return _scanner
```

---

### Task 5: job.event + job.cancel handlers

**Files:**
- Modify: `src/yequ/services/node_service.py` — add two handlers
- Modify: `src/yequ/api/routes/yqp.py` — add dispatch

Add `handle_job_event` and `handle_job_cancel` to node_service.py.

`handle_job_event` writes a TimelineEvent for progress/log/cancelling events.
`handle_job_cancel` triggers the cancel flow via job_service.cancel_job.

---

### Task 6: Invocation API + Lifespan Integration

**Files:**
- Modify: `src/yequ/api/routes/admin.py` — add POST /admin/invocations
- Modify: `src/yequ/api/app.py` — add timeout scanner lifecycle

Add invocation creation endpoint.
Wire timeout scanner start/stop into lifespan.
Add recovery step: on startup, find incomplete jobs and mark claimed/running ones with expired leases as timeout.

---

### Task 7: Tests + Final Verification

**Files:**
- Create: `tests/test_job_state_machine.py` — state machine unit tests
- Create: `tests/test_job_runtime.py` — integration tests (cancel, timeout, recovery, aggregation)

State machine tests verify:
- All valid transitions succeed
- All illegal transitions raise ValueError
- Terminal states reject further transitions
- Audit events are written

Integration tests verify:
- Invocation → Job creation → poll → accept → finish
- Cancel flow: running → cancelling → cancelled
- Timeout: lease expiry → background scanner marks timeout
- Recovery: find_incomplete_jobs returns correct set
- Aggregation: multi-job invocation status computation
```

---

## Stage 3 Exit Criteria

- [ ] Unified transition() function — all status changes use it
- [ ] All valid transitions pass; illegal transitions rejected + audit
- [ ] Terminal state immutability enforced
- [ ] Invocation → Job creation flow
- [ ] Cancel flow (job.cancel endpoint + cancelling state)
- [ ] job.event endpoint (progress/log/cancelling)
- [ ] Background timeout scanner (periodic asyncio task)
- [ ] Recovery: Center restart → incomplete jobs recovered
- [ ] Invocation aggregation (multi-job → aggregate status)
- [ ] State machine unit tests (valid/invalid/terminal/recovery)
- [ ] Integration tests (cancel/timeout/recovery/aggregation)
