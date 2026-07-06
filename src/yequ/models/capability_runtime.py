"""Center capability runtime models.

These tables are the new Center-owned capability identity layer. Raw Node
capability names are implementation sources, not global semantic identity.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from yequ.models.base import Base, TimestampMixin, generate_uuid

if TYPE_CHECKING:
    from yequ.models.node import Node


class CapabilityDefinition(Base, TimestampMixin):
    """Semantic Center capability identity."""

    __tablename__ = "capability_definitions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    capability_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default=generate_uuid, index=True
    )
    canonical_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    capability_type: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    value_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    risk: Mapped[str | None] = mapped_column(String(32), nullable=True)
    effect: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="node")
    plane: Mapped[str] = mapped_column(String(32), nullable=False, default="node_runtime")
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dispatch_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="node_job")
    agent_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    invocation_surface: Mapped[str] = mapped_column(String(32), nullable=False, default="agent")
    workflow_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_contract: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    operation_contract: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    artifact_inputs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    artifact_outputs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    examples: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    sources: Mapped[list[CapabilitySource]] = relationship(
        "CapabilitySource",
        back_populates="definition",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "canonical_name",
            "capability_type",
            name="uq_capability_definitions_canonical_type",
        ),
    )


class CapabilitySource(Base, TimestampMixin):
    """Concrete Node/runtime implementation of a capability definition."""

    __tablename__ = "capability_sources"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    source_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default=generate_uuid, index=True
    )
    definition_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("capability_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_record_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plugin_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    plugin_version: Mapped[str] = mapped_column(String(32), nullable=False)
    registered_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    runtime_id: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    platform_os: Mapped[str | None] = mapped_column(String(32), nullable=True)
    platform_arch: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="loaded")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    unavailable_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_requirements: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    timeout_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resource_keys: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    conflict_policy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    hidden_input_fields: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    failure_modes: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    preflight_supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_progress: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_cancel: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_resume: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    progress_contract: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preconditions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    required_intent_slots: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    scope: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ttl_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    definition: Mapped[CapabilityDefinition] = relationship(
        "CapabilityDefinition",
        back_populates="sources",
    )
    node: Mapped[Node] = relationship("Node", back_populates="capability_sources")

    __table_args__ = (
        UniqueConstraint(
            "node_record_id",
            "plugin_id",
            "registered_name",
            "definition_id",
            name="uq_capability_sources_node_plugin_name_definition",
        ),
    )
