"""SQLAlchemy declarative base and common mixins."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def generate_uuid() -> str:
    """Generate a UUID string with prefix."""
    return f"id_{uuid.uuid4().hex[:16]}"


class Base(DeclarativeBase):
    """Declarative base for all models."""

    pass


class TimestampMixin:
    """Mixin for created_at / updated_at columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
