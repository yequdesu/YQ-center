from __future__ import annotations

import base64

import pytest
from httpx import AsyncClient

from tests.conftest import make_yqp_envelope


@pytest.mark.asyncio
async def test_admin_artifact_upload_list_detail_download(client: AsyncClient) -> None:
    payload = b"hello artifact\n"
    upload = await client.post(
        "/admin/artifacts",
        data={
            "artifact_type": "text",
            "title": "hello.txt",
            "session_id": "sess_test",
            "node_id": "node-a",
        },
        files={"file": ("hello.txt", payload, "text/plain")},
    )
    assert upload.status_code == 201, upload.text
    artifact = upload.json()["artifact"]
    artifact_id = artifact["artifact_id"]
    assert artifact["artifact_type"] == "text"
    assert artifact["title"] == "hello.txt"
    assert artifact["session_id"] == "sess_test"
    assert artifact["node_id"] == "node-a"
    assert artifact["content_type"] == "text/plain"
    assert artifact["size_bytes"] == len(payload)
    assert artifact["sha256"]
    assert artifact["download_url"] == f"/admin/artifacts/{artifact_id}/download"

    listed = await client.get("/admin/artifacts", params={"session_id": "sess_test"})
    assert listed.status_code == 200, listed.text
    artifacts = listed.json()["artifacts"]
    assert [item["artifact_id"] for item in artifacts] == [artifact_id]

    detail = await client.get(f"/admin/artifacts/{artifact_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["artifact"]["artifact_id"] == artifact_id

    downloaded = await client.get(f"/admin/artifacts/{artifact_id}/download")
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == payload
    assert downloaded.headers["content-type"].startswith("text/plain")


@pytest.mark.asyncio
async def test_admin_artifact_missing_download_returns_404(client: AsyncClient) -> None:
    response = await client.get("/admin/artifacts/missing/download")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_yqp_artifact_upload_creates_downloadable_artifact(
    client: AsyncClient,
    provisioned_node,
) -> None:
    node, token = provisioned_node
    payload = b"node artifact bytes"

    response = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "artifact.upload",
            node.node_id,
            payload={
                "artifact_type": "text",
                "content_type": "text/plain",
                "title": "node-output.txt",
                "data_base64": base64.b64encode(payload).decode("ascii"),
                "metadata": {"source": "test"},
                "job_id": "job_test_artifact",
            },
        ),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["message_type"] == "artifact.accepted"
    artifact = data["payload"]["artifact"]
    artifact_id = artifact["artifact_id"]
    assert artifact["node_id"] == node.node_id
    assert artifact["job_id"] == "job_test_artifact"
    assert artifact["size_bytes"] == len(payload)

    downloaded = await client.get(f"/admin/artifacts/{artifact_id}/download")
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == payload

    node_downloaded = await client.get(
        f"/yqp/artifacts/{artifact_id}/download",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert node_downloaded.status_code == 200, node_downloaded.text
    assert node_downloaded.content == payload
    assert node_downloaded.headers["x-yequ-artifact-id"] == artifact_id
    assert node_downloaded.headers["x-yequ-node-id"] == node.node_id


@pytest.mark.asyncio
async def test_yqp_artifact_upload_rejects_invalid_base64(
    client: AsyncClient,
    provisioned_node,
) -> None:
    node, token = provisioned_node

    response = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "artifact.upload",
            node.node_id,
            payload={"data_base64": "not valid base64"},
        ),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_yqp_artifact_upload_rejects_overlong_job_id(
    client: AsyncClient,
    provisioned_node,
) -> None:
    node, token = provisioned_node

    response = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "artifact.upload",
            node.node_id,
            payload={
                "artifact_type": "file",
                "content_type": "text/plain",
                "title": "bad-job-id.txt",
                "data_base64": base64.b64encode(b"bad job id").decode("ascii"),
                "job_id": "job_" + "x" * 40,
            },
        ),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "schema_invalid"
    assert "job_id exceeds max length" in detail["message"]


