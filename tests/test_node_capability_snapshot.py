"""Tests for capability snapshot semantics — full replace on re-register."""

import pytest
from httpx import AsyncClient

from tests.conftest import make_yqp_envelope


async def _setup_node_with_hello(client, db, node_id, token):
    """Provision + hello a node, returning the Node object."""
    from datetime import UTC, datetime

    from yequ.models.node import Node
    from yequ.services.node_auth import hash_token

    now = datetime.now(UTC)
    node = Node(
        node_id=node_id,
        node_name=f"Node {node_id}",
        token_hash=hash_token(token),
        status="online",
        last_heartbeat_at=now,
    )
    db.add(node)
    await db.commit()

    auth = {"Authorization": f"Bearer {token}"}
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope("node.hello", node_id, payload={"daemon_version": "0.1.0"}),
        headers=auth,
    )
    assert resp.status_code == 200, f"hello failed: {resp.text}"
    return node


async def _register(client, node_id, token, functions, *, plugin_id="test.snapshot"):
    auth = {"Authorization": f"Bearer {token}"}
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.register_capabilities",
            node_id,
            payload={
                "plugins": [
                    {
                        "plugin_id": plugin_id,
                        "plugin_version": "1.0",
                        "status": "loaded",
                        "functions": functions,
                        "signals": [],
                    }
                ]
            },
        ),
        headers=auth,
    )
    assert resp.status_code == 200, f"register failed: {resp.text}"


@pytest.mark.asyncio
async def test_first_register_creates_capabilities(client: AsyncClient):
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from yequ.api.deps import get_db
    from yequ.models.capability import Capability

    db_gen = get_db()
    db = await db_gen.__anext__()
    try:
        node = await _setup_node_with_hello(client, db, "snap-a", "tok-snapa")
        await _register(
            client,
            "snap-a",
            "tok-snapa",
            [
                {"name": "snap.func.one", "risk": "safe", "effect": "read"},
                {"name": "snap.func.two", "risk": "safe", "effect": "read"},
                {"name": "snap.func.three", "risk": "safe", "effect": "read"},
            ],
        )

        count_result = await db.execute(
            sa_select(func.count())
            .select_from(Capability)
            .where(
                Capability.node_record_id == node.id,
                Capability.is_active == True,  # noqa: E712
                Capability.capability_type == "function",
            )
        )
        assert count_result.scalar() == 3, "Expected 3 active functions"
    finally:
        await db_gen.aclose()


@pytest.mark.asyncio
async def test_second_register_replaces_old_capabilities(client: AsyncClient):
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from yequ.api.deps import get_db
    from yequ.models.capability import Capability

    db_gen = get_db()
    db = await db_gen.__anext__()
    try:
        node = await _setup_node_with_hello(client, db, "snap-b", "tok-snapb")

        # First: 3 functions
        await _register(
            client,
            "snap-b",
            "tok-snapb",
            [
                {"name": "replace.a", "risk": "safe", "effect": "read"},
                {"name": "replace.b", "risk": "safe", "effect": "read"},
                {"name": "replace.c", "risk": "safe", "effect": "read"},
            ],
        )
        # Second: only 2
        await _register(
            client,
            "snap-b",
            "tok-snapb",
            [
                {"name": "replace.a", "risk": "safe", "effect": "read"},
                {"name": "replace.b", "risk": "safe", "effect": "read"},
            ],
        )

        active_result = await db.execute(
            sa_select(func.count())
            .select_from(Capability)
            .where(
                Capability.node_record_id == node.id,
                Capability.is_active == True,  # noqa: E712
                Capability.capability_type == "function",
            )
        )
        assert active_result.scalar() == 2, "Should have 2 active after re-register"

        all_result = await db.execute(
            sa_select(Capability).where(
                Capability.node_record_id == node.id,
                Capability.capability_type == "function",
            )
        )
        active_names = {c.name for c in all_result.scalars().all() if c.is_active}
        assert active_names == {"replace.a", "replace.b"}
        assert "replace.c" not in active_names
    finally:
        await db_gen.aclose()


@pytest.mark.asyncio
async def test_register_with_renamed_plugin_deactivates_old_prefix(client: AsyncClient):
    from sqlalchemy import select as sa_select

    from yequ.api.deps import get_db
    from yequ.models.capability import Capability
    from yequ.models.capability_runtime import CapabilitySource

    db_gen = get_db()
    db = await db_gen.__anext__()
    try:
        node = await _setup_node_with_hello(client, db, "snap-renamed", "tok-snap-renamed")

        await _register(
            client,
            "snap-renamed",
            "tok-snap-renamed",
            [{"name": "system.metrics.snapshot", "risk": "safe", "effect": "read"}],
            plugin_id="system.metrics",
        )
        await _register(
            client,
            "snap-renamed",
            "tok-snap-renamed",
            [{"name": "windows.metrics.snapshot", "risk": "safe", "effect": "read"}],
            plugin_id="windows.metrics",
        )

        capability_result = await db.execute(
            sa_select(Capability).where(
                Capability.node_record_id == node.id,
                Capability.capability_type == "function",
            )
        )
        active_capability_names = {
            cap.name for cap in capability_result.scalars().all() if cap.is_active
        }
        assert active_capability_names == {"windows.metrics.snapshot"}

        source_result = await db.execute(
            sa_select(CapabilitySource).where(CapabilitySource.node_record_id == node.id)
        )
        active_source_names = {
            source.registered_name for source in source_result.scalars().all() if source.is_active
        }
        assert active_source_names == {"windows.metrics.snapshot"}
    finally:
        await db_gen.aclose()


@pytest.mark.asyncio
async def test_resolver_ignores_inactive_after_snapshot(client: AsyncClient):
    from yequ.api.deps import get_db
    from yequ.services.capability_resolver import resolve_function

    db_gen = get_db()
    db = await db_gen.__anext__()
    try:
        await _setup_node_with_hello(client, db, "snap-c", "tok-snapc")

        # Register func.x
        await _register(
            client,
            "snap-c",
            "tok-snapc",
            [
                {"name": "snap.func.x", "risk": "safe", "effect": "read"},
            ],
        )
        # Re-register: only func.y (snapshot deactivates func.x)
        await _register(
            client,
            "snap-c",
            "tok-snapc",
            [
                {"name": "snap.func.y", "risk": "safe", "effect": "read"},
            ],
        )

        await db.commit()

        result = await resolve_function(db, "snap.func.x", target_node_id="snap-c")
        assert result.available is False, "Deactivated function should be unavailable"

        result = await resolve_function(db, "snap.func.y", target_node_id="snap-c")
        assert result.available is True, "Active function should be available"
    finally:
        await db_gen.aclose()
