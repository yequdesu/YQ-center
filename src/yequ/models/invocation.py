"""Invocation model — represents a semantic call intent by an Actor."""

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from yequ.models.base import Base, TimestampMixin, generate_uuid


class Invocation(Base, TimestampMixin):
    __tablename__ = "invocations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    invocation_id: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)  # user, agent, system
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    function_name: Mapped[str] = mapped_column(String(256), nullable=False)
    input_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    target_node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    call_path: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    max_depth: Mapped[int | None] = mapped_column(nullable=True)
    max_steps: Mapped[int | None] = mapped_column(nullable=True)
    max_total_duration_sec: Mapped[int | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<Invocation(invocation_id={self.invocation_id!r}, status={self.status!r})>"
