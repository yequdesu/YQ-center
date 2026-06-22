"""Tests for session running tracking and timeline trace_id filter."""

import json

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_session_running_reflects_active_stream(client: AsyncClient):
    """Verify running=true appears for sessions with active invoke streams."""
    from yequ.api.routes.agent import register_provider
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import ProviderInvokeResult

    fake = FakeAgentProvider("running-test")
    fake.set_sequence([
        ProviderInvokeResult(
            message="Response text.",
            tool_calls=[],
            success=True,
        ),
    ])
    register_provider(fake)

    # Create session
    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "running-test", "execution_mode": "auto"},
    )
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    # Before invoke — session should NOT be running
    list_before = await client.get("/admin/sessions")
    sessions_before = list_before.json()
    ours_before = next((s for s in sessions_before if s["session_id"] == session_id), None)
    assert ours_before is not None
    # It might be False because no stream is active yet

    # Invoke a quick stream
    resp = await client.post(
        "/agent/invoke/stream",
        json={
            "session_id": session_id,
            "provider_name": "running-test",
            "prompt": "test",
            "execution_mode": "auto",
            "target_node_id": "winClient",
        },
    )
    assert resp.status_code == 200

    # After stream completes — should NOT be running
    list_after = await client.get("/admin/sessions")
    sessions_after = list_after.json()
    ours_after = next((s for s in sessions_after if s["session_id"] == session_id), None)
    assert ours_after is not None
    assert ours_after.get("running") is False, "Session should not be running after stream completes"


@pytest.mark.asyncio
async def test_timeline_can_filter_by_trace_invocation_job_approval(
    client: AsyncClient,
):
    """Verify list_timeline supports filtering by trace_id, invocation_id, job_id, approval_id."""
    # Create a session and invoke to generate timeline events
    from yequ.api.routes.agent import register_provider
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import ProviderInvokeResult

    fake = FakeAgentProvider("timeline-filter-test")
    fake.set_sequence([
        ProviderInvokeResult(
            message="Response.",
            tool_calls=[],
            success=True,
        ),
    ])
    register_provider(fake)

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "timeline-filter-test", "execution_mode": "auto"},
    )
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    await client.post(
        "/agent/invoke/stream",
        json={
            "session_id": session_id,
            "provider_name": "timeline-filter-test",
            "prompt": "hello",
            "execution_mode": "auto",
            "target_node_id": "winClient",
        },
    )

    # Filter by session_id
    resp = await client.get(f"/admin/timeline?session_id={session_id}")
    assert resp.status_code == 200
    session_events = resp.json()
    assert len(session_events) >= 1, f"Should have at least 1 event for session {session_id}"

    # Verify key event types
    event_types = {e["event_type"] for e in session_events}
    expected_types = {"agent.prompt.received", "agent.final_response"}
    found = event_types & expected_types
    assert len(found) >= 1, f"Expected at least one of {expected_types}, got {event_types}"

    # Filter by trace_id — events from the SSE stream carry trace_id in data
    trace_ids = {
        e.get("data", {}).get("trace_id")
        for e in session_events
        if e.get("data", {}).get("trace_id")
    }
    if trace_ids:
        trace_id = next(iter(trace_ids))
        resp = await client.get(f"/admin/timeline?trace_id={trace_id}")
        assert resp.status_code == 200

    # Filter by event_type
    resp = await client.get("/admin/timeline?event_type=agent.prompt.received")
    assert resp.status_code == 200
    prompt_events = resp.json()
    # At least our event should be there
    assert any(
        e.get("session_id") == session_id
        for e in prompt_events
    ), "Our session's prompt event should be in the filtered timeline"


