"""Artifact storage service for Center-owned blobs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from yequ.config import PROJECT_ROOT, Settings, get_settings
from yequ.models.artifact import Artifact, ArtifactBlob


@dataclass(frozen=True, slots=True)
class ArtifactPayload:
    """Bytes and metadata for creating an artifact."""

    data: bytes
    artifact_type: str
    content_type: str | None = None
    title: str | None = None
    summary: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    session_id: str | None = None
    invocation_id: str | None = None
    job_id: str | None = None
    node_id: str | None = None
    capability_source_id: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactDownload:
    """Resolved artifact blob for download."""

    artifact: Artifact
    blob: ArtifactBlob
    path: Path


async def create_artifact(
    db: AsyncSession,
    payload: ArtifactPayload,
    *,
    settings: Settings | None = None,
) -> Artifact:
    """Persist bytes to local storage and create Artifact/ArtifactBlob rows."""

    settings = settings or get_settings()
    max_bytes = settings.artifact_max_upload_bytes
    if len(payload.data) > max_bytes:
        raise ValueError(f"artifact exceeds max upload size: {len(payload.data)} > {max_bytes}")

    sha256 = hashlib.sha256(payload.data).hexdigest()
    artifact = Artifact(
        artifact_type=payload.artifact_type,
        title=payload.title,
        summary=payload.summary,
        metadata_json=payload.metadata,
        session_id=payload.session_id,
        invocation_id=payload.invocation_id,
        job_id=payload.job_id,
        node_id=payload.node_id,
        capability_source_id=payload.capability_source_id,
        content_type=payload.content_type,
        size_bytes=len(payload.data),
        sha256=sha256,
        status="available",
    )
    db.add(artifact)
    await db.flush()

    storage_key = _storage_key(artifact.artifact_id, sha256)
    path = _storage_path(storage_key, settings=settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload.data)

    blob = ArtifactBlob(
        artifact_id=artifact.id,
        storage_backend="local",
        storage_key=storage_key,
        content_type=payload.content_type,
        size_bytes=len(payload.data),
        sha256=sha256,
    )
    db.add(blob)
    await db.flush()
    return artifact


async def list_artifacts(
    db: AsyncSession,
    *,
    session_id: str | None = None,
    invocation_id: str | None = None,
    job_id: str | None = None,
    node_id: str | None = None,
    artifact_type: str | None = None,
    limit: int = 100,
) -> list[Artifact]:
    stmt = (
        select(Artifact)
        .options(selectinload(Artifact.blobs))
        .order_by(Artifact.created_at.desc())
    )
    if session_id:
        stmt = stmt.where(Artifact.session_id == session_id)
    if invocation_id:
        stmt = stmt.where(Artifact.invocation_id == invocation_id)
    if job_id:
        stmt = stmt.where(Artifact.job_id == job_id)
    if node_id:
        stmt = stmt.where(Artifact.node_id == node_id)
    if artifact_type:
        stmt = stmt.where(Artifact.artifact_type == artifact_type)
    stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_artifact(
    db: AsyncSession,
    artifact_id: str,
) -> Artifact:
    result = await db.execute(
        select(Artifact)
        .options(selectinload(Artifact.blobs))
        .where(Artifact.artifact_id == artifact_id)
    )
    artifact = result.scalar_one_or_none()
    if artifact is None:
        raise ValueError(f"Artifact {artifact_id!r} not found")
    return artifact


async def resolve_download(
    db: AsyncSession,
    artifact_id: str,
    *,
    settings: Settings | None = None,
) -> ArtifactDownload:
    artifact = await get_artifact(db, artifact_id)
    if artifact.status != "available":
        raise ValueError(f"Artifact {artifact_id!r} is not available")
    if not artifact.blobs:
        raise ValueError(f"Artifact {artifact_id!r} has no blob")
    blob = artifact.blobs[0]
    if blob.storage_backend != "local":
        raise ValueError(f"Unsupported storage backend {blob.storage_backend!r}")
    path = _storage_path(blob.storage_key, settings=settings or get_settings())
    if not path.exists():
        raise ValueError(f"Artifact blob for {artifact_id!r} is missing")
    return ArtifactDownload(artifact=artifact, blob=blob, path=path)


def artifact_to_dict(artifact: Artifact) -> dict[str, object]:
    blob = artifact.blobs[0] if artifact.blobs else None
    return {
        "artifact_id": artifact.artifact_id,
        "artifact_type": artifact.artifact_type,
        "title": artifact.title,
        "summary": artifact.summary,
        "metadata": artifact.metadata_json,
        "session_id": artifact.session_id,
        "invocation_id": artifact.invocation_id,
        "job_id": artifact.job_id,
        "node_id": artifact.node_id,
        "capability_source_id": artifact.capability_source_id,
        "content_type": artifact.content_type,
        "size_bytes": artifact.size_bytes,
        "sha256": artifact.sha256,
        "status": artifact.status,
        "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
        "blob_id": blob.blob_id if blob else None,
        "download_url": f"/admin/artifacts/{artifact.artifact_id}/download",
    }


def _storage_key(artifact_id: str, sha256: str) -> str:
    return f"{artifact_id[:12]}/{sha256}"


def _storage_path(storage_key: str, *, settings: Settings) -> Path:
    root = Path(settings.artifact_storage_dir)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    root = root.resolve()
    path = (root / storage_key).resolve()
    if root not in path.parents and path != root:
        raise ValueError("artifact storage path escaped storage root")
    return path
