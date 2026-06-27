"""Node Liveness tests — effective status, scheduling, API gates."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from tests.conftest import make_yqp_envelope
from yequ.config import get_settings
from yequ.models.node import Node
from yequ.models.timeline import TimelineEvent
from yequ.protocol.enums import NodeStatus
from yequ.services.node_liveness_service import (
    compute_effective_status,
    is_node_schedulable,
    mark_timed_out_nodes,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ── Unit tests: compute_effective_status ──


@pytest.mark.asyncio
async def test_compute_effective_online(db_session):
    """Fresh heartbeat → effective_status = online."""
    settings = get_settings()
    now = datetime.now(UTC)
    node = Node(
        node_id=f"node-{_uid()}",
        node_name="Test Online",
        token_hash="abc",
        status=NodeStatus.ONLINE,
        last_heartbeat_at=now - timedelta(seconds=5),
        heartbeat_interval_sec=10,
    )
    assert compute_effective_status(node, settings) == NodeStatus.ONLINE


@pytest.mark.asyncio
async def test_compute_effective_degraded(db_session):
    """Heartbeat age > interval * (multiplier - 1) → degraded."""
    settings = get_settings()
    # interval=10, multiplier=3 → degraded threshold = 20s
    now = datetime.now(UTC)
    node = Node(
        node_id=f"node-{_uid()}",
        node_name="Test Degraded",
        token_hash="abc",
        status=NodeStatus.ONLINE,
        last_heartbeat_at=now - timedelta(seconds=25),
        heartbeat_interval_sec=10,
    )
    assert compute_effective_status(node, settings) == NodeStatus.DEGRADED


@pytest.mark.asyncio
async def test_compute_effective_offline(db_session):
    """Heartbeat age > interval * multiplier → offline."""
    settings = get_settings()
    # interval=10, multiplier=3 → offline threshold = 30s
    now = datetime.now(UTC)
    node = Node(
        node_id=f"node-{_uid()}",
        node_name="Test Offline",
        token_hash="abc",
        status=NodeStatus.ONLINE,
        last_heartbeat_at=now - timedelta(seconds=35),
        heartbeat_interval_sec=10,
    )
    assert compute_effective_status(node, settings) == NodeStatus.OFFLINE


@pytest.mark.asyncio
async def test_compute_effective_no_heartbeat(db_session):
    """Never sent heartbeat + status ONLINE → online (freshly hello'd)."""
    settings = get_settings()
    node = Node(
        node_id=f"node-{_uid()}",
        node_name="No HB",
        token_hash="abc",
        status=NodeStatus.ONLINE,
        last_heartbeat_at=None,
    )
    assert compute_effective_status(node, settings) == NodeStatus.ONLINE


@pytest.mark.asyncio
async def test_compute_effective_provisioned(db_session):
    """Freshly provisioned, no heartbeat → online."""
    settings = get_settings()
    node = Node(
        node_id=f"node-{_uid()}",
        node_name="Provisioned",
        token_hash="abc",
        status=NodeStatus.PROVISIONED,
        last_heartbeat_at=None,
    )
    assert compute_effective_status(node, settings) == NodeStatus.ONLINE


# ── Unit tests: schedulability ──


@pytest.mark.asyncio
async def test_is_node_schedulable(db_session):
    """ONLINE → True, DEGRADED/OFFLINE → False."""
    settings = get_settings()
    now = datetime.now(UTC)

    # Online
    online_node = Node(
        node_id=f"node-{_uid()}",
        node_name="Sched Online",
        token_hash="abc",
        status=NodeStatus.ONLINE,
        last_heartbeat_at=now - timedelta(seconds=5),
        heartbeat_interval_sec=10,
    )
    ok, reason = is_node_schedulable(online_node, settings)
    assert ok is True
    assert reason is None

    # Degraded
    degraded_node = Node(
        node_id=f"node-{_uid()}",
        node_name="Sched Degraded",
        token_hash="abc",
        status=NodeStatus.ONLINE,
        last_heartbeat_at=now - timedelta(seconds=25),
        heartbeat_interval_sec=10,
    )
    ok, reason = is_node_schedulable(degraded_node, settings)
    assert ok is False
    assert reason == "node_degraded"

    # Offline
    offline_node = Node(
        node_id=f"node-{_uid()}",
        node_name="Sched Offline",
        token_hash="abc",
        status=NodeStatus.ONLINE,
        last_heartbeat_at=now - timedelta(seconds=35),
        heartbeat_interval_sec=10,
    )
    ok, reason = is_node_schedulable(offline_node, settings)
    assert ok is False
    assert reason == "node_offline"

    # Provisioned (online)
    prov_node = Node(
        node_id=f"node-{_uid()}",
        node_name="Sched Prov",
        token_hash="abc",
        status=NodeStatus.PROVISIONED,
        last_heartbeat_at=None,
    )
    ok, reason = is_node_schedulable(prov_node, settings)
    assert ok is True
    assert reason is None


# ── Integration tests ──


@pytest.mark.asyncio
async def test_mark_timed_out_nodes(db_session):
    """Scanner marks stale nodes offline + writes node.offline timeline."""
    settings = get_settings()
    now = datetime.now(UTC)

    # Create a node with stale heartbeat
    node = Node(
        node_id=f"node-{_uid()}",
        node_name="Stale Node",
        token_hash="abc",
        status=NodeStatus.ONLINE,
        last_heartbeat_at=now - timedelta(seconds=35),
        heartbeat_interval_sec=10,
    )
    db_session.add(node)
    await db_session.commit()

    # Run mark_timed_out_nodes
    timed_out = await mark_timed_out_nodes(db_session, settings)

    assert len(timed_out) == 1
    assert timed_out[0].node_id == node.node_id

    # Verify DB status changed
    await db_session.refresh(node)
    assert node.status == NodeStatus.OFFLINE

    # Verify node.offline timeline event written
    result = await db_session.execute(
        select(TimelineEvent).where(
            TimelineEvent.event_type == "node.offline",
            TimelineEvent.node_id == node.node_id,
        )
    )
    events = result.scalars().all()
    assert len(events) == 1
    assert events[0].data["reason"] == "heartbeat_timeout"


@pytest.mark.asyncio
async def test_heartbeat_restores_online(client: AsyncClient, db_session):
    """Heartbeat from an offline node restores it to online + writes node.online."""
    from yequ.services.node_auth import hash_token
    from yequ.services.node_service import handle_heartbeat

    token = f"tok-{_uid()}"
    node = Node(
        node_id=f"node-{_uid()}",
        node_name="Restore Node",
        token_hash=hash_token(token),
        status=NodeStatus.OFFLINE,
        last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=60),
        heartbeat_interval_sec=10,
    )
    db_session.add(node)
    await db_session.commit()

    # Simulate heartbeat
    settings = get_settings()
    await handle_heartbeat(
        db_session,
        node,
        {
            "daemon_uptime_sec": 3600,
            "running_jobs": 0,
            "status": "online",
        },
        settings,
    )

    await db_session.refresh(node)
    assert node.status == NodeStatus.ONLINE

    # Verify node.online timeline event
    result = await db_session.execute(
        select(TimelineEvent).where(
            TimelineEvent.event_type == "node.online",
            TimelineEvent.node_id == node.node_id,
        )
    )
    events = result.scalars().all()
    assert len(events) >= 1
    assert events[-1].data.get("recovery") is True


