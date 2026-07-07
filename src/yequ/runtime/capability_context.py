"""Capability context construction.

The Agent should receive structured Center state first and rendered prompt text
second.  This module builds the node/capability context used by prompt
generation, SSE diagnostics, and future transcript/run projection.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from yequ.config import get_settings
from yequ.models.capability import Capability
from yequ.models.capability_runtime import CapabilityDefinition, CapabilitySource
from yequ.models.node import Node
from yequ.models.runtime_instance import RuntimeInstance
from yequ.models.ycr import YcrCapabilityContextSnapshot
from yequ.services.node_liveness_service import is_node_schedulable

JsonDict = dict[str, Any]


class ContextFunction(Protocol):
    name: str
    source_nodes: list[str]


async def build_capability_context(
    db: AsyncSession,
    *,
    available_functions: list[ContextFunction],
    target_node_id: str | None,
) -> JsonDict:
    """Build the structured node/capability context for one Agent turn."""

    function_fingerprint = _function_fingerprint(available_functions)
    registry_fingerprint = await _registry_fingerprint(db)
    snapshot_key = _snapshot_key(
        target_node_id=target_node_id,
        function_fingerprint=function_fingerprint,
    )
    cached = await _load_snapshot(
        db,
        snapshot_key=snapshot_key,
        registry_fingerprint=registry_fingerprint,
    )
    if cached is not None:
        return cached

    context = await _build_capability_context_uncached(
        db,
        available_functions=available_functions,
        target_node_id=target_node_id,
    )
    await _store_snapshot(
        db,
        snapshot_key=snapshot_key,
        target_node_id=target_node_id,
        function_fingerprint=function_fingerprint,
        registry_fingerprint=registry_fingerprint,
        context=context,
    )
    return context


async def _build_capability_context_uncached(
    db: AsyncSession,
    *,
    available_functions: list[ContextFunction],
    target_node_id: str | None,
) -> JsonDict:
    routing_mode = "pinned" if target_node_id else "auto"
    source_nodes_by_name: dict[str, set[str]] = defaultdict(set)
    for func in available_functions:
        for node_id in func.source_nodes:
            source_nodes_by_name[func.name].add(node_id)

    result = await db.execute(
        select(Node).options(joinedload(Node.capabilities), joinedload(Node.runtime_instances))
    )
    nodes = []
    tool_count_by_node: dict[str, int] = {}
    settings = get_settings()

    for node in result.unique().scalars().all():
        schedulable, unavailable_reason = is_node_schedulable(node, settings)
        if target_node_id and node.node_id != target_node_id:
            continue

        active_function_count = sum(
            1
            for cap in node.capabilities
            if cap.capability_type == "function" and cap.is_active
        )
        if not target_node_id and not schedulable:
            continue

        tool_count_by_node[node.node_id] = active_function_count if schedulable else 0
        nodes.append(
            {
                "node_id": node.node_id,
                "node_name": node.node_name,
                "platform_os": node.platform_os,
                "platform_arch": node.platform_arch,
                "status": node.status,
                "schedulable": schedulable,
                "unavailable_reason": unavailable_reason,
                "runtime_ids": [
                    runtime.runtime_id
                    for runtime in sorted(
                        node.runtime_instances, key=lambda item: item.runtime_id
                    )
                ],
                "registered_capability_count": active_function_count,
                "capabilities": [],
            }
        )

    same_name_sources = {
        name: sorted(nodes)
        for name, nodes in source_nodes_by_name.items()
        if len(nodes) > 1
    }

    return {
        "routing_mode": routing_mode,
        "target_node_id": target_node_id,
        "nodes": nodes,
        "tool_count_by_node": tool_count_by_node,
        "same_name_capabilities": same_name_sources,
        "snapshot": {"status": "miss"},
    }


async def _load_snapshot(
    db: AsyncSession,
    *,
    snapshot_key: str,
    registry_fingerprint: str,
) -> JsonDict | None:
    result = await db.execute(
        select(YcrCapabilityContextSnapshot).where(
            YcrCapabilityContextSnapshot.snapshot_key == snapshot_key
        )
    )
    record = result.scalar_one_or_none()
    if record is None or record.registry_fingerprint != registry_fingerprint:
        return None
    record.hit_count += 1
    record.last_used_at = datetime.now(UTC)
    context = _jsonable_dict(record.context_json)
    context["snapshot"] = {
        "status": "hit",
        "snapshot_key": record.snapshot_key,
        "registry_fingerprint": record.registry_fingerprint,
        "hit_count": record.hit_count,
    }
    await db.flush()
    return context


async def _store_snapshot(
    db: AsyncSession,
    *,
    snapshot_key: str,
    target_node_id: str | None,
    function_fingerprint: str,
    registry_fingerprint: str,
    context: JsonDict,
) -> None:
    result = await db.execute(
        select(YcrCapabilityContextSnapshot).where(
            YcrCapabilityContextSnapshot.snapshot_key == snapshot_key
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        record = YcrCapabilityContextSnapshot(snapshot_key=snapshot_key)
        db.add(record)
    clean_context = _jsonable_dict({k: v for k, v in context.items() if k != "snapshot"})
    record.target_node_id = target_node_id
    record.function_fingerprint = function_fingerprint
    record.registry_fingerprint = registry_fingerprint
    record.context_json = clean_context
    record.hit_count = 0
    record.last_used_at = datetime.now(UTC)
    context["snapshot"] = {
        "status": "miss",
        "snapshot_key": snapshot_key,
        "registry_fingerprint": registry_fingerprint,
    }
    await db.flush()


async def _registry_fingerprint(db: AsyncSession) -> str:
    rows: list[tuple[str, object, object]] = []
    for label, model in [
        ("nodes", Node),
        ("capabilities", Capability),
        ("capability_definitions", CapabilityDefinition),
        ("capability_sources", CapabilitySource),
        ("runtime_instances", RuntimeInstance),
    ]:
        result = await db.execute(
            select(
                func.count(),
                func.max(model.updated_at),
                func.max(getattr(model, "last_heartbeat_at", model.updated_at)),
            )
        )
        row = result.one()
        rows.append((label, row[0], _fingerprint_value(row[1])))
        rows.append((f"{label}:liveness", "", _fingerprint_value(row[2])))
    return _hash_object(rows)


def _function_fingerprint(available_functions: list[ContextFunction]) -> str:
    return _hash_object(
        [
            {
                "name": function.name,
                "source_nodes": sorted(str(node_id) for node_id in function.source_nodes),
            }
            for function in sorted(available_functions, key=lambda item: item.name)
        ]
    )


def _snapshot_key(*, target_node_id: str | None, function_fingerprint: str) -> str:
    return _hash_object(
        {
            "kind": "capability_context_snapshot",
            "target_node_id": target_node_id,
            "function_fingerprint": function_fingerprint,
        }
    )


def _hash_object(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _fingerprint_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _jsonable_dict(value: JsonDict) -> JsonDict:
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    decoded = json.loads(encoded)
    return decoded if isinstance(decoded, dict) else {}


def render_capability_context_prompt(
    capability_context: JsonDict | None,
    available_functions: list[ContextFunction],
) -> str:
    """Render structured capability context into the provider system prompt."""

    if not capability_context:
        capability_context = {
            "routing_mode": "auto",
            "target_node_id": None,
            "nodes": [],
            "tool_count_by_node": {},
            "same_name_capabilities": {},
        }

    routing_mode = str(capability_context.get("routing_mode") or "auto")
    target_node_id = capability_context.get("target_node_id")
    lines = [f"Routing mode: {routing_mode}"]
    if target_node_id:
        lines.append(f"Current pinned node: {target_node_id}")
    else:
        lines.append("No single current node is pinned.")

    lines.append("")
    lines.append("Nodes:")
    nodes = capability_context.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        lines.append("- No executable node capabilities are currently available.")
        return "\n".join(lines)

    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id") or "unknown")
        platform = node.get("platform_os") or "unknown"
        status = node.get("status") or "unknown"
        schedulable = bool(node.get("schedulable"))
        lines.append("")
        lines.append(f"Node: {node_id}")
        lines.append(f"Platform: {platform}")
        lines.append(f"Status: {status}")
        if not schedulable:
            reason = node.get("unavailable_reason") or "not schedulable"
            lines.append(f"Schedulable: false ({reason})")
        else:
            lines.append("Schedulable: true")
        count = node.get("registered_capability_count")
        if isinstance(count, int):
            lines.append(f"Registered capability count: {count}")
        lines.append("Use capability.search to discover callable capabilities.")

    same_name = capability_context.get("same_name_capabilities")
    if isinstance(same_name, dict) and same_name:
        lines.append("")
        lines.append("Same-name capability sources:")
        for name, node_ids in sorted(same_name.items()):
            if isinstance(node_ids, list):
                lines.append(f"- {name}: {', '.join(str(item) for item in node_ids)}")

    return "\n".join(lines)


