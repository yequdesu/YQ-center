"""pytest fixtures for YeQu Center tests."""

import os
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import yequ.models.api_token  # noqa: F401
import yequ.models.agent_message  # noqa: F401
import yequ.models.agent_turn  # noqa: F401
import yequ.models.approval  # noqa: F401
import yequ.models.capability  # noqa: F401
import yequ.models.invocation  # noqa: F401
import yequ.models.job  # noqa: F401
import yequ.models.maintenance_plan  # noqa: F401

# Import all models so Base.metadata is populated
import yequ.models.node  # noqa: F401
import yequ.models.resource_lock  # noqa: F401
import yequ.models.runtime_instance  # noqa: F401
import yequ.models.session  # noqa: F401
import yequ.models.timeline  # noqa: F401
from yequ.api.app import create_app
from yequ.config import Settings
from yequ.models.base import Base

TEST_DB_PATH = "test_yequ.db"


@pytest.fixture(autouse=True)
def override_settings(monkeypatch, db_engine):
    """Override settings for test environment — uses db_engine's single engine."""
    import yequ.api.deps
    import yequ.db

    test_settings = Settings(
        database_url=f"sqlite+aiosqlite:///{TEST_DB_PATH}",
        debug=True,
        test_mode=True,
        log_level="WARNING",
        require_admin_auth=False,
    )
    monkeypatch.setattr("yequ.config._settings", test_settings)
    monkeypatch.setattr(yequ.db, "_settings", test_settings)
    monkeypatch.setattr(yequ.db, "engine", db_engine)
    monkeypatch.setattr(yequ.db, "async_session_factory", async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False,
    ))
    monkeypatch.setattr(yequ.api.deps, "_get_settings", lambda: test_settings)
    return test_settings


@pytest_asyncio.fixture
async def db_engine():
    """Create a test database engine with fresh tables."""
    db_url = f"sqlite+aiosqlite:///{TEST_DB_PATH}?_journal_mode=WAL"
    engine = create_async_engine(db_url, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

    # Clean up SQLite files. Windows may hold the handle for a short moment
    # after async engine disposal, especially when WAL files were created.
    for suffix in ("", "-wal", "-shm"):
        path = f"{TEST_DB_PATH}{suffix}"
        if not os.path.exists(path):
            continue
        for attempt in range(10):
            try:
                os.remove(path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.1)


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
