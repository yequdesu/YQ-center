"""Durable AgentRunEvent append/query helpers."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.agent_run import AgentRun, AgentRunEvent

JsonDict = dict[str, Any]


async def append_agent_run_event(
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
    occurred_at: datetime | None = None,
) -> AgentRunEvent:
    next_seq = await _next_run_seq(db, run.id)
    event = AgentRunEvent(
        event_id=f"arevt_{secrets.token_hex(8)}",
        run_record_id=run.id,
        run_id=run.run_id,
        session_id=run.session_id,
        turn_id=turn_id if turn_id is not None else run.turn_id,
        step_id=step_id,
        plan_id=plan_id,
        plan_step_id=plan_step_id,
        event_type=event_type,
        source=source,
        payload_json=_bounded_payload(payload or {}),
        occurred_at=occurred_at or datetime.now(UTC),
        seq=next_seq,
    )
    db.add(event)
    await db.flush()
    return event


async def list_agent_run_events(
    db: AsyncSession,
    *,
    run_id: str,
) -> list[AgentRunEvent]:
    result = await db.execute(
        select(AgentRunEvent)
        .where(AgentRunEvent.run_id == run_id)
        .order_by(AgentRunEvent.seq.asc(), AgentRunEvent.created_at.asc())
    )
    return list(result.scalars().all())


def agent_run_event_dict(event: AgentRunEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "session_id": event.session_id,
        "turn_id": event.turn_id,
        "step_id": event.step_id,
        "plan_id": event.plan_id,
        "plan_step_id": event.plan_step_id,
        "event_type": event.event_type,
        "source": event.source,
        "payload": event.payload_json or {},
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        "seq": event.seq,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


async def _next_run_seq(db: AsyncSession, run_record_id: str) -> int:
    result = await db.execute(
        select(func.max(AgentRunEvent.seq)).where(AgentRunEvent.run_record_id == run_record_id)
    )
    current = result.scalar_one_or_none()
    return int(current or 0) + 1


def _bounded_payload(payload: JsonDict) -> JsonDict:
    """Drop accidental huge strings from runtime events.

    Raw tool/provider bodies must live in YCR ContextRef or artifacts. AgentRunEvent
    stores facts and references only.
    """

    return _bound_value(payload)


def _bound_value(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) > 4000:
            return {
                "truncated": True,
                "chars": len(value),
                "preview": value[:1200],
            }
        return value
    if isinstance(value, dict):
        return {str(key): _bound_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_bound_value(item) for item in value[:200]]
    return value
