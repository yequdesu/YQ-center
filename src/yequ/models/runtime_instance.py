"""RuntimeInstance model -- platform-neutral execution contexts inside a Node."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from yequ.models.base import Base, TimestampMixin, generate_uuid

if TYPE_CHECKING:
    from yequ.models.node import Node


class RuntimeInstance(Base, TimestampMixin):
    __tablename__ = "runtime_instances"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    node_record_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    runtime_id: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="privileged")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="online")
    labels: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    owner: Mapped[str | None] = mapped_column(String(256), nullable=True)
    privilege: Mapped[str | None] = mapped_column(String(64), nullable=True)
    interactive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    node: Mapped[Node] = relationship("Node", back_populates="runtime_instances")

    def __repr__(self) -> str:
        return (
            f"<RuntimeInstance(runtime_id={self.runtime_id!r}, "
            f"kind={self.kind!r}, status={self.status!r})>"
        )
