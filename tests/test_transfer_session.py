from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import make_yqp_envelope
from yequ.application.schemas import ExecuteToolCommand
from yequ.application.tool_invocation import ToolInvocationApplicationService
from yequ.models.job import Job
from yequ.models.node import Node
from yequ.models.transfer import TransferSession
from yequ.services.node_auth import hash_token


async def _provision(db: AsyncSession, node_id: str, token: str) -> None:
    db.add(
        Node(
            node_id=node_id,
            node_name=node_id,
            token_hash=hash_token(token),
            status="provisioned",
        )
    )
    await db.commit()


async def _hello(client: AsyncClient, node_id: str, token: str, os_name: str) -> None:
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.hello",
            node_id,
            payload={
                "daemon_version": "0.5.0",
                "node_name": node_id,
                "role": ["compute"],
                "locality": "lan",
                "platform": {"os": os_name, "arch": "x86_64"},
                "runtimes": [
                    {
                        "runtime_id": f"{os_name}-transfer",
                        "kind": "privileged",
                        "status": "online",
                        "interactive": False,
                        "privilege": "user",
                        "labels": [os_name, "transfer"],
                    }
                ],
            },
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


async def _register_transfer_capabilities(
    client: AsyncClient,
    node_id: str,
    token: str,
    prefix: str,
) -> None:
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.register_capabilities",
            node_id,
            payload={
                "plugins": [
                    {
                        "plugin_id": f"{prefix}.transfer",
                        "plugin_version": "1.0",
                        "status": "loaded",
                        "functions": [
                            {
                                "name": f"{prefix}.transfer.croc.send",
                                "description": "Send with croc.",
                                "input_schema": {
                                    "type": "object",
                                    "properties": {
                                        "path": {"type": "string"},
                                        "code": {"type": "string"},
                                    },
                                    "required": ["path", "code"],
                                },
                                "output_schema": {"type": "object"},
                                "risk": "maintenance",
                                "effect": "external",
                                "timeout_sec": 3600,
                                "lease_sec": 30,
                                "execution_requirements": {
                                    "runtime_kind": "privileged",
                                    "labels": [prefix, "transfer"],
                                },
                                "resource_keys": ["node.transfer"],
                                "conflict_policy": "serialize",
                                "hidden_input_fields": ["code"],
                            },
                            {
                                "name": f"{prefix}.transfer.croc.receive",
                                "description": "Receive with croc.",
                                "input_schema": {
                                    "type": "object",
                                    "properties": {
                                        "code": {"type": "string"},
                                        "output_dir": {"type": "string"},
                                    },
                                    "required": ["code"],
                                },
                                "output_schema": {"type": "object"},
                                "risk": "maintenance",
                                "effect": "external",
                                "timeout_sec": 3600,
                                "lease_sec": 30,
                                "execution_requirements": {
                                    "runtime_kind": "privileged",
                                    "labels": [prefix, "transfer"],
                                },
                                "resource_keys": ["node.transfer"],
                                "conflict_policy": "serialize",
                                "hidden_input_fields": ["code"],
                            },
                        ],
                        "signals": [],
                    }
                ]
            },
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_transfer_create_schedules_receiver_and_sender_jobs(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _provision(db_session, "winClient", "win-token")
    await _provision(db_session, "linux-node-01", "linux-token")
    await _hello(client, "winClient", "win-token", "windows")
    await _hello(client, "linux-node-01", "linux-token", "linux")
    await _register_transfer_capabilities(client, "winClient", "win-token", "windows")
    await _register_transfer_capabilities(client, "linux-node-01", "linux-token", "linux")

    result = await ToolInvocationApplicationService(db_session).execute(
        ExecuteToolCommand(
            function_name="transfer.create",
            input_data={
                "source_node_id": "winClient",
                "target_node_id": "linux-node-01",
                "source_path": "E:\\test\\1.mp3",
                "target_output_dir": "/tmp/yequ-transfer",
                "resume_mode": "resume",
                "timeout_sec": 600,
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert result.status == "succeeded"
    assert result.output_data is not None
    transfer = result.output_data["transfer"]
    assert isinstance(transfer, dict)
    assert transfer["status"] == "running"
    assert transfer["source_job_id"]
    assert transfer["target_job_id"]

    jobs_result = await db_session.execute(select(Job).order_by(Job.created_at))
    jobs = list(jobs_result.scalars().all())
    assert [job.node_id for job in jobs] == ["linux-node-01", "winClient"]
    assert [job.function_name for job in jobs] == [
        "linux.transfer.croc.receive",
        "windows.transfer.croc.send",
    ]
    assert jobs[0].input_payload["transfer_id"] == transfer["transfer_id"]
    assert jobs[1].input_payload["transfer_id"] == transfer["transfer_id"]

    session_result = await db_session.execute(select(TransferSession))
    session = session_result.scalar_one()
    assert session.target_job_id == jobs[0].job_id
    assert session.source_job_id == jobs[1].job_id

    status = await ToolInvocationApplicationService(db_session).execute(
        ExecuteToolCommand(
            function_name="transfer.status",
            input_data={"transfer_id": transfer["transfer_id"]},
        )
    )
    assert status.status == "succeeded"
    assert status.output_data is not None
    assert status.output_data["transfer"]["target_job"]["function_name"] == (
        "linux.transfer.croc.receive"
    )


@pytest.mark.asyncio
async def test_transfer_create_returns_structured_conflict_when_transfer_lock_is_held(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _provision(db_session, "winClient", "win-token")
    await _provision(db_session, "linux-node-01", "linux-token")
    await _hello(client, "winClient", "win-token", "windows")
    await _hello(client, "linux-node-01", "linux-token", "linux")
    await _register_transfer_capabilities(client, "winClient", "win-token", "windows")
    await _register_transfer_capabilities(client, "linux-node-01", "linux-token", "linux")

    service = ToolInvocationApplicationService(db_session)
    first = await service.execute(
        ExecuteToolCommand(
            function_name="transfer.create",
            input_data={
                "source_node_id": "winClient",
                "target_node_id": "linux-node-01",
                "source_path": "E:\\test\\1.mp3",
                "target_output_dir": "/tmp/yequ-transfer",
                "resume_mode": "resume",
                "timeout_sec": 600,
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )
    assert first.status == "succeeded"
    assert first.output_data is not None
    assert first.output_data["transfer"]["status"] == "running"

    second = await service.execute(
        ExecuteToolCommand(
            function_name="transfer.create",
            input_data={
                "source_node_id": "winClient",
                "target_node_id": "linux-node-01",
                "source_path": "E:\\test\\2.mp3",
                "target_output_dir": "/tmp/yequ-transfer",
                "resume_mode": "resume",
                "timeout_sec": 600,
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert second.status == "succeeded"
    assert second.output_data is not None
    transfer = second.output_data["transfer"]
    assert isinstance(transfer, dict)
    assert transfer["status"] == "failed"
    assert transfer["error_code"] == "resource_lock_conflict"
    assert "receive_result" in transfer
    assert transfer["receive_result"]["error_code"] == "resource_lock_conflict"
