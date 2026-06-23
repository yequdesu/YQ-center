"""Capability model — stores Function and Signal manifests for each Node."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from yequ.models.base import Base, TimestampMixin, generate_uuid

if TYPE_CHECKING:
    from yequ.models.node import Node


class Capability(Base, TimestampMixin):
    __tablename__ = "capabilities"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    node_record_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plugin_id: Mapped[str] = mapped_column(String(256), nullable=False)
    plugin_version: Mapped[str] = mapped_column(String(32), nullable=False)
    capability_type: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # "function" or "signal"
    name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="loaded")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_visible_name: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Function-specific fields
    input_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    risk: Mapped[str | None] = mapped_column(String(16), nullable=True)
    effect: Mapped[str | None] = mapped_column(String(16), nullable=True)
    timeout_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resource_keys: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    conflict_policy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    execution_context: Mapped[str | None] = mapped_column(String(32), nullable=True)
    execution_requirements: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    hidden_input_fields: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    examples: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    failure_modes: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    preflight_supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Signal-specific fields
    scope: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ttl_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    value_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Snapshot tracking
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    node: Mapped[Node] = relationship("Node", back_populates="capabilities")

    def __repr__(self) -> str:
        return (
            f"<Capability(name={self.name!r}, type={self.capability_type!r},"
            f" node={self.node_record_id!r})>"
        )
