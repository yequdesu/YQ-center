"""Tests for durable Agent turn event streams."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_agent_stream_persists_turn_and_events(client: AsyncClient):
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import ProviderInvokeResult
    from yequ.api.routes.agent import register_provider

    provider = FakeAgentProvider("turn-events-test")
    provider.set_sequence(
        [
            ProviderInvokeResult(
                message="System check completed.",
                tool_calls=[],
                success=True,
            ),
        ]
    )
    register_provider(provider)

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "turn-events-test", "execution_mode": "auto"},
    )
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    stream_resp = await client.post(
        "/agent/invoke/stream",
        json={
            "session_id": session_id,
            "provider_name": "turn-events-test",
            "prompt": "check the machine",
            "execution_mode": "auto",
        },
    )
    assert stream_resp.status_code == 200

    events = _parse_sse_events(stream_resp.text)
    assert events
    turn_ids = {event.get("turn_id") for event in events}
    assert len(turn_ids) == 1
    turn_id = next(iter(turn_ids))
    assert isinstance(turn_id, str)
    assert turn_id.startswith("turn_")
    assert all(event.get("data", {}).get("turn_id") == turn_id for event in events)

    detail_resp = await client.get(f"/admin/sessions/{session_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["turns"][0]["turn_id"] == turn_id
    assert detail["turns"][0]["status"] == "succeeded"
    assert detail["turns"][0]["events"]
    assert detail["turns"][0]["prompt"] == "check the machine"

    turns_resp = await client.get(f"/admin/sessions/{session_id}/turns")
    assert turns_resp.status_code == 200
    assert turns_resp.json()[0]["turn_id"] == turn_id

    event_resp = await client.get(f"/admin/agent-turns/{turn_id}/events")
    assert event_resp.status_code == 200
    persisted = event_resp.json()
    persisted_types = [event["event_type"] for event in persisted]
    assert persisted_types[0] == "stream.open"
    assert "agent.prompt.received" in persisted_types
    assert "agent.completed" in persisted_types
    assert persisted_types[-1] == "stream.close"
    assert [event["seq"] for event in persisted] == list(range(1, len(persisted) + 1))


@pytest.mark.asyncio
async def test_delete_session_deletes_turn_events(client: AsyncClient):
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import ProviderInvokeResult
    from yequ.api.routes.agent import register_provider

    provider = FakeAgentProvider("turn-delete-test")
    provider.set_sequence(
        [
            ProviderInvokeResult(message="Done.", tool_calls=[], success=True),
        ]
    )
    register_provider(provider)

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "turn-delete-test", "execution_mode": "auto"},
    )
    session_id = session_resp.json()["session_id"]
    stream_resp = await client.post(
        "/agent/invoke/stream",
        json={
            "session_id": session_id,
            "provider_name": "turn-delete-test",
            "prompt": "hello",
        },
    )
    turn_id = _parse_sse_events(stream_resp.text)[0]["turn_id"]

    delete_resp = await client.delete(f"/admin/sessions/{session_id}")
    assert delete_resp.status_code == 204

    events_resp = await client.get(f"/admin/agent-turns/{turn_id}/events")
    assert events_resp.status_code == 404


@pytest.mark.asyncio
async def test_agent_plan_stream_reaches_provider_for_existing_session(client: AsyncClient):
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.api.routes.agent import register_provider

    provider = FakeAgentProvider("plan-stream-session-test")
    register_provider(provider)

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "plan-stream-session-test", "execution_mode": "auto"},
    )
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    stream_resp = await client.post(
        "/agent/plan/stream",
        json={
            "session_id": session_id,
            "provider_name": "plan-stream-session-test",
            "prompt": "check status",
            "target_node_id": "test-node",
            "execution_mode": "auto",
        },
    )

    assert stream_resp.status_code == 200
    event_types = [event["event_type"] for event in _parse_sse_events(stream_resp.text)]
    assert "agent.session.resolved" in event_types
    assert "agent.prompt.received" in event_types
    assert "agent.provider.started" in event_types


def _parse_sse_events(text: str) -> list[dict]:
    events: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data: "):
            continue
        events.append(json.loads(line[len("data: ") :]))
    return events
