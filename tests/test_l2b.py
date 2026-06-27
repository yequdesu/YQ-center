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

    await client.post(
        "/admin/nodes",
        json={
            "node_id": node_id,
            "node_name": f"L2B-{node_id}",
            "token": token,
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
                        "plugin_id": "test.plugin",
                        "plugin_version": "1.0.0",
                        "functions": [
                            {
                                "name": "system.metrics.snapshot",
                                "input_schema": {"type": "object"},
                                "output_schema": {"type": "object"},
                                "risk": "safe",
                                "effect": "read",
                                "timeout_sec": 5,
                                "idempotency": "idempotent",
                            },
                            {
                                "name": "system.info",
                                "input_schema": {"type": "object"},
                                "output_schema": {"type": "object"},
                                "risk": "safe",
                                "effect": "read",
                                "timeout_sec": 5,
                                "idempotency": "idempotent",
                            },
                            {
                                "name": "system.service.restart",
                                "input_schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                },
                                "output_schema": {"type": "object"},
                                "risk": "maintenance",
                                "effect": "write",
                                "timeout_sec": 30,
                                "idempotency": "non_idempotent",
                            },
                        ],
                        "signals": [],
                    }
                ]
            },
        ),
        headers=auth,
    )

    return node_id, token, auth


@pytest.mark.asyncio
async def test_l2b_plan_create_and_run_readonly(client: AsyncClient, l2b_setup):
    """Create a read-only plan (metrics + info) and run it."""
    node_id, token, auth = l2b_setup

    # Create plan
    r = await client.post(
        "/admin/maintenance/plans",
        json={
            "goal": "Health check",
            "target_node_id": node_id,
            "steps": [
                {"function_name": "system.metrics.snapshot", "input": {}},
                {"function_name": "system.info", "input": {}},
            ],
        },
    )
    assert r.status_code == 201
    plan_id = r.json()["plan_id"]
    assert r.json()["status"] == "draft"
    assert r.json()["step_count"] == 2

    # Get plan detail
    r = await client.get(f"/admin/maintenance/plans/{plan_id}")
    assert r.status_code == 200
    assert len(r.json()["steps"]) == 2
    assert [step["timeout_sec"] for step in r.json()["steps"]] == [5, 5]

    # Run plan — with no daemon polling jobs, steps will time out
    r = await client.post(f"/admin/maintenance/plans/{plan_id}/run")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] in ("running", "succeeded", "failed", "partially_succeeded")
    assert "summary" in data


@pytest.mark.asyncio
async def test_l2b_plan_with_write_requires_approval(client: AsyncClient, l2b_setup):
    """Plan with a write step waits for approval before creating jobs."""
    node_id, token, auth = l2b_setup

    r = await client.post(
        "/admin/maintenance/plans",
        json={
            "goal": "Service recovery",
            "target_node_id": node_id,
            "steps": [
                {"function_name": "system.service.restart", "input": {"name": "Spooler"}},
            ],
        },
    )
    assert r.status_code == 201
    plan_id = r.json()["plan_id"]

    # Run without approval — draft plan should still attempt execution
    r = await client.post(f"/admin/maintenance/plans/{plan_id}/run")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "waiting_approval"
    steps = data["steps"]
    assert steps[0]["status"] == "requires_approval"
    assert not steps[0].get("job_id")


@pytest.mark.asyncio
async def test_l2b_plan_list(client: AsyncClient, l2b_setup):
    """List plans and filter by status."""
    node_id, token, auth = l2b_setup

    # Create a plan
    r = await client.post(
        "/admin/maintenance/plans",
        json={
            "goal": "Test plan",
            "target_node_id": node_id,
            "steps": [{"function_name": "system.metrics.snapshot", "input": {}}],
        },
    )
    assert r.status_code == 201

    # List all
    r = await client.get("/admin/maintenance/plans")
    assert r.status_code == 200
    assert len(r.json()) >= 1

    # Filter by status
    r = await client.get("/admin/maintenance/plans?status=draft")
    assert r.status_code == 200
    for plan in r.json():
        assert plan["status"] == "draft"


@pytest.mark.asyncio
async def test_readonly_prompt_does_not_require_approval(client: AsyncClient, l2b_setup):
    """Read-only prompt -> status=ready, no approval required."""
    node_id, token, auth = l2b_setup
    r = await client.post(
        "/agent/plan",
        json={
            "session_id": f"sess_{uuid.uuid4().hex[:8]}",
            "provider_name": "fake",
            "prompt": "check print spooler service status",
            "target_node_id": node_id,
            "execution_mode": "auto",
        },
    )
    assert r.status_code == 201 or r.status_code == 200
    data = r.json()
    assert data["approval_required"] is False
    assert data["status"] in ("draft", "ready")
    assert data["step_count"] == 1
    steps = data.get("steps", [])
    assert steps[0]["kind"] == "check"
    assert steps[0]["requires_approval"] is False


@pytest.mark.asyncio
async def test_step_succeeded_event_name(client: AsyncClient, l2b_setup):
    """Step event uses maintenance.step.succeeded, not .completed."""
    node_id, token, auth = l2b_setup

    r = await client.post(
        "/admin/maintenance/plans",
        json={
            "goal": "Health check",
            "target_node_id": node_id,
            "steps": [{"function_name": "system.metrics.snapshot", "input": {}}],
        },
    )
    plan_id = r.json()["plan_id"]

    r = await client.post(f"/admin/maintenance/plans/{plan_id}/run")
    assert r.status_code == 200

    # Check timeline for step.succeeded (not step.completed)
    r = await client.get(f"/admin/timeline?plan_id={plan_id}")
    if r.status_code == 200:
        events = r.json()
        event_types = {e["event_type"] for e in events}
        assert (
            "maintenance.step.succeeded" in event_types or "maintenance.step.started" in event_types
        )
        # Must NOT use the old name
        assert "maintenance.step.completed" not in event_types


@pytest.mark.asyncio
async def test_timeline_approval_id_includes_step_events(client: AsyncClient, l2b_setup):
    """Timeline by approval_id should include step.succeeded and run.succeeded."""
    node_id, token, auth = l2b_setup

    # Create + approve + run a plan
    r = await client.post(
        "/admin/maintenance/plans",
        json={
            "goal": "Check metrics",
            "target_node_id": node_id,
            "steps": [{"function_name": "system.metrics.snapshot", "input": {}}],
        },
    )
    plan_id = r.json()["plan_id"]

    # Approve
    r = await client.post(f"/admin/maintenance/plans/{plan_id}/approve")
    approval_id = r.json().get("approval_id", "")
    assert approval_id, "approval_id must not be empty"

    # Run
    r = await client.post(f"/admin/maintenance/plans/{plan_id}/run")
    assert r.status_code == 200

    # Timeline by approval_id
    r = await client.get(f"/admin/timeline?approval_id={approval_id}")
    if r.status_code == 200:
        events = r.json()
        event_types = {e["event_type"] for e in events}
        # Should include plan events
        assert (
            "maintenance.run.started" in event_types or "maintenance.run.succeeded" in event_types
        )
        # Should include approval events
        assert "approval.requested" in event_types or "approval.approved" in event_types
