"""Tests for structured Agent Planner IR generation."""

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient

from tests.conftest import make_yqp_envelope


def _uid():
    return uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def _register_fake_provider():
    """Register FakeAgentProvider for plan endpoint so it doesn't 404."""
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import AgentResult
    from tests.fakes.agent_functions import default_agent_functions
    from yequ.api.routes.agent import register_provider

    # Clear any existing provider and create fresh
    provider = FakeAgentProvider(provider_name="fake")
    for func in default_agent_functions():
        provider.add_function(func)
    # Default is readonly_check
    provider.set_default_result(
        AgentResult(success=True, output={"message": "readonly_check"}),
    )
    register_provider(provider)


@pytest_asyncio.fixture
async def plan_setup(client: AsyncClient):
    node_id = f"node-{_uid()}"
    token = f"tok-{_uid()}"
    auth = {"Authorization": f"Bearer {token}"}
    await client.post(
        "/admin/nodes",
        json={
            "node_id": node_id,
            "node_name": f"IR-{node_id}",
            "token": token,
        },
    )
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.hello",
            node_id,
            {"daemon_version": "0.1.0"},
        ),
        headers=auth,
    )
    funcs = [
        {
            "name": "system.service.status",
            "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}},
            "output_schema": {"type": "object"},
            "risk": "safe",
            "effect": "read",
            "timeout_sec": 5,
            "idempotency": "idempotent",
        },
        {
            "name": "system.service.ensure_running",
            "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}},
            "output_schema": {"type": "object"},
            "risk": "maintenance",
            "effect": "write",
            "timeout_sec": 30,
            "idempotency": "non_idempotent",
        },
    ]
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.register_capabilities",
            node_id,
            payload={
                "plugins": [
                    {
                        "plugin_id": "test",
                        "plugin_version": "1.0",
                        "functions": funcs,
                        "signals": [],
                    }
                ]
            },
        ),
        headers=auth,
    )
    return node_id, token, auth


@pytest.mark.asyncio
async def test_readonly_check_returns_ready(client: AsyncClient, plan_setup):
    """Read-only prompt should return plan with status=ready, no approval."""
    node_id, token, auth = plan_setup
    r = await client.post(
        "/agent/plan",
        json={
            "session_id": f"sess_{_uid()}",
            "provider_name": "fake",
            "prompt": "check print spooler service status",
            "target_node_id": node_id,
            "execution_mode": "auto",
        },
    )
    assert r.status_code == 201 or r.status_code == 200, (
        f"Status: {r.status_code}, body: {r.text[:500]}"
    )
    data = r.json()
    assert data["status"] in ("draft", "ready"), f"Expected draft/ready, got {data['status']}"
    assert data["approval_required"] is False


@pytest.mark.asyncio
async def test_check_and_fix_returns_waiting_approval(client: AsyncClient, plan_setup):
    """Check+fix prompt should return check+repair+verify, status=waiting_approval."""
    # Override provider to return check_and_fix for this test
    from yequ.agent.provider import AgentResult
    from yequ.api.routes.agent import get_provider

    prov = get_provider("fake")
    prov.set_default_result(AgentResult(success=True, output={"message": "check_and_fix"}))

    node_id, token, auth = plan_setup
    r = await client.post(
        "/agent/plan",
        json={
            "session_id": f"sess_{_uid()}",
            "provider_name": "fake",
            "prompt": "check print spooler and fix if unhealthy",
            "target_node_id": node_id,
            "execution_mode": "auto",
        },
    )
    assert r.status_code == 201 or r.status_code == 200
    data = r.json()
    assert data["approval_required"] is True
    assert data["status"] == "waiting_approval"
    assert len(data["steps"]) >= 2, f"Expected >=2 steps, got {len(data['steps'])}"
    kinds = [s["kind"] for s in data["steps"]]
    assert "check" in kinds
    assert "repair" in kinds
    repair = [s for s in data["steps"] if s["kind"] == "repair"][0]
    assert repair["requires_approval"] is True
    assert repair["condition"] in ("if_previous_unhealthy",)


@pytest.mark.asyncio
async def test_chinese_check_and_fix(client: AsyncClient, plan_setup):
    """Chinese prompt: 检查打印服务，如果不正常就修复"""
    from yequ.agent.provider import AgentResult
    from yequ.api.routes.agent import get_provider

    prov = get_provider("fake")
    prov.set_default_result(AgentResult(success=True, output={"message": "check_and_fix"}))

    node_id, token, auth = plan_setup
    r = await client.post(
        "/agent/plan",
        json={
            "session_id": f"sess_{_uid()}",
            "provider_name": "fake",
            "prompt": "检查打印服务，如果不正常就修复",
            "target_node_id": node_id,
            "execution_mode": "auto",
        },
    )
    assert r.status_code == 201 or r.status_code == 200
    data = r.json()
    assert data["approval_required"] is True
    assert data["status"] == "waiting_approval"
    kinds = [s["kind"] for s in data["steps"]]
    assert "check" in kinds and "repair" in kinds
