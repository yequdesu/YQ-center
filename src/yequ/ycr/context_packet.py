"""Provider-ready YCR context packet construction."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yequ.agent.provider import sanitize_tool_payload_for_agent
from yequ.models.ycr import YcrContextRef
from yequ.ycr.budget import ProjectionProfile, estimate_tokens
from yequ.ycr.projection import project_tool_observation_from_ref

JsonDict = dict[str, object]


async def build_agent_context_packet(
    db: AsyncSession,
    *,
    session_id: str,
    actor_id: str | None,
    provider: str,
    model: str,
    messages: list[JsonDict],
    available_functions: list[JsonDict],
    capability_context: JsonDict | None,
    profile: ProjectionProfile,
    step: int | None = None,
) -> JsonDict:
    packet_id = f"ctxpkt_{uuid.uuid4().hex[:16]}"
    projection_events: list[JsonDict] = []
    projected_messages = [
        await _project_message(db, message, projection_events=projection_events)
        for message in messages
    ]
    projected_capability_context = _project_capability_context(capability_context or {})
    tool_definitions = [_project_tool_definition(item) for item in available_functions]

    message_tokens = estimate_tokens(projected_messages)
    tool_tokens = estimate_tokens(tool_definitions)
    capability_tokens = estimate_tokens(projected_capability_context)
    estimated_input_tokens = message_tokens + tool_tokens + capability_tokens
    ref_count = sum(
        int(event.get("context_estimate", {}).get("ref_count") or 0)
        for event in projection_events
        if isinstance(event.get("context_estimate"), dict)
    )
    raw_tokens = sum(
        int(event.get("context_estimate", {}).get("raw_estimated_tokens") or 0)
        for event in projection_events
        if isinstance(event.get("context_estimate"), dict)
    )
    projected_tokens = sum(
        int(event.get("context_estimate", {}).get("projected_estimated_tokens") or 0)
        for event in projection_events
        if isinstance(event.get("context_estimate"), dict)
    )
    saved_tokens = max(0, raw_tokens - projected_tokens)

    return {
        "packet_id": packet_id,
        "session_id": session_id,
        "actor_id": actor_id,
        "provider": provider,
        "model": model or profile.model,
        "step": step,
        "messages": projected_messages,
        "tool_definitions": tool_definitions,
        "provider_context": {
            "capability_context": projected_capability_context,
            "ycr_packet_id": packet_id,
        },
        "context_estimate": {
            "provider": profile.provider,
            "model": profile.model,
            "estimated_input_tokens": estimated_input_tokens,
            "messages_tokens": message_tokens,
            "tool_schema_tokens": tool_tokens,
            "capability_context_tokens": capability_tokens,
            "raw_estimated_tokens": raw_tokens,
            "projected_estimated_tokens": projected_tokens,
            "saved_estimated_tokens": saved_tokens,
            "ref_count": ref_count,
        },
        "projections": projection_events,
        "refs": [
            ref
            for event in projection_events
            for ref in event.get("refs", [])
            if isinstance(event.get("refs"), list) and isinstance(ref, dict)
        ],
        "ycr": {
            "projected": True,
            "projection_policy": "agent_context_packet_v2",
            "projection_version": 2,
        },
    }


async def _project_message(
    db: AsyncSession,
    message: JsonDict,
    *,
    projection_events: list[JsonDict],
) -> JsonDict:
    role = str(message.get("role") or "")
    projected: JsonDict = {"role": role}
    content = message.get("content")
    if content is not None:
        if role == "tool":
            projected["content"] = await _project_tool_message_content(
                db,
                content,
                projection_events=projection_events,
            )
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


async def _project_tool_message_content(
    db: AsyncSession,
    content: object,
    *,
    projection_events: list[JsonDict],
) -> str:
    if not isinstance(content, str):
        raise ValueError("unprojected_tool_observation")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("unprojected_tool_observation") from exc
    if not isinstance(parsed, dict):
        raise ValueError("unprojected_tool_observation")
    ycr = parsed.get("ycr")
    if not isinstance(ycr, dict) or ycr.get("kind") != "tool_observation_shell":
        raise ValueError("unprojected_tool_observation")
    raw_ref_id = ycr.get("raw_ref")
    if not isinstance(raw_ref_id, str) or not raw_ref_id:
        raise ValueError("context_ref_not_found: missing raw_ref")
    from sqlalchemy import select

    result = await db.execute(select(YcrContextRef).where(YcrContextRef.ref_id == raw_ref_id))
    ref_record = result.scalar_one_or_none()
    if ref_record is None:
        raise ValueError(f"context_ref_not_found: {raw_ref_id}")
    raw_ref = _ref_to_dict(ref_record)
    projected = project_tool_observation_from_ref(
        name=str(parsed.get("name") or ""),
        call_id=str(parsed.get("call_id") or ""),
        status=str(parsed.get("status") or "succeeded"),
        result=ref_record.value_json,
        raw_ref=raw_ref,
        target_node_id=parsed.get("target_node_id"),
    )
    if parsed.get("error") is not None:
        projected["error"] = parsed.get("error")
    if parsed.get("error_code") is not None:
        projected["error_code"] = parsed.get("error_code")
    projection_events.append(
        {
            "kind": "tool_observation_projection",
            "raw_ref": raw_ref_id,
            "call_id": projected.get("call_id"),
            "name": projected.get("name"),
            "status": projected.get("status"),
            "refs": projected.get("result", {}).get("refs")
            if isinstance(projected.get("result"), dict)
            else [],
            "context_estimate": projected.get("result", {}).get("context_estimate")
            if isinstance(projected.get("result"), dict)
            else {},
        }
    )
    return json.dumps(sanitize_tool_payload_for_agent(projected), ensure_ascii=False)


def _project_tool_call(item: dict[str, Any]) -> JsonDict:
    return {
        "call_id": str(item.get("call_id") or ""),
        "name": str(item.get("name") or ""),
        "input": sanitize_tool_payload_for_agent(item.get("input") or {}),
    }


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
    return {
        "routing_mode": str(context.get("routing_mode") or "auto"),
        "target_node_id": context.get("target_node_id"),
        "tool_count_by_node": context.get("tool_count_by_node") or {},
        "same_name_capabilities": context.get("same_name_capabilities") or {},
        "nodes": [
            _project_node_summary(node)
            for node in context.get("nodes", [])
            if isinstance(node, dict)
        ],
        "ycr": {
            "projected": True,
            "projection_policy": "capability_context_projection_v2",
            "projection_version": 2,
        },
    }


def _project_node_summary(node: dict[str, Any]) -> JsonDict:
    return {
        "node_id": node.get("node_id"),
        "node_name": node.get("node_name"),
        "platform_os": node.get("platform_os"),
        "platform_arch": node.get("platform_arch"),
        "status": node.get("status"),
        "schedulable": bool(node.get("schedulable")),
        "unavailable_reason": node.get("unavailable_reason"),
        "capability_count": node.get("capability_count")
        or len(node.get("capabilities") if isinstance(node.get("capabilities"), list) else []),
    }


def _ref_to_dict(record: YcrContextRef) -> JsonDict:
    return {
        "ref_id": record.ref_id,
        "ref_type": record.ref_type,
        "source_anchor": {"type": record.source_type, "id": record.source_id},
        "source_type": record.source_type,
        "source_id": record.source_id,
        "session_id": record.session_id,
        "path": record.source_path,
        "summary": record.summary,
        "trust_level": record.trust_level,
        "projection_policy": record.projection_policy,
        "projection_version": record.projection_version,
        "available_ops": ["inspect", "expand", "tail", "schema", "search"],
    }
