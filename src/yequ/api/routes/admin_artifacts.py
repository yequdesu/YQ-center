"""Admin endpoints for Center artifacts."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.api.deps import get_admin_token, get_db
from yequ.config import get_settings
from yequ.services.artifact_service import (
    ArtifactPayload,
    artifact_to_dict,
    create_artifact,
    get_artifact,
    list_artifacts,
    resolve_download,
)
from yequ.shared_types import JsonObject

router = APIRouter(prefix="/admin/artifacts", tags=["admin-artifacts"])


@router.get("")
async def list_admin_artifacts(
    session_id: str | None = None,
    invocation_id: str | None = None,
    job_id: str | None = None,
    node_id: str | None = None,
    artifact_type: str | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    artifacts = await list_artifacts(
        db,
        session_id=session_id,
        invocation_id=invocation_id,
        job_id=job_id,
        node_id=node_id,
        artifact_type=artifact_type,
        limit=limit,
    )
    return {"artifacts": [artifact_to_dict(item) for item in artifacts]}


@router.post("", status_code=201)
async def upload_admin_artifact(
    file: UploadFile = File(...),
    artifact_type: str = Form("file"),
    title: str | None = Form(None),
    session_id: str | None = Form(None),
    invocation_id: str | None = Form(None),
    job_id: str | None = Form(None),
    node_id: str | None = Form(None),
    capability_source_id: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    data = await file.read()
    try:
        artifact = await create_artifact(
            db,
            ArtifactPayload(
                data=data,
                artifact_type=artifact_type,
                content_type=file.content_type,
                title=title or file.filename,
                metadata={"filename": file.filename} if file.filename else None,
                session_id=session_id,
                invocation_id=invocation_id,
                job_id=job_id,
                node_id=node_id,
                capability_source_id=capability_source_id,
            ),
            settings=get_settings(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(artifact, attribute_names=["blobs"])
    return {"artifact": artifact_to_dict(artifact)}


@router.get("/{artifact_id}")
async def get_admin_artifact(
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    try:
        artifact = await get_artifact(db, artifact_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"artifact": artifact_to_dict(artifact)}


@router.get("/{artifact_id}/download")
async def download_admin_artifact(
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> FileResponse:
    try:
        download = await resolve_download(db, artifact_id, settings=get_settings())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    filename = (
        download.artifact.title
        or download.artifact.artifact_id
    )
    return FileResponse(
        download.path,
        media_type=download.blob.content_type or "application/octet-stream",
        filename=filename,
    )
