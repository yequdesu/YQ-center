"""Admin endpoints for Agent sessions and durable turn history."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.api.deps import get_admin_token, get_db
from yequ.models.agent_message import AgentMessage as AgentMessageModel
from yequ.models.agent_turn import AgentTurn, AgentTurnEvent
from yequ.models.session import Session

router = APIRouter(prefix="/admin", tags=["admin"])


class RenameSessionRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=128)


@router.get("/sessions")
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[dict[str, object]]:
    """List all active sessions, sorted by most recent activity."""
    from sqlalchemy import func

    from yequ.agent.agent_stream import is_session_running

    result = await db.execute(
        select(Session)
        .where(Session.status == "active")
        .order_by(Session.updated_at.desc().nullslast())
        .limit(100)
    )
    sessions = result.scalars().all()

    session_ids = [s.session_id for s in sessions]
    previews: dict[str, str] = {}
    counts: dict[str, int] = {}
    if session_ids:
        count_result = await db.execute(
            select(
                AgentMessageModel.session_id,
                func.count(AgentMessageModel.id).label("cnt"),
            )
            .where(AgentMessageModel.session_id.in_(session_ids))
            .group_by(AgentMessageModel.session_id)
        )
        for row in count_result:
            counts[row.session_id] = row.cnt

        for sid in session_ids:
            preview_result = await db.execute(
                select(AgentMessageModel)
                .where(
                    AgentMessageModel.session_id == sid,
                    AgentMessageModel.role == "user",
                )
                .order_by(AgentMessageModel.created_at.desc())
                .limit(1)
            )
            last_msg = preview_result.scalar_one_or_none()
            if last_msg and last_msg.content:
                previews[sid] = last_msg.content[:80]

    return [
        {
            "session_id": s.session_id,
            "actor_id": s.actor_id,
            "status": s.status,
            "execution_mode": s.execution_mode,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "closed_at": s.closed_at.isoformat() if s.closed_at else None,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            "label": s.label or s.session_id[:8],
            "last_message_preview": previews.get(s.session_id, ""),
            "message_count": counts.get(s.session_id, 0),
            "running": is_session_running(s.session_id),
        }
        for s in sessions
    ]


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> dict[str, object]:
    """Get a session by ID."""
    result = await db.execute(select(Session).where(Session.session_id == session_id))
    sess = result.scalar_one_or_none()
    if sess is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")
    message_result = await db.execute(
        select(AgentMessageModel)
        .where(AgentMessageModel.session_id == session_id)
        .order_by(AgentMessageModel.created_at.asc())
    )
    messages = [_agent_message_dict(m) for m in message_result.scalars().all()]
    turn_result = await db.execute(
        select(AgentTurn)
        .where(AgentTurn.session_id == session_id)
        .order_by(AgentTurn.started_at.asc(), AgentTurn.id.asc())
    )
    turns = [_agent_turn_dict(t) for t in turn_result.scalars().all()]
    return {
        "session_id": sess.session_id,
        "actor_type": sess.actor_type,
        "actor_id": sess.actor_id,
        "status": sess.status,
        "execution_mode": sess.execution_mode,
        "started_at": sess.started_at.isoformat() if sess.started_at else None,
        "updated_at": sess.updated_at.isoformat() if sess.updated_at else None,
        "closed_at": sess.closed_at.isoformat() if sess.closed_at else None,
        "close_reason": sess.close_reason,
        "metadata": sess.metadata_,
        "label": sess.label or sess.session_id[:8],
        "messages": messages,
        "turns": turns,
    }


@router.get("/sessions/{session_id}/messages")
async def list_session_messages(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[dict[str, object]]:
    """Return persisted messages for one Agent session."""
    result = await db.execute(select(Session).where(Session.session_id == session_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")

    message_result = await db.execute(
        select(AgentMessageModel)
        .where(AgentMessageModel.session_id == session_id)
        .order_by(AgentMessageModel.created_at.asc())
    )
    return [_agent_message_dict(m) for m in message_result.scalars().all()]


@router.get("/sessions/{session_id}/turns")
async def list_session_turns(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[dict[str, object]]:
    """Return persisted Agent turns for one session."""
    result = await db.execute(select(Session).where(Session.session_id == session_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")

    turn_result = await db.execute(
        select(AgentTurn)
        .where(AgentTurn.session_id == session_id)
        .order_by(AgentTurn.started_at.asc(), AgentTurn.id.asc())
    )
    return [_agent_turn_dict(t) for t in turn_result.scalars().all()]


@router.get("/agent-turns/{turn_id}/events")
async def list_agent_turn_events(
    turn_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[dict[str, object]]:
    """Return the durable SSE event stream for one Agent turn."""
    result = await db.execute(select(AgentTurn).where(AgentTurn.turn_id == turn_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=f"Agent turn {turn_id!r} not found")

    event_result = await db.execute(
        select(AgentTurnEvent)
        .where(AgentTurnEvent.turn_id == turn_id)
        .order_by(AgentTurnEvent.seq.asc(), AgentTurnEvent.created_at.asc())
    )
    return [_agent_turn_event_dict(e) for e in event_result.scalars().all()]


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: str,
    body: RenameSessionRequest,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> dict[str, str]:
    """Rename a session."""
    result = await db.execute(select(Session).where(Session.session_id == session_id))
    sess = result.scalar_one_or_none()
    if sess is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")
    sess.label = body.label
    await db.commit()
    return {"session_id": session_id, "label": body.label}


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> None:
    """Delete a session and its message history."""
    result = await db.execute(select(Session).where(Session.session_id == session_id))
    sess = result.scalar_one_or_none()
    if sess is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")

    await db.execute(
        sql_delete(AgentMessageModel).where(AgentMessageModel.session_id == session_id)
    )
    await db.execute(sql_delete(AgentTurnEvent).where(AgentTurnEvent.session_id == session_id))
    await db.execute(sql_delete(AgentTurn).where(AgentTurn.session_id == session_id))
    await db.delete(sess)
    await db.commit()


def _agent_message_dict(message: AgentMessageModel) -> dict[str, object]:
    return {
        "message_id": message.message_id,
        "session_id": message.session_id,
        "role": message.role,
        "content": message.content,
        "tool_call_id": message.tool_call_id,
        "tool_calls": message.tool_calls or [],
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def _agent_turn_dict(turn: AgentTurn) -> dict[str, object]:
    return {
        "turn_id": turn.turn_id,
        "session_id": turn.session_id,
        "trace_id": turn.trace_id,
        "provider_name": turn.provider_name,
        "target_node_id": turn.target_node_id,
        "execution_mode": turn.execution_mode,
        "status": turn.status,
        "prompt": turn.prompt,
        "error_code": turn.error_code,
        "error_message": turn.error_message,
        "started_at": turn.started_at.isoformat() if turn.started_at else None,
        "updated_at": turn.updated_at.isoformat() if turn.updated_at else None,
        "completed_at": turn.completed_at.isoformat() if turn.completed_at else None,
        "metadata": turn.metadata_ or {},
    }


def _agent_turn_event_dict(event: AgentTurnEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "turn_id": event.turn_id,
        "session_id": event.session_id,
        "trace_id": event.trace_id,
        "seq": event.seq,
        "event_type": event.event_type,
        "data": event.data or {},
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }
