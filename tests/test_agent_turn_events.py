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
async def test_agent_stream_emits_ycr_context_budget_events(client: AsyncClient, monkeypatch):
    from yequ.agent.agent_stream import agent_invoke_stream
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import ProviderInvokeResult
    from yequ.config import get_settings
    from yequ.ycr.budget import budget_profile_from_settings
    from yequ.ycr.context_packet import build_agent_context_packet

    provider = FakeAgentProvider("turn-ycr-budget-test")
    provider.set_sequence(
        [
            ProviderInvokeResult(
                message="Budget visible.",
                usage={
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                    "total_tokens": 15,
                },
            ),
        ]
    )

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "turn-ycr-budget-test", "execution_mode": "auto"},
    )
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    class FakeYcrClient:
        async def build_turn(self, **payload):
            return build_agent_context_packet(
                session_id=str(payload["session_id"]),
                actor_id=str(payload.get("actor_id") or ""),
                provider=str(payload["provider"]),
                model=str(payload.get("model") or ""),
                messages=list(payload.get("messages") or []),
                available_functions=list(payload.get("available_functions") or []),
                capability_context=dict(payload.get("capability_context") or {}),
                budget=budget_profile_from_settings(get_settings()),
                step=int(payload.get("step") or 1),
            )

    import yequ.agent.agent_stream as agent_stream_module

    monkeypatch.setattr(agent_stream_module, "get_ycr_client", lambda: FakeYcrClient())

    events = [
        event
        async for event in agent_invoke_stream(
            provider,
            session_id=session_id,
            prompt="show ycr budget",
            available_functions=[],
            execution_mode="auto",
        )
    ]

    ycr_events = [event for event in events if event["event_type"] == "agent.ycr.context"]
    assert [event["data"]["phase"] for event in ycr_events] == [
        "provider_input",
        "provider_output",
    ]
    assert ycr_events[0]["data"]["tokens"]["upload_estimated"] > 0
    assert ycr_events[1]["data"]["tokens"]["download_estimated"] > 0
    assert ycr_events[1]["data"]["tokens"]["upload_actual"] == 12
    assert ycr_events[1]["data"]["tokens"]["download_actual"] == 3


@pytest.mark.asyncio
async def test_agent_stream_fails_closed_when_ycr_build_turn_fails(
    client: AsyncClient, monkeypatch
):
    from yequ.agent.agent_stream import agent_invoke_stream
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.ycr.client import YcrError

    provider = FakeAgentProvider("turn-ycr-fail-closed-test")

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "turn-ycr-fail-closed-test", "execution_mode": "auto"},
    )
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    class FailingYcrClient:
        async def build_turn(self, **payload):
            raise YcrError("context_router_unavailable", "YCR unavailable")

    import yequ.agent.agent_stream as agent_stream_module

    monkeypatch.setattr(agent_stream_module, "get_ycr_client", lambda: FailingYcrClient())

    events = [
        event
        async for event in agent_invoke_stream(
            provider,
            session_id=session_id,
            prompt="do not reach provider",
            available_functions=[],
            execution_mode="auto",
        )
    ]

    event_types = [event["event_type"] for event in events]
    failed_events = [event for event in events if event["event_type"] == "agent.failed"]
    assert "agent.provider.started" not in event_types
    assert len(failed_events) == 1
    assert failed_events[0]["data"]["error_code"] == "context_router_unavailable"


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
