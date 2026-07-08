"""Durable AgentRun checkpoint service."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.agent_plan import AgentPlan
from yequ.models.agent_run import AgentRun, AgentRunEvent, AgentRunStep
from yequ.models.agent_turn import AgentTurn
from yequ.runtime.agent_status import TERMINAL_AGENT_RUN_STATUSES

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
    from yequ.runtime.task_state import TASK_STATE_KEY, initial_task_state

    run_metadata = dict(metadata or {})
    run_metadata.setdefault(TASK_STATE_KEY, initial_task_state(user_message))
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
        metadata_json=run_metadata,
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
    if run.status in TERMINAL_AGENT_RUN_STATUSES and status != run.status:
        raise ValueError(
            f"AgentRun {run.run_id!r} is terminal ({run.status}) "
            f"and cannot transition to {status!r}"
        )
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
    if status in TERMINAL_AGENT_RUN_STATUSES:
        run.completed_at = run.completed_at or datetime.now(UTC)
    await db.flush()
    return run


async def promote_approval_wait_to_operation_wait(
    db: AsyncSession,
    *,
    session_id: str | None,
    approval_id: str,
    operation_id: str,
    wait_handle: dict[str, Any] | None,
    invocation_id: str | None,
    job_id: str | None,
) -> list[str]:
    """Move the paused AgentRun from approval wait to the real execution Operation.

    The approval Operation only represents the decision gate. After approve-and-run
    creates the actual Job Operation, the original AgentRun must wait on that new
    operation so the server-side operation reporter can resume the task.
    """

    if not session_id or not approval_id or not operation_id:
        return []

    result = await db.execute(
        select(AgentRun)
        .where(AgentRun.session_id == session_id)
        .where(AgentRun.status == "waiting_approval")
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(50)
    )
    promoted_run_ids: list[str] = []
    updated_turn_ids: set[str] = set()
    updated_plan_tool_calls: dict[str, str | None] = {}

    for run in result.scalars().all():
        metadata = dict(run.metadata_json or {})
        waiting = metadata.get("waiting")
        if not isinstance(waiting, dict) or waiting.get("approval_id") != approval_id:
            continue

        promoted_waiting = dict(waiting)
        promoted_waiting.update(
            {
                "status": "waiting_operation",
                "operation_id": operation_id,
                "wait_handle": wait_handle or {},
                "approval_id": approval_id,
                "invocation_id": invocation_id,
                "job_id": job_id,
            }
        )
        metadata["waiting"] = promoted_waiting
        metadata["approved_operation"] = {
            "approval_id": approval_id,
            "operation_id": operation_id,
            "invocation_id": invocation_id,
            "job_id": job_id,
        }
        run.status = "waiting_operation"
        run.metadata_json = metadata
        promoted_run_ids.append(run.run_id)
        if run.turn_id:
            updated_turn_ids.add(run.turn_id)
        plan_id = metadata.get("plan_id")
        if isinstance(plan_id, str) and plan_id:
            call_id = promoted_waiting.get("call_id")
            updated_plan_tool_calls[plan_id] = call_id if isinstance(call_id, str) else None

    for turn_id in updated_turn_ids:
        turn_result = await db.execute(select(AgentTurn).where(AgentTurn.turn_id == turn_id))
        turn = turn_result.scalar_one_or_none()
        if turn is not None and turn.status == "waiting_approval":
            turn.status = "waiting_operation"
            metadata = dict(turn.metadata_ or {})
            metadata["approved_operation"] = {
                "approval_id": approval_id,
                "operation_id": operation_id,
                "invocation_id": invocation_id,
                "job_id": job_id,
            }
            turn.metadata_ = metadata

    for plan_id, tool_call_id in updated_plan_tool_calls.items():
        plan_result = await db.execute(select(AgentPlan).where(AgentPlan.plan_id == plan_id))
        plan = plan_result.scalar_one_or_none()
        if plan is None or plan.status != "waiting_approval":
            continue
        from yequ.runtime.agent_plan_service import update_agent_plan_status

        await update_agent_plan_status(
            db,
            plan_id,
            status="waiting_operation",
            operation_id=operation_id,
            tool_call_id=tool_call_id,
        )

    await db.flush()
    return promoted_run_ids


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
    events_result = await db.execute(
        select(AgentRunEvent)
        .where(AgentRunEvent.run_record_id == run.id)
        .order_by(AgentRunEvent.seq.asc(), AgentRunEvent.created_at.asc())
    )
    steps = list(steps_result.scalars().all())
    events = list(events_result.scalars().all())
    return agent_run_dict(run, steps, events)


async def get_latest_agent_run_for_session(
    db: AsyncSession,
    *,
    session_id: str,
) -> dict[str, object] | None:
    result = await db.execute(
        select(AgentRun)
        .where(AgentRun.session_id == session_id)
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(20)
    )
    run = next(
        (
            item
            for item in result.scalars().all()
            if not _is_internal_run(item.metadata_json)
        ),
        None,
    )
    if run is None:
        return None
    return await get_agent_run_projection(db, run.run_id)


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


def agent_run_dict(
    run: AgentRun,
    steps: list[AgentRunStep],
    events: list[AgentRunEvent] | None = None,
) -> dict[str, object]:
    from yequ.runtime.agent_run_events import agent_run_event_dict
    from yequ.runtime.task_state import get_task_state

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
        "task_state": get_task_state(run),
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
        "events": [agent_run_event_dict(event) for event in events or []],
    }


def _is_internal_run(metadata: object) -> bool:
    if not isinstance(metadata, dict):
        return False
    return bool(metadata.get("internal")) or metadata.get("run_kind") in {
        "operation_report",
        "approval_resume",
    }
