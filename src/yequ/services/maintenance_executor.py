"""Maintenance Plan Executor — runs approved plans step by step."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.maintenance_plan import (
    MaintenancePlan,
    MaintenanceRun,
    MaintenanceStep,
    RollbackHint,
)
from yequ.models.timeline import TimelineEvent
from yequ.services.invocation_service import create_invocation, start_invocation
from yequ.services.job_service import create_job


def _make_artifact_id() -> str:
    return f"art_{uuid.uuid4().hex[:16]}"


def _make_hint_id() -> str:
    return f"hint_{uuid.uuid4().hex[:16]}"


async def execute_plan_run(
    db: AsyncSession,
    plan: MaintenancePlan,
    run: MaintenanceRun,
    *,
    approval_id: str = "",
    dry_run: bool = False,
) -> MaintenanceRun:
    """Execute all steps of a plan in order.

    Rules:
    - Steps execute in seq order
    - depends_on steps must be completed before the step starts
    - continue_on_failure=False stops the run on failure
    - L2 write steps without approval_id go to waiting_approval
    - Each step creates an Invocation + Job
    - Per-step ResourceLock
    """
    result = await db.execute(
        select(MaintenanceStep)
        .where(MaintenanceStep.plan_id == plan.plan_id)
        .order_by(MaintenanceStep.seq)
    )
    steps = list(result.scalars().all())

    completed_steps: set[str] = set()

    # Write run started timeline event
    await _write_maintenance_timeline(
        db, "maintenance.run.started", run.run_id, plan.plan_id, plan.target_node_id,
    )

    for step in steps:
        # Check depends_on
        if step.depends_on:
            unmet = [s for s in step.depends_on if s not in completed_steps]
            if unmet:
                step.status = "skipped"
                step.error = f"Unmet dependencies: {unmet}"
                await db.flush()
                continue

        # Write step started timeline event
        await _write_maintenance_timeline(
            db, "maintenance.step.started", run.run_id, plan.plan_id, plan.target_node_id,
            step_id=step.step_id, function_name=step.function_name,
        )

        step.status = "running"
        step.started_at = datetime.now(UTC)
        run.current_step_id = step.step_id

        # Create invocation + job for this step
        try:
            inv = await create_invocation(
                db,
                actor_type="system",
                actor_id="maintenance_executor",
                session_id=plan.session_id,
                function_name=step.function_name,
                input_payload=step.input_data or {},
                target_node_id=plan.target_node_id,
                execution_mode="auto",
            )
            inv = start_invocation(inv)

            job = await create_job(
                db,
                invocation_id=inv.invocation_id,
                node_id=plan.target_node_id,
                function_name=step.function_name,
                input_payload=step.input_data or {},
                timeout_sec=step.timeout_sec,
                resource_keys=step.resource_keys or [],
                dry_run=dry_run,
                approval_id=approval_id if approval_id else None,
            )

            step.invocation_id = inv.invocation_id
            step.job_id = job.job_id
            step.status = "running"
            await db.flush()

        except Exception as e:
            step.status = "failed"
            step.error = str(e)[:500]
            step.finished_at = datetime.now(UTC)

            # Write step failed timeline event
            await _write_maintenance_timeline(
                db, "maintenance.step.failed", run.run_id, plan.plan_id,
                plan.target_node_id,
                step_id=step.step_id, function_name=step.function_name,
                job_id=step.job_id, invocation_id=step.invocation_id,
                error=str(e)[:500],
            )

            if not step.continue_on_failure:
                run.status = "failed"
                run.finished_at = datetime.now(UTC)
                await db.flush()
                await _write_maintenance_timeline(
                    db, "maintenance.run.failed", run.run_id, plan.plan_id,
                    plan.target_node_id,
                )
                await db.commit()
                return run

            await db.flush()
            continue

        # Write step completed timeline event
        await _write_maintenance_timeline(
            db, "maintenance.step.completed", run.run_id, plan.plan_id,
            plan.target_node_id,
            step_id=step.step_id, function_name=step.function_name,
            job_id=step.job_id, invocation_id=step.invocation_id,
        )

        completed_steps.add(step.step_id)

    run.current_step_id = None
    run.status = "succeeded"
    await db.flush()
    return run


async def finalize_run(
    db: AsyncSession,
    plan: MaintenancePlan,
    run: MaintenanceRun,
) -> MaintenanceRun:
    """Check all steps and finalize the run status."""
    result = await db.execute(
        select(MaintenanceStep).where(MaintenanceStep.plan_id == plan.plan_id)
    )
    steps = list(result.scalars().all())

    statuses = {s.status for s in steps}
    if statuses == {"succeeded"}:
        run.status = "succeeded"
    elif "failed" in statuses:
        run.status = "failed"
    elif statuses == {"pending"}:
        run.status = "pending"
    else:
        run.status = "partially_succeeded"

    now = datetime.now(UTC)
    run.finished_at = now
    plan.finished_at = now
    plan.status = run.status

    # Build summary
    run.summary = {
        "total_steps": len(steps),
        "succeeded": len([s for s in steps if s.status == "succeeded"]),
        "failed": len([s for s in steps if s.status == "failed"]),
        "skipped": len([s for s in steps if s.status == "skipped"]),
    }

    # Write run completed/failed timeline event
    event_type = (
        "maintenance.run.completed"
        if run.status == "succeeded"
        else "maintenance.run.failed"
    )
    await _write_maintenance_timeline(
        db, event_type, run.run_id, plan.plan_id, plan.target_node_id,
    )

    await db.commit()
    return run


async def add_rollback_hint(
    db: AsyncSession,
    run_id: str,
    step_id: str,
    reason: str,
    recommended_action: str,
    function_name: str | None = None,
    input_data: dict | None = None,
    risk: str = "maintenance",
) -> RollbackHint:
    """Add a rollback hint for a failed step."""
    hint = RollbackHint(
        hint_id=_make_hint_id(),
        run_id=run_id,
        step_id=step_id,
        reason=reason,
        recommended_action=recommended_action,
        function_name=function_name,
        input_data=input_data,
        risk=risk,
        created_at=datetime.now(UTC),
    )
    db.add(hint)
    await db.commit()
    return hint


async def update_step_result(
    db: AsyncSession,
    step: MaintenanceStep,
    status: str,
    result: dict | None = None,
    error: str | None = None,
) -> MaintenanceStep:
    """Update a step with its final result."""
    step.status = status
    step.result = result
    step.error = error
    step.finished_at = datetime.now(UTC)
    await db.commit()
    return step


async def _write_maintenance_timeline(
    db: AsyncSession,
    event_type: str,
    run_id: str,
    plan_id: str,
    target_node_id: str,
    *,
    step_id: str | None = None,
    function_name: str | None = None,
    job_id: str | None = None,
    invocation_id: str | None = None,
    error: str | None = None,
) -> None:
    """Write a maintenance timeline event."""
    result = await db.execute(select(func.max(TimelineEvent.global_seq)))
    max_seq = result.scalar() or 0

    event = TimelineEvent(
        global_seq=max_seq + 1,
        event_type=event_type,
        actor_type="system",
        actor_id="maintenance_executor",
        node_id=target_node_id,
        job_id=job_id,
        invocation_id=invocation_id,
        data={
            "run_id": run_id,
            "plan_id": plan_id,
            "step_id": step_id,
            "function_name": function_name,
            "error": error,
        },
        timestamp=datetime.now(UTC),
    )
    db.add(event)
    await db.flush()
