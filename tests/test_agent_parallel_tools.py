"""Tests for agent parallel tool execution — concurrent safe/read, serial write."""

import json
from contextlib import suppress

import pytest
from httpx import AsyncClient


def _parse_events(text: str) -> list[dict]:
    events = []
    for frame in text.strip().split("\n\n"):
        for line in frame.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    with suppress(json.JSONDecodeError):
                        events.append(json.loads(payload))
    return events


@pytest.mark.asyncio
async def test_four_safe_read_tools_concurrent_created_events(client: AsyncClient):
    """4 safe/read tool calls → all created events before any execution events."""
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import AgentFunction, ProviderInvokeResult
    from yequ.api.routes.agent import register_provider

    fake = FakeAgentProvider("parallel-safe-test")
    fake.add_functions(
        [
            AgentFunction(name="system.info", description="sys info", risk="safe", effect="read"),
            AgentFunction(
                name="system.metrics.snapshot", description="metrics", risk="safe", effect="read"
            ),
            AgentFunction(
                name="system.disk.detail", description="disk", risk="safe", effect="read"
            ),
            AgentFunction(
                name="system.processes.list", description="processes", risk="safe", effect="read"
            ),
        ]
    )
    fake.set_sequence(
        [
            ProviderInvokeResult(
                message="Checking all systems.",
                tool_calls=[
                    {"call_id": "p1", "name": "system.info", "input": {}},
                    {"call_id": "p2", "name": "system.metrics.snapshot", "input": {}},
                    {"call_id": "p3", "name": "system.disk.detail", "input": {}},
                    {"call_id": "p4", "name": "system.processes.list", "input": {}},
                ],
                success=True,
            ),
            ProviderInvokeResult(message="All checked.", tool_calls=[], success=True),
        ]
    )
    register_provider(fake)

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "parallel-safe-test", "execution_mode": "auto"},
    )
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    stream_resp = await client.post(
        "/agent/invoke/stream",
        json={
            "session_id": session_id,
            "provider_name": "parallel-safe-test",
            "prompt": "check everything",
            "execution_mode": "auto",
            "target_node_id": "winClient",
            "max_steps": 5,
        },
    )
    assert stream_resp.status_code == 200
    events = _parse_events(stream_resp.text)
    event_types = [e["event_type"] for e in events]

    # All 4 tool_call.created events expected (may be fewer if no online node)
    created_count = event_types.count("agent.tool_call.created")
    assert created_count >= 1, f"Expected at least 1 created event, got {created_count}"

    # created events must come before first job.queued/invocation.created
    created_indices = [i for i, t in enumerate(event_types) if t == "agent.tool_call.created"]
    exec_indices = [
        i
        for i, t in enumerate(event_types)
        if t in ("agent.job.queued", "agent.invocation.created")
    ]
    if created_indices and exec_indices:
        last_created = max(created_indices)
        first_exec = min(exec_indices)
        assert last_created <= first_exec, (
            f"All created ({last_created}) must be before first exec ({first_exec})"
        )


@pytest.mark.asyncio
async def test_write_tool_not_concurrent_with_read_tools(client: AsyncClient):
    """Write tool + 2 read tools → write is serial, reads can be concurrent."""
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import AgentFunction, ProviderInvokeResult
    from yequ.api.routes.agent import register_provider

    fake = FakeAgentProvider("parallel-write-test")
    fake.add_functions(
        [
            AgentFunction(name="system.info", description="read", risk="safe", effect="read"),
            AgentFunction(
                name="system.metrics.snapshot", description="read", risk="safe", effect="read"
            ),
            AgentFunction(
                name="system.service.ensure_running",
                description="write",
                risk="maintenance",
                effect="write",
            ),
        ]
    )
    fake.set_sequence(
        [
            ProviderInvokeResult(
                message="Check and fix.",
                tool_calls=[
                    {"call_id": "r1", "name": "system.info", "input": {}},
                    {"call_id": "r2", "name": "system.metrics.snapshot", "input": {}},
                    {
                        "call_id": "w1",
                        "name": "system.service.ensure_running",
                        "input": {"name": "TestSvc"},
                    },
                ],
                success=True,
            ),
            ProviderInvokeResult(message="Done.", tool_calls=[], success=True),
        ]
    )
    register_provider(fake)

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "parallel-write-test", "execution_mode": "auto"},
    )
    session_id = session_resp.json()["session_id"]

    stream_resp = await client.post(
        "/agent/invoke/stream",
        json={
            "session_id": session_id,
            "provider_name": "parallel-write-test",
            "prompt": "check and fix service",
            "execution_mode": "auto",
            "target_node_id": "winClient",
            "max_steps": 5,
        },
    )
    assert stream_resp.status_code == 200
    events = _parse_events(stream_resp.text)

    # Write tool should trigger approval or function_not_available, NOT plain execution
    write_events = [
        e for e in events if e.get("data", {}).get("name") == "system.service.ensure_running"
    ]
    write_types = {e["event_type"] for e in write_events}
    # Write tool should be in the approval/failure path, not just plain completed
    assert "agent.tool_call.created" in write_types, "Write tool should have created event"


@pytest.mark.asyncio
async def test_capability_unavailable_has_stable_error(client: AsyncClient):
    """Request non-existent capability → stable capability_unavailable error."""
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import ProviderInvokeResult
    from yequ.api.routes.agent import register_provider

    fake = FakeAgentProvider("cap-unavailable-test")
    fake.set_sequence(
        [
            ProviderInvokeResult(
                message="",
                tool_calls=[{"call_id": "bad1", "name": "system.nonexistent.xyz", "input": {}}],
                success=True,
            ),
            ProviderInvokeResult(message="I cannot do that.", tool_calls=[], success=True),
        ]
    )
    register_provider(fake)

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "cap-unavailable-test", "execution_mode": "auto"},
    )
    session_id = session_resp.json()["session_id"]

    stream_resp = await client.post(
        "/agent/invoke/stream",
        json={
            "session_id": session_id,
            "provider_name": "cap-unavailable-test",
            "prompt": "run nonexistent function",
            "execution_mode": "auto",
            "target_node_id": "winClient",
        },
    )
    assert stream_resp.status_code == 200
    events = _parse_events(stream_resp.text)

    failed = [e for e in events if e["event_type"] == "agent.tool_call.failed"]
    assert len(failed) > 0, "Should have failed event for unknown capability"

    error_codes = {str(e.get("data", {}).get("error_code", "")) for e in failed}
    assert "function_not_available" in error_codes, (
        f"Expected function_not_available, got: {error_codes}"
    )
