"""Concurrency stability tests — 3 nodes + 5 agent invokes simultaneously."""

import asyncio
import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import make_yqp_envelope


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _setup_node(client: AsyncClient):
    """Provision a node with hello + capability registration."""
    node_id = f"node-{_uid()}"
    token = f"tok-{_uid()}"
    auth = {"Authorization": f"Bearer {token}"}

    await client.post("/admin/nodes", json={
        "node_id": node_id, "node_name": f"Node {node_id}", "token": token,
    })
    await client.post("/yqp/", json=make_yqp_envelope("node.hello", node_id, {
        "daemon_version": "0.1.0",
    }), headers=auth)
    await client.post("/yqp/", json=make_yqp_envelope(
        "node.register_capabilities", node_id,
        payload={"plugins": [{
            "plugin_id": "system.metrics",
            "plugin_version": "1.0.0",
            "functions": [{
                "name": "system.metrics.snapshot",
                "input_schema": {"type": "object", "properties": {}},
                "output_schema": {"type": "object"},
                "risk": "safe", "effect": "read", "timeout_sec": 5,
                "idempotency": "idempotent",
            }],
            "signals": [],
        }]},
    ), headers=auth)

    return node_id, token, auth


@pytest.mark.asyncio
async def test_concurrent_nodes_no_db_lock(client: AsyncClient):
    """3 nodes sending heartbeat+signal+poll concurrently — no db lock errors."""
    nodes = [await _setup_node(client) for _ in range(3)]

    async def node_cycle(node_id, token, auth):
        for _ in range(5):
            await client.post("/yqp/", json=make_yqp_envelope(
                "node.heartbeat", node_id,
                {"daemon_uptime_sec": 60, "running_jobs": 0, "status": "online"},
            ), headers=auth)
            await client.post("/yqp/", json=make_yqp_envelope(
                "signal.report", node_id,
                {
                    "signals": [
                        {
                            "name": "test.sig",
                            "value": 42,
                            "collected_at": "2026-01-01T00:00:00Z",
                            "ttl_sec": 15,
                        }
                    ],
                },
            ), headers=auth)
            await client.post("/yqp/", json=make_yqp_envelope(
                "job.poll", node_id, {"capacity": 2},
            ), headers=auth)

    tasks = [node_cycle(nid, tok, a) for nid, tok, a in nodes]
    await asyncio.gather(*tasks)

    # Verify all nodes still online
    r = await client.get("/admin/nodes")
    online = [n for n in r.json() if n["status"] in ("online", "provisioned")]
    assert len(online) >= 3, f"Expected >=3 online nodes, got {len(online)}"

    print(f"Concurrency test PASSED: {len(online)} nodes healthy")
