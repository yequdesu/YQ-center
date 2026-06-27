"""Current Signal state store."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from yequ.models.base import Base, generate_uuid


class SignalState(Base):
    """Latest accepted value for a Node Signal."""

    __tablename__ = "signal_states"
    __table_args__ = (
        UniqueConstraint("node_id", "signal_name", name="uq_signal_states_node_signal"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    node_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    capability_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    signal_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    value_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    scope: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ttl_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    freshness_status: Mapped[str] = mapped_column(String(16), nullable=False, default="fresh")
    quality: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<SignalState(node_id={self.node_id!r}, "
            f"signal_name={self.signal_name!r}, freshness={self.freshness_status!r})>"
        )
