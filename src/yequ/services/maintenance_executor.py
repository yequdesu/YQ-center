"""Maintenance Plan Executor — runs approved plans step by step."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.agent.agent_service import _wait_invocation_terminal
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
                step.skip_reason = f"Unmet dependencies: {unmet}"
                step.finished_at = datetime.now(UTC)
                await db.flush()
                continue

        # --- Condition check ---
        if step.condition == "if_previous_unhealthy":
            deps = step.depends_on or []
            unhealthy = False
            for dep_seq in [int(d) for d in deps]:
                dep_step = next((s for s in steps if s.seq == dep_seq), None)
                if dep_step:
                    result = dep_step.result or {}
                    found = result.get("found", True)
                    state = result.get("state", result.get("status", ""))
                    if not found or str(state).lower() != "running":
                        unhealthy = True
            if not unhealthy:
                step.status = "skipped"
                step.skip_reason = "previous check showed healthy"
                step.finished_at = datetime.now(UTC)
                await db.flush()
                continue
        elif step.condition == "after_repair":
            # Execute verify step regardless
            pass
        elif step.condition == "if_previous_failed":
            deps = step.depends_on or []
            dep_steps_r = await db.execute(
                select(MaintenanceStep)
                .where(MaintenanceStep.plan_id == plan.plan_id)
                .where(MaintenanceStep.seq.in_([int(d) for d in deps]))
            )
            dep_steps = list(dep_steps_r.scalars().all())
            any_failed = any(ds.status == "failed" for ds in dep_steps)
            if not any_failed:
                step.status = "skipped"
                step.skip_reason = "previous step did not fail"
                step.finished_at = datetime.now(UTC)
                await db.flush()
                continue
        elif step.condition == "manual":
            step.status = "skipped"
            step.skip_reason = "manual step requires operator intervention"
            step.finished_at = datetime.now(UTC)
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
            start_invocation(inv)

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
            await db.commit()  # MUST commit before waiting — otherwise poll sees nothing

            # Wait for step to reach terminal state
            deadline = datetime.now(UTC) + timedelta(seconds=step.timeout_sec + 30)
            final_status = await _wait_invocation_terminal(inv.invocation_id, deadline)

            # Collect result
            from yequ.models.invocation import Invocation
            inv_result = await db.execute(
                select(Invocation).where(Invocation.invocation_id == inv.invocation_id)
            )
            inv_final = inv_result.scalar_one_or_none()

            step.finished_at = datetime.now(UTC)
            if final_status == "succeeded":
                step.status = "succeeded"
                step.result = inv_final.result if inv_final else {}
                completed_steps.add(step.step_id)
                await _write_maintenance_timeline(
                    db, "maintenance.step.completed", run.run_id, plan.plan_id,
                    plan.target_node_id,
                    step_id=step.step_id, function_name=step.function_name,
                    job_id=step.job_id, invocation_id=step.invocation_id,
                )
            else:
                step.status = "failed"
                step.error = f"Step ended with {final_status}"
                await _write_maintenance_timeline(
                    db, "maintenance.step.failed", run.run_id, plan.plan_id,
                    plan.target_node_id,
                    step_id=step.step_id, function_name=step.function_name,
                    job_id=step.job_id, invocation_id=step.invocation_id,
                    error=step.error,
                )
                if not step.continue_on_failure:
                    run.status = "failed"
                    run.finished_at = datetime.now(UTC)
                    await _write_maintenance_timeline(
                        db, "maintenance.run.failed", run.run_id, plan.plan_id,
                        plan.target_node_id,
                    )
                    await db.commit()
                    return run

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

    now = datetime.now(UTC)
    run.finished_at = now
    plan.finished_at = now

    # Build summary
    run.summary = {
        "total_steps": len(steps),
        "succeeded": len([s for s in steps if s.status == "succeeded"]),
        "failed": len([s for s in steps if s.status == "failed"]),
        "skipped": len([s for s in steps if s.status == "skipped"]),
    }
    # Run status: failed > partially_succeeded > succeeded (skipped not counted as failed)
    if any(s.status == "failed" for s in steps):
        run.status = "failed"
    elif all(s.status in ("succeeded", "skipped") for s in steps):
        run.status = "succeeded"
    else:
        run.status = "partially_succeeded"
    plan.status = run.status

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
