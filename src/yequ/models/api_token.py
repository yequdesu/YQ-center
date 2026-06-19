"""API Token model — for Admin and Agent scoped tokens."""

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from yequ.models.base import Base, generate_uuid


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    token_hash: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)  # "admin" or "agent"
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<ApiToken(scope={self.scope!r}, label={self.label!r})>"
