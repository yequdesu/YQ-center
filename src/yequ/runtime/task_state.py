"""Run-level TaskState for deterministic Agent runtime decisions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.agent_run import AgentRun

JsonDict = dict[str, Any]

TASK_STATE_KEY = "task_state"
TASK_COMPLETION_STATUSES = {
    "in_progress",
    "waiting_approval",
    "waiting_operation",
    "ask_user",
    "complete",
    "failed",
}


def initial_task_state(objective: str | None) -> JsonDict:
    text = " ".join((objective or "").strip().split())
    return {
        "objective": {"text": text[:1000], "created_from": "user_prompt"},
        "facts": [],
        "blockers": [],
        "pending_operations": [],
        "pending_approvals": [],
        "artifacts": [],
        "working_set": {"capabilities": [], "nodes": [], "artifacts": []},
        "completion": {
            "status": "in_progress",
            "criteria": [],
            "satisfied": [],
            "missing": [],
        },
        "last_decision": {"action": "continue_llm", "reason_code": "initial"},
    }


def get_task_state(run: AgentRun) -> JsonDict:
    metadata = run.metadata_json if isinstance(run.metadata_json, dict) else {}
    state = metadata.get(TASK_STATE_KEY)
    if not isinstance(state, dict):
        return initial_task_state(run.user_message)
    return normalize_task_state(state, objective=run.user_message)


async def set_task_state(db: AsyncSession, run: AgentRun, state: JsonDict) -> AgentRun:
    metadata = dict(run.metadata_json or {})
    metadata[TASK_STATE_KEY] = normalize_task_state(state, objective=run.user_message)
    run.metadata_json = metadata
    await db.flush()
    return run


def normalize_task_state(value: JsonDict, *, objective: str | None = None) -> JsonDict:
    state = deepcopy(value)
    base = initial_task_state(objective)
    for key, default_value in base.items():
        if key not in state:
            state[key] = deepcopy(default_value)
    if not isinstance(state.get("objective"), dict):
        state["objective"] = base["objective"]
    if not isinstance(state.get("facts"), list):
        state["facts"] = []
    if not isinstance(state.get("blockers"), list):
        state["blockers"] = []
    if not isinstance(state.get("pending_operations"), list):
        state["pending_operations"] = []
    if not isinstance(state.get("pending_approvals"), list):
        state["pending_approvals"] = []
    if not isinstance(state.get("artifacts"), list):
        state["artifacts"] = []
    if not isinstance(state.get("working_set"), dict):
        state["working_set"] = deepcopy(base["working_set"])
    for key in ["capabilities", "nodes", "artifacts"]:
        if not isinstance(state["working_set"].get(key), list):
            state["working_set"][key] = []
    if not isinstance(state.get("completion"), dict):
        state["completion"] = deepcopy(base["completion"])
    completion = state["completion"]
    if completion.get("status") not in TASK_COMPLETION_STATUSES:
        completion["status"] = "in_progress"
    for key in ["criteria", "satisfied", "missing"]:
        if not isinstance(completion.get(key), list):
            completion[key] = []
    if not isinstance(state.get("last_decision"), dict):
        state["last_decision"] = deepcopy(base["last_decision"])
    return state


def append_unique_item(items: list[Any], item: JsonDict, *, key: str) -> None:
    identity = item.get(key)
    if identity is None:
        items.append(item)
        return
    for index, existing in enumerate(items):
        if isinstance(existing, dict) and existing.get(key) == identity:
            merged = dict(existing)
            merged.update(item)
            items[index] = merged
            return
    items.append(item)


def remove_item_by_key(items: list[Any], *, key: str, value: object) -> None:
    items[:] = [
        item
        for item in items
        if not (isinstance(item, dict) and item.get(key) == value)
    ]
