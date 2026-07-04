"""Persistent YCR context references and retrieval chunks."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from yequ.models.base import Base, TimestampMixin, generate_uuid


class YcrContextRef(Base, TimestampMixin):
    """Durable context reference metadata."""

    __tablename__ = "ycr_context_refs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    ref_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    ref_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_path: Mapped[str] = mapped_column(Text, nullable=False, default="$")
    source_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    projection_policy: Mapped[str] = mapped_column(String(128), nullable=False)
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    trust_level: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="node_reported_fact",
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_json: Mapped[dict[str, Any] | list[Any] | str | int | float | bool | None] = (
        mapped_column(JSON, nullable=True)
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class YcrContextChunk(Base, TimestampMixin):
    """Searchable chunk derived from a context ref."""

    __tablename__ = "ycr_context_chunks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    chunk_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    ref_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ycr_context_refs.ref_id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    path: Mapped[str] = mapped_column(Text, nullable=False, default="$")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trust_level: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="node_reported_fact",
    )
    embedding_json: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)


class YcrContextLedger(Base, TimestampMixin):
    """Projection and retrieval ledger for YCR observability."""

    __tablename__ = "ycr_context_ledgers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    ledger_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    ref_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    raw_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    projected_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_estimated_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    projected_estimated_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    projection_policy: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
