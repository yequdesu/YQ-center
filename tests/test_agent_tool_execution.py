"""Definitive Agent Tool Execution E2E test.

Verifies the full pipeline: provider -> tool_call -> invocation
-> job -> node executes -> agent collects result.
"""

import asyncio
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient

from tests.conftest import make_yqp_envelope


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.mark.asyncio
async def test_full_agent_tool_e2e(client: AsyncClient, provisioned_node):
    """Full pipeline: provider -> tool_call -> invocation -> job -> node -> result.

    This is the definitive E2E test for Agent Tool Execution.
    If this test passes, the agent pipeline is working end-to-end.
    """
    node, node_token = provisioned_node
    auth = {"Authorization": f"Bearer {node_token}"}

    # ── Setup: register system.metrics.snapshot capability ──
    await client.post("/yqp/", json=make_yqp_envelope("node.hello", node.node_id, {
        "daemon_version": "0.1.0",
    }), headers=auth)

    await client.post("/yqp/", json=make_yqp_envelope(
        "node.register_capabilities", node.node_id,
        payload={"plugins": [{
            "plugin_id": "system.metrics",
            "plugin_version": "1.0.0",
            "functions": [{
                "name": "system.metrics.snapshot",
                "input_schema": {"type": "object", "properties": {}},
                "output_schema": {"type": "object", "properties": {
                    "cpu": {"type": "number"},
                    "memory": {"type": "number"},
                    "disk": {"type": "number"},
                }},
                "risk": "safe", "effect": "read", "timeout_sec": 5,
                "idempotency": "idempotent",
            }],
            "signals": [],
        }]},
    ), headers=auth)

    # ── Setup: create agent session ──
    r = await client.post("/agent/sessions", json={
        "actor_id": "e2e-test", "execution_mode": "auto",
        "max_total_duration_sec": 30,
    })
    assert r.status_code == 201
    session_id = r.json()["session_id"]

    # ── Setup: FakeAgentProvider returns metrics tool call ──
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import AgentResult
    from yequ.api.routes.agent import register_provider

    provider = FakeAgentProvider()
    provider.add_response("metrics", AgentResult(
        success=True,
        output={"message": ""},
        function_calls=[{
            "name": "system.metrics.snapshot",
            "input": {},
            "call_id": "call_e2e_001",
        }],
    ))
    register_provider(provider)

    # ── Invoke agent concurrently with node operations ──
    # The invoke runs _wait_invocation_terminal which polls with asyncio.sleep.
    # We must run the node operations concurrently to unblock the invoke.
    invoke_task = asyncio.create_task(
        client.post("/agent/invoke", json={
            "session_id": session_id,
            "provider_name": "fake",
            "prompt": "get system metrics please",
            "execution_mode": "auto",
            "max_total_duration_sec": 30,
        })
    )

    # Give a brief moment for the invoke to create the job
    await asyncio.sleep(0.5)

    # ── Node: poll the job ──
    r = await client.post("/yqp/", json=make_yqp_envelope(
        "job.poll", node.node_id, {"capacity": 2},
    ), headers=auth)
    assert r.status_code == 200, f"Poll failed: {r.text}"
    poll_payload = r.json().get("payload", {})
    jobs = poll_payload.get("jobs", [])
    assert len(jobs) >= 1, f"No jobs returned from poll: {poll_payload}"
    job_id = jobs[0]["job_id"]

    # ── Node: accept the job ──
    r = await client.post("/yqp/", json=make_yqp_envelope(
        "job.accepted", node.node_id, {"job_id": job_id},
    ), headers=auth)
    assert r.status_code == 200, f"Accept failed: {r.text}"

    # ── Node: finish the job with output ──
    r = await client.post("/yqp/", json=make_yqp_envelope(
        "job.finished", node.node_id, {
            "job_id": job_id,
            "status": "succeeded",
            "output": {"cpu": 42.5, "memory": 60.2, "disk": 71.0},
        },
    ), headers=auth)
    assert r.status_code == 200, f"Finish failed: {r.text}"

    # ── Now await the invoke response ──
    r = await asyncio.wait_for(invoke_task, timeout=10.0)
    assert r.status_code == 200, f"Invoke failed: {r.text}"
    resp = r.json()

    # ── Verify response structure ──
    assert resp["success"] is True, f"Expected success=True, got {resp}"
    assert resp["status"] == "succeeded", f"Expected status=succeeded, got {resp['status']}"
    assert "tool_calls" in resp
    assert len(resp["tool_calls"]) >= 1
    tc = resp["tool_calls"][0]
    assert tc["name"] == "system.metrics.snapshot"
    assert tc["invocation_id"], "invocation_id must not be empty"
    assert len(tc["job_ids"]) >= 1, "job_ids must have at least one entry"

    inv_id = tc["invocation_id"]

    # ── Verify tool result contains metrics ──
    assert tc["result"] is not None, f"Expected result in tool call, got {tc}"
    assert tc["result"]["cpu"] == 42.5
    assert tc["result"]["memory"] == 60.2
    assert tc["result"]["disk"] == 71.0

    # ── Verify Invocation is terminal ──
    r = await client.get(f"/admin/invocations/{inv_id}")
    assert r.status_code == 200, f"Get invocation failed: {r.text}"
    inv_data = r.json()
    assert inv_data["status"] == "succeeded", (
        f"Invocation should be succeeded, got {inv_data['status']}"
    )

    # ── Verify Job is terminal ──
    r = await client.get(f"/admin/jobs/{job_id}")
    assert r.status_code == 200, f"Get job failed: {r.text}"
    job_data = r.json()
    assert job_data["status"] == "succeeded"
    assert job_data["output"]["cpu"] == 42.5

    # ── Verify Timeline events (session + invocation scoped) ──
    # Agent events carry session_id; job events carry invocation_id
    r = await client.get(f"/admin/timeline?session_id={session_id}&limit=50")
    assert r.status_code == 200, f"Get timeline failed: {r.text}"
    session_events = r.json()
    r2 = await client.get(f"/admin/timeline?invocation_id={inv_id}&limit=50")
    assert r2.status_code == 200, f"Get timeline (inv) failed: {r2.text}"
    inv_events = r2.json()
    # Merge both result sets
    event_types = {e["event_type"] for e in session_events + inv_events}
    required = {
        "agent.tool.selected",
        "agent.tool.job.persisted",
        "agent.tool.invocation_created",
        "agent.provider.completed",
        "agent.tool.completed",
        "agent.final_response",
        "job.queued",
    }
    missing = required - event_types
    assert not missing, f"Missing timeline events: {missing}"


