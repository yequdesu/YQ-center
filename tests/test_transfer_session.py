from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import make_yqp_envelope
from yequ.agent.provider import AgentFunction
from yequ.agent.tool_stream import execute_tool_calls_scheduled
from yequ.application.schemas import ExecuteToolCommand
from yequ.application.tool_invocation import ToolInvocationApplicationService
from yequ.models.agent_run import AgentRunStep
from yequ.models.job import Job
from yequ.models.node import Node
from yequ.models.operation import Operation, OperationEvent
from yequ.models.session import Session
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

    assert result.status == "waiting_operation"
    assert result.output_data is not None
    transfer = result.output_data["transfer"]
    operation = result.output_data["operation"]
    wait_handle = result.output_data["wait_handle"]
    assert isinstance(transfer, dict)
    assert isinstance(operation, dict)
    assert isinstance(wait_handle, dict)
    assert transfer["status"] == "running"
    assert transfer["source_job_id"]
    assert transfer["target_job_id"]
    assert operation["kind"] == "transfer"
    assert operation["ref_id"] == transfer["transfer_id"]
    assert wait_handle["operation_id"] == operation["operation_id"]
    assert result.operation_id == operation["operation_id"]
    assert result.execution_plan is not None
    assert result.execution_plan["decision"] == "workflow_operation"

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
    operation_result = await db_session.execute(select(Operation))
    operation_model = operation_result.scalar_one()
    assert operation_model.ref_type == "transfer_session"
    assert operation_model.ref_id == session.transfer_id
    event_result = await db_session.execute(select(OperationEvent))
    assert event_result.scalar_one().event_type == "operation.created"

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
    operation_status = await ToolInvocationApplicationService(db_session).execute(
        ExecuteToolCommand(
            function_name="operation.status",
            input_data={"operation_id": operation["operation_id"]},
        )
    )
    assert operation_status.status == "succeeded"
    assert operation_status.output_data is not None
    assert operation_status.output_data["operation"]["ref_id"] == transfer["transfer_id"]
    assert operation_status.output_data["transfer"]["transfer_id"] == transfer["transfer_id"]


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
    assert first.status == "waiting_operation"
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

    assert second.status == "waiting_operation"
    assert second.output_data is not None
    transfer = second.output_data["transfer"]
    assert isinstance(transfer, dict)
    assert transfer["status"] == "failed"
    assert transfer["error_code"] == "resource_lock_conflict"
    assert "receive_result" in transfer
    assert transfer["receive_result"]["error_code"] == "resource_lock_conflict"


