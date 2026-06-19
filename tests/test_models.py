"""Tests for database model instantiation and persistence."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models import Node, Capability, Invocation, Job, TimelineEvent, Session
from yequ.protocol import JobStatus, NodeStatus, RiskLevel, Effect, Idempotency


@pytest.mark.asyncio
async def test_create_and_query_node(db_session: AsyncSession):
    """Node model should persist and be queryable."""
    node = Node(
        node_id="test-node-1",
        node_name="Test Node",
        token_hash="hash_abc123",
        role="compute",
        locality="local",
        status=NodeStatus.PROVISIONED,
    )
    db_session.add(node)
    await db_session.commit()

    result = await db_session.execute(
        select(Node).where(Node.node_id == "test-node-1")
    )
    fetched = result.scalar_one()
    assert fetched.node_name == "Test Node"
    assert fetched.status == NodeStatus.PROVISIONED
    assert fetched.created_at is not None


@pytest.mark.asyncio
async def test_create_job_with_status(db_session: AsyncSession):
    """Job model should store status and transition history."""
    job = Job(
        job_id="job_test_001",
        invocation_id="inv_001",
        node_id="test-node-1",
        function_name="system.metrics.snapshot",
        status=JobStatus.CREATED,
        timeout_sec=30,
        lease_sec=10,
    )
    db_session.add(job)
    await db_session.commit()

    result = await db_session.execute(
        select(Job).where(Job.job_id == "job_test_001")
    )
    fetched = result.scalar_one()
    assert fetched.status == JobStatus.CREATED
    assert fetched.timeout_sec == 30
    assert fetched.attempt == 1


@pytest.mark.asyncio
async def test_create_capability_function(db_session: AsyncSession):
    """Capability model should store function manifests."""
    # Need a node first for the FK constraint
    node = Node(
        node_id="cap-node-1",
        node_name="Cap Test Node",
        token_hash="hash_cap",
        status=NodeStatus.ONLINE,
    )
    db_session.add(node)
    await db_session.flush()

    cap = Capability(
        node_record_id=node.id,
        plugin_id="system.metrics",
        plugin_version="0.1.0",
        capability_type="function",
        name="system.metrics.snapshot",
        risk=RiskLevel.SAFE,
        effect=Effect.READ,
        timeout_sec=5,
        idempotency=Idempotency.IDEMPOTENT,
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"cpu": {"type": "number"}}},
    )
    db_session.add(cap)
    await db_session.commit()

    result = await db_session.execute(
        select(Capability).where(Capability.name == "system.metrics.snapshot")
    )
    fetched = result.scalar_one()
    assert fetched.capability_type == "function"
    assert fetched.risk == RiskLevel.SAFE
    assert fetched.input_schema == {"type": "object", "properties": {}}


@pytest.mark.asyncio
async def test_create_capability_signal(db_session: AsyncSession):
    """Capability model should store signal manifests."""
    node = Node(
        node_id="cap-signal-node",
        node_name="Cap Signal Node",
        token_hash="hash_signal",
        status=NodeStatus.ONLINE,
    )
    db_session.add(node)
    await db_session.flush()

    cap = Capability(
        node_record_id=node.id,
        plugin_id="system.metrics",
        plugin_version="0.1.0",
        capability_type="signal",
        name="system.cpu.usage",
        scope="node",
        ttl_sec=15,
        value_schema={"type": "number", "minimum": 0, "maximum": 100},
    )
    db_session.add(cap)
    await db_session.commit()

    result = await db_session.execute(
        select(Capability).where(Capability.name == "system.cpu.usage")
    )
    fetched = result.scalar_one()
    assert fetched.capability_type == "signal"
    assert fetched.scope == "node"
    assert fetched.ttl_sec == 15


@pytest.mark.asyncio
async def test_create_invocation(db_session: AsyncSession):
    """Invocation model should persist call intent."""
    inv = Invocation(
        invocation_id="inv_test_001",
        actor_type="user",
        actor_id="user_test",
        session_id="sess_001",
        function_name="system.metrics.snapshot",
        call_path=["system.metrics.snapshot"],
        max_depth=3,
        max_steps=10,
        max_total_duration_sec=300,
    )
    db_session.add(inv)
    await db_session.commit()

    result = await db_session.execute(
        select(Invocation).where(Invocation.invocation_id == "inv_test_001")
    )
    fetched = result.scalar_one()
    assert fetched.actor_type == "user"
    assert fetched.call_path == ["system.metrics.snapshot"]
    assert fetched.max_depth == 3


@pytest.mark.asyncio
async def test_create_timeline_event(db_session: AsyncSession):
    """TimelineEvent should be insertable."""
    event = TimelineEvent(
        global_seq=1,
        event_type="job.started",
        actor_type="system",
        actor_id="node_test",
        node_id="test-node-1",
        job_id="job_001",
        invocation_id="inv_001",
        data={"message": "job started"},
    )
    db_session.add(event)
    await db_session.commit()

    result = await db_session.execute(
        select(TimelineEvent).where(TimelineEvent.job_id == "job_001")
    )
    fetched = result.scalar_one()
    assert fetched.event_type == "job.started"
    assert fetched.global_seq is not None


@pytest.mark.asyncio
async def test_create_session(db_session: AsyncSession):
    """Session model should persist interactive context."""
    sess = Session(
        session_id="sess_test_001",
        actor_type="user",
        actor_id="user_1",
        execution_mode="auto",
        status="active",
    )
    db_session.add(sess)
    await db_session.commit()

    result = await db_session.execute(
        select(Session).where(Session.session_id == "sess_test_001")
    )
    fetched = result.scalar_one()
    assert fetched.actor_type == "user"
    assert fetched.execution_mode == "auto"
    assert fetched.status == "active"


@pytest.mark.asyncio
async def test_node_capability_relationship(db_session: AsyncSession):
    """Node and Capability should have a working relationship."""
    node = Node(
        node_id="test-node-rel",
        node_name="Relationship Test",
        token_hash="hash_xyz",
        status=NodeStatus.ONLINE,
    )
    cap1 = Capability(
        node_record_id="",  # placeholder, will be set by relationship
        plugin_id="sys.metrics",
        plugin_version="0.1.0",
        capability_type="function",
        name="sys.metrics.cpu",
    )
    cap2 = Capability(
        node_record_id="",  # placeholder, will be set by relationship
        plugin_id="sys.metrics",
        plugin_version="0.1.0",
        capability_type="signal",
        name="sys.cpu.usage",
    )
    node.capabilities.extend([cap1, cap2])
    db_session.add(node)
    await db_session.commit()

    result = await db_session.execute(
        select(Node).where(Node.node_id == "test-node-rel")
    )
    fetched = result.scalar_one()
    assert len(fetched.capabilities) == 2
    assert {c.name for c in fetched.capabilities} == {"sys.metrics.cpu", "sys.cpu.usage"}
