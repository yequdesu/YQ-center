"""FastAPI dependency injection."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from yequ.config import Settings
from yequ.config import get_settings as _get_settings
from yequ.db import get_db as _get_db


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency: yields an async database session."""
    async for session in _get_db():
        yield session


def get_settings() -> Settings:
    """Dependency: provides application settings."""
    return _get_settings()
