"""Transfer session model for cross-node or node-center file movement."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from yequ.models.base import Base, TimestampMixin, generate_uuid


class TransferSession(Base, TimestampMixin):
    """Control-plane record for one multi-job transfer operation."""

    __tablename__ = "transfer_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    transfer_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default=generate_uuid, index=True
    )
    transport: Mapped[str] = mapped_column(String(32), nullable=False, default="croc")
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="node_to_node")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created", index=True)

    source_node_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    target_node_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    target_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_output_dir: Mapped[str | None] = mapped_column(Text, nullable=True)

    source_invocation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    target_invocation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    source_job_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    target_job_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    relay_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resume_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="resume")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_by: Mapped[str] = mapped_column(String(32), nullable=False, default="agent")
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