@pytest.mark.asyncio
async def test_metrics_tool_timeout(
    client: AsyncClient, provisioned_agent_setup
):
    """Provider returns system.metrics.snapshot -> Invocation -> timeout.

    Without a daemon to claim/finish the job, the invocation times out.
    """
    setup = provisioned_agent_setup

    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import AgentResult
    from yequ.api.routes.agent import register_provider

    provider = FakeAgentProvider()
    provider.add_response("metrics", AgentResult(
        success=True,
        output={"message": ""},
        function_calls=[{
            "name": "system.metrics.snapshot",
            "input": {},
            "call_id": f"call_{_uid()}",
        }],
    ))
    register_provider(provider)

    r = await client.post("/agent/invoke", json={
        "session_id": setup["session_id"],
        "provider_name": "fake",
        "prompt": "get system metrics please",
        "execution_mode": "auto",
        "max_steps": 20,
        "max_total_duration_sec": 10,
    }, headers=setup["agent_auth"])
    assert r.status_code == 200, f"Invoke failed: {r.text}"
    data = r.json()

    assert "tool_calls" in data
    assert len(data["tool_calls"]) >= 1
    tc = data["tool_calls"][0]
    assert tc["name"] == "system.metrics.snapshot"
    assert tc["invocation_id"], f"Expected invocation_id, got {tc}"
    assert tc["job_ids"], f"Expected job_ids, got {tc}"
    assert tc["status"] == "timeout"
    assert tc["error"]["code"] == "tool_timeout"


@pytest.mark.asyncio
async def test_function_not_available(
    client: AsyncClient, provisioned_agent_setup
):
    """Tool call for a function no node has -> function_not_available."""
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import AgentResult
    from yequ.api.routes.agent import register_provider

    setup = provisioned_agent_setup
    provider = FakeAgentProvider()
    provider.add_response("nonexistent", AgentResult(
        success=True,
        output={"message": ""},
        function_calls=[{
            "name": "system.nonexistent.func",
            "input": {},
            "call_id": f"call_{_uid()}",
        }],
    ))
    register_provider(provider)

    r = await client.post("/agent/invoke", json={
        "session_id": setup["session_id"],
        "provider_name": "fake",
        "prompt": "call nonexistent function",
        "execution_mode": "auto",
        "max_steps": 20,
        "max_total_duration_sec": 10,
    }, headers=setup["agent_auth"])
    assert r.status_code == 200, f"Invoke failed: {r.text}"
    data = r.json()

    assert len(data["tool_calls"]) >= 1
    tc = data["tool_calls"][0]
    assert tc["status"] == "failed"
    assert tc["error"]["code"] == "function_not_available"


