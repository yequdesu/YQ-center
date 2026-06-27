"""Maintenance service — create, approve, run, cancel MaintenancePlans."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.capability import Capability
from yequ.models.maintenance_plan import (
    MaintenancePlan,
    MaintenanceRun,
    MaintenanceStep,
)
from yequ.models.node import Node
from yequ.models.timeline import TimelineEvent
from yequ.services.timeline_writer import add_timeline_event


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
    now = datetime.now(UTC)
    cap_by_name: dict[str, Capability] = {}
    node_result = await db.execute(select(Node).where(Node.node_id == target_node_id))
    node = node_result.scalar_one_or_none()
    if node is not None:
        cap_result = await db.execute(
            select(Capability).where(
                Capability.node_record_id == node.id,
                Capability.capability_type == "function",
                Capability.is_active,
            )
        )
        cap_by_name = {cap.name: cap for cap in cap_result.scalars().all()}

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
        capability = cap_by_name.get(step_data["function_name"])
        step_risk = step_data.get("risk") or (capability.risk if capability else "safe")
        step_effect = capability.effect if capability else "read"
        requires_approval = step_data.get("requires_approval")
        if requires_approval is None:
            requires_approval = step_effect in ("write", "destructive") or step_risk in (
                "maintenance",
                "destructive",
                "catastrophic",
            )

        resource_keys = step_data.get("resource_keys")
        if resource_keys is None and capability and capability.resource_keys:
            resource_keys = list(capability.resource_keys)

        step = MaintenanceStep(
            step_id=_make_step_id(),
            plan_id=plan.plan_id,
            seq=i + 1,
            function_name=step_data["function_name"],
            input_data=step_data.get("input", {}),
            depends_on=step_data.get("depends_on"),
            continue_on_failure=step_data.get("continue_on_failure", False),
            timeout_sec=step_data.get(
                "timeout_sec",
                capability.timeout_sec if capability and capability.timeout_sec else 30,
            ),
            resource_keys=resource_keys,
            expected_result_schema=step_data.get("expected_result_schema"),
            status="pending",
            kind=step_data.get("kind", "check"),
            condition=step_data.get("condition", "always"),
            requires_approval=requires_approval,
            risk=step_risk,
            rollback_hint=step_data.get("rollback_hint"),
        )
        db.add(step)

    await db.commit()

    # Write plan.created timeline event
    timeline_event = TimelineEvent(
        global_seq=0,
        event_type="maintenance.plan.created",
        actor_type="system",
        actor_id=actor_id,
        node_id=target_node_id,
        data={
            "plan_id": plan.plan_id,
            "goal": goal,
            "risk": risk,
            "execution_mode": execution_mode,
            "step_count": len(steps),
        },
        timestamp=datetime.now(UTC),
    )
    await add_timeline_event(db, timeline_event)

    return plan


async def approve_plan(
    db: AsyncSession,
    plan: MaintenancePlan,
    approval_id: str,
) -> MaintenancePlan:
    """Approve a plan (links an existing approval)."""
    plan.status = "approved"
    plan.approval_id = approval_id
    plan.approved_at = datetime.now(UTC)
    await db.commit()

    # Write plan.approved timeline event
    timeline_event = TimelineEvent(
        global_seq=0,
        event_type="maintenance.plan.approved",
        actor_type="system",
        actor_id=approval_id,
        node_id=plan.target_node_id,
        data={
            "plan_id": plan.plan_id,
            "goal": plan.goal,
            "approval_id": approval_id,
        },
        timestamp=datetime.now(UTC),
    )
    await add_timeline_event(db, timeline_event)

    # Also write approval.approved for L2 audit chain
    approved_event = TimelineEvent(
        global_seq=0,
        event_type="approval.approved",
        actor_type="admin",
        actor_id="admin",
        node_id=plan.target_node_id,
        data={
            "approval_id": approval_id,
            "plan_id": plan.plan_id,
            "goal": plan.goal,
        },
        timestamp=datetime.now(UTC),
    )
    await add_timeline_event(db, approved_event)

    return plan


async def run_plan(
    db: AsyncSession,
    plan: MaintenancePlan,
) -> MaintenanceRun:
    """Start a MaintenanceRun for an approved plan."""
    now = datetime.now(UTC)
    run = MaintenanceRun(
        run_id=_make_run_id(),
        plan_id=plan.plan_id,
        status="running",
        started_at=now,
    )
    db.add(run)
    plan.status = "running"
    plan.started_at = now

    # Consume approval if plan has one
    if plan.approval_id:
        from yequ.models.approval import ApprovalRequest
        from yequ.services.approval_service import consume_approval

        apv_result = await db.execute(
            select(ApprovalRequest).where(ApprovalRequest.approval_id == plan.approval_id)
        )
        approval = apv_result.scalar_one_or_none()
        if approval:
            await consume_approval(db, approval, invocation_id=run.run_id)

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
