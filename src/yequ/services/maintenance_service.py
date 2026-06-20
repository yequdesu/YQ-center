"""Maintenance service — create, approve, run, cancel MaintenancePlans."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.maintenance_plan import (
    MaintenancePlan,
    MaintenanceRun,
    MaintenanceStep,
)


def _make_plan_id() -> str:
    return f"plan_{uuid.uuid4().hex[:16]}"


def _make_step_id() -> str:
    return f"step_{uuid.uuid4().hex[:16]}"


def _make_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:16]}"


async def create_plan(
    db: AsyncSession,
    *,
    goal: str,
    actor_id: str,
    target_node_id: str,
    steps: list[dict],
    session_id: str | None = None,
    risk: str = "maintenance",
    max_total_duration_sec: int | None = None,
    rollback_strategy: str | None = None,
    execution_mode: str = "auto",
) -> MaintenancePlan:
    """Create a MaintenancePlan with steps."""
    now = datetime.now(datetime.UTC)
    plan = MaintenancePlan(
        plan_id=_make_plan_id(),
        goal=goal,
        actor_id=actor_id,
        session_id=session_id,
        target_node_id=target_node_id,
        risk=risk,
        status="draft",
        max_total_duration_sec=max_total_duration_sec,
        rollback_strategy=rollback_strategy,
        created_at=now,
    )
    db.add(plan)
    await db.flush()

    for i, step_data in enumerate(steps):
        step = MaintenanceStep(
            step_id=_make_step_id(),
            plan_id=plan.plan_id,
            seq=i + 1,
            function_name=step_data["function_name"],
            input_data=step_data.get("input", {}),
            depends_on=step_data.get("depends_on"),
            continue_on_failure=step_data.get("continue_on_failure", False),
            timeout_sec=step_data.get("timeout_sec", 30),
            resource_keys=step_data.get("resource_keys"),
            expected_result_schema=step_data.get("expected_result_schema"),
            status="pending",
        )
        db.add(step)

    await db.commit()
    return plan


async def approve_plan(
    db: AsyncSession,
    plan: MaintenancePlan,
    approval_id: str,
) -> MaintenancePlan:
    """Approve a plan (links an existing approval)."""
    plan.status = "approved"
    plan.approval_id = approval_id
    plan.approved_at = datetime.now(datetime.UTC)
    await db.commit()
    return plan


async def run_plan(
    db: AsyncSession,
    plan: MaintenancePlan,
) -> MaintenanceRun:
    """Start a MaintenanceRun for an approved plan."""
    now = datetime.now(datetime.UTC)
    run = MaintenanceRun(
        run_id=_make_run_id(),
        plan_id=plan.plan_id,
        status="running",
        started_at=now,
    )
    db.add(run)
    plan.status = "running"
    plan.started_at = now
    await db.commit()
    return run


async def get_steps(db: AsyncSession, plan_id: str) -> list[MaintenanceStep]:
    """Get steps for a plan, ordered by seq."""
    result = await db.execute(
        select(MaintenanceStep)
        .where(MaintenanceStep.plan_id == plan_id)
        .order_by(MaintenanceStep.seq)
    )
    return list(result.scalars().all())
