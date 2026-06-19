"""Node model — represents a connected device/execution environment."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from yequ.models.base import Base, TimestampMixin, generate_uuid
from yequ.protocol import NodeStatus

if TYPE_CHECKING:
    from yequ.models.capability import Capability


class Node(Base, TimestampMixin):
    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    node_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    node_name: Mapped[str] = mapped_column(String(256), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="compute")
    locality: Mapped[str] = mapped_column(String(16), nullable=False, default="local")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=NodeStatus.PROVISIONED)
    daemon_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    platform_os: Mapped[str | None] = mapped_column(String(32), nullable=True)
    platform_arch: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_interval_sec: Mapped[int | None] = mapped_column(nullable=True)
    job_delivery_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)

    capabilities: Mapped[list[Capability]] = relationship(
        "Capability", back_populates="node", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Node(node_id={self.node_id!r}, status={self.status!r})>"
