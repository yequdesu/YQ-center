"""Prompt Matrix -- deterministic FakeProvider tests for CI.

Verifies that the Agent correctly routes prompts to the right Functions.
Uses FakeAgentProvider to return pre-configured tool calls.

KNOWN DESIGN:
- /agent/invoke is a synchronous HTTP endpoint that blocks until the
  invocation finishes (it calls _wait_invocation_terminal internally).
- Therefore the Node operations (poll/accept/finish the job) MUST happen
  CONCURRENTLY with the invoke call.
- We use asyncio.create_task() for the invoke, then yield control so the
  invoke can create the job, then the node does poll/accept/finish,
  then we await the invoke task.
"""

import asyncio
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient

from tests.conftest import make_yqp_envelope


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def provisioned_agent_setup(client: AsyncClient) -> dict:
    """Full provisioning: node + hello + capabilities + agent session.

    Returns all identifiers needed for agent invoke tests.
    """

    node_id = f"node-{_uid()}"
    node_token = f"tok-{_uid()}"
    auth = {"Authorization": f"Bearer {node_token}"}

    # Provision node
    r = await client.post(
        "/admin/nodes",
        json={
            "node_id": node_id,
            "node_name": "Agent Test Node",
            "token": node_token,
        },
    )
    assert r.status_code == 201, f"Provision failed: {r.text}"

    # Hello
    r = await client.post(
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
    assert r.status_code == 200, f"Hello failed: {r.text}"

    # Register capabilities with system.metrics.snapshot
    r = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.register_capabilities",
            node_id,
            payload={
                "plugins": [
                    {
                        "plugin_id": "system.metrics",
                        "plugin_version": "1.0.0",
                        "functions": [
                            {
                                "name": "system.metrics.snapshot",
                                "input_schema": {"type": "object", "properties": {}},
                                "output_schema": {
                                    "type": "object",
                                    "properties": {
                                        "cpu": {"type": "number"},
                                        "memory": {"type": "number"},
                                        "disk": {"type": "number"},
                                    },
                                },
                                "risk": "safe",
                                "effect": "read",
                                "timeout_sec": 5,
                                "idempotency": "idempotent",
                            }
                        ],
                        "signals": [],
                    }
                ]
            },
        ),
        headers=auth,
    )
    assert r.status_code == 200, f"Register caps failed: {r.text}"

    # Create agent session
    r = await client.post(
        "/agent/sessions",
        json={
            "actor_id": "test-agent",
            "execution_mode": "auto",
            "max_total_duration_sec": 60,
        },
    )
    assert r.status_code == 201, f"Create session failed: {r.text}"
    session_id = r.json()["session_id"]

    return {
        "node_id": node_id,
        "node_token": node_token,
        "auth": auth,
        "session_id": session_id,
        "agent_auth": {},
    }


