"""Runtime Context contract tests.

These tests keep Center's routing model platform-neutral. Nodes may implement
their runtimes with Windows services, user workers, systemd units, containers,
or other mechanisms, but Center only sees RuntimeInstance attributes and
function execution requirements.
"""

import pytest
from httpx import AsyncClient

from tests.conftest import make_yqp_envelope


def _runtimes(node_id: str) -> list[dict]:
    return [
        {
            "runtime_id": f"{node_id}/runtime/privileged",
            "kind": "privileged",
            "status": "online",
            "labels": ["maintenance"],
            "interactive": False,
            "privilege": "elevated",
        },
        {
            "runtime_id": f"{node_id}/runtime/interactive",
            "kind": "interactive",
            "status": "online",
            "labels": ["desktop", "user-profile"],
            "interactive": True,
            "owner": "test-user",
        },
    ]


async def _hello(client: AsyncClient, node_id: str, token: str) -> None:
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.hello",
            node_id,
            payload={
                "daemon_version": "0.2.0",
                "platform": {"os": "test-os", "arch": "x64"},
                "runtimes": _runtimes(node_id),
            },
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


async def _register(client: AsyncClient, node_id: str, token: str) -> None:
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.register_capabilities",
            node_id,
            payload={
                "runtimes": _runtimes(node_id),
                "plugins": [
                    {
                        "plugin_id": "runtime.test",
                        "plugin_version": "1.0.0",
                        "functions": [
                            {
                                "name": "runtime.desktop.list",
                                "description": "List desktop resources",
                                "risk": "safe",
                                "effect": "read",
                                "timeout_sec": 30,
                                "execution_requirements": {
                                    "runtime_kind": "interactive",
                                    "interactive": True,
                                    "labels": ["desktop"],
                                },
                            },
                            {
                                "name": "runtime.maintenance.restart",
                                "description": "Restart a service",
                                "risk": "maintenance",
                                "effect": "write",
                                "timeout_sec": 30,
                                "execution_requirements": {
                                    "runtime_kind": "privileged",
                                    "labels": ["maintenance"],
                                },
                            },
                        ],
                        "signals": [],
                    }
                ],
            },
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_runtime_instances_are_reported_and_queryable(
    client: AsyncClient,
    provisioned_node,
):
    node, token = provisioned_node
    await _hello(client, node.node_id, token)

    resp = await client.get(f"/admin/nodes/{node.node_id}/runtimes")
    assert resp.status_code == 200
    runtimes = resp.json()
    assert [r["runtime_id"] for r in runtimes] == [
        f"{node.node_id}/runtime/interactive",
        f"{node.node_id}/runtime/privileged",
    ]
    assert any(r["kind"] == "interactive" and r["interactive"] for r in runtimes)


@pytest.mark.asyncio
async def test_resolver_selects_runtime_by_platform_neutral_requirements(
    client: AsyncClient,
    provisioned_node,
):
    node, token = provisioned_node
    await _hello(client, node.node_id, token)
    await _register(client, node.node_id, token)

    from yequ.db import async_session_factory
    from yequ.services.capability_resolver import resolve_function

    async with async_session_factory() as db:
        desktop = await resolve_function(
            db,
            "runtime.desktop.list",
            requested_node_id=node.node_id,
        )
        assert desktop.available is True
        assert desktop.runtime_id == f"{node.node_id}/runtime/interactive"
        assert desktop.execution_requirements["runtime_kind"] == "interactive"

        maintenance = await resolve_function(
            db,
            "runtime.maintenance.restart",
            requested_node_id=node.node_id,
        )
        assert maintenance.available is True
        assert maintenance.runtime_id == f"{node.node_id}/runtime/privileged"
        assert maintenance.execution_requirements["runtime_kind"] == "privileged"


@pytest.mark.asyncio
async def test_job_poll_includes_runtime_contract(
    client: AsyncClient,
    provisioned_node,
):
    node, token = provisioned_node
    auth = {"Authorization": f"Bearer {token}"}
    await _hello(client, node.node_id, token)
    await _register(client, node.node_id, token)

    create_resp = await client.post(
        "/admin/invocations",
        json={
            "function_name": "runtime.desktop.list",
            "target_node_id": node.node_id,
            "input": {},
        },
    )
    assert create_resp.status_code == 201

    poll_resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope("job.poll", node.node_id, {"capacity": 1}),
        headers=auth,
    )
    assert poll_resp.status_code == 200
    jobs = poll_resp.json()["payload"]["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["runtime_id"] == f"{node.node_id}/runtime/interactive"
    assert jobs[0]["execution_requirements"]["runtime_kind"] == "interactive"
