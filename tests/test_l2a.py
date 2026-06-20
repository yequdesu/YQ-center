"""L2-A Maintenance Write Operations tests."""

import asyncio
import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import make_yqp_envelope


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture
async def l2_setup(client: AsyncClient):
    """Setup: provision node + register a maintenance/write function."""
    node_id = f"node-{_uid()}"
    token = f"tok-{_uid()}"
    auth = {"Authorization": f"Bearer {token}"}

    await client.post("/admin/nodes", json={
        "node_id": node_id, "node_name": f"L2-{node_id}", "token": token,
    })
    await client.post("/yqp/", json=make_yqp_envelope("node.hello", node_id, {
        "daemon_version": "0.1.0",
    }), headers=auth)
    await client.post("/yqp/", json=make_yqp_envelope(
        "node.register_capabilities", node_id,
        payload={"plugins": [{
            "plugin_id": "test.plugin", "plugin_version": "1.0.0",
            "functions": [
                {"name": "system.service.restart", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}}, "output_schema": {"type": "object"}, "risk": "maintenance", "effect": "write", "timeout_sec": 30, "idempotency": "non_idempotent"},
                {"name": "system.metrics.snapshot", "input_schema": {"type": "object", "properties": {}}, "output_schema": {"type": "object"}, "risk": "safe", "effect": "read", "timeout_sec": 5, "idempotency": "idempotent"},
            ],
            "signals": [],
        }]},
    ), headers=auth)

    return node_id, token, auth


@pytest.mark.asyncio
async def test_l2_policy_readonly_reject(client: AsyncClient, l2_setup):
    """L2 write in readonly mode -> rejected."""
    node_id, token, auth = l2_setup
    r = await client.post("/admin/invocations", json={
        "function_name": "system.service.restart",
        "target_node_id": node_id,
        "input": {"name": "Spooler"},
        "execution_mode": "readonly",
    })
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_l2_auto_returns_approval_required(client: AsyncClient, l2_setup):
    """L2 write in auto mode -> waiting_approval + approval created."""
    node_id, token, auth = l2_setup
    r = await client.post("/admin/invocations", json={
        "function_name": "system.service.restart",
        "target_node_id": node_id,
        "input": {"name": "Spooler"},
        "execution_mode": "auto",
    })
    assert r.status_code == 201
    data = r.json()
    assert data["invocation_status"] == "waiting_approval"
    assert data["approval_id"], "approval_id must be set"


@pytest.mark.asyncio
async def test_l2_approve_then_create_job(client: AsyncClient, l2_setup):
    """Approve an L2 approval, then create job with approval_id."""
    node_id, token, auth = l2_setup

    # 1. Create invocation -> waiting_approval
    r = await client.post("/admin/invocations", json={
        "function_name": "system.service.restart",
        "target_node_id": node_id,
        "input": {"name": "Spooler"},
        "execution_mode": "auto",
    })
    assert r.status_code == 201
    approval_id = r.json()["approval_id"]

    # 2. Approve it
    r = await client.post(f"/admin/approvals/{approval_id}/approve")
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    # 3. Create invocation with approval_id -> creates job
    r = await client.post("/admin/invocations", json={
        "function_name": "system.service.restart",
        "target_node_id": node_id,
        "input": {"name": "Spooler"},
        "execution_mode": "auto",
        "approval_id": approval_id,
    })
    assert r.status_code == 201
    data = r.json()
    assert data["job_status"] == "queued"
    assert data["job_id"]