@pytest_asyncio.fixture
async def provisioned_node_metrics_service(client: AsyncClient) -> dict:
    """Provision a node with both metrics.snapshot and service.status."""
    node_id = f"node-{_uid()}"
    node_token = f"tok-{_uid()}"
    auth = {"Authorization": f"Bearer {node_token}"}

    await client.post(
        "/admin/nodes",
        json={
            "node_id": node_id,
            "node_name": "Multi-Cap Node",
            "token": node_token,
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
                        "plugin_id": "system.metrics",
                        "plugin_version": "1.0.0",
                        "functions": [
                            {
                                "name": "system.metrics.snapshot",
                                "input_schema": {"type": "object", "properties": {}},
                                "output_schema": {"type": "object"},
                                "risk": "safe",
                                "effect": "read",
                                "timeout_sec": 5,
                                "idempotency": "idempotent",
                            },
                            {
                                "name": "system.service.status",
                                "input_schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                },
                                "output_schema": {"type": "object"},
                                "risk": "safe",
                                "effect": "read",
                                "timeout_sec": 5,
                                "idempotency": "idempotent",
                            },
                            {
                                "name": "system.processes.list",
                                "input_schema": {"type": "object", "properties": {}},
                                "output_schema": {"type": "object"},
                                "risk": "safe",
                                "effect": "read",
                                "timeout_sec": 5,
                                "idempotency": "idempotent",
                            },
                            {
                                "name": "system.info",
                                "input_schema": {"type": "object", "properties": {}},
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

    # Create agent session
    r = await client.post(
        "/agent/sessions",
        json={
            "actor_id": "pm-test",
            "execution_mode": "auto",
            "max_total_duration_sec": 60,
        },
    )
    assert r.status_code == 201
    session_id = r.json()["session_id"]

    return {
        "node_id": node_id,
        "node_token": node_token,
        "auth": auth,
        "session_id": session_id,
    }


@pytest.mark.asyncio
async def test_prompt_matrix_metrics(client: AsyncClient, provisioned_agent_setup):
    """Prompt: 'get system metrics' -> system.metrics.snapshot"""
    setup = provisioned_agent_setup

    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import AgentResult
    from yequ.api.routes.agent import register_provider

    provider = FakeAgentProvider()
    provider.add_response(
        "metrics",
        AgentResult(
            success=True,
            output={"message": ""},
            function_calls=[
                {"name": "system.metrics.snapshot", "input": {}, "call_id": "call_pm1"}
            ],
        ),
    )
    register_provider(provider)

    # Invoke concurrently so node can poll/accept/finish
    invoke_task = asyncio.create_task(
        client.post(
            "/agent/invoke",
            json={
                "session_id": setup["session_id"],
                "provider_name": "fake",
                "prompt": "get system metrics",
                "execution_mode": "auto",
                "max_total_duration_sec": 30,
            },
        )
    )

    await asyncio.sleep(0.5)

    # Node: poll -> accept -> finish
    r = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.poll",
            setup["node_id"],
            {"capacity": 2},
        ),
        headers=setup["auth"],
    )
    assert r.status_code == 200, f"Poll failed: {r.text}"
    jobs = r.json().get("payload", {}).get("jobs", [])
    assert len(jobs) >= 1, f"No jobs from poll: {r.json()}"
    job_id = jobs[0]["job_id"]

    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.accepted",
            setup["node_id"],
            {"job_id": job_id},
        ),
        headers=setup["auth"],
    )

    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.finished",
            setup["node_id"],
            {
                "job_id": job_id,
                "status": "succeeded",
                "output": {"cpu": 7.2, "memory": 42.8, "disk": 68.58},
            },
        ),
        headers=setup["auth"],
    )

    r = await asyncio.wait_for(invoke_task, timeout=15.0)
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True, f"Expected success=True, got {data}"
    assert len(data["tool_calls"]) >= 1
    assert data["tool_calls"][0]["name"] == "system.metrics.snapshot"
    assert data["tool_calls"][0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_prompt_matrix_service_status(client: AsyncClient, provisioned_node_metrics_service):
    """Prompt: 'check EventLog service' -> system.service.status with name=EventLog"""
    setup = provisioned_node_metrics_service

    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import AgentResult
    from yequ.api.routes.agent import register_provider

    provider = FakeAgentProvider()
    provider.add_response(
        "service",
        AgentResult(
            success=True,
            output={"message": ""},
            function_calls=[
                {
                    "name": "system.service.status",
                    "input": {"name": "EventLog"},
                    "call_id": "call_pm2",
                }
            ],
        ),
    )
    register_provider(provider)

    invoke_task = asyncio.create_task(
        client.post(
            "/agent/invoke",
            json={
                "session_id": setup["session_id"],
                "provider_name": "fake",
                "prompt": "check EventLog service status",
                "execution_mode": "auto",
                "max_total_duration_sec": 30,
            },
        )
    )

    await asyncio.sleep(0.5)

    r = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.poll",
            setup["node_id"],
            {"capacity": 2},
        ),
        headers=setup["auth"],
    )
    assert r.status_code == 200
    jobs = r.json().get("payload", {}).get("jobs", [])
    assert len(jobs) >= 1
    job_id = jobs[0]["job_id"]

    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.accepted",
            setup["node_id"],
            {"job_id": job_id},
        ),
        headers=setup["auth"],
    )

    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.finished",
            setup["node_id"],
            {
                "job_id": job_id,
                "status": "succeeded",
                "output": {"name": "EventLog", "status": "Running", "start_type": "Auto"},
            },
        ),
        headers=setup["auth"],
    )

    r = await asyncio.wait_for(invoke_task, timeout=15.0)
    assert r.status_code == 200
    data = r.json()
    assert len(data["tool_calls"]) >= 1
    assert data["tool_calls"][0]["name"] == "system.service.status"


@pytest.mark.asyncio
async def test_prompt_matrix_processes(client: AsyncClient, provisioned_node_metrics_service):
    """Prompt: 'list processes' -> system.processes.list"""
    setup = provisioned_node_metrics_service

    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import AgentResult
    from yequ.api.routes.agent import register_provider

    provider = FakeAgentProvider()
    provider.add_response(
        "process",
        AgentResult(
            success=True,
            output={"message": ""},
            function_calls=[{"name": "system.processes.list", "input": {}, "call_id": "call_pm3"}],
        ),
    )
    register_provider(provider)

    invoke_task = asyncio.create_task(
        client.post(
            "/agent/invoke",
            json={
                "session_id": setup["session_id"],
                "provider_name": "fake",
                "prompt": "list running processes",
                "execution_mode": "auto",
                "max_total_duration_sec": 30,
            },
        )
    )

    await asyncio.sleep(0.5)

    r = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.poll",
            setup["node_id"],
            {"capacity": 2},
        ),
        headers=setup["auth"],
    )
    assert r.status_code == 200
    jobs = r.json().get("payload", {}).get("jobs", [])
    assert len(jobs) >= 1
    job_id = jobs[0]["job_id"]

    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.accepted",
            setup["node_id"],
            {"job_id": job_id},
        ),
        headers=setup["auth"],
    )

    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.finished",
            setup["node_id"],
            {
                "job_id": job_id,
                "status": "succeeded",
                "output": {"count": 42, "processes": [{"name": "svchost.exe", "pid": 1234}]},
            },
        ),
        headers=setup["auth"],
    )

    r = await asyncio.wait_for(invoke_task, timeout=15.0)
    assert r.status_code == 200
    data = r.json()
    assert len(data["tool_calls"]) >= 1
    assert data["tool_calls"][0]["name"] == "system.processes.list"