@pytest.mark.asyncio
async def test_transfer_status_cancels_peer_when_one_side_fails(
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
    created = await service.execute(
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
    assert created.status == "waiting_operation"
    assert created.operation_id
    assert created.output_data is not None
    transfer = created.output_data["transfer"]
    assert isinstance(transfer, dict)

    jobs_result = await db_session.execute(select(Job).order_by(Job.created_at))
    target_job, source_job = list(jobs_result.scalars().all())
    target_job.status = "failed"
    target_job.error_code = "received_file_missing"
    target_job.error_message = "croc returned success but no file found"
    source_job.status = "running"
    await db_session.commit()

    status = await service.execute(
        ExecuteToolCommand(
            function_name="transfer.status",
            input_data={"transfer_id": transfer["transfer_id"]},
        )
    )

    assert status.status == "succeeded"
    await db_session.refresh(source_job)
    assert source_job.status == "cancelling"
    assert source_job.cancel_reason == "transfer_peer_failed"

    operation_status = await service.execute(
        ExecuteToolCommand(
            function_name="operation.status",
            input_data={"operation_id": created.operation_id},
        )
    )
    assert operation_status.status == "succeeded"
    assert operation_status.output_data is not None
    assert operation_status.output_data["operation"]["status"] == "failed"


@pytest.mark.asyncio
async def test_operation_cancel_cancels_transfer_peer_jobs(
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
    created = await service.execute(
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
    assert created.operation_id

    cancelled = await service.execute(
        ExecuteToolCommand(
            function_name="operation.cancel",
            input_data={
                "operation_id": created.operation_id,
                "reason": "test_cancel",
            },
        )
    )

    assert cancelled.status == "succeeded"
    assert cancelled.output_data is not None
    assert cancelled.output_data["operation"]["status"] == "cancelled"

    jobs_result = await db_session.execute(select(Job).order_by(Job.created_at))
    jobs = list(jobs_result.scalars().all())
    assert {job.status for job in jobs} == {"cancelled"}
    assert cancelled.output_data["transfer"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_agent_transfer_create_emits_waiting_operation_events(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _provision(db_session, "winClient", "win-token")
    await _provision(db_session, "linux-node-01", "linux-token")
    await _hello(client, "winClient", "win-token", "windows")
    await _hello(client, "linux-node-01", "linux-token", "linux")
    await _register_transfer_capabilities(client, "winClient", "win-token", "windows")
    await _register_transfer_capabilities(client, "linux-node-01", "linux-token", "linux")

    available = [
        AgentFunction(
            name="transfer.create",
            description="Create transfer",
            input_schema={"type": "object", "properties": {}},
            risk="maintenance",
            effect="external",
        )
    ]

    events = [
        event
        async for event in execute_tool_calls_scheduled(
            db_session,
            make_event=lambda event_type, data=None: {
                "event_type": event_type,
                "data": data or {},
            },
            actor_id="test-agent",
            session_id="sess_test",
            tool_calls=[
                {
                    "name": "transfer.create",
                    "call_id": "call_transfer_1",
                    "input": {
                        "source_node_id": "winClient",
                        "target_node_id": "linux-node-01",
                        "source_path": "E:\\test\\1.mp3",
                        "target_output_dir": "/tmp/yequ-transfer",
                    },
                }
            ],
            known_functions={"transfer.create"},
            available_functions=available,
            call_path=[],
            execution_mode="auto",
            max_depth=5,
            max_total_duration_sec=300,
            started_at=None,
        )
    ]

    event_types = [event["event_type"] for event in events]
    assert "agent.operation.created" in event_types
    assert "agent.operation.waiting" in event_types
    assert "agent.run.waiting" in event_types
    assert "agent.tool_call.waiting_operation" in event_types
    assert "agent.job.queued" not in event_types


@pytest.mark.asyncio
async def test_resume_operation_stream_injects_operation_observation(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _provision(db_session, "winClient", "win-token")
    await _provision(db_session, "linux-node-01", "linux-token")
    await _hello(client, "winClient", "win-token", "windows")
    await _hello(client, "linux-node-01", "linux-token", "linux")
    await _register_transfer_capabilities(client, "winClient", "win-token", "windows")
    await _register_transfer_capabilities(client, "linux-node-01", "linux-token", "linux")

    created = await ToolInvocationApplicationService(db_session).execute(
        ExecuteToolCommand(
            function_name="transfer.create",
            input_data={
                "source_node_id": "winClient",
                "target_node_id": "linux-node-01",
                "source_path": "E:\\test\\1.mp3",
                "target_output_dir": "/tmp/yequ-transfer",
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )
    assert created.operation_id

    session_id = "sess_resume_operation"
    db_session.add(
        Session(
            session_id=session_id,
            actor_type="agent",
            actor_id="test-agent",
            status="active",
            execution_mode="auto",
            label="resume",
        )
    )
    await db_session.commit()

    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import AgentResult
    from yequ.api.routes.agent import register_provider

    provider = FakeAgentProvider(provider_name="fake-resume-operation")
    provider.set_default_result(
        AgentResult(
            success=True,
            output={"message": "operation summarized"},
            function_calls=[],
        )
    )
    register_provider(provider)

    response = await client.post(
        "/agent/resume-operation/stream",
        json={
            "session_id": session_id,
            "provider_name": "fake-resume-operation",
            "operation_id": created.operation_id,
            "execution_mode": "auto",
        },
    )

    assert response.status_code == 200
    assert "operation summarized" in response.text
    assert provider.last_messages is not None
    user_messages = [
        message.content
        for message in provider.last_messages
        if message.role == "user" and message.content
    ]
    assert user_messages
    assert created.operation_id in user_messages[-1]
    assert "operation" in user_messages[-1]
    assert "transfer" in user_messages[-1]

    step_result = await db_session.execute(select(AgentRunStep))
    step = step_result.scalar_one()
    assert step.step_type == "operation_observation"
    assert step.input_data == {"operation_id": created.operation_id}
    assert step.output_data is not None
    assert step.output_data["operation"]["operation_id"] == created.operation_id
