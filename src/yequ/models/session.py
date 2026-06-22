"""Session model — interactive context for a user or agent."""

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from yequ.models.base import Base, TimestampMixin, generate_uuid


class Session(Base, TimestampMixin):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    session_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)  # user or agent
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    metadata_: Mapped[dict[str, object] | None] = mapped_column("metadata", JSON, nullable=True)

    def __repr__(self) -> str:
        return f"<Session(session_id={self.session_id!r}, status={self.status!r})>"
