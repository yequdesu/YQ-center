"""Durable AgentRun checkpoint service."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.agent_run import AgentRun, AgentRunStep

RESUMABLE_AGENT_RUN_STATUSES = {"waiting_operation", "waiting_approval", "failed"}


async def create_agent_run(
    db: AsyncSession,
    *,
    session_id: str,
    provider_name: str,
    execution_mode: str,
    target_node_id: str | None,
    user_message: str | None,
    trace_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> AgentRun:
    now = datetime.now(UTC)
    run = AgentRun(
        run_id=f"arun_{secrets.token_hex(8)}",
        session_id=session_id,
        trace_id=trace_id,
        provider_name=provider_name,
        status="created",
        execution_mode=execution_mode,
        target_node_id=target_node_id,
        user_message=user_message,
        started_at=now,
        metadata_json=metadata or {},
    )
    db.add(run)
    await db.flush()
    return run


async def append_agent_run_step(
    db: AsyncSession,
    run: AgentRun,
    *,
    step_index: int,
    step_type: str,
    status: str,
    input_data: dict[str, Any] | None = None,
    output_data: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AgentRunStep:
    now = datetime.now(UTC)
    step = AgentRunStep(
        step_id=f"arstep_{secrets.token_hex(8)}",
        run_record_id=run.id,
        step_index=step_index,
        step_type=step_type,
        status=status,
        input_data=input_data,
        output_data=output_data,
        error_code=error_code,
        error_message=error_message,
        started_at=now,
        completed_at=now,
        metadata_json=metadata or {},
    )
    db.add(step)
    await db.flush()
    return step


async def update_agent_run_status(
    db: AsyncSession,
    run: AgentRun,
    *,
    status: str,
    final_message: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AgentRun:
    run.status = status
    if final_message is not None:
        run.final_message = final_message
    if error_code is not None:
        run.error_code = error_code
    if error_message is not None:
        run.error_message = error_message
    if metadata:
        merged = dict(run.metadata_json or {})
        merged.update(metadata)
        run.metadata_json = merged
    if status in {"succeeded", "failed", "cancelled"}:
        run.completed_at = run.completed_at or datetime.now(UTC)
    await db.flush()
    return run


async def get_agent_run_projection(db: AsyncSession, run_id: str) -> dict[str, object]:
    result = await db.execute(select(AgentRun).where(AgentRun.run_id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise ValueError(f"AgentRun {run_id!r} not found")
    steps_result = await db.execute(
        select(AgentRunStep)
        .where(AgentRunStep.run_record_id == run.id)
        .order_by(AgentRunStep.step_index.asc(), AgentRunStep.created_at.asc())
    )
    steps = list(steps_result.scalars().all())
    return agent_run_dict(run, steps)


async def get_last_resumable_agent_run(
    db: AsyncSession,
    *,
    session_id: str,
) -> dict[str, object]:
    result = await db.execute(
        select(AgentRun)
        .where(
            AgentRun.session_id == session_id,
            AgentRun.status.in_(RESUMABLE_AGENT_RUN_STATUSES),
        )
        .order_by(AgentRun.created_at.desc())
        .limit(1)
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise ValueError(f"No resumable AgentRun for session {session_id!r}")
    return await get_agent_run_projection(db, run.run_id)


def agent_run_dict(run: AgentRun, steps: list[AgentRunStep]) -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "session_id": run.session_id,
        "trace_id": run.trace_id,
        "provider_name": run.provider_name,
        "status": run.status,
        "execution_mode": run.execution_mode,
        "target_node_id": run.target_node_id,
        "user_message": run.user_message,
        "final_message": run.final_message,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "metadata": run.metadata_json or {},
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "steps": [
            {
                "step_id": step.step_id,
                "step_index": step.step_index,
                "step_type": step.step_type,
                "status": step.status,
                "input_data": step.input_data,
                "output_data": step.output_data,
                "error_code": step.error_code,
                "error_message": step.error_message,
                "metadata": step.metadata_json or {},
                "started_at": step.started_at.isoformat() if step.started_at else None,
                "completed_at": step.completed_at.isoformat() if step.completed_at else None,
            }
            for step in steps
        ],
    }
