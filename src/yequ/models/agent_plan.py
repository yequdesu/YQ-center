"""Generic Agent Runtime Plan models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from yequ.models.base import Base, TimestampMixin, generate_uuid


class AgentPlan(Base, TimestampMixin):
    """Task-level plan owned by one AgentRun."""

    __tablename__ = "agent_plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    plan_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default=generate_uuid, index=True
    )
    session_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    turn_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    target_node_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    steps: Mapped[list[AgentPlanStep]] = relationship(
        "AgentPlanStep",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="AgentPlanStep.step_index",
    )


class AgentPlanStep(Base, TimestampMixin):
    """One coarse task step inside a generic Agent plan."""

    __tablename__ = "agent_plan_steps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    step_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default=generate_uuid, index=True
    )
    plan_record_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("agent_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="agent_task")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    operation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    plan: Mapped[AgentPlan] = relationship("AgentPlan", back_populates="steps")