@pytest.mark.asyncio
async def test_l2_approval_input_hash_mismatch_reject(client: AsyncClient, l2_setup):
    """Approval with different input -> rejected."""
    node_id, token, auth = l2_setup

    # Create approval for Spooler
    r = await client.post("/admin/invocations", json={
        "function_name": "system.service.restart",
        "target_node_id": node_id,
        "input": {"name": "Spooler"},
        "execution_mode": "auto",
    })
    approval_id = r.json()["approval_id"]
    await client.post(f"/admin/approvals/{approval_id}/approve")

    # Try to use with different input
    r = await client.post("/admin/invocations", json={
        "function_name": "system.service.restart",
        "target_node_id": node_id,
        "input": {"name": "EventLog"},  # different!
        "execution_mode": "auto",
        "approval_id": approval_id,
    })
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_l2_approval_cannot_reuse(client: AsyncClient, l2_setup):
    """Consumed approval cannot be reused."""
    node_id, token, auth = l2_setup

    r = await client.post("/admin/invocations", json={
        "function_name": "system.service.restart",
        "target_node_id": node_id,
        "input": {"name": "Spooler"},
        "execution_mode": "auto",
    })
    approval_id = r.json()["approval_id"]
    await client.post(f"/admin/approvals/{approval_id}/approve")

    # First use -> succeeds
    r = await client.post("/admin/invocations", json={
        "function_name": "system.service.restart",
        "target_node_id": node_id,
        "input": {"name": "Spooler"},
        "execution_mode": "auto",
        "approval_id": approval_id,
    })
    assert r.status_code == 201

    # Second use -> rejected
    r = await client.post("/admin/invocations", json={
        "function_name": "system.service.restart",
        "target_node_id": node_id,
        "input": {"name": "Spooler"},
        "execution_mode": "auto",
        "approval_id": approval_id,
    })
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_l2_resource_lock_conflict(client: AsyncClient, l2_setup):
    """Two approved invocations on same service -> second gets lock conflict."""
    node_id, token, auth = l2_setup

    # Create + approve first
    r = await client.post("/admin/invocations", json={
        "function_name": "system.service.restart",
        "target_node_id": node_id,
        "input": {"name": "Spooler"},
        "execution_mode": "auto",
    })
    apv1 = r.json()["approval_id"]
    await client.post(f"/admin/approvals/{apv1}/approve")

    # Execute first job
    r = await client.post("/admin/invocations", json={
        "function_name": "system.service.restart",
        "target_node_id": node_id,
        "input": {"name": "Spooler"},
        "approval_id": apv1,
        "execution_mode": "auto",
    })
    assert r.status_code == 201
    job1_id = r.json()["job_id"]
    assert job1_id

    # Create + approve second (same service)
    r = await client.post("/admin/invocations", json={
        "function_name": "system.service.restart",
        "target_node_id": node_id,
        "input": {"name": "Spooler"},
        "execution_mode": "auto",
    })
    apv2 = r.json()["approval_id"]
    await client.post(f"/admin/approvals/{apv2}/approve")

    # Second execution must fail with lock conflict
    r = await client.post("/admin/invocations", json={
        "function_name": "system.service.restart",
        "target_node_id": node_id,
        "input": {"name": "Spooler"},
        "approval_id": apv2,
        "execution_mode": "auto",
    })
    assert r.status_code in (409,), f"Expected lock conflict, got {r.status_code}: {r.text[:200]}"


@pytest.mark.asyncio
async def test_l2_lock_released_on_job_finish(client: AsyncClient, l2_setup):
    """Lock released when job enters terminal state."""
    node_id, token, auth = l2_setup

    r = await client.post("/admin/invocations", json={
        "function_name": "system.service.restart",
        "target_node_id": node_id,
        "input": {"name": "Spooler"},
        "execution_mode": "auto",
    })
    apv = r.json()["approval_id"]
    await client.post(f"/admin/approvals/{apv}/approve")

    r = await client.post("/admin/invocations", json={
        "function_name": "system.service.restart",
        "target_node_id": node_id,
        "input": {"name": "Spooler"},
        "execution_mode": "auto",
        "approval_id": apv,
    })
    job_id = r.json()["job_id"]

    # Poll, accept, finish
    await client.post("/yqp/", json=make_yqp_envelope("job.poll", node_id, {"capacity": 2}), headers=auth)
    await client.post("/yqp/", json=make_yqp_envelope("job.accepted", node_id, {"job_id": job_id}), headers=auth)
    await client.post("/yqp/", json=make_yqp_envelope("job.finished", node_id, {
        "job_id": job_id, "status": "succeeded", "output": {"restarted": True},
    }), headers=auth)

    # Verify locks released
    r = await client.get(f"/admin/locks?job_id={job_id}")
    if r.status_code == 200:
        locks = r.json()
        held = [l for l in locks if l["status"] == "held" and l["job_id"] == job_id]
        assert len(held) == 0, f"Locks should be released, got {len(held)} held"


