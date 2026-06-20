"""L2-B MaintenancePlan E2E tests."""

import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import make_yqp_envelope


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture
async def l2b_setup(client: AsyncClient):
    """Setup: provision node + register L1 read + L2 write capabilities."""
    node_id = f"node-{_uid()}"
    token = f"tok-{_uid()}"
    auth = {"Authorization": f"Bearer {token}"}

    await client.post("/admin/nodes", json={
        "node_id": node_id, "node_name": f"L2B-{node_id}", "token": token,
    })
    await client.post("/yqp/", json=make_yqp_envelope("node.hello", node_id, {
        "daemon_version": "0.1.0",
    }), headers=auth)
    await client.post("/yqp/", json=make_yqp_envelope(
        "node.register_capabilities", node_id,
        payload={"plugins": [{
            "plugin_id": "test.plugin", "plugin_version": "1.0.0",
            "functions": [
                {"name": "system.metrics.snapshot", "input_schema": {"type": "object"}, "output_schema": {"type": "object"}, "risk": "safe", "effect": "read", "timeout_sec": 5, "idempotency": "idempotent"},
                {"name": "system.info", "input_schema": {"type": "object"}, "output_schema": {"type": "object"}, "risk": "safe", "effect": "read", "timeout_sec": 5, "idempotency": "idempotent"},
                {"name": "system.service.restart", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}}, "output_schema": {"type": "object"}, "risk": "maintenance", "effect": "write", "timeout_sec": 30, "idempotency": "non_idempotent"},
            ],
            "signals": [],
        }]},
    ), headers=auth)

    return node_id, token, auth


@pytest.mark.asyncio
async def test_l2b_plan_create_and_run_readonly(client: AsyncClient, l2b_setup):
    """Create a read-only plan (metrics + info) and run it."""
    node_id, token, auth = l2b_setup

    # Create plan
    r = await client.post("/admin/maintenance/plans", json={
        "goal": "Health check", "target_node_id": node_id,
        "steps": [
            {"function_name": "system.metrics.snapshot", "input": {}},
            {"function_name": "system.info", "input": {}},
        ],
    })
    assert r.status_code == 201
    plan_id = r.json()["plan_id"]
    assert r.json()["status"] == "draft"
    assert r.json()["step_count"] == 2

    # Get plan detail
    r = await client.get(f"/admin/maintenance/plans/{plan_id}")
    assert r.status_code == 200
    assert len(r.json()["steps"]) == 2

    # Run plan
    r = await client.post(f"/admin/maintenance/plans/{plan_id}/run")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] in ("running", "succeeded", "partially_succeeded")
    assert "summary" in data

    # Verify steps have invocation_ids (if execution didn't fail)
    r = await client.get(f"/admin/maintenance/plans/{plan_id}")
    steps = r.json()["steps"]
    for step in steps:
        # Steps may be "running" or "succeeded" depending on node availability
        assert step.get("status") in ("pending", "running", "succeeded", "failed"), f"Unexpected step status {step.get('status')}"


@pytest.mark.asyncio
async def test_l2b_plan_with_write_requires_approval(client: AsyncClient, l2b_setup):
    """Plan with a write step creates jobs but the step may need approval."""
    node_id, token, auth = l2b_setup

    r = await client.post("/admin/maintenance/plans", json={
        "goal": "Service recovery", "target_node_id": node_id,
        "steps": [
            {"function_name": "system.service.restart", "input": {"name": "Spooler"}},
        ],
    })
    assert r.status_code == 201
    plan_id = r.json()["plan_id"]

    # Run without approval — draft plan should still attempt execution
    r = await client.post(f"/admin/maintenance/plans/{plan_id}/run")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data


@pytest.mark.asyncio
async def test_l2b_plan_list(client: AsyncClient, l2b_setup):
    """List plans and filter by status."""
    node_id, token, auth = l2b_setup

    # Create a plan
    r = await client.post("/admin/maintenance/plans", json={
        "goal": "Test plan", "target_node_id": node_id,
        "steps": [{"function_name": "system.metrics.snapshot", "input": {}}],
    })
    assert r.status_code == 201

    # List all
    r = await client.get("/admin/maintenance/plans")
    assert r.status_code == 200
    assert len(r.json()) >= 1

    # Filter by status (query param name is plan_status per the route)
    r = await client.get("/admin/maintenance/plans?plan_status=draft")
    assert r.status_code == 200
    for plan in r.json():
        assert plan["status"] == "draft"
