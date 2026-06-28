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
from yequ.agent.runtime_state import TERMINAL_AGENT_RUN_STATUSES, status_for_stream_event
from yequ.models.agent_turn import AgentTurn, AgentTurnEvent


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
    async with yequ_db.async_session_factory() as session:
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
                metadata_=metadata or {},
            )
        )
        await session.commit()
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
            turn.updated_at = now
            if turn.status in TERMINAL_AGENT_RUN_STATUSES:
                turn.completed_at = now
            if event_type in {"agent.failed", "agent.provider.failed"}:
                turn.error_code = str(data.get("error_code") or "") or None
                turn.error_message = str(data.get("message") or "") or None
        await session.commit()


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
