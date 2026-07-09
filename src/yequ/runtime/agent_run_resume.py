"""AgentRun event application and backend resume entry points."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.agent_run import AgentRun
from yequ.runtime.agent_run_events import append_agent_run_event
from yequ.runtime.observation_reducer import reduce_agent_run_event
from yequ.runtime.replanner import decide_next_action
from yequ.runtime.task_state import get_task_state

JsonDict = dict[str, Any]


async def append_event_and_reduce(
    db: AsyncSession,
    run: AgentRun,
    *,
    event_type: str,
    source: str,
    payload: JsonDict | None = None,
    turn_id: str | None = None,
    step_id: str | None = None,
    plan_id: str | None = None,
    plan_step_id: str | None = None,
) -> JsonDict:
    event = await append_agent_run_event(
        db,
        run,
        event_type=event_type,
        source=source,
        payload=payload,
        turn_id=turn_id,
        step_id=step_id,
        plan_id=plan_id,
        plan_step_id=plan_step_id,
    )
    return await reduce_agent_run_event(db, run, event)


async def append_event_to_latest_waiting_run(
    db: AsyncSession,
    *,
    session_id: str | None,
    operation_id: str | None = None,
    approval_id: str | None = None,
    event_type: str,
    source: str,
    payload: JsonDict | None = None,
) -> list[str]:
    if not session_id:
        return []
    result = await db.execute(
        select(AgentRun)
        .where(AgentRun.session_id == session_id)
        .where(AgentRun.status.in_({"waiting_operation", "waiting_approval"}))
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(50)
    )
    updated: list[str] = []
    for run in result.scalars().all():
        waiting = (run.metadata_json or {}).get("waiting")
        if not isinstance(waiting, dict):
            continue
        if operation_id and waiting.get("operation_id") != operation_id:
            continue
        if approval_id and waiting.get("approval_id") != approval_id:
            continue
        enriched_payload = dict(waiting)
        enriched_payload.update(payload or {})
        state = await append_event_and_reduce(
            db,
            run,
            event_type=event_type,
            source=source,
            payload=enriched_payload,
            plan_id=_string((run.metadata_json or {}).get("plan_id")),
        )
        decision = decide_next_action(state)
        metadata = dict(run.metadata_json or {})
        metadata["task_state"] = state
        metadata["last_replanner_decision"] = decision.to_dict()
        run.metadata_json = metadata
        updated.append(run.run_id)
    await db.flush()
    return updated


def get_run_replanner_decision(run: AgentRun) -> JsonDict:
    return decide_next_action(get_task_state(run)).to_dict()


def _string(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