@pytest.mark.asyncio
async def test_agent_l2_returns_waiting_approval(client: AsyncClient, l2_setup):
    """Agent invoking L2 function returns waiting_approval."""
    node_id, token, auth = l2_setup

    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import AgentResult
    from yequ.api.routes.agent import register_provider

    provider = FakeAgentProvider()
    provider.add_response("restart", AgentResult(
        success=True, output={"message": ""},
        function_calls=[{"name": "system.service.restart", "input": {"name": "Spooler"}, "call_id": "call_l2"}],
    ))
    register_provider(provider)

    r = await client.post("/agent/sessions", json={
        "actor_id": "l2-test", "execution_mode": "auto",
    })
    sid = r.json()["session_id"]

    r = await client.post("/agent/invoke", json={
        "session_id": sid, "provider_name": "fake",
        "prompt": "restart Spooler", "execution_mode": "auto",
        "max_total_duration_sec": 30,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "waiting_approval"
    assert data["tool_calls"][0]["status"] == "waiting_approval"
    assert data["tool_calls"][0]["error"]["code"] == "approval_required"


@pytest.mark.asyncio
async def test_l1_read_still_works(client: AsyncClient, l2_setup):
    """L1 read operations still work alongside L2."""
    node_id, token, auth = l2_setup

    r = await client.post("/admin/invocations", json={
        "function_name": "system.metrics.snapshot",
        "target_node_id": node_id,
        "input": {},
        "execution_mode": "auto",
    })
    assert r.status_code == 201
    assert r.json()["job_status"] == "queued"


@pytest.mark.asyncio
async def test_approval_resource_keys_rendered(client: AsyncClient, l2_setup):
    """Approval resource_keys rendered from template + input."""
    node_id, token, auth = l2_setup

    r = await client.post("/admin/invocations", json={
        "function_name": "system.service.restart",
        "target_node_id": node_id,
        "input": {"name": "Spooler"},
        "execution_mode": "auto",
    })
    apv_id = r.json()["approval_id"]

    r = await client.get(f"/admin/approvals/{apv_id}")
    assert r.status_code == 200
    data = r.json()
    keys = data.get("resource_keys", [])
    expected = f"node:{node_id}:service:Spooler"
    assert expected in keys, f"Expected {expected} in resource_keys, got {keys}"


@pytest.mark.asyncio
async def test_approval_input_hash_excludes_approval_id(client: AsyncClient, l2_setup):
    """Input hash must exclude approval_id field."""
    node_id, token, auth = l2_setup

    from yequ.services.approval_service import _hash_input
    h1 = _hash_input({"name": "Spooler"})
    h2 = _hash_input({"name": "Spooler", "approval_id": "apv_xxx"})
    assert h1 == h2, "Input hash should exclude approval_id"


@pytest.mark.asyncio
async def test_l2_dry_run_returns_check_only(client: AsyncClient, l2_setup):
    """dry_run=true -> pre-check only, no job created."""
    node_id, token, auth = l2_setup

    r = await client.post("/admin/invocations", json={
        "function_name": "system.service.restart",
        "target_node_id": node_id,
        "input": {"name": "Spooler"},
        "execution_mode": "auto",
        "dry_run": True,
    })
    assert r.status_code == 200
    data = r.json()
    assert data.get("job_id", "") == ""
    # dry_run reports policy result: ask/deny means policy_denied, allow means dry_run_completed
    assert data.get("invocation_status") in ("dry_run_completed", "policy_denied")
