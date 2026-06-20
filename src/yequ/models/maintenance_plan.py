"""MaintenancePlan — orchestrates multi-step maintenance operations."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from yequ.models.base import Base, generate_uuid


class MaintenancePlan(Base):
    __tablename__ = "maintenance_plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    plan_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_node_id: Mapped[str] = mapped_column(String(128), nullable=False)
    risk: Mapped[str] = mapped_column(String(16), nullable=False, default="maintenance")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    approval_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resource_keys: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=list)
    max_total_duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rollback_strategy: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<MaintenancePlan(plan_id={self.plan_id!r}, status={self.status!r})>"


class MaintenanceStep(Base):
    __tablename__ = "maintenance_steps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    step_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    seq: Mapped[int] = mapped_column("order_", Integer, nullable=False)
    function_name: Mapped[str] = mapped_column(String(256), nullable=False)
    input_data: Mapped[dict | None] = mapped_column("input_", JSON, nullable=True)
    depends_on: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    continue_on_failure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    timeout_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    resource_keys: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    expected_result_schema: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    invocation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<MaintenanceStep(step_id={self.step_id!r}, seq={self.seq}, "
            f"status={self.status!r})>"
        )


class MaintenanceRun(Base):
    __tablename__ = "maintenance_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_step_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    def __repr__(self) -> str:
        return f"<MaintenanceRun(run_id={self.run_id!r}, status={self.status!r})>"


class MaintenanceArtifact(Base):
    __tablename__ = "maintenance_artifacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    artifact_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    step_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    storage_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<MaintenanceArtifact(name={self.name!r}, type={self.artifact_type!r})>"


class RollbackHint(Base):
    __tablename__ = "rollback_hints"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    hint_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    step_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False)
    function_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    input_data: Mapped[dict | None] = mapped_column("input_", JSON, nullable=True)
    risk: Mapped[str] = mapped_column(String(16), nullable=False, default="maintenance")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<RollbackHint(hint_id={self.hint_id!r}, reason={self.reason[:50]!r})>"