@pytest.mark.asyncio
async def test_agent_artifact_meta_tools_list_and_present(db_session) -> None:
    from yequ.application.schemas import ExecuteToolCommand
    from yequ.config import get_settings
    from yequ.runtime import CenterExecutionRuntime
    from yequ.services.artifact_service import ArtifactPayload, create_artifact

    artifact = await create_artifact(
        db_session,
        ArtifactPayload(
            data=b"fake png bytes",
            artifact_type="image",
            content_type="image/png",
            title="sample.png",
            session_id="sess_artifact_meta",
            node_id="winClient",
        ),
        settings=get_settings(),
    )
    await db_session.commit()

    service = CenterExecutionRuntime(db_session)
    listed = await service.execute(
        ExecuteToolCommand(
            function_name="artifact.list",
            input_data={"artifact_type": "image"},
            session_id="sess_artifact_meta",
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert listed.status == "succeeded"
    assert listed.invocation_id is None
    assert listed.job_id is None
    assert listed.output_data is not None
    listed_artifacts = listed.output_data["artifacts"]
    assert isinstance(listed_artifacts, list)
    assert listed_artifacts[0]["artifact_id"] == artifact.artifact_id

    presented = await service.execute(
        ExecuteToolCommand(
            function_name="artifact.present",
            input_data={"artifact_id": artifact.artifact_id},
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert presented.status == "succeeded"
    assert presented.invocation_id is None
    assert presented.job_id is None
    assert presented.output_data is not None
    assert presented.output_data["presentation"] == {
        "kind": "artifact_gallery",
        "count": 1,
    }
    presented_artifacts = presented.output_data["artifacts"]
    assert isinstance(presented_artifacts, list)
    assert presented_artifacts[0]["content_type"] == "image/png"
    assert presented_artifacts[0]["download_url"] == (
        f"/admin/artifacts/{artifact.artifact_id}/download"
    )


@pytest.mark.asyncio
async def test_agent_artifact_read_text_supports_pattern_range_and_line_glob(db_session) -> None:
    from yequ.application.schemas import ExecuteToolCommand
    from yequ.config import get_settings
    from yequ.runtime import CenterExecutionRuntime
    from yequ.services.artifact_service import ArtifactPayload, create_artifact

    payload = "\n".join(
        [
            "INFO boot",
            "WARN disk",
            "ERROR permission",
            "INFO recovered",
            "ERROR retry",
        ]
    ).encode("utf-8")
    artifact = await create_artifact(
        db_session,
        ArtifactPayload(
            data=payload,
            artifact_type="log",
            content_type="text/plain",
            title="journal-test.log",
            session_id="sess_artifact_read_text",
            node_id="linux-node-01",
        ),
        settings=get_settings(),
    )
    await db_session.commit()

    service = CenterExecutionRuntime(db_session)
    ranged = await service.execute(
        ExecuteToolCommand(
            function_name="artifact.read_text",
            input_data={
                "artifact_pattern": "*.log",
                "mode": "range",
                "line_start": -3,
                "line_end": -1,
            },
            session_id="sess_artifact_read_text",
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert ranged.status == "succeeded"
    assert ranged.output_data is not None
    assert ranged.output_data["artifact"]["artifact_id"] == artifact.artifact_id
    assert ranged.output_data["content"] == "ERROR permission\nINFO recovered\nERROR retry"
    assert ranged.output_data["read"]["line_selection"]["line_start"] == -3

    filtered = await service.execute(
        ExecuteToolCommand(
            function_name="artifact.read_text",
            input_data={
                "artifact_id": artifact.artifact_id,
                "mode": "full",
                "line_glob": "ERROR*",
            },
            session_id="sess_artifact_read_text",
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert filtered.status == "succeeded"
    assert filtered.output_data is not None
    assert filtered.output_data["content"] == "ERROR permission\nERROR retry"
    assert filtered.output_data["read"]["line_selection"]["line_glob"] == "ERROR*"


@pytest.mark.asyncio
async def test_agent_center_meta_groups_open_artifacts(db_session) -> None:
    from yequ.application.schemas import ExecuteToolCommand
    from yequ.runtime import CenterExecutionRuntime

    service = CenterExecutionRuntime(db_session)
    groups = await service.execute(
        ExecuteToolCommand(
            function_name="capability.groups",
            input_data={},
            session_id="sess_center_groups",
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert groups.status == "succeeded"
    assert groups.output_data is not None
    assert "artifacts" in {group["group_id"] for group in groups.output_data["groups"]}

    opened = await service.execute(
        ExecuteToolCommand(
            function_name="capability.group.open",
            input_data={"group_id": "artifacts", "projection": "invoke_ready"},
            session_id="sess_center_groups",
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert opened.status == "succeeded"
    assert opened.output_data is not None
    capabilities = opened.output_data["capabilities"]
    assert "artifact.read_text" in {capability["capability_ref"] for capability in capabilities}
    read_text = next(
        capability
        for capability in capabilities
        if capability["capability_ref"] == "artifact.read_text"
    )
    assert read_text["input_schema"]["properties"]["mode"]["enum"] == [
        "head",
        "tail",
        "range",
        "full",
    ]


@pytest.mark.asyncio
async def test_artifact_deploy_requires_preflight_before_job(db_session) -> None:
    from sqlalchemy import select

    from yequ.application.schemas import ExecuteToolCommand
    from yequ.models.job import Job
    from yequ.models.operation import Operation
    from yequ.runtime import CenterExecutionRuntime

    result = await CenterExecutionRuntime(db_session).execute(
        ExecuteToolCommand(
            function_name="artifact.deploy",
            input_data={
                "artifact_id": "id_test",
                "target_node_id": "linux-node-01",
                "output_path": "/tmp/yequ-transfer/a.bin",
                "mode": "overwrite",
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert result.status == "failed"
    assert result.error_code == "preflight_required"
    assert result.error_details["required_facts"] == ["artifact.deploy.preflight"]
    assert result.risk == "maintenance"
    assert result.effect == "write"
    job_count = await db_session.execute(select(Job))
    assert job_count.scalars().all() == []
    operation_count = await db_session.execute(select(Operation))
    assert operation_count.scalars().all() == []


@pytest.mark.asyncio
async def test_artifact_deploy_delegates_to_node_download_job(
    client: AsyncClient,
    db_session,
    provisioned_node,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import select

    from yequ.application.artifact_deploy import ArtifactDeployApplicationService
    from yequ.application.schemas import ExecuteToolCommand, ExecuteToolResult
    from yequ.config import get_settings
    from yequ.models.approval import ApprovalRequest
    from yequ.models.artifact import ArtifactDeployPreflight
    from yequ.models.job import Job
    from yequ.runtime import CenterExecutionRuntime
    from yequ.services.approval_service import approve_approval
    from yequ.services.artifact_service import ArtifactPayload, create_artifact

    node, token = provisioned_node
    hello = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.hello",
            node.node_id,
            payload={
                "daemon_version": "0.2.0",
                "platform": {"os": "linux", "arch": "x86_64"},
                "runtimes": [
                    {
                        "runtime_id": "sudo-limited",
                        "kind": "privileged",
                        "status": "online",
                        "privilege": "root",
                        "labels": ["linux", "artifact", "filesystem"],
                    }
                ],
            },
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert hello.status_code == 200, hello.text
    registered = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.register_capabilities",
            node.node_id,
            payload={
                "plugins": [
                    {
                        "plugin_id": "linux.artifact",
                        "plugin_version": "1.0",
                        "status": "loaded",
                        "functions": [
                            {
                                "name": "linux.artifact.download_file",
                                "description": "Download a Center artifact to Linux.",
                                "input_schema": {
                                    "type": "object",
                                    "properties": {
                                        "artifact_id": {"type": "string"},
                                        "output_path": {"type": "string"},
                                        "mode": {"type": "string"},
                                    },
                                    "required": ["artifact_id", "output_path"],
                                },
                                "output_schema": {"type": "object"},
                                "risk": "maintenance",
                                "effect": "write",
                                "timeout_sec": 300,
                                "execution_requirements": {
                                    "runtime_kind": "privileged",
                                    "labels": ["linux", "artifact"],
                                },
                                "resource_keys": ["node.filesystem", "center.artifact"],
                                "conflict_policy": "serialize",
                            }
                        ],
                        "signals": [],
                    }
                ]
            },
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert registered.status_code == 200, registered.text

    artifact = await create_artifact(
        db_session,
        ArtifactPayload(
            data=b"deploy me",
            artifact_type="file",
            content_type="application/octet-stream",
            title="deploy.bin",
        ),
        settings=get_settings(),
    )
    await db_session.commit()

    async def fake_target_stat(self, command):
        del self
        return ExecuteToolResult(
            status="succeeded",
            function_name="capability.invoke",
            target_node_id=command.target_node_id,
            output_data={
                "path": command.output_path,
                "found": False,
                "parent_exists": True,
                "writable": True,
                "free_bytes": 4096,
            },
        )

    monkeypatch.setattr(
        ArtifactDeployApplicationService,
        "_invoke_target_stat",
        fake_target_stat,
    )
    service = CenterExecutionRuntime(db_session)
    preflight_result = await service.execute(
        ExecuteToolCommand(
            function_name="artifact.deploy.preflight",
            input_data={
                "artifact_id": artifact.artifact_id,
                "target_node_id": node.node_id,
                "output_path": "/tmp/yequ-transfer/deploy.bin",
                "mode": "overwrite",
            },
            actor_type="agent",
            actor_id="test-agent",
            session_id="sess_artifact_deploy",
        )
    )
    assert preflight_result.status == "succeeded"
    assert preflight_result.output_data is not None
    preflight = preflight_result.output_data["preflight"]
    assert preflight["allowed"] is True
    assert preflight["preflight_id"].startswith("apf_")
    preflight_record_result = await db_session.execute(select(ArtifactDeployPreflight))
    preflight_record = preflight_record_result.scalar_one()
    assert preflight_record.preflight_id == preflight["preflight_id"]

    command = ExecuteToolCommand(
        function_name="artifact.deploy",
        input_data={
            "artifact_id": artifact.artifact_id,
            "target_node_id": node.node_id,
            "output_path": "/tmp/yequ-transfer/deploy.bin",
            "mode": "overwrite",
            "preflight_id": preflight["preflight_id"],
        },
        actor_type="agent",
        actor_id="test-agent",
        session_id="sess_artifact_deploy",
        wait_for_result=False,
    )
    approval_required = await service.execute(command)

    assert approval_required.status == "approval_required"
    assert approval_required.approval_id
    approval_result = await db_session.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.approval_id == approval_required.approval_id
        )
    )
    approval = approval_result.scalar_one()
    assert approval.function_name == "linux.artifact.download_file"
    await approve_approval(db_session, approval, approved_by="test-admin")

    result = await service.execute(
        ExecuteToolCommand(
            function_name="artifact.deploy",
            input_data={
                "artifact_id": artifact.artifact_id,
                "target_node_id": node.node_id,
                "output_path": "/tmp/yequ-transfer/deploy.bin",
                "mode": "overwrite",
                "preflight_id": preflight["preflight_id"],
            },
            actor_type="agent",
            actor_id="test-agent",
            session_id="sess_artifact_deploy",
            wait_for_result=False,
            approval_id=approval.approval_id,
        )
    )

    assert result.status == "waiting_operation"
    assert result.function_name == "artifact.deploy"
    assert result.operation_id
    assert result.output_data["artifact_deploy"] == {
        "artifact_id": artifact.artifact_id,
        "target_node_id": node.node_id,
        "output_path": "/tmp/yequ-transfer/deploy.bin",
        "mode": "overwrite",
        "preflight_id": preflight["preflight_id"],
        "node_capability_ref": "artifact.download_file",
    }

    job_result = await db_session.execute(select(Job).where(Job.job_id == result.job_id))
    job = job_result.scalar_one()
    assert job.node_id == node.node_id
    assert job.function_name == "linux.artifact.download_file"
    assert job.input_payload["artifact_id"] == artifact.artifact_id
    assert job.input_payload["output_path"] == "/tmp/yequ-transfer/deploy.bin"
    assert job.input_payload["mode"] == "overwrite"


@pytest.mark.asyncio
async def test_job_operation_status_projects_linked_artifacts(db_session) -> None:
    from yequ.config import get_settings
    from yequ.models.job import Job
    from yequ.services.artifact_service import ArtifactPayload, create_artifact
    from yequ.services.operation_service import OperationService

    job = Job(
        job_id="job_artifact_projection",
        invocation_id="inv_artifact_projection",
        node_id="winClient",
        function_name="windows.screen.capture",
        status="succeeded",
        timeout_sec=30,
        lease_sec=30,
        output={"artifacts": [{"artifact_id": "placeholder"}]},
    )
    db_session.add(job)
    await db_session.flush()

    artifact = await create_artifact(
        db_session,
        ArtifactPayload(
            data=b"fake screenshot bytes",
            artifact_type="image",
            content_type="image/png",
            title="screen.png",
            job_id=job.job_id,
            invocation_id=job.invocation_id,
            node_id=job.node_id,
        ),
        settings=get_settings(),
    )
    operation = await OperationService(db_session).create_for_job(
        job,
        actor_type="agent",
        actor_id="test-agent",
        session_id="sess_artifact_projection",
    )

    status = await OperationService(db_session).status(str(operation["operation_id"]))

    assert status["operation"]["status"] == "succeeded"
    artifacts = status["artifacts"]
    assert isinstance(artifacts, list)
    assert artifacts[0]["artifact_id"] == artifact.artifact_id
    assert status["operation"]["progress_message"] == "1 artifact(s) available"
