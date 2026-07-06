"""Capability context construction.

The Agent should receive structured Center state first and rendered prompt text
second.  This module builds the node/capability context used by prompt
generation, SSE diagnostics, and future transcript/run projection.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from yequ.config import get_settings
from yequ.models.node import Node
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
    }


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


