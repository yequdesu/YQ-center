"""Integration tests for Job Runtime -- cancel, timeout, recovery, aggregation."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.job import Job
from yequ.models.resource_lock import ResourceLock
from yequ.protocol import InvocationStatus, JobStatus, LockStatus

# ── Cancel Flow ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_queued_job(db_session: AsyncSession):
    """Cancel a QUEUED job -- should go directly to CANCELLED."""
    from yequ.services.job_service import cancel_job

    job = Job(
        job_id="job_cancel_q",
        invocation_id="inv_cancel",
        node_id="test-node",
        function_name="test.func",
        status=JobStatus.QUEUED,
        timeout_sec=30,
    )
    db_session.add(job)
    await db_session.flush()

    await cancel_job(db_session, job, reason="test_cancel", node_id="test-node")
    await db_session.commit()

    result = await db_session.execute(select(Job).where(Job.job_id == "job_cancel_q"))
    fetched = result.scalar_one()
    assert fetched.status == JobStatus.CANCELLED
    assert fetched.finished_at is not None


@pytest.mark.asyncio
async def test_cancel_queued_job_releases_resource_lock(db_session: AsyncSession):
    """Immediate queued cancellation must not leave a held resource lock."""
    from yequ.services.job_service import cancel_job

    job = Job(
        job_id="job_cancel_q_lock",
        invocation_id="inv_cancel_lock",
        node_id="test-node",
        function_name="test.func",
        status=JobStatus.QUEUED,
        timeout_sec=30,
    )
    lock = ResourceLock(
        lock_id="lock_cancel_q",
        resource_key="node:test-node:transfer",
        job_id=job.job_id,
        invocation_id=job.invocation_id,
        node_id=job.node_id,
        status=LockStatus.HELD,
        created_at=datetime.now(UTC),
    )
    db_session.add_all([job, lock])
    await db_session.flush()

    await cancel_job(db_session, job, reason="test_cancel", node_id="test-node")
    await db_session.commit()

    result = await db_session.execute(
        select(ResourceLock).where(ResourceLock.lock_id == "lock_cancel_q")
    )
    fetched_lock = result.scalar_one()
    assert fetched_lock.status == LockStatus.RELEASED
    assert fetched_lock.released_at is not None


@pytest.mark.asyncio
async def test_cancel_running_job_goes_to_cancelling(db_session: AsyncSession):
    """Cancel a RUNNING job -- should go to CANCELLING (Daemon must stop it)."""
    from yequ.services.job_service import cancel_job

    job = Job(
        job_id="job_cancel_r",
        invocation_id="inv_cancel",
        node_id="test-node",
        function_name="test.func",
        status=JobStatus.RUNNING,
        timeout_sec=30,
    )
    db_session.add(job)
    await db_session.flush()

    await cancel_job(db_session, job, reason="user_requested", node_id="test-node")
    await db_session.commit()

    result = await db_session.execute(select(Job).where(Job.job_id == "job_cancel_r"))
    fetched = result.scalar_one()
    assert fetched.status == JobStatus.CANCELLING
    assert fetched.cancel_reason == "user_requested"


# ── Timeout Flow ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_expired_jobs(db_session: AsyncSession):
    """find_expired_jobs should return jobs with past lease_expires_at."""
    from yequ.services.job_service import find_expired_jobs

    now = datetime.now(UTC)

    # Expired job -- lease 10 minutes ago
    expired = Job(
        job_id="job_expired_1",
        invocation_id="inv_timeout",
        node_id="test-node",
        function_name="test.func",
        status=JobStatus.RUNNING,
        lease_expires_at=now - timedelta(minutes=10),
    )
    # Active job -- lease 10 minutes from now
    active = Job(
        job_id="job_active_1",
        invocation_id="inv_timeout",
        node_id="test-node",
        function_name="test.func",
        status=JobStatus.RUNNING,
        lease_expires_at=now + timedelta(minutes=10),
    )
    cancelling = Job(
        job_id="job_cancelling_expired",
        invocation_id="inv_timeout",
        node_id="test-node",
        function_name="test.func",
        status=JobStatus.CANCELLING,
        lease_expires_at=now - timedelta(minutes=10),
    )
    db_session.add_all([expired, active, cancelling])
    await db_session.commit()

    found = await find_expired_jobs(db_session)
    found_ids = {j.job_id for j in found}
    assert "job_expired_1" in found_ids
    assert "job_cancelling_expired" in found_ids
    assert "job_active_1" not in found_ids


@pytest.mark.asyncio
async def test_acquire_lock_releases_stale_terminal_owner(db_session: AsyncSession):
    """A stale held lock owned by a terminal job should not block future jobs."""
    from yequ.services.resource_lock_service import acquire_lock

    old_job = Job(
        job_id="job_stale_terminal_lock",
        invocation_id="inv_stale_lock",
        node_id="test-node",
        function_name="test.func",
        status=JobStatus.CANCELLED,
        timeout_sec=30,
    )
    old_lock = ResourceLock(
        lock_id="lock_stale_terminal",
        resource_key="node:test-node:transfer",
        job_id=old_job.job_id,
        invocation_id=old_job.invocation_id,
        node_id=old_job.node_id,
        status=LockStatus.HELD,
        created_at=datetime.now(UTC),
    )
    new_job = Job(
        job_id="job_new_lock_owner",
        invocation_id="inv_new_lock",
        node_id="test-node",
        function_name="test.func",
        status=JobStatus.QUEUED,
        timeout_sec=30,
    )
    db_session.add_all([old_job, old_lock, new_job])
    await db_session.flush()

    new_lock = await acquire_lock(
        db_session,
        "node:test-node:transfer",
        new_job.job_id,
        new_job.invocation_id,
        new_job.node_id,
    )
    await db_session.commit()

    assert new_lock.job_id == new_job.job_id
    result = await db_session.execute(
        select(ResourceLock).where(ResourceLock.lock_id == "lock_stale_terminal")
    )
    fetched_old_lock = result.scalar_one()
    assert fetched_old_lock.status == LockStatus.RELEASED
    assert fetched_old_lock.released_at is not None


@pytest.mark.asyncio
async def test_timeout_job_transitions(db_session: AsyncSession):
    """timeout_job should transition CLAIMED/RUNNING to TIMEOUT."""
    from yequ.services.job_service import timeout_job

    job = Job(
        job_id="job_to_timeout",
        invocation_id="inv_timeout",
        node_id="test-node",
        function_name="test.func",
        status=JobStatus.RUNNING,
        lease_expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    db_session.add(job)
    await db_session.flush()

    await timeout_job(db_session, job, node_id="test")
    await db_session.commit()

    result = await db_session.execute(select(Job).where(Job.job_id == "job_to_timeout"))
    fetched = result.scalar_one()
    assert fetched.status == JobStatus.TIMEOUT
    assert fetched.finished_at is not None


# ── Recovery ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extend_expired_jobs_for_recovery_keeps_jobs_non_terminal(
    db_session: AsyncSession,
):
    """Startup recovery should give Nodes time to reconcile local terminal results."""
    from yequ.services.job_service import extend_expired_jobs_for_recovery

    now = datetime.now(UTC)
    job = Job(
        job_id="job_recovery_grace",
        invocation_id="inv_recovery_grace",
        node_id="test-node",
        function_name="test.func",
        status=JobStatus.RUNNING,
        timeout_sec=30,
        lease_sec=10,
        lease_expires_at=now - timedelta(seconds=30),
    )
    db_session.add(job)
    await db_session.flush()

    count = await extend_expired_jobs_for_recovery(
        db_session,
        recovery_window_sec=300,
        now=now,
    )
    await db_session.commit()

    result = await db_session.execute(select(Job).where(Job.job_id == "job_recovery_grace"))
    fetched = result.scalar_one()
    assert count == 1
    assert fetched.status == JobStatus.RUNNING
    assert fetched.finished_at is None
    assert fetched.lease_expires_at is not None
    assert fetched.lease_expires_at > now


@pytest.mark.asyncio
async def test_find_incomplete_jobs(db_session: AsyncSession):
    """find_incomplete_jobs should return non-terminal jobs only."""
    from yequ.services.job_service import find_incomplete_jobs

    running = Job(
        job_id="job_inc_1",
        invocation_id="inv_rec",
        node_id="test-node",
        function_name="test.func",
        status=JobStatus.RUNNING,
    )
    queued = Job(
        job_id="job_inc_2",
        invocation_id="inv_rec",
        node_id="test-node",
        function_name="test.func",
        status=JobStatus.QUEUED,
    )
    succeeded = Job(
        job_id="job_inc_3",
        invocation_id="inv_rec",
        node_id="test-node",
        function_name="test.func",
        status=JobStatus.SUCCEEDED,
    )
    db_session.add_all([running, queued, succeeded])
    await db_session.commit()

    incomplete = await find_incomplete_jobs(db_session)
    incomplete_ids = {j.job_id for j in incomplete}
    assert "job_inc_1" in incomplete_ids  # running
    assert "job_inc_2" in incomplete_ids  # queued
    assert "job_inc_3" not in incomplete_ids  # terminal


# ── Invocation Aggregation ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_aggregate_all_succeeded(db_session: AsyncSession):
    """All jobs succeeded -> invocation succeeded."""
    from yequ.services.invocation_service import aggregate_invocation_status

    inv_id = "inv_agg_success"
    db_session.add_all(
        [
            Job(
                job_id="j_s1",
                invocation_id=inv_id,
                node_id="n1",
                function_name="f",
                status=JobStatus.SUCCEEDED,
            ),
            Job(
                job_id="j_s2",
                invocation_id=inv_id,
                node_id="n1",
                function_name="f",
                status=JobStatus.SUCCEEDED,
            ),
        ]
    )
    await db_session.commit()

    status = await aggregate_invocation_status(db_session, inv_id)
    assert status == InvocationStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_aggregate_one_failed(db_session: AsyncSession):
    """Any failed job -> invocation failed."""
    from yequ.services.invocation_service import aggregate_invocation_status

    inv_id = "inv_agg_fail"
    db_session.add_all(
        [
            Job(
                job_id="j_f1",
                invocation_id=inv_id,
                node_id="n1",
                function_name="f",
                status=JobStatus.SUCCEEDED,
            ),
            Job(
                job_id="j_f2",
                invocation_id=inv_id,
                node_id="n1",
                function_name="f",
                status=JobStatus.FAILED,
            ),
        ]
    )
    await db_session.commit()

    status = await aggregate_invocation_status(db_session, inv_id)
    assert status == InvocationStatus.FAILED


@pytest.mark.asyncio
async def test_aggregate_some_running(db_session: AsyncSession):
    """Some jobs still running -> invocation running."""
    from yequ.services.invocation_service import aggregate_invocation_status

    inv_id = "inv_agg_running"
    db_session.add_all(
        [
            Job(
                job_id="j_r1",
                invocation_id=inv_id,
                node_id="n1",
                function_name="f",
                status=JobStatus.SUCCEEDED,
            ),
            Job(
                job_id="j_r2",
                invocation_id=inv_id,
                node_id="n1",
                function_name="f",
                status=JobStatus.RUNNING,
            ),
        ]
    )
    await db_session.commit()

    status = await aggregate_invocation_status(db_session, inv_id)
    assert status == InvocationStatus.RUNNING


@pytest.mark.asyncio
async def test_aggregate_empty(db_session: AsyncSession):
    """No jobs -> invocation pending."""
    from yequ.services.invocation_service import aggregate_invocation_status

    status = await aggregate_invocation_status(db_session, "inv_nonexistent")
    assert status == InvocationStatus.PENDING


# ── Invocation -> Job Integration ────────────────────────────────────


@pytest.mark.asyncio
async def test_create_invocation_and_job(db_session: AsyncSession):
    """Creating an invocation should fan out to a queued job."""
    from yequ.services.invocation_service import create_invocation, start_invocation
    from yequ.services.job_service import create_job

    inv = await create_invocation(
        db_session,
        actor_type="user",
        actor_id="tester",
        function_name="system.metrics.snapshot",
        target_node_id="test-node-1",
        input_payload={},
    )
    start_invocation(inv)
    assert inv.status == InvocationStatus.RUNNING

    job = await create_job(
        db_session,
        invocation_id=inv.invocation_id,
        node_id="test-node-1",
        function_name="system.metrics.snapshot",
    )
    await db_session.commit()

    assert job.status == JobStatus.QUEUED
    assert job.invocation_id == inv.invocation_id
