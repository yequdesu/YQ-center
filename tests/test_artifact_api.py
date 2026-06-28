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
