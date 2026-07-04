"""Provider-ready YCR context packet construction."""

from __future__ import annotations

import json
import uuid
from typing import Any

from yequ.agent.provider import sanitize_tool_payload_for_agent
from yequ.ycr.budget import BudgetProfile, estimate_tokens

JsonDict = dict[str, object]


def build_agent_context_packet(
    *,
    session_id: str,
    actor_id: str | None,
    provider: str,
    model: str,
    messages: list[JsonDict],
    available_functions: list[JsonDict],
    capability_context: JsonDict | None,
    budget: BudgetProfile,
    step: int | None = None,
) -> JsonDict:
    """Build the only provider input shape Agent Runtime may use.

    This packet is intentionally provider-agnostic. Providers still own
    transport-specific formatting, but the message history and runtime context
    must pass through this boundary before a model invocation.
    """

    packet_id = f"ctxpkt_{uuid.uuid4().hex[:16]}"
    projected_messages = [_project_message(message) for message in messages]
    projected_capability_context = _project_capability_context(capability_context or {})
    tool_definitions = [_project_tool_definition(item) for item in available_functions]

    message_tokens = estimate_tokens(projected_messages)
    tool_tokens = estimate_tokens(tool_definitions)
    capability_tokens = estimate_tokens(projected_capability_context)
    estimated_input_tokens = message_tokens + tool_tokens + capability_tokens
    usable_budget = max(0, budget.max_input_tokens - budget.reserved_response_tokens)
    if estimated_input_tokens > usable_budget:
        raise ValueError(
            "context_budget_exceeded: "
            f"estimated_input_tokens={estimated_input_tokens} budget={usable_budget}"
        )

    return {
        "packet_id": packet_id,
        "session_id": session_id,
        "actor_id": actor_id,
        "provider": provider,
        "model": model or budget.model,
        "step": step,
        "messages": projected_messages,
        "tool_definitions": tool_definitions,
        "provider_context": {
            "capability_context": projected_capability_context,
            "ycr_packet_id": packet_id,
        },
        "budget": {
            "provider": budget.provider,
            "model": budget.model,
            "max_input_tokens": budget.max_input_tokens,
            "reserved_response_tokens": budget.reserved_response_tokens,
            "estimated_input_tokens": estimated_input_tokens,
        },
        "budget_report": {
            "messages_tokens": message_tokens,
            "tool_schema_tokens": tool_tokens,
            "capability_context_tokens": capability_tokens,
            "dropped_blocks": [],
            "projected_blocks": ["messages", "tool_definitions", "capability_context"],
            "truncated_paths": _capability_context_truncated_paths(projected_capability_context),
        },
        "refs": [],
        "ycr": {
            "projected": True,
            "projection_policy": "agent_context_packet_v1",
            "projection_version": 1,
        },
    }


def _project_message(message: JsonDict) -> JsonDict:
    role = str(message.get("role") or "")
    projected: JsonDict = {"role": role}
    content = message.get("content")
    if content is not None:
        if role == "tool":
            projected["content"] = _project_tool_message_content(content)
        else:
            projected["content"] = str(content)
    tool_call_id = message.get("tool_call_id")
    if isinstance(tool_call_id, str) and tool_call_id:
        projected["tool_call_id"] = tool_call_id
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        projected["tool_calls"] = [
            _project_tool_call(item) for item in tool_calls if isinstance(item, dict)
        ]
    message_id = message.get("message_id")
    if isinstance(message_id, str) and message_id:
        projected["message_id"] = message_id
    return projected


def _project_tool_message_content(content: object) -> str:
    if not isinstance(content, str):
        raise ValueError("unprojected_tool_observation")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("unprojected_tool_observation") from exc
    sanitized = sanitize_tool_payload_for_agent(parsed)
    if not _is_projected_tool_observation(sanitized):
        raise ValueError("unprojected_tool_observation")
    return json.dumps(sanitized, ensure_ascii=False)


def _project_tool_call(item: dict[str, Any]) -> JsonDict:
    return {
        "call_id": str(item.get("call_id") or ""),
        "name": str(item.get("name") or ""),
        "input": sanitize_tool_payload_for_agent(item.get("input") or {}),
    }


def _is_projected_tool_observation(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    ycr = value.get("ycr")
    if isinstance(ycr, dict) and ycr.get("projected") is True:
        return True
    result = value.get("result")
    return isinstance(result, dict) and bool(result.get("projection_policy"))


def _project_tool_definition(item: JsonDict) -> JsonDict:
    input_schema = item.get("input_schema")
    return {
        "name": str(item.get("name") or ""),
        "description": str(item.get("description") or ""),
        "risk": str(item.get("risk") or "safe"),
        "effect": str(item.get("effect") or "read"),
        "source_nodes": list(item.get("source_nodes") or []),
        "input_schema": input_schema if isinstance(input_schema, dict) else {},
    }


def _project_capability_context(context: JsonDict) -> JsonDict:
    projected_nodes: list[JsonDict] = []
    projected: JsonDict = {
        "routing_mode": str(context.get("routing_mode") or "auto"),
        "target_node_id": context.get("target_node_id"),
        "tool_count_by_node": context.get("tool_count_by_node") or {},
        "same_name_capabilities": context.get("same_name_capabilities") or {},
        "nodes": projected_nodes,
        "ycr": {
            "projected": True,
            "projection_policy": "capability_context_projection_v1",
            "projection_version": 1,
        },
    }
    nodes = context.get("nodes")
    if not isinstance(nodes, list):
        return projected

    for node in nodes:
        if not isinstance(node, dict):
            continue
        capabilities = node.get("capabilities")
        cap_items = capabilities if isinstance(capabilities, list) else []
        max_caps = 48
        projected_node: JsonDict = {
            "node_id": node.get("node_id"),
            "node_name": node.get("node_name"),
            "platform_os": node.get("platform_os"),
            "platform_arch": node.get("platform_arch"),
            "status": node.get("status"),
            "schedulable": bool(node.get("schedulable")),
            "unavailable_reason": node.get("unavailable_reason"),
            "capabilities": [
                _project_capability_summary(cap)
                for cap in cap_items[:max_caps]
                if isinstance(cap, dict)
            ],
            "capability_count": len(cap_items),
            "omitted_capability_count": max(0, len(cap_items) - max_caps),
        }
        projected_nodes.append(projected_node)
    return projected


def _project_capability_summary(capability: dict[str, Any]) -> JsonDict:
    return {
        "name": str(capability.get("name") or ""),
        "capability_id": capability.get("capability_id"),
        "description": str(capability.get("description") or ""),
        "effect": str(capability.get("effect") or "read"),
        "risk": str(capability.get("risk") or "safe"),
        "timeout_sec": capability.get("timeout_sec"),
        "execution_context": capability.get("execution_context"),
    }


def _capability_context_truncated_paths(context: JsonDict) -> list[str]:
    paths: list[str] = []
    nodes = context.get("nodes")
    if not isinstance(nodes, list):
        return paths
    for index, node in enumerate(nodes):
        if isinstance(node, dict) and int(node.get("omitted_capability_count") or 0) > 0:
            paths.append(f"$.nodes[{index}].capabilities")
    return paths
