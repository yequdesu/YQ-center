"""Tests for current SignalState query and freshness behavior."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from tests.conftest import make_yqp_envelope
from yequ.models.signal_state import SignalState
from yequ.models.timeline import TimelineEvent
from yequ.services.signal_state_service import mark_stale_signals


@pytest.mark.asyncio
async def test_admin_signal_state_query_and_stale_refresh(client, provisioned_node, db_session):
    node, token = provisioned_node
    auth = {"Authorization": f"Bearer {token}"}

    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.hello",
            node.node_id,
            payload={"daemon_version": "0.1.0"},
        ),
        headers=auth,
    )
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.register_capabilities",
            node.node_id,
            payload={
                "plugins": [
                    {
                        "plugin_id": "signals.test",
                        "plugin_version": "1.0.0",
                        "functions": [],
                        "signals": [
                            {
                                "name": "cpu.usage",
                                "value_schema": {
                                    "type": "number",
                                    "minimum": 0,
                                    "maximum": 100,
                                },
                                "scope": "node",
                                "ttl_sec": 30,
                            }
                        ],
                    }
                ]
            },
        ),
        headers=auth,
    )

    report = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "signal.report",
            node.node_id,
            payload={"signals": [{"name": "cpu.usage", "value": 42, "ttl_sec": 1}]},
        ),
        headers=auth,
    )
    assert report.status_code == 200
    assert report.json()["payload"]["accepted"] == 1

    fresh = await client.get(f"/admin/nodes/{node.node_id}/signals")
    assert fresh.status_code == 200
    assert fresh.json()[0]["signal_name"] == "cpu.usage"
    assert fresh.json()[0]["freshness_status"] == "fresh"

    result = await db_session.execute(
        select(SignalState).where(
            SignalState.node_id == node.node_id,
            SignalState.signal_name == "cpu.usage",
        )
    )
    state = result.scalar_one()
    state.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    stale = await client.get(f"/admin/signals?node_id={node.node_id}&freshness_status=stale")
    assert stale.status_code == 200
    body = stale.json()
    assert len(body) == 1
    assert body[0]["signal_name"] == "cpu.usage"
    assert body[0]["freshness_status"] == "stale"
    assert body[0]["quality"] == "stale"

    node_detail = await client.get(f"/admin/nodes/{node.node_id}")
    assert node_detail.status_code == 200
    assert node_detail.json()["stale_signal_count"] == 1
    assert node_detail.json()["signal_stale"] is True

    nodes = await client.get("/admin/nodes")
    listed = next(n for n in nodes.json() if n["node_id"] == node.node_id)
    assert listed["stale_signal_count"] == 1
    assert listed["signal_stale"] is True


@pytest.mark.asyncio
async def test_node_signal_query_unknown_node_returns_404(client):
    response = await client.get("/admin/nodes/missing-node/signals")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_mark_stale_signals_writes_timeline_once(db_session):
    now = datetime.now(UTC)
    state = SignalState(
        node_id="node-signal-stale",
        signal_name="battery.level",
        value=10,
        value_schema={"type": "number"},
        scope="node",
        ttl_sec=1,
        freshness_status="fresh",
        quality="ok",
        reported_at=now - timedelta(seconds=5),
        expires_at=now - timedelta(seconds=4),
    )
    db_session.add(state)
    await db_session.commit()

    stale = await mark_stale_signals(db_session, now=now)
    assert [signal.signal_name for signal in stale] == ["battery.level"]
    assert state.freshness_status == "stale"
    assert state.quality == "stale"

    again = await mark_stale_signals(db_session, now=now)
    assert again == []

    result = await db_session.execute(
        select(TimelineEvent).where(
            TimelineEvent.event_type == "signal.stale",
            TimelineEvent.node_id == "node-signal-stale",
        )
    )
    events = result.scalars().all()
    assert len(events) == 1
    assert events[0].data["signal_name"] == "battery.level"