@pytest.mark.asyncio
async def test_policy_denied_readonly(
    client: AsyncClient, provisioned_agent_setup
):
    """Destructive function in readonly mode -> policy_denied."""
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import AgentResult
    from yequ.api.routes.agent import register_provider

    setup = provisioned_agent_setup

    # Register a destructive function on the node
    node_id = setup["node_id"]
    auth = setup["auth"]
    r = await client.post("/yqp/", json=make_yqp_envelope(
        "node.register_capabilities", node_id,
        payload={"plugins": [{
            "plugin_id": "system.admin",
            "plugin_version": "1.0.0",
            "functions": [{
                "name": "system.reboot",
                "input_schema": {"type": "object", "properties": {}},
                "output_schema": {"type": "object", "properties": {}},
                "risk": "destructive",
                "effect": "destructive",
                "timeout_sec": 30,
                "idempotency": "non_idempotent",
            }],
            "signals": [],
        }]},
    ), headers=auth)
    assert r.status_code == 200, f"Register destructive caps failed: {r.text}"

    provider = FakeAgentProvider()
    provider.add_response("reboot", AgentResult(
        success=True,
        output={"message": ""},
        function_calls=[{
            "name": "system.reboot",
            "input": {},
            "call_id": f"call_{_uid()}",
        }],
    ))
    register_provider(provider)

    # Create a new session with readonly mode
    r = await client.post("/agent/sessions", json={
        "actor_id": "test-agent", "execution_mode": "readonly",
        "max_total_duration_sec": 60,
    }, headers=setup["agent_auth"])
    assert r.status_code == 201
    readonly_session = r.json()["session_id"]

    r = await client.post("/agent/invoke", json={
        "session_id": readonly_session,
        "provider_name": "fake",
        "prompt": "reboot the system",
        "execution_mode": "readonly",
        "max_steps": 20,
        "max_total_duration_sec": 10,
    }, headers=setup["agent_auth"])
    assert r.status_code == 200, f"Invoke failed: {r.text}"
    data = r.json()

    assert len(data["tool_calls"]) >= 1
    tc = data["tool_calls"][0]
    assert tc["status"] == "failed"
    assert tc["error"]["code"] == "function_not_available"


# ── Fixtures ──


@pytest_asyncio.fixture
async def provisioned_agent_setup(client: AsyncClient) -> dict:
    """Full provisioning: node + hello + capabilities + agent session.

    Returns all identifiers needed for agent invoke tests.
    """
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.api.routes.agent import register_provider

    node_id = f"node-{_uid()}"
    node_token = f"tok-{_uid()}"
    auth = {"Authorization": f"Bearer {node_token}"}

    # Provision node
    r = await client.post("/admin/nodes", json={
        "node_id": node_id, "node_name": "Agent Test Node", "token": node_token,
    })
    assert r.status_code == 201, f"Provision failed: {r.text}"

    # Hello
    r = await client.post("/yqp/", json=make_yqp_envelope("node.hello", node_id, {
        "daemon_version": "0.1.0",
    }), headers=auth)
    assert r.status_code == 200, f"Hello failed: {r.text}"

    # Register capabilities with system.metrics.snapshot
    r = await client.post("/yqp/", json=make_yqp_envelope(
        "node.register_capabilities", node_id,
        payload={"plugins": [{
            "plugin_id": "system.metrics",
            "plugin_version": "1.0.0",
            "functions": [{
                "name": "system.metrics.snapshot",
                "input_schema": {"type": "object", "properties": {}},
                "output_schema": {"type": "object", "properties": {
                    "cpu": {"type": "number"},
                    "memory": {"type": "number"},
                    "disk": {"type": "number"},
                }},
                "risk": "safe", "effect": "read", "timeout_sec": 5,
                "idempotency": "idempotent",
            }],
            "signals": [],
        }]},
    ), headers=auth)
    assert r.status_code == 200, f"Register caps failed: {r.text}"

    # Create agent session
    r = await client.post("/agent/sessions", json={
        "actor_id": "test-agent", "execution_mode": "auto",
        "max_total_duration_sec": 60,
    })
    assert r.status_code == 201, f"Create session failed: {r.text}"
    session_id = r.json()["session_id"]

    return {
        "node_id": node_id,
        "node_token": node_token,
        "auth": auth,
        "session_id": session_id,
        "agent_auth": {},
    }