@pytest_asyncio.fixture
async def node_setup(client: AsyncClient):
    """Setup: provision node + register capabilities."""
    node_id = f"node-{_uid()}"
    token = f"tok-{_uid()}"
    auth = {"Authorization": f"Bearer {token}"}

    await client.post(
        "/admin/nodes",
        json={
            "node_id": node_id,
            "node_name": f"Liveness-{node_id}",
            "token": token,
        },
    )
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.hello",
            node_id,
            {
                "daemon_version": "0.1.0",
            },
        ),
        headers=auth,
    )
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.register_capabilities",
            node_id,
            payload={
                "plugins": [
                    {
                        "plugin_id": "test.plugin",
                        "plugin_version": "1.0.0",
                        "functions": [
                            {
                                "name": "system.metrics.snapshot",
                                "input_schema": {"type": "object"},
                                "output_schema": {"type": "object"},
                                "risk": "safe",
                                "effect": "read",
                                "timeout_sec": 5,
                                "idempotency": "idempotent",
                            },
                        ],
                        "signals": [],
                    }
                ]
            },
        ),
        headers=auth,
    )

    return node_id, token, auth


@pytest.mark.asyncio
async def test_admin_nodes_api_liveness_fields(client: AsyncClient, node_setup):
    """GET /admin/nodes returns effective_status, heartbeat_stale, schedulable etc."""
    node_id, token, auth = node_setup
    settings = get_settings()

    # Update the node with a fresh heartbeat so it's online
    from yequ.db import async_session_factory

    async with async_session_factory() as db:
        result = await db.execute(select(Node).where(Node.node_id == node_id))
        node = result.scalar_one()
        node.last_heartbeat_at = datetime.now(UTC)
        node.heartbeat_interval_sec = settings.default_heartbeat_interval_sec
        await db.commit()

    # Hit the API
    r = await client.get(f"/admin/nodes/{node_id}")
    assert r.status_code == 200
    data = r.json()

    assert "effective_status" in data
    assert "stored_status" in data
    assert "heartbeat_age_sec" in data
    assert "heartbeat_stale" in data
    assert "schedulable" in data

    # Fresh heartbeat → online + schedulable
    assert data["effective_status"] in ("online", "provisioned")
    # heartbeat_age_sec should be small
    assert data["heartbeat_age_sec"] is None or data["heartbeat_age_sec"] < 30
    assert data["schedulable"] is True


