"""Tests for application-layer tool invocation."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tests.conftest import make_yqp_envelope
from yequ.application import ExecuteToolCommand, ToolInvocationApplicationService
from yequ.models.approval import ApprovalRequest
from yequ.models.job import Job

pytestmark = pytest.mark.asyncio


def _runtimes(node_id: str) -> list[dict[str, object]]:
    return [
        {
            "runtime_id": f"{node_id}/runtime/default",
            "kind": "privileged",
            "status": "online",
            "interactive": False,
            "privilege": "elevated",
        }
    ]


async def _register_function(
    client,
    node_id: str,
    token: str,
    *,
    name: str,
    risk: str = "safe",
    effect: str = "read",
    resource_keys: list[str] | None = None,
) -> None:
    auth = {"Authorization": f"Bearer {token}"}
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.hello",
            node_id,
            payload={
                "daemon_version": "0.1.0",
                "runtimes": _runtimes(node_id),
            },
        ),
        headers=auth,
    )
    response = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.register_capabilities",
            node_id,
            payload={
                "runtimes": _runtimes(node_id),
                "plugins": [
                    {
                        "plugin_id": "test",
                        "plugin_version": "1.0.0",
                        "functions": [
                            {
                                "name": name,
                                "risk": risk,
                                "effect": effect,
                                "input_schema": {"type": "object"},
                                "output_schema": {"type": "object"},
                                "resource_keys": resource_keys or [],
                            }
                        ],
                    }
                ],
            },
        ),
        headers=auth,
    )
    assert response.status_code == 200


async def test_execute_safe_function_creates_invocation_and_job(
    client,
    db_session,
    provisioned_node,
) -> None:
    node, token = provisioned_node
    await _register_function(client, node.node_id, token, name="test.echo")
    await db_session.rollback()

    result = await ToolInvocationApplicationService(db_session).execute(
        ExecuteToolCommand(
            actor_type="agent",
            actor_id="agent-test",
            session_id="sess-test",
            function_name="test.echo",
            input_data={"value": "ok"},
            target_node_id=node.node_id,
        )
    )

    assert result.status == "created"
    assert result.invocation_id
    assert result.job_id
    assert result.target_node_id == node.node_id

    job_result = await db_session.execute(select(Job).where(Job.job_id == result.job_id))
    job = job_result.scalar_one()
    assert job.status == "queued"
    assert job.function_name == "test.echo"


async def test_execute_write_function_returns_approval_required(
    client,
    db_session,
    provisioned_node,
) -> None:
    node, token = provisioned_node
    await _register_function(
        client,
        node.node_id,
        token,
        name="test.write",
        risk="safe",
        effect="write",
        resource_keys=["service:{name}"],
    )
    await db_session.rollback()

    result = await ToolInvocationApplicationService(db_session).execute(
        ExecuteToolCommand(
            actor_type="agent",
            actor_id="agent-test",
            session_id="sess-test",
            function_name="test.write",
            input_data={"name": "demo"},
            target_node_id=node.node_id,
        )
    )

    assert result.status == "approval_required"
    assert result.approval_id
    assert result.invocation_id

    approval_result = await db_session.execute(
        select(ApprovalRequest).where(ApprovalRequest.approval_id == result.approval_id)
    )
    approval = approval_result.scalar_one()
    assert approval.status == "pending"
    assert approval.function_name == "test.write"


async def test_execute_unknown_function_returns_unavailable(
    db_session,
    provisioned_node,
) -> None:
    node, _token = provisioned_node

    result = await ToolInvocationApplicationService(db_session).execute(
        ExecuteToolCommand(
            function_name="missing.function",
            target_node_id=node.node_id,
        )
    )

    assert result.status == "unavailable"
    assert result.error_code
