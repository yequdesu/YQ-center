"""Job model — represents an actual execution task dispatched to a Node."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from yequ.models.base import Base, TimestampMixin, generate_uuid


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    job_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    invocation_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    function_name: Mapped[str] = mapped_column(String(256), nullable=False)
    input_payload: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="created")
    timeout_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    lease_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    approval_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resource_keys: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    output: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    progress_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    progress_message: Mapped[str | None] = mapped_column(String(512), nullable=True)

    def __repr__(self) -> str:
        return f"<Job(job_id={self.job_id!r}, status={self.status!r}, node={self.node_id!r})>"
