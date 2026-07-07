from __future__ import annotations

from datetime import UTC, datetime

import pytest

from yequ.api.routes.agent import _available_functions
from yequ.models.capability import Capability
from yequ.models.node import Node
from yequ.models.ycr import YcrCapabilityContextSnapshot
from yequ.runtime.capability_context import (
    build_capability_context,
    render_capability_context_prompt,
)
from yequ.services.node_auth import hash_token


@pytest.mark.asyncio
async def test_capability_context_groups_by_node(db_session):
    await _add_node_capability(
        db_session,
        node_id="winClient",
        platform_os="windows",
        capability_name="system.info",
        description="Read Windows system information.",
    )
    await _add_node_capability(
        db_session,
        node_id="linux-node-01",
        platform_os="linux",
        capability_name="linux.system.info",
        description="Read Linux system information.",
    )
    await db_session.commit()

    functions = await _available_functions(db_session)
    context = await build_capability_context(
        db_session,
        available_functions=functions,
        target_node_id=None,
    )

    assert context["routing_mode"] == "auto"
    assert context["target_node_id"] is None
    nodes = {node["node_id"]: node for node in context["nodes"]}
    assert nodes["winClient"]["platform_os"] == "windows"
    assert nodes["linux-node-01"]["platform_os"] == "linux"
    assert nodes["winClient"]["registered_capability_count"] == 1
    assert nodes["linux-node-01"]["registered_capability_count"] == 1
    assert nodes["winClient"]["capabilities"] == []
    assert nodes["linux-node-01"]["capabilities"] == []

    prompt = render_capability_context_prompt(context, functions)
    assert "Routing mode: auto" in prompt
    assert "No single current node is pinned." in prompt
    assert "Node: winClient" in prompt
    assert "Node: linux-node-01" in prompt
    assert "Use capability.search to discover callable capabilities." in prompt


@pytest.mark.asyncio
async def test_pinned_capability_context_excludes_other_nodes(db_session):
    await _add_node_capability(
        db_session,
        node_id="winClient",
        platform_os="windows",
        capability_name="system.info",
        description="Read Windows system information.",
    )
    await _add_node_capability(
        db_session,
        node_id="linux-node-01",
        platform_os="linux",
        capability_name="linux.system.info",
        description="Read Linux system information.",
    )
    await db_session.commit()

    functions = await _available_functions(db_session, target_node_id="linux-node-01")
    context = await build_capability_context(
        db_session,
        available_functions=functions,
        target_node_id="linux-node-01",
    )

    assert context["routing_mode"] == "pinned"
    assert context["target_node_id"] == "linux-node-01"
    assert [node["node_id"] for node in context["nodes"]] == ["linux-node-01"]
    assert context["tool_count_by_node"] == {"linux-node-01": 1}
    assert context["nodes"][0]["capabilities"] == []

    prompt = render_capability_context_prompt(context, functions)
    assert "Current pinned node: linux-node-01" in prompt
    assert "Node: winClient" not in prompt
    assert "Registered capability count: 1" in prompt


@pytest.mark.asyncio
async def test_capability_context_uses_versioned_snapshot(db_session):
    from sqlalchemy import select

    await _add_node_capability(
        db_session,
        node_id="winClient",
        platform_os="windows",
        capability_name="system.info",
        description="Read Windows system information.",
    )
    await db_session.commit()

    functions = await _available_functions(db_session)
    first = await build_capability_context(
        db_session,
        available_functions=functions,
        target_node_id=None,
    )
    await db_session.commit()
    second = await build_capability_context(
        db_session,
        available_functions=functions,
        target_node_id=None,
    )
    await db_session.commit()

    assert first["snapshot"]["status"] == "miss"
    assert second["snapshot"]["status"] == "hit"
    snapshot = (
        await db_session.execute(select(YcrCapabilityContextSnapshot))
    ).scalar_one()
    assert snapshot.hit_count == 1


@pytest.mark.asyncio
async def test_capability_context_snapshot_invalidates_when_registry_changes(db_session):
    await _add_node_capability(
        db_session,
        node_id="winClient",
        platform_os="windows",
        capability_name="system.info",
        description="Read Windows system information.",
    )
    await db_session.commit()

    functions = await _available_functions(db_session)
    await build_capability_context(
        db_session,
        available_functions=functions,
        target_node_id=None,
    )
    await db_session.commit()
    await _add_node_capability(
        db_session,
        node_id="linux-node-01",
        platform_os="linux",
        capability_name="linux.system.info",
        description="Read Linux system information.",
    )
    await db_session.commit()

    updated = await build_capability_context(
        db_session,
        available_functions=functions,
        target_node_id=None,
    )

    assert updated["snapshot"]["status"] == "miss"
    nodes = {node["node_id"]: node for node in updated["nodes"]}
    assert set(nodes) == {"winClient", "linux-node-01"}


async def _add_node_capability(
    db_session,
    *,
    node_id: str,
    platform_os: str,
    capability_name: str,
    description: str,
) -> None:
    node = Node(
        node_id=node_id,
        node_name=node_id,
        token_hash=hash_token(f"{node_id}-token"),
        status="online",
        platform_os=platform_os,
        last_heartbeat_at=datetime.now(UTC),
    )
    db_session.add(node)
    await db_session.flush()
    db_session.add(
        Capability(
            node_record_id=node.id,
            plugin_id=f"{platform_os}.system",
            plugin_version="1.0",
            capability_type="function",
            name=capability_name,
            status="loaded",
            description=description,
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object"},
            risk="safe",
            effect="read",
            is_active=True,
        )
    )

