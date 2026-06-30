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
