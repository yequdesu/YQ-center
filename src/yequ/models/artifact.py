"""Artifact and blob metadata for large or multimodal outputs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from yequ.models.base import Base, TimestampMixin, generate_uuid


class Artifact(Base, TimestampMixin):
    """Logical artifact linked to an Agent session, Invocation, Job, or Node."""

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    artifact_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default=generate_uuid, index=True
    )
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    invocation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    node_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    capability_source_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="available")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    blobs: Mapped[list[ArtifactBlob]] = relationship(
        "ArtifactBlob",
        back_populates="artifact",
        cascade="all, delete-orphan",
    )


class ArtifactBlob(Base, TimestampMixin):
    """Physical storage reference for an artifact payload."""

    __tablename__ = "artifact_blobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    blob_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default=generate_uuid, index=True
    )
    artifact_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    artifact: Mapped[Artifact] = relationship("Artifact", back_populates="blobs")
