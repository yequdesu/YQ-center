"""Transfer session model for cross-node or node-center file movement."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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


class TransferAttempt(Base, TimestampMixin):
    """One Center-created execution attempt for a TransferSession."""

    __tablename__ = "transfer_attempts"
    __table_args__ = (
        UniqueConstraint(
            "transfer_session_id", "attempt", name="uq_transfer_attempt_session_attempt"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    transfer_session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("transfer_sessions.id"), nullable=False, index=True
    )
    transfer_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)

    source_job_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    target_job_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    relay_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    relay_url_masked: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created", index=True)
    resumable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class TransferPreflight(Base, TimestampMixin):
    """Persisted transfer preflight facts bound to one explicit intent."""

    __tablename__ = "transfer_preflights"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    preflight_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default=generate_uuid, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    intent_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    source_node_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    target_node_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    target_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_output_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="resume")

    source_fact: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    target_fact: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    failed_preconditions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)

    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
