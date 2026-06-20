"""Tests for readonly admin query endpoints."""

import pytest
from httpx import AsyncClient

from tests.conftest import make_yqp_envelope


async def _create_approval(client: AsyncClient, node_id: str) -> str:
    """Helper: create an approved approval and return its id."""
    # Create approval
    r = await client.post("/admin/approvals", json={
        "function_name": "test.func",
        "target_node_id": node_id,
        "input_data": {},
        "risk": "maintenance",
        "effect": "write",
    })
    approval_id = r.json()["approval_id"]
    # Approve it
    await client.post(f"/admin/approvals/{approval_id}/approve")
    return approval_id


async def _invoke(client: AsyncClient, node_id: str, **extra) -> dict:
    """Helper: create an invocation with approval."""
    approval_id = await _create_approval(client, node_id)
    body = {"function_name": "test.func", "target_node_id": node_id,
            "approval_id": approval_id, **extra}
    r = await client.post("/admin/invocations", json=body)
    assert r.status_code == 201
    return r.json()


@pytest.mark.asyncio
async def test_list_nodes(client: AsyncClient):
    """GET /admin/nodes should return list of nodes."""
    # Provision a node first
    await client.post("/admin/nodes", json={
        "node_id": "admin-test-node",
        "node_name": "Admin Test",
        "token": "tok-12345678",
    })
    r = await client.get("/admin/nodes")
    assert r.status_code == 200
    nodes = r.json()
    assert isinstance(nodes, list)
    assert any(n["node_id"] == "admin-test-node" for n in nodes)


@pytest.mark.asyncio
async def test_get_node_detail(client: AsyncClient):
    """GET /admin/nodes/{node_id} should return node details."""
    await client.post("/admin/nodes", json={
        "node_id": "detail-node", "node_name": "Detail Node", "token": "tok-detail",
    })
    r = await client.get("/admin/nodes/detail-node")
    assert r.status_code == 200
    assert r.json()["node_name"] == "Detail Node"
    assert "token_hash" in r.json()


@pytest.mark.asyncio
async def test_get_node_404(client: AsyncClient):
    """GET /admin/nodes/{node_id} for unknown node should return 404."""
    r = await client.get("/admin/nodes/nonexistent-node")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_capabilities(client: AsyncClient):
    """GET /admin/capabilities should return active capabilities."""
    token = "tok-cap-test"
    node_id = "cap-test-node"
    auth = {"Authorization": f"Bearer {token}"}

    await client.post("/admin/nodes", json={
        "node_id": node_id, "node_name": "Cap Test", "token": token,
    })
    # Register capabilities via YQP
    await client.post("/yqp/", json=make_yqp_envelope(
        "node.register_capabilities", node_id,
        payload={"plugins": [{
            "plugin_id": "test.plugin",
            "plugin_version": "1.0.0",
            "functions": [{"name": "test.func", "input_schema": {}, "output_schema": {},
                           "risk": "safe", "effect": "read", "timeout_sec": 5,
                           "idempotency": "idempotent"}],
            "signals": [],
        }]},
    ), headers=auth)

    r = await client.get(f"/admin/capabilities?node_id={node_id}")
    assert r.status_code == 200
    caps = r.json()
    assert len(caps) >= 1
    assert any(c["name"] == "test.func" for c in caps)


@pytest.mark.asyncio
async def test_list_jobs(client: AsyncClient):
    """GET /admin/jobs should list jobs with filters."""
    token = "tok-job-list"
    node_id = "job-list-node"
    await client.post("/admin/nodes", json={
        "node_id": node_id, "node_name": "Job List", "token": token,
    })

    # Create an invocation (which creates a job)
    await _invoke(client, node_id)

    # List all jobs
    r = await client.get("/admin/jobs")
    assert r.status_code == 200
    assert len(r.json()) >= 1

    # Filter by node
    r = await client.get(f"/admin/jobs?node_id={node_id}")
    assert r.status_code == 200
    assert all(j["node_id"] == node_id for j in r.json())

    # Filter by status
    r = await client.get("/admin/jobs?status=queued")
    assert r.status_code == 200
    assert all(j["status"] == "queued" for j in r.json())


@pytest.mark.asyncio
async def test_get_job_detail(client: AsyncClient):
    """GET /admin/jobs/{job_id} should return job details."""
    token = "tok-jd-123"
    node_id = "jd-node"
    await client.post("/admin/nodes", json={
        "node_id": node_id, "node_name": "JD", "token": token,
    })
    data = await _invoke(client, node_id)
    job_id = data["job_id"]

    r = await client.get(f"/admin/jobs/{job_id}")
    assert r.status_code == 200
    assert r.json()["job_id"] == job_id
    assert r.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_get_job_404(client: AsyncClient):
    """GET /admin/jobs/{job_id} for unknown job should return 404."""
    r = await client.get("/admin/jobs/nonexistent-job")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_invocation_detail(client: AsyncClient):
    """GET /admin/invocations/{invocation_id} should include jobs."""
    token = "tok-inv-12"
    node_id = "inv-node"
    await client.post("/admin/nodes", json={
        "node_id": node_id, "node_name": "Inv", "token": token,
    })
    data = await _invoke(client, node_id)
    inv_id = data["invocation_id"]

    r = await client.get(f"/admin/invocations/{inv_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["function_name"] == "test.func"
    assert len(data["jobs"]) >= 1
    assert data["jobs"][0]["status"] == "queued"


@pytest.mark.asyncio
async def test_list_timeline_filtered(client: AsyncClient):
    """GET /admin/timeline should support event_type and job_id filters."""
    token = "tok-tl"
    node_id = "tl-node"
    auth = {"Authorization": f"Bearer {token}"}

    await client.post("/admin/nodes", json={
        "node_id": node_id, "node_name": "TL", "token": token,
    })
    # Do a hello to create timeline events
    await client.post("/yqp/", json=make_yqp_envelope(
        "node.hello", node_id,
        payload={"daemon_version": "0.1.0"},
    ), headers=auth)

    # List timeline
    r = await client.get("/admin/timeline")
    assert r.status_code == 200
    events = r.json()
    assert isinstance(events, list)

    # Filter by event_type
    r = await client.get("/admin/timeline?event_type=job.queued")
    assert r.status_code == 200

    # Filter by node_id
    r = await client.get(f"/admin/timeline?node_id={node_id}")
    assert r.status_code == 200
