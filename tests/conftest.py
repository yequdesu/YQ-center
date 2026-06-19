"""pytest fixtures for YeQu Center tests."""

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yequ.api.app import create_app
from yequ.config import Settings
from yequ.models.base import Base

# Import all models so Base.metadata is populated
import yequ.models.node  # noqa: F401
import yequ.models.capability  # noqa: F401
import yequ.models.invocation  # noqa: F401
import yequ.models.job  # noqa: F401
import yequ.models.timeline  # noqa: F401
import yequ.models.session  # noqa: F401

TEST_DB_PATH = "test_yequ.db"


@pytest.fixture(autouse=True)
def override_settings(monkeypatch):
    """Override settings for test environment."""
    test_settings = Settings(
        database_url=f"sqlite+aiosqlite:///{TEST_DB_PATH}",
        debug=True,
        log_level="WARNING",
    )
    # Patch the singleton cache so get_settings() returns test settings
    monkeypatch.setattr("yequ.config._settings", test_settings)
    # Patch the module-level _settings in yequ.db (which is the result of get_settings())
    import yequ.db
    monkeypatch.setattr(yequ.db, "_settings", test_settings)
    # Patch the deps module too — it imports the function reference
    import yequ.api.deps
    monkeypatch.setattr(yequ.api.deps, "_get_settings", lambda: test_settings)
    return test_settings


@pytest_asyncio.fixture
async def db_engine():
    """Create a test database engine with fresh tables."""
    db_url = f"sqlite+aiosqlite:///{TEST_DB_PATH}"
    engine = create_async_engine(db_url, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

    # Clean up test DB file
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(db_engine) -> AsyncGenerator[AsyncClient, None]:
    """Create a test HTTP client."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
