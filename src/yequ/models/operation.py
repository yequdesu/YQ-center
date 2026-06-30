"""Operation Bus models for Center Execution Runtime v2."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from yequ.models.base import Base, TimestampMixin, generate_uuid


class Operation(Base, TimestampMixin):
    """Waitable runtime shell for a long or composite Center operation."""

    __tablename__ = "operations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    operation_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default=generate_uuid, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created", index=True)
    ref_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    ref_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False, default="agent")
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    wait_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    resume_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    cancel_supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class OperationEvent(Base):
    """Runtime event stream for Operation projection and future dispatch."""

    __tablename__ = "operation_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    event_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default=generate_uuid, index=True
    )
    operation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("operations.operation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