@pytest.mark.asyncio
async def test_invocation_rejects_offline_node(client: AsyncClient, db_session):
    """POST /admin/invocations returns 409 for offline node."""
    from yequ.services.node_auth import hash_token

    token = f"tok-{_uid()}"
    node = Node(
        node_id=f"node-{_uid()}",
        node_name="Offline Node",
        token_hash=hash_token(token),
        status=NodeStatus.OFFLINE,
        last_heartbeat_at=None,
    )
    db_session.add(node)
    await db_session.commit()

    r = await client.post(
        "/admin/invocations",
        json={
            "function_name": "system.metrics.snapshot",
            "target_node_id": node.node_id,
            "input": {},
        },
    )
    # Should reject — 409 NODE_UNAVAILABLE
    assert r.status_code == 409, f"Got {r.status_code}: {r.text[:300]}"
    body = r.json()
    # FastAPI wraps HTTPException detail; may be nested
    detail = body.get("detail", body)
    assert "NODE_UNAVAILABLE" in str(detail)
    assert node.node_id in str(detail)


@pytest.mark.asyncio
async def test_capability_available_false_when_node_offline(client: AsyncClient, node_setup):
    """Capability shows available=false when node is offline."""
    node_id, token, auth = node_setup
    settings = get_settings()

    # Set node heartbeat to stale
    from yequ.db import async_session_factory

    async with async_session_factory() as db:
        result = await db.execute(select(Node).where(Node.node_id == node_id))
        node = result.scalar_one()
        node.last_heartbeat_at = datetime.now(UTC) - timedelta(seconds=60)
        node.heartbeat_interval_sec = settings.default_heartbeat_interval_sec
        await db.commit()

    r = await client.get(f"/admin/capabilities?node_id={node_id}")
    assert r.status_code == 200
    capabilities = r.json()
    for cap in capabilities:
        assert "node_status" in cap
        assert "available" in cap
        assert cap["available"] is False
        assert cap["unavailable_reason"] == "node_offline"
