"""Generic Agent Runtime Plan service."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.agent_plan import AgentPlan, AgentPlanStep

TERMINAL_AGENT_PLAN_STATUSES = {"succeeded", "failed", "cancelled"}


async def create_agent_plan(
    db: AsyncSession,
    *,
    session_id: str,
    run_id: str,
    provider_name: str,
    execution_mode: str,
    target_node_id: str | None,
    objective: str,
    metadata: dict[str, Any] | None = None,
) -> AgentPlan:
    plan = AgentPlan(
        plan_id=f"aplan_{secrets.token_hex(8)}",
        session_id=session_id,
        run_id=run_id,
        status="active",
        objective=_trim_objective(objective),
        provider_name=provider_name,
        execution_mode=execution_mode,
        target_node_id=target_node_id,
        metadata_json=metadata or {},
    )
    db.add(plan)
    await db.flush()
    db.add(
        AgentPlanStep(
            step_id=f"apstep_{secrets.token_hex(8)}",
            plan_record_id=plan.id,
            step_index=1,
            kind="agent_task",
            title=_trim_objective(objective),
            status="active",
            metadata_json={"source": "agent.invoke.stream"},
        )
    )
    await db.flush()
    return plan


async def update_agent_plan_status(
    db: AsyncSession,
    plan_id: str,
    *,
    status: str,
    operation_id: str | None = None,
    tool_call_id: str | None = None,
) -> AgentPlan:
    result = await db.execute(select(AgentPlan).where(AgentPlan.plan_id == plan_id))
    plan = result.scalar_one_or_none()
    if plan is None:
        raise ValueError(f"AgentPlan {plan_id!r} not found")
    if plan.status in TERMINAL_AGENT_PLAN_STATUSES and status != plan.status:
        raise ValueError(
            f"AgentPlan {plan.plan_id!r} is terminal ({plan.status}) "
            f"and cannot transition to {status!r}"
        )
    plan.status = status
    if status in TERMINAL_AGENT_PLAN_STATUSES:
        plan.completed_at = plan.completed_at or datetime.now(UTC)
    step = await _first_step(db, plan)
    if step is not None:
        step.status = _step_status_for_plan(status)
        if operation_id:
            step.operation_id = operation_id
        if tool_call_id:
            step.tool_call_id = tool_call_id
        if step.status in TERMINAL_AGENT_PLAN_STATUSES | {"waiting_operation", "waiting_approval"}:
            step.completed_at = step.completed_at or datetime.now(UTC)
    await db.flush()
    return plan


async def get_agent_plan_projection(db: AsyncSession, plan_id: str) -> dict[str, object]:
    result = await db.execute(select(AgentPlan).where(AgentPlan.plan_id == plan_id))
    plan = result.scalar_one_or_none()
    if plan is None:
        raise ValueError(f"AgentPlan {plan_id!r} not found")
    steps_result = await db.execute(
        select(AgentPlanStep)
        .where(AgentPlanStep.plan_record_id == plan.id)
        .order_by(AgentPlanStep.step_index.asc(), AgentPlanStep.created_at.asc())
    )
    return agent_plan_dict(plan, list(steps_result.scalars().all()))


async def get_latest_agent_plan_for_session(
    db: AsyncSession,
    *,
    session_id: str,
) -> dict[str, object] | None:
    result = await db.execute(
        select(AgentPlan)
        .where(AgentPlan.session_id == session_id)
        .order_by(AgentPlan.created_at.desc(), AgentPlan.id.desc())
        .limit(20)
    )
    plan = next(
        (
            item
            for item in result.scalars().all()
            if not _is_internal_plan(item.metadata_json)
        ),
        None,
    )
    if plan is None:
        return None
    steps_result = await db.execute(
        select(AgentPlanStep)
        .where(AgentPlanStep.plan_record_id == plan.id)
        .order_by(AgentPlanStep.step_index.asc(), AgentPlanStep.created_at.asc())
    )
    return agent_plan_dict(plan, list(steps_result.scalars().all()))


def agent_plan_dict(plan: AgentPlan, steps: list[AgentPlanStep]) -> dict[str, object]:
    return {
        "plan_id": plan.plan_id,
        "session_id": plan.session_id,
        "run_id": plan.run_id,
        "turn_id": plan.turn_id,
        "status": plan.status,
        "objective": plan.objective,
        "provider_name": plan.provider_name,
        "execution_mode": plan.execution_mode,
        "target_node_id": plan.target_node_id,
        "completed_at": plan.completed_at.isoformat() if plan.completed_at else None,
        "metadata": plan.metadata_json or {},
        "steps": [
            {
                "step_id": step.step_id,
                "step_index": step.step_index,
                "kind": step.kind,
                "title": step.title,
                "status": step.status,
                "operation_id": step.operation_id,
                "tool_call_id": step.tool_call_id,
                "completed_at": step.completed_at.isoformat() if step.completed_at else None,
                "metadata": step.metadata_json or {},
            }
            for step in steps
        ],
    }


async def _first_step(db: AsyncSession, plan: AgentPlan) -> AgentPlanStep | None:
    result = await db.execute(
        select(AgentPlanStep)
        .where(AgentPlanStep.plan_record_id == plan.id)
        .order_by(AgentPlanStep.step_index.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _step_status_for_plan(status: str) -> str:
    if status in {
        "succeeded",
        "failed",
        "cancelled",
        "waiting_operation",
        "waiting_approval",
    }:
        return status
    return "active"


def _trim_objective(value: str) -> str:
    text = " ".join(value.strip().split())
    return text[:500] if text else "Agent task"


def _is_internal_plan(metadata: object) -> bool:
    if not isinstance(metadata, dict):
        return False
    return bool(metadata.get("internal")) or metadata.get("run_kind") in {
        "operation_report",
        "approval_resume",
    }
