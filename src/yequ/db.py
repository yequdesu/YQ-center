"""Database engine and session factory.

Uses SQLAlchemy 2.0 async API. SQLite for dev, configurable to PostgreSQL.
SQLite busy_timeout enabled for concurrent write access.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from yequ.config import get_settings

_settings = get_settings()
_is_sqlite = "sqlite" in _settings.database_url

_sqlite_connect_args = {
    "check_same_thread": False,
    "timeout": 30,  # wait up to 30s for write lock instead of immediate error
}

_engine_kwargs: dict[str, object] = {
    "echo": _settings.debug,
    "connect_args": _sqlite_connect_args if _is_sqlite else {},
}

if _is_sqlite:
    _engine_kwargs["poolclass"] = NullPool
else:
    _server_settings = {
        "application_name": _settings.postgres_application_name,
        "idle_in_transaction_session_timeout": str(
            _settings.postgres_idle_in_transaction_session_timeout_ms
        ),
        "statement_timeout": str(_settings.postgres_statement_timeout_ms),
        "lock_timeout": str(_settings.postgres_lock_timeout_ms),
    }
    _engine_kwargs.update(
        {
            "connect_args": {"server_settings": _server_settings},
            "pool_size": _settings.database_pool_size,
            "max_overflow": _settings.database_max_overflow,
            "pool_timeout": _settings.database_pool_timeout_sec,
            "pool_recycle": _settings.database_pool_recycle_sec,
            "pool_pre_ping": True,
        }
    )

engine = create_async_engine(_settings.database_url, **_engine_kwargs)

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
