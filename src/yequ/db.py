"""Database engine and session factory.

Uses SQLAlchemy 2.0 async API. SQLite for dev, configurable to PostgreSQL.
SQLite busy_timeout enabled for concurrent write access.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yequ.config import get_settings

_settings = get_settings()

_sqlite_connect_args = {
    "check_same_thread": False,
    "timeout": 30,  # wait up to 30s for write lock instead of immediate error
}

engine = create_async_engine(
    _settings.database_url,
    echo=_settings.debug,
    connect_args=_sqlite_connect_args if "sqlite" in _settings.database_url else {},
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields an AsyncSession."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
