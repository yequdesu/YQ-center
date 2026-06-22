"""Tests for capability snapshot semantics — full replace on re-register."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_first_register_creates_capabilities(client: AsyncClient):
    """First registration: 3 functions become active."""
    from yequ.services.node_auth import hash_token
    from yequ.models.node import Node
    from yequ.models.capability import Capability
    from datetime import UTC, datetime

    from yequ.api.deps import get_db

    node_id = "snapshot-first-node"
    db_gen = get_db()
    db = await db_gen.__anext__()
    try:
        node = Node(
            node_id=node_id,
            node_name="Snapshot First",
            token_hash=hash_token("snap-first-token"),
            status="online",
            last_heartbeat_at=datetime.now(UTC),
        )
        db.add(node)
        await db.flush()

        # Simulate register_capabilities with 3 functions
        from tests.conftest import make_yqp_envelope
        auth = {"Authorization": f"Bearer snap-first-token"}
        resp = await client.post(
            "/yqp/",
            json=make_yqp_envelope(
                "node.register_capabilities", node_id,
                payload={
                    "plugins": [{
                        "plugin_id": "test.snapshot",
                        "plugin_version": "1.0",
                        "status": "loaded",
                        "functions": [
                            {"name": "snap.func.one", "risk": "safe", "effect": "read"},
                            {"name": "snap.func.two", "risk": "safe", "effect": "read"},
                            {"name": "snap.func.three", "risk": "safe", "effect": "read"},
                        ],
                        "signals": [],
                    }],
                },
            ),
            headers=auth,
        )
        assert resp.status_code == 200

        # All 3 should be active
        from sqlalchemy import select as sa_select, func
        count_result = await db.execute(
            sa_select(func.count()).select_from(Capability).where(
                Capability.node_record_id == node.id,
                Capability.is_active == True,  # noqa: E712
                Capability.capability_type == "function",
            )
        )
        active_count = count_result.scalar()
        assert active_count == 3, f"Expected 3 active functions, got {active_count}"
    finally:
        await db_gen.aclose()


@pytest.mark.asyncio
async def test_second_register_replaces_old_capabilities(client: AsyncClient):
    """Re-register with 2 functions: old 3rd becomes inactive, 2 new active."""
    from yequ.services.node_auth import hash_token
    from yequ.models.node import Node
    from yequ.models.capability import Capability
    from datetime import UTC, datetime

    from yequ.api.deps import get_db

    node_id = "snapshot-replace-node"
    db_gen = get_db()
    db = await db_gen.__anext__()
    try:
        node = Node(
            node_id=node_id,
            node_name="Snapshot Replace",
            token_hash=hash_token("snap-replace-token"),
            status="online",
            last_heartbeat_at=datetime.now(UTC),
        )
        db.add(node)
        await db.flush()

        auth = {"Authorization": f"Bearer snap-replace-token"}
        from tests.conftest import make_yqp_envelope

        # First registration: 3 functions
        await client.post(
            "/yqp/",
            json=make_yqp_envelope(
                "node.register_capabilities", node_id,
                payload={"plugins": [{
                    "plugin_id": "test.replace",
                    "plugin_version": "1.0",
                    "status": "loaded",
                    "functions": [
                        {"name": "replace.func.a", "risk": "safe", "effect": "read"},
                        {"name": "replace.func.b", "risk": "safe", "effect": "read"},
                        {"name": "replace.func.c", "risk": "safe", "effect": "read"},
                    ],
                    "signals": [],
                }]},
            ),
            headers=auth,
        )

        # Second registration: only 2 functions
        await client.post(
            "/yqp/",
            json=make_yqp_envelope(
                "node.register_capabilities", node_id,
                payload={"plugins": [{
                    "plugin_id": "test.replace",
                    "plugin_version": "1.0",
                    "status": "loaded",
                    "functions": [
                        {"name": "replace.func.a", "risk": "safe", "effect": "read"},
                        {"name": "replace.func.b", "risk": "safe", "effect": "read"},
                    ],
                    "signals": [],
                }]},
            ),
            headers=auth,
        )

        from sqlalchemy import select as sa_select, func
        # Active count should be 2
        active_result = await db.execute(
            sa_select(func.count()).select_from(Capability).where(
                Capability.node_record_id == node.id,
                Capability.is_active == True,  # noqa: E712
                Capability.capability_type == "function",
            )
        )
        assert active_result.scalar() == 2, "Should have 2 active functions after re-register"

        # Total count (active + inactive) should be >= 3 (old ones become inactive)
        from sqlalchemy import select as sa_select_all
        all_result = await db.execute(
            sa_select(Capability).where(
                Capability.node_record_id == node.id,
                Capability.capability_type == "function",
            )
        )
        all_caps = all_result.scalars().all()
        active_names = {c.name for c in all_caps if c.is_active}
        assert active_names == {"replace.func.a", "replace.func.b"}
        assert "replace.func.c" not in active_names
    finally:
        await db_gen.aclose()


@pytest.mark.asyncio
async def test_resolver_ignores_inactive_after_snapshot(client: AsyncClient):
    """After re-register removes a function, resolver won't select it."""
    from yequ.services.node_auth import hash_token
    from yequ.models.node import Node
    from yequ.services.capability_resolver import resolve_function
    from datetime import UTC, datetime

    from yequ.api.deps import get_db

    node_id = "snapshot-resolve-node"
    db_gen = get_db()
    db = await db_gen.__anext__()
    try:
        node = Node(
            node_id=node_id,
            node_name="Snapshot Resolve",
            token_hash=hash_token("snap-resolve-token"),
            status="online",
            last_heartbeat_at=datetime.now(UTC),
        )
        db.add(node)
        await db.flush()

        auth = {"Authorization": f"Bearer snap-resolve-token"}
        from tests.conftest import make_yqp_envelope

        # Register func.x then re-register without it
        for functions in [
            [{"name": "resolve.func.x", "risk": "safe", "effect": "read"}],
            [{"name": "resolve.func.y", "risk": "safe", "effect": "read"}],
        ]:
            await client.post(
                "/yqp/",
                json=make_yqp_envelope(
                    "node.register_capabilities", node_id,
                    payload={"plugins": [{
                        "plugin_id": "test.resolve_snap",
                        "plugin_version": "1.0",
                        "status": "loaded",
                        "functions": functions,
                        "signals": [],
                    }]},
                ),
                headers=auth,
            )

        await db.commit()

        # resolve.func.x should NOT be available (it was deactivated by snapshot)
        result = await resolve_function(
            db, "resolve.func.x",
            requested_node_id=node_id,
        )
        assert result.available is False, "Deactivated function should be unavailable"

        # resolve.func.y SHOULD be available
        result = await resolve_function(
            db, "resolve.func.y",
            requested_node_id=node_id,
        )
        assert result.available is True, "Active function should be available"
    finally:
        await db_gen.aclose()
