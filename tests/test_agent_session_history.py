"""Regression tests for persisted Agent session history."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_session_uses_phase5_default_step_budget(client: AsyncClient):
    from yequ.agent.limits import DEFAULT_AGENT_MAX_STEPS

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "limits-test", "execution_mode": "auto"},
    )
    assert session_resp.status_code == 201
    assert session_resp.json()["max_steps"] == DEFAULT_AGENT_MAX_STEPS


@pytest.mark.asyncio
async def test_stream_invoke_persists_session_messages(client: AsyncClient):
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.api.routes.agent import register_provider

    provider = FakeAgentProvider("history-test-provider")
    register_provider(provider)

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "history-test", "execution_mode": "auto"},
    )
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    stream_resp = await client.post(
        "/agent/invoke/stream",
        json={
            "session_id": session_id,
            "provider_name": "history-test-provider",
            "prompt": "hello persisted history",
            "execution_mode": "auto",
        },
    )
    assert stream_resp.status_code == 200
    assert "agent.completed" in stream_resp.text

    detail_resp = await client.get(f"/admin/sessions/{session_id}")
    assert detail_resp.status_code == 200

    messages = detail_resp.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "hello persisted history"
    assert messages[1]["content"] == "default fake response"
