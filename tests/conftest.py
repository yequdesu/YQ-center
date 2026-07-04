"""pytest fixtures for YeQu Center tests."""

import gc
import os
import sys
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import yequ.models.agent_message  # noqa: F401
import yequ.models.agent_run  # noqa: F401
import yequ.models.agent_turn  # noqa: F401
import yequ.models.api_token  # noqa: F401
import yequ.models.approval  # noqa: F401
import yequ.models.artifact  # noqa: F401
import yequ.models.capability  # noqa: F401
import yequ.models.capability_runtime  # noqa: F401
import yequ.models.invocation  # noqa: F401
import yequ.models.job  # noqa: F401
import yequ.models.maintenance_plan  # noqa: F401

# Import all models so Base.metadata is populated
import yequ.models.node  # noqa: F401
import yequ.models.operation  # noqa: F401
import yequ.models.resource_lock  # noqa: F401
import yequ.models.runtime_instance  # noqa: F401
import yequ.models.session  # noqa: F401
import yequ.models.signal_state  # noqa: F401
import yequ.models.timeline  # noqa: F401
import yequ.models.transfer  # noqa: F401
import yequ.models.ycr  # noqa: F401
import yequ.models.yqp_message  # noqa: F401
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
        artifact_storage_dir="test_artifacts",
        ycr_embedding_provider="local_hash",
        ycr_embedding_model="local-hash-v1",
    )
    monkeypatch.setattr("yequ.config._settings", test_settings)
    monkeypatch.setattr(yequ.db, "_settings", test_settings)
    monkeypatch.setattr(yequ.db, "engine", db_engine)
    monkeypatch.setattr(
        yequ.db,
        "async_session_factory",
        async_sessionmaker(
            db_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        ),
    )
    monkeypatch.setattr(yequ.api.deps, "_get_settings", lambda: test_settings)

    class TestYcrClient:
        async def status(self):
            return {"status": "ready", "mode": "test"}

        async def build_turn(self, **payload):
            from yequ.ycr.budget import budget_profile_from_settings
            from yequ.ycr.context_packet import build_agent_context_packet

            return build_agent_context_packet(
                session_id=str(payload["session_id"]),
                actor_id=str(payload.get("actor_id") or ""),
                provider=str(payload["provider"]),
                model=str(payload.get("model") or ""),
                messages=list(payload.get("messages") or []),
                available_functions=list(payload.get("available_functions") or []),
                capability_context=dict(payload.get("capability_context") or {}),
                budget=budget_profile_from_settings(test_settings),
                step=int(payload.get("step") or 1),
            )

        async def project_tool_observation(self, **payload):
            from yequ.ycr import projection
            from yequ.ycr.budget import budget_profile_from_settings

            return projection.project_tool_observation(
                name=str(payload.get("name") or ""),
                call_id=str(payload.get("call_id") or ""),
                status=str(payload.get("status") or "succeeded"),
                result=payload.get("result"),
                target_node_id=payload.get("target_node_id"),
                budget=budget_profile_from_settings(test_settings),
            )

        async def project_context_blocks(self, blocks):
            from yequ.ycr import projection
            from yequ.ycr.budget import budget_profile_from_settings

            return projection.project_context_blocks(
                blocks,
                budget=budget_profile_from_settings(test_settings),
            )

        async def prompt_with_context(self, prompt, context_blocks):
            from yequ.ycr import projection
            from yequ.ycr.budget import budget_profile_from_settings

            return projection.prompt_with_projected_context(
                prompt,
                context_blocks,
                budget=budget_profile_from_settings(test_settings),
            )

        async def operation_resume_prompt(self, observation, *, user_message=""):
            from yequ.ycr import projection
            from yequ.ycr.budget import budget_profile_from_settings

            return projection.operation_resume_prompt(
                observation,
                user_message=user_message,
                budget=budget_profile_from_settings(test_settings),
            )

        async def agent_run_resume_prompt(self, run_projection, *, operation_observation):
            from yequ.ycr import projection
            from yequ.ycr.budget import budget_profile_from_settings

            return projection.agent_run_resume_prompt(
                run_projection,
                operation_observation=operation_observation,
                budget=budget_profile_from_settings(test_settings),
            )

        async def tool_search(
            self,
            *,
            query=None,
            node_id=None,
            platform_os=None,
            limit=10,
            filters=None,
        ):
            from yequ.ycr.capability_gateway import search_capability_registry

            async with yequ.db.async_session_factory() as session:
                return await search_capability_registry(
                    session,
                    query=query,
                    node_id=node_id,
                    platform_os=platform_os,
                    filters=filters or {},
                    limit=limit,
                )

        async def tool_describe(
            self,
            *,
            capability_ref,
            node_id=None,
            sections=None,
            projection="invoke_ready",
        ):
            from yequ.ycr.capability_gateway import describe_capability_registry

            async with yequ.db.async_session_factory() as session:
                return await describe_capability_registry(
                    session,
                    capability_ref=capability_ref,
                    node_id=node_id,
                    sections=sections or [],
                    projection=projection,
                )

        async def inspect(self, ref_id):
            from yequ.ycr.ref_store import inspect_ref

            async with yequ.db.async_session_factory() as session:
                return await inspect_ref(session, ref_id)

        async def expand(self, ref_id, *, path="$", limit=20):
            from yequ.ycr.ref_store import expand_ref

            async with yequ.db.async_session_factory() as session:
                return await expand_ref(session, ref_id, path=path, limit=limit)

        async def tail(self, ref_id, *, path="$", lines=40):
            from yequ.ycr.ref_store import tail_ref

            async with yequ.db.async_session_factory() as session:
                return await tail_ref(session, ref_id, path=path, lines=lines)

        async def schema(self, ref_id, *, path="$"):
            from yequ.ycr.ref_store import schema_ref

            async with yequ.db.async_session_factory() as session:
                return await schema_ref(session, ref_id, path=path)

        async def search(self, ref_id, *, query, limit=10):
            from yequ.ycr.ref_store import search_ref

            async with yequ.db.async_session_factory() as session:
                return await search_ref(session, ref_id, query=query, limit=limit)

        async def rehydrate(self, ref_id):
            from yequ.ycr.ref_store import rehydrate_ref

            async with yequ.db.async_session_factory() as session:
                return await rehydrate_ref(session, ref_id)

    fake_ycr_client = TestYcrClient()
    import yequ.ycr.client

    monkeypatch.setattr(yequ.ycr.client, "get_ycr_client", lambda: fake_ycr_client)
    agent_stream_module = sys.modules.get("yequ.agent.agent_stream")
    if agent_stream_module is not None:
        monkeypatch.setattr(agent_stream_module, "get_ycr_client", lambda: fake_ycr_client)
    return test_settings


@pytest_asyncio.fixture
async def db_engine():
    """Create a test database engine with fresh tables."""
    db_url = f"sqlite+aiosqlite:///{TEST_DB_PATH}?_journal_mode=WAL"
    engine = create_async_engine(db_url, echo=False, poolclass=NullPool)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    gc.collect()

    # Clean up SQLite files. Windows may hold the handle for a short moment
    # after async engine disposal, especially when WAL files were created.
    for suffix in ("", "-wal", "-shm"):
        path = f"{TEST_DB_PATH}{suffix}"
        if not os.path.exists(path):
            continue
        for attempt in range(30):
            try:
                os.remove(path)
                break
            except PermissionError:
                if attempt == 29:
                    raise
                time.sleep(0.1)
    if os.path.exists("test_artifacts"):
        import shutil

        shutil.rmtree("test_artifacts")


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
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.hello",
            node.node_id,
            payload={"daemon_version": "0.1.0"},
        ),
        headers=auth,
    )
    return node, token
