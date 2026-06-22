"""Test job.poll capacity — running_jobs subtracts from available slots.

Tests the handle_job_poll function directly (bypasses HTTP for reliable DB access).
"""

import pytest
from datetime import UTC, datetime


async def _setup_node_with_queued_jobs(db, node_id: str, token: str, count: int):
    """Create a node with `count` queued jobs. Returns (node, job_ids)."""
    from yequ.services.node_auth import hash_token
    from yequ.models.node import Node
    from yequ.models.job import Job
    from yequ.models.invocation import Invocation
    from yequ.protocol import JobStatus

    now = datetime.now(UTC)
    node = Node(
        node_id=node_id,
        node_name=f"Node {node_id}",
        token_hash=hash_token(token),
        status="online",
        last_heartbeat_at=now,
    )
    db.add(node)
    await db.flush()

    job_ids = []
    for i in range(count):
        inv = Invocation(
            invocation_id=f"inv_{node_id}_{i}",
            actor_type="agent", actor_id="test",
            function_name="system.info", status="running",
            started_at=now,
        )
        db.add(inv)
        await db.flush()
        job = Job(
            job_id=f"job_{node_id}_{i}",
            invocation_id=inv.invocation_id,
            node_id=node_id,
            function_name="system.info",
            status=JobStatus.QUEUED,
            input_payload={}, timeout_sec=30, lease_sec=30,
        )
        db.add(job)
        job_ids.append(job.job_id)
    await db.commit()
    return node, job_ids


@pytest.mark.asyncio
async def test_capacity_4_running_0_queued_4_returns_4(db_session):
    """running_jobs=0, capacity=4, queued=4 → 4 jobs returned."""
    from yequ.services.node_service import handle_job_poll
    node, _ = await _setup_node_with_queued_jobs(db_session, "cap4-node", "tok-cap4", 4)
    result = await handle_job_poll(
        db_session, node, {"capacity": 4, "running_jobs": []}, None,
    )
    assert "jobs" in result
    assert len(result["jobs"]) == 4


@pytest.mark.asyncio
async def test_capacity_4_running_4_returns_empty(db_session):
    """running_jobs=4, capacity=4 → job.empty (0 jobs)."""
    from yequ.services.node_service import handle_job_poll
    node, _ = await _setup_node_with_queued_jobs(db_session, "full-node", "tok-full", 4)
    result = await handle_job_poll(
        db_session, node,
        {"capacity": 4, "running_jobs": ["a", "b", "c", "d"]}, None,
    )
    assert result["jobs"] == [], (
        f"Expected empty when running_jobs == capacity, got {result['jobs']}"
    )


@pytest.mark.asyncio
async def test_capacity_4_running_5_returns_empty(db_session):
    """running_jobs=5, capacity=4 → job.empty (0 jobs)."""
    from yequ.services.node_service import handle_job_poll
    node, _ = await _setup_node_with_queued_jobs(db_session, "overfull-node", "tok-over", 4)
    result = await handle_job_poll(
        db_session, node,
        {"capacity": 4, "running_jobs": ["a", "b", "c", "d", "e"]}, None,
    )
    assert result["jobs"] == [], (
        f"Expected empty when running_jobs > capacity, got {result['jobs']}"
    )


@pytest.mark.asyncio
async def test_capacity_4_running_2_queued_4_returns_2(db_session):
    """running_jobs=2, capacity=4, queued=4 → 2 jobs returned."""
    from yequ.services.node_service import handle_job_poll
    node, _ = await _setup_node_with_queued_jobs(db_session, "half-node", "tok-half", 4)
    result = await handle_job_poll(
        db_session, node,
        {"capacity": 4, "running_jobs": ["a", "b"]}, None,
    )
    assert len(result["jobs"]) == 2
