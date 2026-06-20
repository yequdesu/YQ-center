"""ResourceLock model — prevents concurrent conflicting writes."""

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from yequ.models.base import Base, generate_uuid


class ResourceLock(Base):
    __tablename__ = "resource_locks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    lock_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    resource_key: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    invocation_id: Mapped[str] = mapped_column(String(32), nullable=False)
    node_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="held")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<ResourceLock(resource={self.resource_key!r}, status={self.status!r})>"
