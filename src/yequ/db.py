"""Database engine and session factory.

Uses SQLAlchemy 2.0 async API. SQLite for dev, configurable to PostgreSQL.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yequ.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    echo=_settings.debug,
    connect_args={"check_same_thread": False} if "sqlite" in _settings.database_url else {},
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
