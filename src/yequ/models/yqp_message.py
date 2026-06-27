"""Durable YQP message deduplication records."""

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from yequ.models.base import Base, generate_uuid


class YqpMessage(Base):
    """A recently seen YQP message_id within the replay protection window."""

    __tablename__ = "yqp_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    message_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    message_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
