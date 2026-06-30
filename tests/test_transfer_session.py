from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import make_yqp_envelope
from yequ.agent.provider import AgentFunction
from yequ.agent.tool_stream import execute_tool_calls_scheduled
from yequ.application.schemas import ExecuteToolCommand, ExecuteToolResult
from yequ.models.agent_message import AgentMessage
from yequ.models.agent_run import AgentRun, AgentRunStep
from yequ.models.job import Job
from yequ.models.node import Node
from yequ.models.operation import Operation, OperationEvent
from yequ.models.session import Session
from yequ.models.transfer import TransferPreflight, TransferSession
from yequ.runtime import CenterExecutionRuntime
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


async def _fake_transfer_status_success(self, command, *, node_id: str):
    del self, command
    return ExecuteToolResult(
        status="succeeded",
        function_name="capability.invoke",
        output_data={
            "node_id": node_id,
            "installed": True,
            "allow_send": True,
            "allow_receive": True,
            "version": "test-croc",
        },
    )


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

    result = await CenterExecutionRuntime(db_session).execute(
        ExecuteToolCommand(
            function_name="transfer.create",
            input_data={
                "source_node_id": "winClient",
                "target_node_id": "linux-node-01",
                "source_path": "E:\\test\\1.mp3",
                "target_output_dir": "/tmp/yequ-transfer",
                "resume_mode": "resume",
                "timeout_sec": 600,
                "skip_preflight": True,
                "skip_reason": "test exercises transfer workflow scheduling",
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
    assert [job.node_id for job in jobs] == ["winClient", "linux-node-01"]
    assert [job.function_name for job in jobs] == [
        "windows.transfer.croc.send",
        "linux.transfer.croc.receive",
    ]
    assert jobs[0].input_payload["transfer_id"] == transfer["transfer_id"]
    assert jobs[1].input_payload["transfer_id"] == transfer["transfer_id"]

    session_result = await db_session.execute(select(TransferSession))
    session = session_result.scalar_one()
    assert session.source_job_id == jobs[0].job_id
    assert session.target_job_id == jobs[1].job_id
    operation_result = await db_session.execute(select(Operation))
    operation_model = operation_result.scalar_one()
    assert operation_model.ref_type == "transfer_session"
    assert operation_model.ref_id == session.transfer_id
    event_result = await db_session.execute(select(OperationEvent))
    assert event_result.scalar_one().event_type == "operation.created"

    status = await CenterExecutionRuntime(db_session).execute(
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
    operation_status = await CenterExecutionRuntime(db_session).execute(
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
async def test_transfer_operation_projects_job_progress(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _provision(db_session, "winClient", "win-token")
    await _provision(db_session, "linux-node-01", "linux-token")
    await _hello(client, "winClient", "win-token", "windows")
    await _hello(client, "linux-node-01", "linux-token", "linux")
    await _register_transfer_capabilities(client, "winClient", "win-token", "windows")
    await _register_transfer_capabilities(client, "linux-node-01", "linux-token", "linux")

    service = CenterExecutionRuntime(db_session)
    created = await service.execute(
        ExecuteToolCommand(
            function_name="transfer.create",
            input_data={
                "source_node_id": "winClient",
                "target_node_id": "linux-node-01",
                "source_path": "E:\\test\\large.zip",
                "target_output_dir": "/tmp/yequ-transfer",
                "resume_mode": "overwrite",
                "timeout_sec": 600,
                "skip_preflight": True,
                "skip_reason": "test exercises operation progress projection",
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )
    assert created.status == "waiting_operation"
    assert created.operation_id

    jobs_result = await db_session.execute(select(Job).order_by(Job.created_at))
    source_job, target_job = list(jobs_result.scalars().all())
    source_job.status = "running"
    source_job.progress_pct = 60
    source_job.progress_message = "Sending file"
    source_job.progress_detail = {
        "bytes_transferred": 60_000_000,
        "total_bytes": 100_000_000,
        "rate_bytes_per_sec": 5_000_000,
        "eta_sec": 8,
        "last_progress_at": "2026-06-30T12:00:02+00:00",
        "progress_source": "croc_output",
    }
    target_job.status = "running"
    target_job.progress_pct = 40
    target_job.progress_message = "Receiving file"
    target_job.progress_detail = {
        "bytes_transferred": 40_000_000,
        "total_bytes": 100_000_000,
        "rate_bytes_per_sec": 4_000_000,
        "eta_sec": 12,
        "last_progress_at": "2026-06-30T12:00:01+00:00",
        "progress_source": "partial_file_probe",
    }
    await db_session.commit()

    operation_status = await service.execute(
        ExecuteToolCommand(
            function_name="operation.status",
            input_data={"operation_id": created.operation_id},
        )
    )

    assert operation_status.status == "succeeded"
    assert operation_status.output_data is not None
    operation = operation_status.output_data["operation"]
    transfer = operation_status.output_data["transfer"]
    assert operation["status"] == "running"
    assert operation["progress_pct"] == 50
    assert operation["progress_message"] == "Sending file; Receiving file"
    assert operation["progress_detail"]["phase"] == "transferring"
    assert transfer["summary"]["progress"]["pct"] == 50
    assert transfer["summary"]["progress"]["phase"] == "transferring"
    assert transfer["summary"]["progress"]["bytes_transferred"] == 60_000_000
    assert transfer["summary"]["progress"]["size_bytes"] == 100_000_000
    assert transfer["summary"]["progress"]["rate_bytes_per_sec"] == 5_000_000
    assert transfer["summary"]["progress"]["eta_sec"] == 8
    assert transfer["summary"]["progress"]["last_progress_at"] == "2026-06-30T12:00:02+00:00"
    assert transfer["summary"]["progress"]["source"]["progress_pct"] == 60
    assert transfer["summary"]["progress"]["target"]["progress_pct"] == 40


@pytest.mark.asyncio
async def test_transfer_create_guard_requires_explicit_landing_path(
    db_session: AsyncSession,
) -> None:
    result = await CenterExecutionRuntime(db_session).execute(
        ExecuteToolCommand(
            function_name="transfer.create",
            input_data={
                "source_node_id": "winClient",
                "target_node_id": "linux-node-01",
                "source_path": "E:\\test\\1.mp3",
                "resume_mode": "resume",
                "timeout_sec": 600,
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert result.status == "failed"
    assert result.error_code == "needs_input"
    assert result.error_details is not None
    assert result.error_details["missing_slots"] == ["target_output_dir_or_target_path"]
    assert result.output_data == {"guard": result.error_details}

    transfer_count = await db_session.execute(select(TransferSession))
    assert transfer_count.scalars().all() == []
    operation_count = await db_session.execute(select(Operation))
    assert operation_count.scalars().all() == []
    job_count = await db_session.execute(select(Job))
    assert job_count.scalars().all() == []


@pytest.mark.asyncio
async def test_transfer_create_guard_requires_preflight_or_explicit_skip(
    db_session: AsyncSession,
) -> None:
    result = await CenterExecutionRuntime(db_session).execute(
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

    assert result.status == "failed"
    assert result.error_code == "preflight_required"
    assert result.error_details is not None
    assert result.error_details["required_facts"] == ["transfer.preflight"]

    transfer_count = await db_session.execute(select(TransferSession))
    assert transfer_count.scalars().all() == []
    operation_count = await db_session.execute(select(Operation))
    assert operation_count.scalars().all() == []
    job_count = await db_session.execute(select(Job))
    assert job_count.scalars().all() == []


@pytest.mark.asyncio
async def test_transfer_create_guard_requires_explicit_resume_mode(
    db_session: AsyncSession,
) -> None:
    result = await CenterExecutionRuntime(db_session).execute(
        ExecuteToolCommand(
            function_name="transfer.create",
            input_data={
                "source_node_id": "winClient",
                "target_node_id": "linux-node-01",
                "source_path": "E:\\test\\1.mp3",
                "target_output_dir": "/tmp/yequ-transfer",
                "timeout_sec": 600,
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert result.status == "failed"
    assert result.error_code == "needs_input"
    assert result.error_details is not None
    assert result.error_details["missing_slots"] == ["resume_mode"]

    transfer_count = await db_session.execute(select(TransferSession))
    assert transfer_count.scalars().all() == []
    operation_count = await db_session.execute(select(Operation))
    assert operation_count.scalars().all() == []
    job_count = await db_session.execute(select(Job))
    assert job_count.scalars().all() == []


@pytest.mark.asyncio
async def test_transfer_preflight_aggregates_local_stat_without_creating_transfer(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yequ.application.transfer import TransferApplicationService

    async def fake_stat(self, command, *, node_id: str, path: str, include_sha256: bool):
        del self, command
        if node_id == "winClient":
            return ExecuteToolResult(
                status="succeeded",
                function_name="capability.invoke",
                output_data={
                    "path": path,
                    "found": True,
                    "readable": True,
                    "is_file": True,
                    "size_bytes": 1024,
                    "sha256": "a" * 64 if include_sha256 else None,
                },
            )
        return ExecuteToolResult(
            status="succeeded",
            function_name="capability.invoke",
            output_data={
                "path": path,
                "found": True,
                "parent_exists": True,
                "writable": True,
                "free_bytes": 4096,
            },
        )

    monkeypatch.setattr(TransferApplicationService, "_invoke_stat_capability", fake_stat)
    monkeypatch.setattr(
        TransferApplicationService,
        "_invoke_status_capability",
        _fake_transfer_status_success,
    )

    result = await CenterExecutionRuntime(db_session).execute(
        ExecuteToolCommand(
            function_name="transfer.preflight",
            input_data={
                "source_node_id": "winClient",
                "target_node_id": "linux-node-01",
                "source_path": "E:\\test\\1.mp3",
                "target_output_dir": "/tmp/yequ-transfer",
                "resume_mode": "resume",
                "include_sha256": True,
                "ttl_sec": 999,
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert result.status == "succeeded"
    assert result.execution_plan is not None
    assert result.execution_plan["decision"] == "inline"
    assert result.output_data is not None
    preflight = result.output_data["preflight"]
    assert preflight["preflight_id"].startswith("tpf_")
    assert preflight["observed_at"]
    assert preflight["ttl_sec"] == 300
    assert preflight["expires_at"]
    assert preflight["allowed"] is True
    assert preflight["decision"] == "allow"
    assert preflight["failed_preconditions"] == []
    assert preflight["source"]["sha256"] == "a" * 64
    assert preflight["source"]["runtime"]["allow_send"] is True
    assert preflight["target"]["runtime"]["allow_receive"] is True

    transfer_count = await db_session.execute(select(TransferSession))
    assert transfer_count.scalars().all() == []
    operation_count = await db_session.execute(select(Operation))
    assert operation_count.scalars().all() == []
    job_count = await db_session.execute(select(Job))
    assert job_count.scalars().all() == []
    preflight_records = await db_session.execute(select(TransferPreflight))
    record = preflight_records.scalar_one()
    assert record.preflight_id == preflight["preflight_id"]
    assert record.allowed is True
    assert record.source_fact["observed_at"] == preflight["observed_at"]
    assert record.target_fact["observed_at"] == preflight["observed_at"]


@pytest.mark.asyncio
async def test_transfer_preflight_requires_explicit_resume_mode(
    db_session: AsyncSession,
) -> None:
    result = await CenterExecutionRuntime(db_session).execute(
        ExecuteToolCommand(
            function_name="transfer.preflight",
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

    assert result.status == "succeeded"
    assert result.output_data is not None
    preflight = result.output_data["preflight"]
    assert preflight["allowed"] is False
    assert preflight["decision"] == "needs_input"
    assert preflight["missing_slots"] == ["resume_mode"]

    preflight_records = await db_session.execute(select(TransferPreflight))
    assert preflight_records.scalars().all() == []


@pytest.mark.asyncio
async def test_transfer_preflight_reports_target_not_writable(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yequ.application.transfer import TransferApplicationService

    async def fake_stat(self, command, *, node_id: str, path: str, include_sha256: bool):
        del self, command, path, include_sha256
        if node_id == "winClient":
            return ExecuteToolResult(
                status="succeeded",
                function_name="capability.invoke",
                output_data={"found": True, "readable": True, "size_bytes": 1024},
            )
        return ExecuteToolResult(
            status="succeeded",
            function_name="capability.invoke",
            output_data={
                "found": True,
                "parent_exists": True,
                "writable": False,
                "free_bytes": 4096,
            },
        )

    monkeypatch.setattr(TransferApplicationService, "_invoke_stat_capability", fake_stat)
    monkeypatch.setattr(
        TransferApplicationService,
        "_invoke_status_capability",
        _fake_transfer_status_success,
    )

    result = await CenterExecutionRuntime(db_session).execute(
        ExecuteToolCommand(
            function_name="transfer.preflight",
            input_data={
                "source_node_id": "winClient",
                "target_node_id": "linux-node-01",
                "source_path": "E:\\test\\1.mp3",
                "target_output_dir": "/root",
                "resume_mode": "resume",
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert result.status == "succeeded"
    assert result.output_data is not None
    preflight = result.output_data["preflight"]
    assert preflight["allowed"] is False
    assert preflight["decision"] == "preflight_failed"
    assert preflight["failed_preconditions"] == [
        {"fact": "target.writable", "code": "target_not_writable"}
    ]


@pytest.mark.asyncio
async def test_transfer_preflight_reports_target_receive_disabled(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yequ.application.transfer import TransferApplicationService

    async def fake_stat(self, command, *, node_id: str, path: str, include_sha256: bool):
        del self, command, node_id, path, include_sha256
        return ExecuteToolResult(
            status="succeeded",
            function_name="capability.invoke",
            output_data={
                "found": True,
                "readable": True,
                "parent_exists": True,
                "writable": True,
                "size_bytes": 1024,
                "free_bytes": 4096,
            },
        )

    async def fake_status(self, command, *, node_id: str):
        del self, command
        return ExecuteToolResult(
            status="succeeded",
            function_name="capability.invoke",
            output_data={
                "node_id": node_id,
                "installed": True,
                "allow_send": True,
                "allow_receive": node_id != "linux-node-01",
            },
        )

    monkeypatch.setattr(TransferApplicationService, "_invoke_stat_capability", fake_stat)
    monkeypatch.setattr(TransferApplicationService, "_invoke_status_capability", fake_status)

    result = await CenterExecutionRuntime(db_session).execute(
        ExecuteToolCommand(
            function_name="transfer.preflight",
            input_data={
                "source_node_id": "winClient",
                "target_node_id": "linux-node-01",
                "source_path": "E:\\test\\1.mp3",
                "target_output_dir": "/tmp/yequ-transfer",
                "resume_mode": "resume",
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert result.status == "succeeded"
    assert result.output_data is not None
    preflight = result.output_data["preflight"]
    assert preflight["allowed"] is False
    assert preflight["decision"] == "preflight_failed"
    assert preflight["failed_preconditions"] == [
        {"fact": "target.runtime.allow_receive", "code": "receive_not_allowed"}
    ]


@pytest.mark.asyncio
async def test_transfer_create_rejects_mismatched_preflight(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yequ.application.transfer import TransferApplicationService

    async def fake_stat(self, command, *, node_id: str, path: str, include_sha256: bool):
        del self, command, node_id, path, include_sha256
        return ExecuteToolResult(
            status="succeeded",
            function_name="capability.invoke",
            output_data={
                "found": True,
                "readable": True,
                "parent_exists": True,
                "writable": True,
                "size_bytes": 1024,
                "free_bytes": 4096,
            },
        )

    monkeypatch.setattr(TransferApplicationService, "_invoke_stat_capability", fake_stat)
    monkeypatch.setattr(
        TransferApplicationService,
        "_invoke_status_capability",
        _fake_transfer_status_success,
    )
    preflight_result = await CenterExecutionRuntime(db_session).execute(
        ExecuteToolCommand(
            function_name="transfer.preflight",
            input_data={
                "source_node_id": "winClient",
                "target_node_id": "linux-node-01",
                "source_path": "E:\\test\\1.mp3",
                "target_output_dir": "/tmp/yequ-transfer",
                "resume_mode": "resume",
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )
    assert preflight_result.output_data is not None
    preflight_id = preflight_result.output_data["preflight"]["preflight_id"]

    result = await CenterExecutionRuntime(db_session).execute(
        ExecuteToolCommand(
            function_name="transfer.create",
            input_data={
                "source_node_id": "winClient",
                "target_node_id": "linux-node-01",
                "source_path": "E:\\test\\different.mp3",
                "target_output_dir": "/tmp/yequ-transfer",
                "resume_mode": "resume",
                "preflight_id": preflight_id,
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert result.status == "failed"
    assert result.error_code == "invalid_input"
    assert "preflight_intent_mismatch" in (result.error_message or "")
    transfer_count = await db_session.execute(select(TransferSession))
    assert transfer_count.scalars().all() == []
    job_count = await db_session.execute(select(Job))
    assert job_count.scalars().all() == []


@pytest.mark.asyncio
async def test_transfer_create_passes_preflight_size_to_receiver(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yequ.application.transfer import TransferApplicationService

    async def fake_stat(self, command, *, node_id: str, path: str, include_sha256: bool):
        del self, command, node_id, path, include_sha256
        return ExecuteToolResult(
            status="succeeded",
            function_name="capability.invoke",
            output_data={
                "found": True,
                "readable": True,
                "parent_exists": True,
                "writable": True,
                "size_bytes": 12345,
                "free_bytes": 54321,
            },
        )

    captured_inputs: list[dict[str, object]] = []

    async def fake_invoke_capability(self, command, *, node_id, capability_ref, tool_input):
        del self, command, node_id, capability_ref
        captured_inputs.append(dict(tool_input))
        return ExecuteToolResult(
            status="created",
            function_name="capability.invoke",
            invocation_id=f"inv_{len(captured_inputs)}",
            job_id=f"job_{len(captured_inputs)}",
        )

    monkeypatch.setattr(TransferApplicationService, "_invoke_stat_capability", fake_stat)
    monkeypatch.setattr(
        TransferApplicationService,
        "_invoke_status_capability",
        _fake_transfer_status_success,
    )
    monkeypatch.setattr(TransferApplicationService, "_invoke_capability", fake_invoke_capability)

    service = CenterExecutionRuntime(db_session)
    preflight_result = await service.execute(
        ExecuteToolCommand(
            function_name="transfer.preflight",
            input_data={
                "source_node_id": "winClient",
                "target_node_id": "linux-node-01",
                "source_path": "E:\\test\\1.mp3",
                "target_output_dir": "/tmp/yequ-transfer",
                "resume_mode": "resume",
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )
    assert preflight_result.output_data is not None
    preflight_id = preflight_result.output_data["preflight"]["preflight_id"]

    result = await service.execute(
        ExecuteToolCommand(
            function_name="transfer.create",
            input_data={
                "source_node_id": "winClient",
                "target_node_id": "linux-node-01",
                "source_path": "E:\\test\\1.mp3",
                "target_output_dir": "/tmp/yequ-transfer",
                "resume_mode": "resume",
                "preflight_id": preflight_id,
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert result.status == "waiting_operation"
    assert "expected_size_bytes" not in captured_inputs[0]
    assert captured_inputs[1]["expected_size_bytes"] == 12345


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

    service = CenterExecutionRuntime(db_session)
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
                "skip_preflight": True,
                "skip_reason": "test exercises resource lock conflict",
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
                "skip_preflight": True,
                "skip_reason": "test exercises resource lock conflict",
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
    assert "send_result" in transfer
    assert transfer["send_result"]["error_code"] == "resource_lock_conflict"


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

    service = CenterExecutionRuntime(db_session)
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
                "skip_preflight": True,
                "skip_reason": "test exercises transfer status",
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

    service = CenterExecutionRuntime(db_session)
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
                "skip_preflight": True,
                "skip_reason": "test exercises operation cancel",
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
                        "resume_mode": "resume",
                        "skip_preflight": True,
                        "skip_reason": "test exercises waiting operation events",
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

    created = await CenterExecutionRuntime(db_session).execute(
        ExecuteToolCommand(
            function_name="transfer.create",
            input_data={
                "source_node_id": "winClient",
                "target_node_id": "linux-node-01",
                "source_path": "E:\\test\\1.mp3",
                "target_output_dir": "/tmp/yequ-transfer",
                "resume_mode": "resume",
                "skip_preflight": True,
                "skip_reason": "test exercises resume operation",
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
            "user_message": "请结合这个传输结果继续说明下一步。",
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
    assert "请结合这个传输结果继续说明下一步。" in user_messages[-1]
    assert "operation" in user_messages[-1]
    assert "transfer" in user_messages[-1]

    step_result = await db_session.execute(
        select(AgentRunStep).where(AgentRunStep.step_type == "operation_observation")
    )
    step = step_result.scalar_one()
    assert step.step_type == "operation_observation"
    assert step.input_data == {"operation_id": created.operation_id}
    assert step.output_data is not None
    assert step.output_data["operation"]["operation_id"] == created.operation_id


@pytest.mark.asyncio
async def test_invoke_stream_loads_operation_context_refs(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _provision(db_session, "winClient", "win-token")
    await _provision(db_session, "linux-node-01", "linux-token")
    await _hello(client, "winClient", "win-token", "windows")
    await _hello(client, "linux-node-01", "linux-token", "linux")
    await _register_transfer_capabilities(client, "winClient", "win-token", "windows")
    await _register_transfer_capabilities(client, "linux-node-01", "linux-token", "linux")

    created = await CenterExecutionRuntime(db_session).execute(
        ExecuteToolCommand(
            function_name="transfer.create",
            input_data={
                "source_node_id": "winClient",
                "target_node_id": "linux-node-01",
                "source_path": "E:\\test\\1.mp3",
                "target_output_dir": "/tmp/yequ-transfer",
                "resume_mode": "resume",
                "skip_preflight": True,
                "skip_reason": "test exercises context refs",
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )
    assert created.operation_id

    session_id = "sess_context_refs"
    db_session.add(
        Session(
            session_id=session_id,
            actor_type="agent",
            actor_id="test-agent",
            status="active",
            execution_mode="auto",
            label="context refs",
        )
    )
    await db_session.commit()

    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import AgentResult
    from yequ.api.routes.agent import register_provider

    provider = FakeAgentProvider(provider_name="fake-context-refs")
    provider.set_default_result(
        AgentResult(
            success=True,
            output={"message": "context summarized"},
            function_calls=[],
        )
    )
    register_provider(provider)

    raw_prompt = "请继续总结结果。"
    visible_prompt = f"[Operation {created.operation_id}]\n{raw_prompt}"
    response = await client.post(
        "/agent/invoke/stream",
        json={
            "session_id": session_id,
            "provider_name": "fake-context-refs",
            "prompt": raw_prompt,
            "user_visible_prompt": visible_prompt,
            "context_refs": [
                {
                    "type": "operation",
                    "operation_id": created.operation_id,
                    "mode": "observation",
                }
            ],
            "execution_mode": "auto",
        },
    )

    assert response.status_code == 200
    assert "context summarized" in response.text
    assert "agent.context_block.loaded" in response.text
    assert provider.last_messages is not None
    user_messages = [
        message.content
        for message in provider.last_messages
        if message.role == "user" and message.content
    ]
    assert user_messages
    assert raw_prompt in user_messages[-1]
    assert visible_prompt not in user_messages[-1]
    assert "Center context blocks follow" in user_messages[-1]
    assert created.operation_id in user_messages[-1]

    saved_message_result = await db_session.execute(
        select(AgentMessage)
        .where(AgentMessage.session_id == session_id, AgentMessage.role == "user")
        .order_by(AgentMessage.created_at)
    )
    saved_user_messages = list(saved_message_result.scalars().all())
    assert saved_user_messages
    assert saved_user_messages[-1].content == visible_prompt

    step_result = await db_session.execute(
        select(AgentRunStep).where(AgentRunStep.step_type == "operation_observation")
    )
    steps = list(step_result.scalars().all())
    assert steps
    assert steps[-1].input_data == {"operation_id": created.operation_id}

    run_result = await db_session.execute(
        select(AgentRun).where(AgentRun.provider_name == "fake-context-refs")
    )
    runs = list(run_result.scalars().all())
    run = next(
        item
        for item in runs
        if (item.metadata_json or {}).get("source") == "agent.invoke.stream"
    )
    assert run.metadata_json is not None
    assert run.metadata_json["context_refs"] == [
        {
            "type": "operation",
            "operation_id": created.operation_id,
            "mode": "observation",
        }
    ]
    assert run.metadata_json["context_blocks"][0]["operation_id"] == created.operation_id
