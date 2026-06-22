"""Tests for Capability Resolver — node selection and error handling."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_resolve_online_node_succeeds(client: AsyncClient):
    """Specified online node with function → success."""
    from yequ.services.node_auth import hash_token
    from yequ.models.node import Node
    from yequ.models.capability import Capability
    from yequ.services.capability_resolver import resolve_function
    from datetime import UTC, datetime

    from yequ.api.deps import get_db

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
            db, "system.resolver.test",
            requested_node_id="resolver-online-node",
        )
        assert result.available is True
        assert result.node_id == "resolver-online-node"
        assert result.function_name == "system.resolver.test"
    finally:
        await db_gen.aclose()


@pytest.mark.asyncio
async def test_resolve_offline_node_fails(client: AsyncClient):
    """Offline node with function → available=False."""
    from yequ.services.node_auth import hash_token
    from yequ.models.node import Node
    from yequ.models.capability import Capability
    from yequ.services.capability_resolver import resolve_function

    from yequ.api.deps import get_db

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
            db, "system.resolver.offline_test",
            requested_node_id="resolver-offline-node",
        )
        assert result.available is False
        assert result.unavailable_reason is not None
    finally:
        await db_gen.aclose()


@pytest.mark.asyncio
async def test_resolve_auto_selects_online_node(client: AsyncClient):
    """No target_node_id → picks online node automatically."""
    from yequ.services.node_auth import hash_token
    from yequ.models.node import Node
    from yequ.models.capability import Capability
    from yequ.services.capability_resolver import resolve_function
    from datetime import UTC, datetime

    from yequ.api.deps import get_db

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
    from yequ.services.capability_resolver import resolve_function

    from yequ.api.deps import get_db

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
    from yequ.services.node_auth import hash_token
    from yequ.models.node import Node
    from yequ.models.capability import Capability
    from yequ.services.capability_resolver import resolve_function
    from datetime import UTC, datetime

    from yequ.api.deps import get_db

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
            db, "system.inactive.test",
            requested_node_id="inactive-cap-node",
        )
        assert result.available is False
    finally:
        await db_gen.aclose()


@pytest.mark.asyncio
async def test_capability_list_shows_executable_field(client: AsyncClient):
    """GET /admin/capabilities returns executable, inactive_reason fields."""
    from yequ.services.node_auth import hash_token
    from yequ.models.node import Node
    from yequ.models.capability import Capability
    from datetime import UTC, datetime

    from yequ.api.deps import get_db

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
