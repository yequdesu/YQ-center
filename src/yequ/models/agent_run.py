"""Durable Agent run graph/checkpoint models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from yequ.models.base import Base, TimestampMixin, generate_uuid


class AgentRun(Base, TimestampMixin):
    """Durable execution record for one Agent run."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default=generate_uuid, index=True
    )
    session_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    turn_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created", index=True)
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    target_node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    steps: Mapped[list[AgentRunStep]] = relationship(
        "AgentRunStep",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="AgentRunStep.step_index",
    )
    events: Mapped[list[AgentRunEvent]] = relationship(
        "AgentRunEvent",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="AgentRunEvent.seq",
    )


class AgentRunStep(Base, TimestampMixin):
    """Ordered step inside an Agent run graph."""

    __tablename__ = "agent_run_steps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    step_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default=generate_uuid, index=True
    )
    run_record_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created", index=True)
    parent_step_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    capability_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    capability_source_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    node_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    invocation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    input_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    run: Mapped[AgentRun] = relationship("AgentRun", back_populates="steps")


class AgentRunEvent(Base, TimestampMixin):
    """Run-scoped durable event source for Agent runtime state."""

    __tablename__ = "agent_run_events"
    __table_args__ = (
        UniqueConstraint("run_record_id", "seq", name="uq_agent_run_events_run_seq"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    run_record_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    turn_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    step_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    plan_step_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)

    run: Mapped[AgentRun] = relationship("AgentRun", back_populates="events")