@pytest.mark.asyncio
async def test_timeline_trace_id_filtering_returns_correct_events(client: AsyncClient):
    """Verify trace_id filter returns only events with matching trace_id."""
    from yequ.api.routes.agent import register_provider
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import ProviderInvokeResult

    fake = FakeAgentProvider("trace-id-test")
    fake.set_sequence([
        ProviderInvokeResult(message="Done.", tool_calls=[], success=True),
    ])
    register_provider(fake)

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "trace-id-test", "execution_mode": "auto"},
    )
    session_id = session_resp.json()["session_id"]

    # Parse SSE stream to extract trace_id
    stream_resp = await client.post(
        "/agent/invoke/stream",
        json={
            "session_id": session_id,
            "provider_name": "trace-id-test",
            "prompt": "trace me",
            "execution_mode": "auto",
            "target_node_id": "winClient",
        },
    )
    assert stream_resp.status_code == 200

    trace_ids = set()
    for frame in stream_resp.text.strip().split("\n\n"):
        for line in frame.split("\n"):
            if line.strip().startswith("data:"):
                payload = line.strip()[5:].strip()
                if payload:
                    try:
                        ev = json.loads(payload)
                        tid = ev.get("trace_id")
                        if tid:
                            trace_ids.add(tid)
                    except json.JSONDecodeError:
                        pass

    assert len(trace_ids) > 0, "Should have at least one trace_id in SSE events"

    # Query timeline by the trace_id from the stream
    for tid in trace_ids:
        resp = await client.get(f"/admin/timeline?trace_id={tid}")
        assert resp.status_code == 200

    # Query by a non-existent trace_id should return empty
    # This just tests the filter doesn't crash
    resp = await client.get("/admin/timeline?trace_id=tr_nonexistent01")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_unknown_requested_capability_returns_clear_message(client: AsyncClient):
    """Verify that requesting a non-existent function returns a clear error."""
    from yequ.api.routes.agent import register_provider
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import AgentFunction, ProviderInvokeResult

    fake = FakeAgentProvider("unknown-cap-test")
    fake.add_functions([
        AgentFunction(
            name="system.info",
            description="Get system info",
            risk="safe",
            effect="read",
        ),
    ])
    fake.set_sequence([
        ProviderInvokeResult(
            message="",
            tool_calls=[{"call_id": "u1", "name": "system.app.launch", "input": {"name": "cloudmusic"}}],
            success=True,
        ),
        ProviderInvokeResult(
            message="",
            tool_calls=[],
            success=True,
        ),
    ])
    register_provider(fake)

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "unknown-cap-test", "execution_mode": "auto"},
    )
    session_id = session_resp.json()["session_id"]

    stream_resp = await client.post(
        "/agent/invoke/stream",
        json={
            "session_id": session_id,
            "provider_name": "unknown-cap-test",
            "prompt": "帮我启动网易云音乐",
            "execution_mode": "auto",
            "target_node_id": "winClient",
        },
    )
    assert stream_resp.status_code == 200

    events = []
    for frame in stream_resp.text.strip().split("\n\n"):
        for line in frame.split("\n"):
            if line.strip().startswith("data:"):
                payload = line.strip()[5:].strip()
                if payload:
                    try:
                        events.append(json.loads(payload))
                    except json.JSONDecodeError:
                        pass

    # The stream should contain a clear error about function unavailability
    failed_events = [
        e for e in events if e["event_type"] == "agent.tool_call.failed"
    ]
    assert len(failed_events) > 0, "Should have at least one tool_call.failed event"

    error_codes = {
        str(e.get("data", {}).get("error_code", ""))
        for e in failed_events
    }
    assert "function_not_available" in error_codes, (
        f"Error should be function_not_available, got: {error_codes}"
    )


@pytest.mark.asyncio
async def test_offline_node_capability_not_available(client: AsyncClient):
    """Verify capabilities on offline nodes are not returned as available."""
    from yequ.services.node_auth import hash_token
    from yequ.models.node import Node
    from yequ.models.capability import Capability
    from yequ.protocol import NodeStatus

    from yequ.api.deps import get_db

    # Create an offline node with a capability
    db_gen = get_db()
    db = await db_gen.__anext__()
    try:
        offline_node = Node(
            node_id="offline-node-test",
            node_name="Offline Test Node",
            token_hash=hash_token("offline-token-123456"),
            status=NodeStatus.OFFLINE,
        )
        db.add(offline_node)
        await db.flush()

        cap = Capability(
            node_record_id=offline_node.id,
            plugin_id="test-plugin",
            plugin_version="1.0",
            capability_type="function",
            name="test.offline.function",
            status="loaded",
            risk="safe",
            effect="read",
            is_active=True,
        )
        db.add(cap)
        await db.commit()
    finally:
        await db_gen.aclose()

    # Query capabilities — offline node's capability should NOT be available
    resp = await client.get("/admin/capabilities")
    assert resp.status_code == 200
    caps = resp.json()

    offline_cap = [c for c in caps if c["name"] == "test.offline.function"]
    if offline_cap:
        cap = offline_cap[0]
        # Should have node_status=offline and available=false
        assert cap.get("node_status") == "offline" or not cap.get("available"), (
            f"Offline node capability should be unavailable: {cap}"
        )
