"""Tests for Capability Resolver — node selection and error handling."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_resolve_online_node_succeeds(client: AsyncClient):
    """Specified online node with function → success."""
    from datetime import UTC, datetime

    from yequ.api.deps import get_db
    from yequ.models.capability import Capability
    from yequ.models.node import Node
    from yequ.services.capability_resolver import resolve_function
    from yequ.services.node_auth import hash_token

    db_gen = get_db()
    db = await db_gen.__anext__()
    try:
        node = Node(
            node_id="resolver-online-node",
            node_name="Resolver Online",
            token_hash=hash_token("resolver-token-1"),
            status="online",
            last_heartbeat_at=datetime.now(UTC),
        )
        db.add(node)
        await db.flush()

        cap = Capability(
            node_record_id=node.id,
            plugin_id="test.resolver",
            plugin_version="1.0",
            capability_type="function",
            name="system.resolver.test",
            status="loaded",
            risk="safe",
            effect="read",
            is_active=True,
        )
        db.add(cap)
        await db.commit()

        result = await resolve_function(
            db,
            "system.resolver.test",
            target_node_id="resolver-online-node",
        )
        assert result.available is True
        assert result.node_id == "resolver-online-node"
        assert result.function_name == "system.resolver.test"
    finally:
        await db_gen.aclose()


@pytest.mark.asyncio
async def test_resolve_offline_node_fails(client: AsyncClient):
    """Offline node with function → available=False."""
    from yequ.api.deps import get_db
    from yequ.models.capability import Capability
    from yequ.models.node import Node
    from yequ.services.capability_resolver import resolve_function
    from yequ.services.node_auth import hash_token

    db_gen = get_db()
    db = await db_gen.__anext__()
    try:
        node = Node(
            node_id="resolver-offline-node",
            node_name="Resolver Offline",
            token_hash=hash_token("resolver-token-2"),
            status="offline",
        )
        db.add(node)
        await db.flush()

        cap = Capability(
            node_record_id=node.id,
            plugin_id="test.resolver",
            plugin_version="1.0",
            capability_type="function",
            name="system.resolver.offline_test",
            status="loaded",
            risk="safe",
            effect="read",
            is_active=True,
        )
        db.add(cap)
        await db.commit()

        result = await resolve_function(
            db,
            "system.resolver.offline_test",
            target_node_id="resolver-offline-node",
        )
        assert result.available is False
        assert result.unavailable_reason is not None
    finally:
        await db_gen.aclose()


@pytest.mark.asyncio
async def test_resolve_auto_selects_online_node(client: AsyncClient):
    """No target_node_id → picks online node automatically."""
    from datetime import UTC, datetime

    from yequ.api.deps import get_db
    from yequ.models.capability import Capability
    from yequ.models.node import Node
    from yequ.services.capability_resolver import resolve_function
    from yequ.services.node_auth import hash_token

    db_gen = get_db()
    db = await db_gen.__anext__()
    try:
        node = Node(
            node_id="resolver-auto-node",
            node_name="Auto Select Node",
            token_hash=hash_token("resolver-token-3"),
            status="online",
            last_heartbeat_at=datetime.now(UTC),
        )
        db.add(node)
        await db.flush()

        cap = Capability(
            node_record_id=node.id,
            plugin_id="test.auto",
            plugin_version="1.0",
            capability_type="function",
            name="system.auto.select",
            status="loaded",
            risk="safe",
            effect="read",
            is_active=True,
        )
        db.add(cap)
        await db.commit()

        result = await resolve_function(db, "system.auto.select")
        assert result.available is True
        assert result.node_id == "resolver-auto-node"
    finally:
        await db_gen.aclose()


@pytest.mark.asyncio
async def test_no_online_node_returns_capability_unavailable(client: AsyncClient):
    """No online node has the function → available=False + error message."""
    from yequ.api.deps import get_db
    from yequ.services.capability_resolver import resolve_function

    db_gen = get_db()
    db = await db_gen.__anext__()
    try:
        result = await resolve_function(db, "nonexistent.function.v3")
        assert result.available is False
        assert any(
            term in (result.unavailable_reason or "").lower()
            for term in ["no online node", "no nodes", "capability", "not found"]
        ), f"Unexpected message: {result.unavailable_reason}"
    finally:
        await db_gen.aclose()


@pytest.mark.asyncio
async def test_inactive_capability_not_available(client: AsyncClient):
    """Inactive capability → not selected by resolver."""
    from datetime import UTC, datetime

    from yequ.api.deps import get_db
    from yequ.models.capability import Capability
    from yequ.models.node import Node
    from yequ.services.capability_resolver import resolve_function
    from yequ.services.node_auth import hash_token

    db_gen = get_db()
    db = await db_gen.__anext__()
    try:
        node = Node(
            node_id="inactive-cap-node",
            node_name="Inactive Cap Node",
            token_hash=hash_token("inactive-token-1"),
            status="online",
            last_heartbeat_at=datetime.now(UTC),
        )
        db.add(node)
        await db.flush()

        cap = Capability(
            node_record_id=node.id,
            plugin_id="test.inactive",
            plugin_version="1.0",
            capability_type="function",
            name="system.inactive.test",
            status="loaded",
            risk="safe",
            effect="read",
            is_active=False,  # explicitly inactive
        )
        db.add(cap)
        await db.commit()

        result = await resolve_function(
            db,
            "system.inactive.test",
            target_node_id="inactive-cap-node",
        )
        assert result.available is False
    finally:
        await db_gen.aclose()


@pytest.mark.asyncio
async def test_capability_list_shows_executable_field(client: AsyncClient):
    """GET /admin/capabilities returns executable, inactive_reason fields."""
    from datetime import UTC, datetime

    from yequ.api.deps import get_db
    from yequ.models.capability import Capability
    from yequ.models.node import Node
    from yequ.services.node_auth import hash_token

    db_gen = get_db()
    db = await db_gen.__anext__()
    try:
        node = Node(
            node_id="executable-cap-node",
            node_name="Executable Cap Node",
            token_hash=hash_token("exec-cap-token-1"),
            status="online",
            last_heartbeat_at=datetime.now(UTC),
        )
        db.add(node)
        await db.flush()
        cap = Capability(
            node_record_id=node.id,
            plugin_id="test.exec",
            plugin_version="1.0",
            capability_type="function",
            name="system.exec.test",
            status="loaded",
            risk="safe",
            effect="read",
            is_active=True,
        )
        db.add(cap)
        await db.commit()
    finally:
        await db_gen.aclose()

    resp = await client.get("/admin/capabilities?node_id=executable-cap-node")
    assert resp.status_code == 200
    caps = resp.json()
    our_cap = [c for c in caps if c["name"] == "system.exec.test"]
    assert len(our_cap) == 1
    cap = our_cap[0]
    assert "executable" in cap, f"Missing 'executable' field in {cap}"
    assert "inactive_reason" in cap
    assert "approval_required" in cap
    assert "conflict_policy" in cap
    assert "node_id" in cap


async def _add_resolver_node(
    db,
    *,
    node_id: str,
    function_name: str,
    locality: str = "lan",
    heartbeat=None,
    risk: str = "safe",
    effect: str = "read",
):
    from datetime import UTC, datetime

    from yequ.models.capability import Capability
    from yequ.models.node import Node
    from yequ.services.node_auth import hash_token

    node = Node(
        node_id=node_id,
        node_name=node_id,
        token_hash=hash_token(f"tok-{node_id}"),
        status="online",
        locality=locality,
        last_heartbeat_at=heartbeat or datetime.now(UTC),
    )
    db.add(node)
    await db.flush()

    db.add(
        Capability(
            node_record_id=node.id,
            plugin_id="test.routing",
            plugin_version="1.0",
            capability_type="function",
            name=function_name,
            status="loaded",
            risk=risk,
            effect=effect,
            is_active=True,
        )
    )
    await db.commit()
    return node


async def _add_running_job(db, *, node_id: str, job_id: str, function_name: str):
    from datetime import UTC, datetime

    from yequ.models.invocation import Invocation
    from yequ.models.job import Job
    from yequ.protocol import JobStatus

    now = datetime.now(UTC)
    invocation = Invocation(
        invocation_id=f"inv_{job_id}",
        actor_type="agent",
        actor_id="resolver-test",
        function_name=function_name,
        status="running",
        started_at=now,
    )
    db.add(invocation)
    await db.flush()
    db.add(
        Job(
            job_id=job_id,
            invocation_id=invocation.invocation_id,
            node_id=node_id,
            function_name=function_name,
            status=JobStatus.RUNNING,
            input_payload={},
            timeout_sec=30,
            lease_sec=30,
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_resolver_prefers_locality_before_wan(db_session):
    from datetime import UTC, datetime, timedelta

    from yequ.services.capability_resolver import resolve_function

    function_name = "system.routing.locality"
    now = datetime.now(UTC)
    await _add_resolver_node(
        db_session,
        node_id="resolver-wan-newer",
        function_name=function_name,
        locality="wan",
        heartbeat=now + timedelta(seconds=30),
    )
    await _add_resolver_node(
        db_session,
        node_id="resolver-lan-older",
        function_name=function_name,
        locality="lan",
        heartbeat=now,
    )

    result = await resolve_function(db_session, function_name)

    assert result.available is True
    assert result.node_id == "resolver-lan-older"


@pytest.mark.asyncio
async def test_resolver_prefers_fewer_running_jobs_with_same_locality(db_session):
    from datetime import UTC, datetime

    from yequ.services.capability_resolver import resolve_function

    function_name = "system.routing.load"
    heartbeat = datetime.now(UTC)
    await _add_resolver_node(
        db_session,
        node_id="resolver-busy",
        function_name=function_name,
        locality="lan",
        heartbeat=heartbeat,
    )
    await _add_resolver_node(
        db_session,
        node_id="resolver-idle",
        function_name=function_name,
        locality="lan",
        heartbeat=heartbeat,
    )
    await _add_running_job(
        db_session,
        node_id="resolver-busy",
        job_id="job_resolver_busy_1",
        function_name=function_name,
    )

    result = await resolve_function(db_session, function_name)

    assert result.available is True
    assert result.node_id == "resolver-idle"


@pytest.mark.asyncio
async def test_resolver_prefers_newer_heartbeat_after_load_tie(db_session):
    from datetime import UTC, datetime, timedelta

    from yequ.services.capability_resolver import resolve_function

    function_name = "system.routing.heartbeat"
    now = datetime.now(UTC)
    await _add_resolver_node(
        db_session,
        node_id="resolver-old-heartbeat",
        function_name=function_name,
        locality="lan",
        heartbeat=now - timedelta(seconds=30),
    )
    await _add_resolver_node(
        db_session,
        node_id="resolver-new-heartbeat",
        function_name=function_name,
        locality="lan",
        heartbeat=now,
    )

    result = await resolve_function(db_session, function_name)

    assert result.available is True
    assert result.node_id == "resolver-new-heartbeat"


@pytest.mark.asyncio
async def test_resolver_uses_node_id_ascending_as_stable_tiebreaker(db_session):
    from datetime import UTC, datetime

    from yequ.services.capability_resolver import resolve_function

    function_name = "system.routing.tie"
    heartbeat = datetime.now(UTC)
    await _add_resolver_node(
        db_session,
        node_id="resolver-z",
        function_name=function_name,
        locality="lan",
        heartbeat=heartbeat,
    )
    await _add_resolver_node(
        db_session,
        node_id="resolver-a",
        function_name=function_name,
        locality="lan",
        heartbeat=heartbeat,
    )

    result = await resolve_function(db_session, function_name)

    assert result.available is True
    assert result.node_id == "resolver-a"


@pytest.mark.asyncio
async def test_resolver_honors_required_effect_and_risk(db_session):
    from yequ.services.capability_resolver import resolve_function

    function_name = "system.routing.contract"
    await _add_resolver_node(
        db_session,
        node_id="resolver-read-safe",
        function_name=function_name,
        risk="safe",
        effect="read",
    )

    result = await resolve_function(
        db_session,
        function_name,
        required_effect="write",
        required_risk="maintenance",
    )

    assert result.available is False
