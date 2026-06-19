"""pytest fixtures for YeQu Center tests."""

import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import yequ.models.api_token  # noqa: F401
import yequ.models.capability  # noqa: F401
import yequ.models.invocation  # noqa: F401
import yequ.models.job  # noqa: F401

# Import all models so Base.metadata is populated
import yequ.models.node  # noqa: F401
import yequ.models.session  # noqa: F401
import yequ.models.timeline  # noqa: F401
from yequ.api.app import create_app
from yequ.config import Settings
from yequ.models.base import Base

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
    # Recreate the engine with test settings so the app's get_db() uses the test DB
    engine = create_async_engine(
        test_settings.database_url,
        echo=test_settings.debug,
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(yequ.db, "engine", engine)
    # Also rebuild async_session_factory using the test engine
    monkeypatch.setattr(yequ.db, "async_session_factory", async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    ))
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


def make_yqp_envelope(
    message_type: str,
    node_id: str,
    payload: dict | None = None,
    message_id: str | None = None,
) -> dict:
    """Build a YQP envelope dict for testing."""
    return {
        "yqp_version": "0.1",
        "message_id": message_id or f"msg_{uuid.uuid4().hex[:16]}",
        "message_type": message_type,
        "trace_id": f"tr_{uuid.uuid4().hex[:16]}",
        "node_id": node_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "payload": payload or {},
    }


@pytest_asyncio.fixture
async def provisioned_node(db_session):
    """Provision a test node and return (node, token) for YQP tests."""
    from yequ.models.node import Node
    from yequ.services.node_auth import hash_token

    token = "test-token-" + uuid.uuid4().hex[:8]
    node = Node(
        node_id=f"test-node-{uuid.uuid4().hex[:8]}",
        node_name="YQP Test Node",
        token_hash=hash_token(token),
        status="provisioned",
    )
    db_session.add(node)
    await db_session.commit()
    return node, token


@pytest_asyncio.fixture
async def node_with_hello(client, provisioned_node):
    """Provision + hello, return (node, token)."""
    from tests.conftest import make_yqp_envelope

    node, token = provisioned_node
    auth = {"Authorization": f"Bearer {token}"}
    await client.post("/yqp/", json=make_yqp_envelope(
        "node.hello", node.node_id, payload={"daemon_version": "0.1.0"},
    ), headers=auth)
    return node, token
