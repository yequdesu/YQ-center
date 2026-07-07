"""Durable Agent turn/event state.

An AgentTurn is one user-visible /agent/invoke/stream request. AgentTurnEvent
stores the exact SSE event stream emitted for that turn, so the console can
rebuild UI state after refresh and backend services can reason about pending
approval/tool state without scraping chat text.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from yequ import db as yequ_db
from yequ.models.agent_plan import AgentPlan
from yequ.models.agent_run import AgentRun
from yequ.models.agent_turn import AgentTurn, AgentTurnEvent
from yequ.runtime.agent_status import (
    TERMINAL_AGENT_RUN_STATUSES,
    is_open_agent_status,
    status_for_stream_event,
)
from yequ.services.session_audit import record_session_audit_event


def make_turn_id() -> str:
    return f"turn_{uuid.uuid4().hex[:16]}"


async def create_agent_turn(
    *,
    session_id: str,
    prompt: str,
    provider_name: str,
    target_node_id: str | None,
    execution_mode: str,
    trace_id: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    turn_id = make_turn_id()
    metadata = metadata or {}
    async with yequ_db.async_session_factory() as session:
        if _is_internal_turn(metadata):
            await _fail_stale_internal_turns(
                session,
                session_id=session_id,
                now=now,
                replacement_turn_id=turn_id,
            )
        session.add(
            AgentTurn(
                turn_id=turn_id,
                session_id=session_id,
                trace_id=trace_id,
                provider_name=provider_name,
                target_node_id=target_node_id,
                execution_mode=execution_mode,
                status="created",
                prompt=prompt,
                started_at=now,
                updated_at=now,
                metadata_=metadata,
            )
        )
        await session.commit()
    record_session_audit_event(
        session_id,
        "agent.turn.created",
        {
            "turn_id": turn_id,
            "prompt": prompt,
            "provider_name": provider_name,
            "target_node_id": target_node_id,
            "execution_mode": execution_mode,
            "metadata": metadata,
        },
        turn_id=turn_id,
        trace_id=trace_id,
        source="agent.turn",
        event_time=now,
    )
    return turn_id


async def record_agent_turn_event(turn_id: str, event: dict[str, Any]) -> None:
    event_type = str(event.get("event_type") or "")
    session_id = str(event.get("session_id") or "")
    trace_id = str(event.get("trace_id") or "")
    data = _jsonish_dict(event.get("data"))
    now = _parse_event_time(event.get("timestamp"))

    async with yequ_db.async_session_factory() as session:
        seq_result = await session.execute(
            select(func.max(AgentTurnEvent.seq)).where(AgentTurnEvent.turn_id == turn_id)
        )
        seq = int(seq_result.scalar() or 0) + 1
        session.add(
            AgentTurnEvent(
                event_id=str(event.get("event_id") or f"evt_{uuid.uuid4().hex[:16]}"),
                turn_id=turn_id,
                session_id=session_id,
                trace_id=trace_id,
                seq=seq,
                event_type=event_type,
                data=data,
                created_at=now,
            )
        )

        turn_result = await session.execute(select(AgentTurn).where(AgentTurn.turn_id == turn_id))
        turn = turn_result.scalar_one_or_none()
        if turn is not None:
            status = _status_for_event(event_type, data)
            if status:
                turn.status = status
            if event_type == "stream.close":
                _finalize_unclosed_turn(turn, now)
            turn.updated_at = now
            if turn.status in TERMINAL_AGENT_RUN_STATUSES:
                turn.completed_at = now
            if event_type in {"agent.failed", "agent.provider.failed"}:
                turn.error_code = str(data.get("error_code") or "") or None
                turn.error_message = str(data.get("message") or "") or None
            await _link_agent_run_to_turn(session, turn, data)
        await session.commit()
    record_session_audit_event(
        session_id,
        event_type,
        {
            "event": event,
            "data": data,
        },
        turn_id=turn_id,
        trace_id=trace_id,
        source="agent.turn_event",
        event_time=now,
    )


async def list_agent_turns(session_id: str) -> list[AgentTurn]:
    async with yequ_db.async_session_factory() as session:
        result = await session.execute(
            select(AgentTurn)
            .where(AgentTurn.session_id == session_id)
            .order_by(AgentTurn.started_at.asc(), AgentTurn.id.asc())
        )
        return list(result.scalars().all())


async def list_agent_turn_events(turn_id: str) -> list[AgentTurnEvent]:
    async with yequ_db.async_session_factory() as session:
        result = await session.execute(
            select(AgentTurnEvent)
            .where(AgentTurnEvent.turn_id == turn_id)
            .order_by(AgentTurnEvent.seq.asc(), AgentTurnEvent.created_at.asc())
        )
        return list(result.scalars().all())


def _status_for_event(event_type: str, data: dict[str, Any]) -> str | None:
    return status_for_stream_event(event_type, str(data.get("error_code") or "") or None)


async def _fail_stale_internal_turns(
    session,
    *,
    session_id: str,
    now: datetime,
    replacement_turn_id: str,
) -> None:
    result = await session.execute(
        select(AgentTurn)
        .where(AgentTurn.session_id == session_id)
        .where(AgentTurn.status.notin_(TERMINAL_AGENT_RUN_STATUSES))
        .order_by(AgentTurn.started_at.asc(), AgentTurn.id.asc())
    )
    for turn in result.scalars().all():
        if not _is_internal_turn(turn.metadata_):
            continue
        if not is_open_agent_status(turn.status):
            continue
        turn.status = "failed"
        turn.error_code = "internal_turn_replaced"
        turn.error_message = (
            "Internal Agent turn was still open when a replacement internal turn started."
        )
        turn.completed_at = now
        turn.updated_at = now
        metadata = dict(turn.metadata_ or {})
        metadata["replaced_by_turn_id"] = replacement_turn_id
        turn.metadata_ = metadata


def _finalize_unclosed_turn(turn: AgentTurn, now: datetime) -> None:
    if not is_open_agent_status(turn.status):
        return
    turn.status = "failed"
    turn.error_code = "stream_closed_before_terminal"
    turn.error_message = "Agent stream closed before the turn reached a terminal or waiting state."
    turn.completed_at = now


def _is_internal_turn(metadata: object) -> bool:
    return isinstance(metadata, dict) and metadata.get("suppress_user_message") is True


async def _link_agent_run_to_turn(session, turn: AgentTurn, data: dict[str, Any]) -> None:
    run_id = data.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return
    result = await session.execute(select(AgentRun).where(AgentRun.run_id == run_id))
    run = result.scalar_one_or_none()
    if run is None or run.turn_id:
        return
    run.turn_id = turn.turn_id
    plan_result = await session.execute(select(AgentPlan).where(AgentPlan.run_id == run_id))
    for plan in plan_result.scalars().all():
        if not plan.turn_id:
            plan.turn_id = turn.turn_id


def _parse_event_time(value: object) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)


def _jsonish_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}