@pytest.mark.asyncio
async def test_prompt_matrix_multi_tool(client: AsyncClient, provisioned_node_metrics_service):
    """Prompt: 'full system check' -> multiple tools"""
    setup = provisioned_node_metrics_service

    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import AgentResult
    from yequ.api.routes.agent import register_provider

    provider = FakeAgentProvider()
    provider.add_response(
        "full",
        AgentResult(
            success=True,
            output={"message": ""},
            function_calls=[
                {"name": "system.metrics.snapshot", "input": {}, "call_id": "call_pm4a"},
                {"name": "system.info", "input": {}, "call_id": "call_pm4b"},
            ],
        ),
    )
    register_provider(provider)

    # Multi-tool: create invoke, then handle jobs one at a time.
    # The agent processes tool calls *sequentially* (creating the next
    # job only after the previous one completes), so we must poll,
    # accept, finish each job in order.
    invoke_task = asyncio.create_task(
        client.post(
            "/agent/invoke",
            json={
                "session_id": setup["session_id"],
                "provider_name": "fake",
                "prompt": "full system check",
                "execution_mode": "auto",
                "max_total_duration_sec": 30,
            },
        )
    )

    # Handle first tool call: metrics.snapshot
    await asyncio.sleep(0.5)
    r = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.poll",
            setup["node_id"],
            {"capacity": 2},
        ),
        headers=setup["auth"],
    )
    assert r.status_code == 200
    jobs = r.json().get("payload", {}).get("jobs", [])
    assert len(jobs) >= 1
    job1_id = jobs[0]["job_id"]

    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.accepted",
            setup["node_id"],
            {"job_id": job1_id},
        ),
        headers=setup["auth"],
    )
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.finished",
            setup["node_id"],
            {
                "job_id": job1_id,
                "status": "succeeded",
                "output": {"result": "ok"},
            },
        ),
        headers=setup["auth"],
    )

    # Now poll for the second tool call: system.info
    # The agent may need a moment to create the second job after the first one finishes
    job2_id = None
    for _attempt in range(10):
        await asyncio.sleep(0.3)
        r = await client.post(
            "/yqp/",
            json=make_yqp_envelope(
                "job.poll",
                setup["node_id"],
                {"capacity": 2},
            ),
            headers=setup["auth"],
        )
        jobs2 = r.json().get("payload", {}).get("jobs", [])
        if jobs2:
            job2_id = jobs2[0]["job_id"]
            break

    assert job2_id is not None, "Expected second job after first completed"

    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.accepted",
            setup["node_id"],
            {"job_id": job2_id},
        ),
        headers=setup["auth"],
    )
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.finished",
            setup["node_id"],
            {
                "job_id": job2_id,
                "status": "succeeded",
                "output": {"result": "ok"},
            },
        ),
        headers=setup["auth"],
    )

    r = await asyncio.wait_for(invoke_task, timeout=15.0)
    assert r.status_code == 200
    data = r.json()
    assert len(data["tool_calls"]) >= 2
    names = {tc["name"] for tc in data["tool_calls"]}
    assert "system.metrics.snapshot" in names
    assert "system.info" in names


@pytest.mark.asyncio
async def test_prompt_matrix_unknown_function_rejected(
    client: AsyncClient, provisioned_agent_setup
):
    """Unknown function name -> function_not_available (L1 gate: not in known_functions)."""
    setup = provisioned_agent_setup

    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import AgentResult
    from yequ.api.routes.agent import register_provider

    provider = FakeAgentProvider()
    provider.add_response(
        "reboot",
        AgentResult(
            success=True,
            output={"message": ""},
            function_calls=[{"name": "system.reboot", "input": {}, "call_id": "call_pm5"}],
        ),
    )
    register_provider(provider)

    # system.reboot is not in the registered fake tool set, so it should be rejected
    # at the L1 gate (function_not_available). The invoke should return quickly
    # without creating any jobs, so we can call it synchronously.
    r = await client.post(
        "/agent/invoke",
        json={
            "session_id": setup["session_id"],
            "provider_name": "fake",
            "prompt": "reboot system",
            "execution_mode": "auto",
            "max_total_duration_sec": 10,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data["tool_calls"]) >= 1
    tc = data["tool_calls"][0]
    assert tc["status"] == "failed"
    assert tc["error"]["code"] == "function_not_available"
