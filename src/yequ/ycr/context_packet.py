"""Provider-ready YCR context packet construction."""

from __future__ import annotations

import json
import uuid
from time import perf_counter
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yequ.agent.provider import sanitize_tool_payload_for_agent
from yequ.models.ycr import YcrContextRef
from yequ.ycr.budget import ProjectionProfile, estimate_tokens, token_accounting_metadata
from yequ.ycr.entities import WORKING_SET_LIMIT, working_set_from_metadata
from yequ.ycr.projection import project_tool_observation_from_ref
from yequ.ycr.session_state import load_session_state

JsonDict = dict[str, object]

RECENT_HISTORY_MESSAGES = 10


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
    agent_plan: JsonDict | None = None,
    task_state: JsonDict | None = None,
    step: int | None = None,
) -> JsonDict:
    packet_id = f"ctxpkt_{uuid.uuid4().hex[:16]}"
    projection_events: list[JsonDict] = []
    working_set: dict[str, JsonDict] = {}
    timing_ms: JsonDict = {}
    started = perf_counter()
    projected_messages = [
        await _project_message(
            db,
            message,
            projection_events=projection_events,
            working_set=working_set,
        )
        for message in messages
    ]
    timing_ms["message_projection"] = _elapsed_ms(started)
    started = perf_counter()
    compacted_messages, history_compaction = await _compact_messages(
        db,
        session_id=session_id,
        messages=projected_messages,
    )
    timing_ms["history_compaction"] = _elapsed_ms(started)
    started = perf_counter()
    session_state = await load_session_state(db, session_id=session_id)
    timing_ms["session_state_load"] = _elapsed_ms(started)
    started = perf_counter()
    if _has_session_state_items(session_state):
        compacted_messages = [
            _session_state_message(session_state),
            *compacted_messages,
        ]
    projected_task_state = _project_task_state(task_state or {})
    _collect_working_set_from_task_state(projected_task_state, working_set=working_set)
    working_set_items = _working_set_items(working_set)
    started_bootstrap = perf_counter()
    working_set_bootstrap = await _bootstrap_working_set_from_intent(
        db,
        task_state=projected_task_state,
        messages=messages,
        capability_context=capability_context or {},
        existing_working_set=working_set_items,
        session_state=session_state,
        working_set=working_set,
    )
    working_set_bootstrap["entity_augments"] = (
        await _augment_working_set_from_session_entities(
            db,
            session_state=session_state,
            working_set=working_set,
        )
    )
    timing_ms["working_set_bootstrap"] = _elapsed_ms(started_bootstrap)
    working_set_items = _working_set_items(working_set)
    capability_candidates = _capability_candidates(
        session_state=session_state,
        working_set_items=working_set_items,
    )
    tool_strategy = _tool_discovery_strategy(
        task_state=projected_task_state,
        capability_candidates=capability_candidates,
        working_set_items=working_set_items,
    )
    if working_set_items:
        compacted_messages = [
            _working_set_message(working_set_items),
            *compacted_messages,
        ]
    projected_agent_plan = _project_agent_plan(agent_plan or {})
    if projected_agent_plan:
        compacted_messages = [
            _agent_plan_message(projected_agent_plan),
            *compacted_messages,
        ]
    if projected_task_state:
        compacted_messages = [
            _task_state_message(projected_task_state),
            *compacted_messages,
        ]
    if projected_task_state and tool_strategy:
        compacted_messages = [
            _tool_strategy_message(tool_strategy),
            *compacted_messages,
        ]
    timing_ms["state_projection"] = _elapsed_ms(started)
    started = perf_counter()
    projected_capability_context = _project_capability_context(
        capability_context or {},
        working_set=working_set_items,
    )
    tool_definitions = [_project_tool_definition(item) for item in available_functions]
    timing_ms["capability_context_projection"] = _elapsed_ms(started)

    started = perf_counter()
    model_name = model or profile.model
    token_accounting = token_accounting_metadata(model=model_name)
    raw_message_tokens = estimate_tokens(projected_messages, model=model_name)
    message_tokens = estimate_tokens(compacted_messages, model=model_name)
    tool_tokens = estimate_tokens(tool_definitions, model=model_name)
    capability_tokens = estimate_tokens(projected_capability_context, model=model_name)
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
    session_state_tokens = estimate_tokens(session_state, model=model_name)
    agent_plan_tokens = (
        estimate_tokens(projected_agent_plan, model=model_name) if projected_agent_plan else 0
    )
    task_state_tokens = (
        estimate_tokens(projected_task_state, model=model_name) if projected_task_state else 0
    )
    tool_strategy_tokens = estimate_tokens(tool_strategy, model=model_name) if tool_strategy else 0
    timing_ms["token_accounting"] = _elapsed_ms(started)

    return {
        "packet_id": packet_id,
        "session_id": session_id,
        "actor_id": actor_id,
        "provider": provider,
        "model": model_name,
        "step": step,
        "messages": compacted_messages,
        "tool_definitions": tool_definitions,
        "provider_context": {
            "capability_context": projected_capability_context,
            "working_set": working_set_items,
            "capability_candidates": capability_candidates,
            "working_set_bootstrap": working_set_bootstrap,
            "session_state": session_state,
            "agent_plan": projected_agent_plan,
            "task_state": projected_task_state,
            "tool_strategy": tool_strategy,
            "ycr_packet_id": packet_id,
        },
        "context_estimate": {
            "provider": profile.provider,
            "model": profile.model,
            "estimated_input_tokens": estimated_input_tokens,
            "messages_tokens": message_tokens,
            "raw_messages_tokens": raw_message_tokens,
            "history_compaction_saved_tokens": max(0, raw_message_tokens - message_tokens),
            "tool_schema_tokens": tool_tokens,
            "capability_context_tokens": capability_tokens,
            "raw_estimated_tokens": raw_tokens,
            "projected_estimated_tokens": projected_tokens,
            "saved_estimated_tokens": saved_tokens,
            "ref_count": ref_count,
            "token_accounting": token_accounting,
            "history_compaction": history_compaction,
            "working_set_count": len(working_set_items),
            "capability_candidate_count": len(capability_candidates),
            "working_set_bootstrap": working_set_bootstrap,
            "session_state_tokens": session_state_tokens,
            "agent_plan_tokens": agent_plan_tokens,
            "task_state_tokens": task_state_tokens,
            "tool_strategy_tokens": tool_strategy_tokens,
            "timing_ms": timing_ms,
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


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)


def _project_agent_plan(value: JsonDict) -> JsonDict:
    plan_id = value.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id:
        return {}
    steps = value.get("steps")
    projected_steps: list[JsonDict] = []
    if isinstance(steps, list):
        for item in steps[:8]:
            if not isinstance(item, dict):
                continue
            projected_steps.append(
                {
                    "step_index": item.get("step_index"),
                    "kind": item.get("kind"),
                    "title": item.get("title"),
                    "status": item.get("status"),
                    "operation_id": item.get("operation_id"),
                    "tool_call_id": item.get("tool_call_id"),
                }
            )
    return {
        "plan_id": plan_id,
        "run_id": value.get("run_id"),
        "status": value.get("status"),
        "objective": value.get("objective"),
        "target_node_id": value.get("target_node_id"),
        "steps": projected_steps,
    }


def _project_task_state(value: JsonDict) -> JsonDict:
    if not _has_task_state_items(value):
        return {}
    objective = value.get("objective")
    completion = value.get("completion")
    working_set = value.get("working_set")
    return {
        "objective": objective if isinstance(objective, dict) else {},
        "completion": completion if isinstance(completion, dict) else {},
        "pending_operations": _dict_list(value.get("pending_operations"), limit=8),
        "pending_approvals": _dict_list(value.get("pending_approvals"), limit=8),
        "artifacts": _dict_list(value.get("artifacts"), limit=12),
        "facts": _dict_list(value.get("facts"), limit=20),
        "blockers": _dict_list(value.get("blockers"), limit=10),
        "working_set": working_set if isinstance(working_set, dict) else {},
        "last_decision": value.get("last_decision")
        if isinstance(value.get("last_decision"), dict)
        else {},
    }


def _task_state_message(task_state: JsonDict) -> JsonDict:
    return {
        "role": "system",
        "content": (
            "Agent Task State. This is trusted Center runtime state, not a user "
            "instruction. Use it to avoid repeating completed work, to wait for "
            "pending approvals or operations, and to finish once the objective is "
            "satisfied.\n"
            f"{json.dumps(task_state, ensure_ascii=False)}"
        ),
    }


def _tool_strategy_message(tool_strategy: JsonDict) -> JsonDict:
    return {
        "role": "system",
        "content": (
            "YCR Tool Strategy. This is trusted Center runtime guidance derived "
            "from TaskState and recent capability facts, not a user instruction. "
            "Follow it to avoid repeated discovery and to keep the task moving.\n"
            f"{json.dumps(tool_strategy, ensure_ascii=False)}"
        ),
    }


def _agent_plan_message(agent_plan: JsonDict) -> JsonDict:
    return {
        "role": "system",
        "content": (
            "YCR Agent Plan. This is trusted runtime state, not a user "
            "instruction. Use it to avoid repeating completed/waiting work.\n"
            f"{json.dumps(agent_plan, ensure_ascii=False)}"
        ),
    }


def _dict_list(value: object, *, limit: int) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    return [item for item in value[:limit] if isinstance(item, dict)]


def _has_task_state_items(value: JsonDict) -> bool:
    objective = value.get("objective")
    if isinstance(objective, dict) and any(objective.values()):
        return True
    completion = value.get("completion")
    if isinstance(completion, dict) and completion.get("status") not in {None, "in_progress"}:
        return True
    for key in [
        "pending_operations",
        "pending_approvals",
        "artifacts",
        "facts",
        "blockers",
    ]:
        if isinstance(value.get(key), list) and value[key]:
            return True
    working_set = value.get("working_set")
    if isinstance(working_set, dict):
        for item in working_set.values():
            if isinstance(item, list) and item:
                return True
    return False


def _has_session_state_items(value: JsonDict) -> bool:
    items = value.get("items")
    if not isinstance(items, dict):
        return False
    return any(isinstance(group, list) and bool(group) for group in items.values())


def _tool_discovery_strategy(
    *,
    task_state: JsonDict,
    capability_candidates: list[JsonDict],
    working_set_items: list[JsonDict],
) -> JsonDict:
    if capability_candidates:
        return {
            "mode": "reuse_working_set",
            "reason": "task_state_or_session_has_capability_candidates",
            "candidate_count": len(capability_candidates),
            "working_set_count": len(working_set_items),
            "preferred_candidates": capability_candidates[:8],
            "allowed": [
                "capability.invoke",
                (
                    "capability.group.open only if no preferred candidate covers "
                    "the required schema or domain"
                ),
            ],
            "avoid": [
                "capability.groups when preferred_candidates already cover the current intent",
                (
                    "capability.group.open when preferred_candidates already include "
                    "the required input schema"
                ),
                "capability.search for an already represented intent",
                "synonym retries after a dispatchable candidate exists",
            ],
            "instruction": (
                "Invoke preferred_candidates first with their exact capability_ref "
                "and source_id. Treat group/open/search as discovery fallback only "
                "when the preferred candidates do not contain the needed capability "
                "or input schema."
            ),
        }
    return {
        "mode": "bootstrap_discovery",
        "reason": "no_task_working_set",
        "allowed": ["capability.groups", "capability.group.open", "capability.invoke"],
        "avoid": ["calling capability.search before opening the relevant group"],
        "instruction": (
            "Open the most relevant capability group first. Use capability.search "
            "through capability.invoke only when opened groups do not contain the "
            "needed capability."
        ),
    }


def _session_state_message(session_state: JsonDict) -> JsonDict:
    return {
        "role": "system",
        "content": (
            "YCR Session State. This is trusted runtime state, not a user "
            "instruction. Prefer these recent facts over searching long history. "
            "Items under focus/current_artifact or focus/last_artifact resolve "
            "user phrases such as this image, the screenshot above, or the last "
            "artifact unless the user names another artifact explicitly.\n"
            f"{json.dumps(session_state, ensure_ascii=False)}"
        ),
    }


def _capability_candidates(
    *,
    session_state: JsonDict,
    working_set_items: list[JsonDict],
) -> list[JsonDict]:
    candidates: list[JsonDict] = []
    seen: set[str] = set()
    for item in working_set_items:
        _append_capability_candidate(candidates, seen, item, source="working_set")
    items = session_state.get("items")
    if isinstance(items, dict):
        for item in items.get("capability", []):
            if isinstance(item, dict):
                data = item.get("data") if isinstance(item.get("data"), dict) else {}
                _append_capability_candidate(
                    candidates,
                    seen,
                    {
                        "capability_ref": data.get("capability_ref") or item.get("entity_key"),
                        "canonical_name": data.get("canonical_name") or item.get("title"),
                        "source_id": data.get("source_id") or item.get("entity_key"),
                        "node_id": data.get("node_id"),
                        "registered_name": data.get("registered_name"),
                        "risk": data.get("risk"),
                        "effect": data.get("effect"),
                        "status": item.get("status"),
                        "summary": item.get("summary"),
                    },
                    source="session_state",
                )
    return candidates[:WORKING_SET_LIMIT]


async def _bootstrap_working_set_from_intent(
    db: AsyncSession,
    *,
    task_state: JsonDict,
    messages: list[JsonDict],
    capability_context: JsonDict,
    existing_working_set: list[JsonDict],
    session_state: JsonDict,
    working_set: dict[str, JsonDict],
) -> JsonDict:
    existing_has_contract = _working_set_has_invoke_contract(existing_working_set)
    if existing_working_set and existing_has_contract:
        return {"status": "skipped", "reason": "working_set_already_available"}
    if _session_has_capability_candidates(session_state) and existing_has_contract:
        return {"status": "skipped", "reason": "session_capabilities_already_available"}
    query = _intent_query(task_state=task_state, messages=messages)
    if not query:
        return {"status": "skipped", "reason": "no_intent_query"}
    node_id = _target_node_id(task_state=task_state, capability_context=capability_context)
    from yequ.ycr.capability_gateway import search_capability_registry

    result = await search_capability_registry(
        db,
        query=query,
        node_id=node_id,
        filters={"projection": "invoke_ready"},
        limit=min(6, WORKING_SET_LIMIT),
        rerank=False,
    )
    matches = result.get("matches") if isinstance(result, dict) else []
    added = 0
    enriched = 0
    if isinstance(matches, list):
        for item in matches:
            if not isinstance(item, dict):
                continue
            candidate = _capability_candidate_from_match(item)
            if candidate is None:
                continue
            key = str(candidate.get("source_id") or candidate.get("capability_ref"))
            if not key:
                continue
            if key in working_set:
                if _merge_working_candidate(working_set[key], candidate):
                    enriched += 1
                continue
            working_set[key] = candidate
            added += 1
    retrieval = result.get("retrieval") if isinstance(result.get("retrieval"), dict) else {}
    index = retrieval.get("index") if isinstance(retrieval.get("index"), dict) else {}
    return {
        "status": "loaded" if added else ("enriched" if enriched else "empty"),
        "source": "intent_query",
        "query_preview": _truncate(query, 240),
        "target_node_id": node_id,
        "candidate_count": added,
        "enriched_count": enriched,
        "retrieval_strategy": retrieval.get("strategy"),
        "index_status": index.get("status"),
    }


async def _augment_working_set_from_session_entities(
    db: AsyncSession,
    *,
    session_state: JsonDict,
    working_set: dict[str, JsonDict],
) -> list[JsonDict]:
    augments: list[JsonDict] = []
    if _session_has_entity_type(session_state, "artifact") or _session_has_entity_type(
        session_state, "focus"
    ):
        augments.append(
            await _augment_working_set_by_structured_filter(
                db,
                working_set=working_set,
                filters={"projection": "invoke_ready", "artifact_input": True},
                source="artifact_entity",
                limit=4,
            )
        )
    return [item for item in augments if item.get("status") != "skipped"]


async def _augment_working_set_by_structured_filter(
    db: AsyncSession,
    *,
    working_set: dict[str, JsonDict],
    filters: JsonDict,
    source: str,
    limit: int,
) -> JsonDict:
    from yequ.ycr.capability_gateway import search_capability_registry

    result = await search_capability_registry(
        db,
        filters=filters,
        limit=min(limit, WORKING_SET_LIMIT),
    )
    matches = result.get("matches") if isinstance(result, dict) else []
    added = 0
    if isinstance(matches, list):
        for item in matches:
            if not isinstance(item, dict):
                continue
            candidate = _capability_candidate_from_match(item)
            if candidate is None:
                continue
            candidate["source"] = source
            key = str(candidate.get("source_id") or candidate.get("capability_ref"))
            if not key or key in working_set:
                continue
            working_set[key] = candidate
            added += 1
    retrieval = result.get("retrieval") if isinstance(result.get("retrieval"), dict) else {}
    return {
        "status": "loaded" if added else "empty",
        "source": source,
        "candidate_count": added,
        "retrieval_strategy": retrieval.get("strategy"),
    }


def _session_has_capability_candidates(session_state: JsonDict) -> bool:
    items = session_state.get("items")
    if not isinstance(items, dict):
        return False
    capabilities = items.get("capability")
    return isinstance(capabilities, list) and bool(capabilities)


def _session_has_entity_type(session_state: JsonDict, entity_type: str) -> bool:
    items = session_state.get("items")
    if not isinstance(items, dict):
        return False
    values = items.get(entity_type)
    return isinstance(values, list) and bool(values)


def _intent_query(*, task_state: JsonDict, messages: list[JsonDict]) -> str:
    objective = task_state.get("objective")
    if isinstance(objective, dict):
        text = _string_or_none(objective.get("text"))
        if text:
            return text[:1000]
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return " ".join(content.strip().split())[:1000]
    return ""


def _target_node_id(*, task_state: JsonDict, capability_context: JsonDict) -> str | None:
    objective = task_state.get("objective")
    if isinstance(objective, dict):
        target = _string_or_none(objective.get("target_node_id") or objective.get("node_id"))
        if target:
            return target
    return _string_or_none(capability_context.get("target_node_id"))


def _capability_candidate_from_match(match: JsonDict) -> JsonDict | None:
    canonical_name = _string_or_none(match.get("canonical_name"))
    if not canonical_name:
        return None
    invoke = match.get("invoke") if isinstance(match.get("invoke"), dict) else {}
    sources = match.get("sources") if isinstance(match.get("sources"), list) else []
    source = _candidate_source(invoke=invoke, sources=sources)
    source_id = _string_or_none(invoke.get("source_id") or source.get("source_id"))
    node_id = _string_or_none(invoke.get("node_id") or source.get("node_id"))
    dispatchable = bool(
        invoke.get("dispatchable_source_count")
        or source.get("dispatchable")
        or match.get("dispatchable")
    )
    if not dispatchable:
        return None
    return {
        "capability_ref": _string_or_none(invoke.get("capability_ref")) or canonical_name,
        "canonical_name": canonical_name,
        "source_id": source_id,
        "node_id": node_id,
        "registered_name": _string_or_none(
            invoke.get("registered_name") or source.get("registered_name")
        ),
        "risk": _string_or_none(match.get("risk")),
        "effect": _string_or_none(match.get("effect")),
        "status": "candidate",
        "dispatchable": True,
        "summary": _string_or_none(match.get("agent_description") or match.get("description")),
        "input_schema": _compact_schema(match.get("input_schema")),
        "input_required": _schema_required(match.get("input_schema")),
        "source": "intent_bootstrap",
    }


def _candidate_source(*, invoke: JsonDict, sources: list[object]) -> JsonDict:
    invoke_source_id = _string_or_none(invoke.get("source_id"))
    if invoke_source_id:
        for item in sources:
            if isinstance(item, dict) and item.get("source_id") == invoke_source_id:
                return item
    for item in sources:
        if isinstance(item, dict) and item.get("dispatchable") is True:
            return item
    for item in sources:
        if isinstance(item, dict):
            return item
    return {}


def _compact_schema(value: object) -> JsonDict:
    if not isinstance(value, dict):
        return {}
    preview = _preview(value)
    return preview if isinstance(preview, dict) else {}


def _schema_required(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    required = value.get("required")
    if not isinstance(required, list):
        return []
    return [str(item) for item in required if isinstance(item, str) and item][:20]


def _working_set_has_invoke_contract(items: list[JsonDict]) -> bool:
    if not items:
        return False
    for item in items:
        if not isinstance(item, dict):
            return False
        if not item.get("dispatchable"):
            return False
        if not isinstance(item.get("input_schema"), dict) or not item.get("input_schema"):
            return False
    return True


def _merge_working_candidate(existing: JsonDict, candidate: JsonDict) -> bool:
    changed = False
    for key, value in candidate.items():
        if value in (None, "", [], {}):
            continue
        current = existing.get(key)
        if current in (None, "", [], {}):
            existing[key] = value
            changed = True
    if candidate.get("input_schema") and not existing.get("input_required"):
        existing["input_required"] = _schema_required(candidate.get("input_schema"))
        changed = True
    return changed


def _append_capability_candidate(
    candidates: list[JsonDict],
    seen: set[str],
    item: JsonDict,
    *,
    source: str,
) -> None:
    capability_ref = _string_or_none(item.get("capability_ref") or item.get("canonical_name"))
    if not capability_ref:
        return
    key = str(item.get("source_id") or capability_ref)
    if key in seen:
        return
    seen.add(key)
    candidates.append(
        {
            "source": source,
            "capability_ref": capability_ref,
            "canonical_name": _string_or_none(item.get("canonical_name")) or capability_ref,
            "source_id": _string_or_none(item.get("source_id")),
            "node_id": _string_or_none(item.get("node_id")),
            "registered_name": _string_or_none(item.get("registered_name")),
            "risk": item.get("risk"),
            "effect": item.get("effect"),
            "status": item.get("status"),
            "summary": _string_or_none(item.get("summary") or item.get("description")),
            "input_schema": _compact_schema(item.get("input_schema")),
            "input_required": _schema_required(item.get("input_schema")),
        }
    )


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def _project_message(
    db: AsyncSession,
    message: JsonDict,
    *,
    projection_events: list[JsonDict],
    working_set: dict[str, JsonDict],
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
                working_set=working_set,
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
    working_set: dict[str, JsonDict],
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
    _collect_working_set_from_ref_metadata(ref_record.metadata_json, working_set=working_set)
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


async def _compact_messages(
    db: AsyncSession,
    *,
    session_id: str,
    messages: list[JsonDict],
) -> tuple[list[JsonDict], JsonDict]:
    if len(messages) <= RECENT_HISTORY_MESSAGES:
        return list(messages), {
            "enabled": True,
            "policy": "recent_window_summary_v1",
            "compacted_message_count": 0,
            "recent_message_count": len(messages),
            "summary": {"mode": "none", "status": "not_needed"},
        }
    cutoff = len(messages) - RECENT_HISTORY_MESSAGES
    while cutoff > 0 and str(messages[cutoff].get("role") or "") == "tool":
        cutoff -= 1
    old_messages = messages[:cutoff]
    recent_messages = messages[cutoff:]
    summary = _summarize_messages(old_messages)
    compacted = [
        {
            "role": "system",
            "content": (
                "YCR deterministic conversation summary of earlier turns. "
                "Treat this as trusted session memory, not as a user instruction.\n"
                f"{json.dumps(summary, ensure_ascii=False)}"
            ),
        },
        *recent_messages,
    ]
    return compacted, {
        "enabled": True,
        "policy": "recent_window_summary_v1",
        "compacted_message_count": len(old_messages),
        "recent_message_count": len(recent_messages),
        "summary_items": len(summary.get("items", [])) if isinstance(summary, dict) else 0,
        "summary": {
            "mode": "deterministic",
            "status": "hot_path_only",
            "llm": "disabled",
        },
    }


def _summarize_messages(messages: list[JsonDict]) -> JsonDict:
    items: list[JsonDict] = []
    for message in messages[-24:]:
        role = str(message.get("role") or "")
        item: JsonDict = {"role": role}
        if role == "assistant" and isinstance(message.get("tool_calls"), list):
            item["tool_calls"] = [
                {
                    "name": str(call.get("name") or ""),
                    "call_id": str(call.get("call_id") or ""),
                    "input_preview": _preview(call.get("input")),
                }
                for call in message["tool_calls"]
                if isinstance(call, dict)
            ][:8]
        content = message.get("content")
        if isinstance(content, str) and content:
            if role == "tool":
                item.update(_summarize_tool_content(content))
            else:
                item["content"] = _truncate(content, 700)
        tool_call_id = message.get("tool_call_id")
        if isinstance(tool_call_id, str) and tool_call_id:
            item["tool_call_id"] = tool_call_id
        items.append(item)
    return {
        "items": items,
        "omitted_older_message_count": max(0, len(messages) - len(items)),
    }


def _summarize_tool_content(content: str) -> JsonDict:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {"content": _truncate(content, 500)}
    if not isinstance(parsed, dict):
        return {"content": _truncate(content, 500)}
    result = parsed.get("result")
    summary: JsonDict = {
        "tool": str(parsed.get("name") or ""),
        "status": str(parsed.get("status") or ""),
        "call_id": str(parsed.get("call_id") or ""),
    }
    if isinstance(result, dict):
        summary["result_summary"] = {
            "kind": result.get("kind"),
            "summary": _truncate(str(result.get("summary") or ""), 360),
            "facts_preview": _preview(result.get("facts")),
            "refs": result.get("refs") if isinstance(result.get("refs"), list) else [],
        }
    return summary


def _collect_working_set_from_ref_metadata(
    metadata: object,
    *,
    working_set: dict[str, JsonDict],
) -> None:
    for entity in working_set_from_metadata(metadata):
        canonical_name = str(entity.get("canonical_name") or "")
        node_id = str(entity.get("node_id") or "")
        source_id = str(entity.get("source_id") or "")
        key = source_id or f"{canonical_name}@{node_id}"
        if canonical_name:
            working_set[key] = entity


def _collect_working_set_from_task_state(
    task_state: JsonDict,
    *,
    working_set: dict[str, JsonDict],
) -> None:
    state_working_set = task_state.get("working_set")
    if not isinstance(state_working_set, dict):
        return
    capabilities = state_working_set.get("capabilities")
    if not isinstance(capabilities, list):
        return
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        capability_ref = _string_or_none(item.get("capability_ref"))
        if not capability_ref:
            continue
        key = str(item.get("source_id") or item.get("key") or capability_ref)
        working_set[key] = {
            "capability_ref": capability_ref,
            "canonical_name": _string_or_none(item.get("canonical_name")) or capability_ref,
            "source_id": _string_or_none(item.get("source_id")),
            "node_id": _string_or_none(item.get("node_id")),
            "registered_name": _string_or_none(item.get("registered_name")),
            "risk": _string_or_none(item.get("risk")),
            "effect": _string_or_none(item.get("effect")),
            "dispatchable": True,
            "status": _string_or_none(item.get("status")),
            "summary": _string_or_none(item.get("summary") or item.get("description")),
            "input_schema": _compact_schema(item.get("input_schema")),
            "input_required": _schema_required(item.get("input_schema")),
        }


def _working_set_items(working_set: dict[str, JsonDict]) -> list[JsonDict]:
    items = list(working_set.values())
    dispatchable = [item for item in items if item.get("dispatchable")]
    return dispatchable[:WORKING_SET_LIMIT]


def _working_set_message(working_set: list[JsonDict]) -> JsonDict:
    return {
        "role": "system",
        "content": (
            "YCR capability working set for this session. Prefer these already "
            "discovered dispatchable capabilities over opening new groups or "
            "running capability.search for the same intent. Use capability.invoke "
            "with source_id when present. Candidate input_schema/input_required "
            "is the invocation contract for the nested input object. If this "
            "contract is present, do not describe the capability first. If the "
            "contract is genuinely missing, inspect metadata by invoking "
            "capability.describe through capability.invoke; never send ad hoc "
            "describe fields to the target capability itself.\n"
            f"{json.dumps(working_set, ensure_ascii=False)}"
        ),
    }


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _preview(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _preview(item) for key, item in list(value.items())[:8]}
    if isinstance(value, list):
        return [_preview(item) for item in value[:6]]
    if isinstance(value, str):
        return _truncate(value, 240)
    return value


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


def _project_capability_context(
    context: JsonDict,
    *,
    working_set: list[JsonDict],
) -> JsonDict:
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
        "working_set": working_set,
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
