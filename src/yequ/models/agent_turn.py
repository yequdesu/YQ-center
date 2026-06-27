"""AgentTurn models -- durable state source for one user-visible agent turn."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from yequ.models.base import Base, generate_uuid


class AgentTurn(Base):
    __tablename__ = "agent_turns"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    turn_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    target_node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received", index=True)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)


class AgentTurnEvent(Base):
    __tablename__ = "agent_turn_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    turn_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
