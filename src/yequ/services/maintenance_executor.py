"""Maintenance Plan Executor — runs approved plans step by step."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.agent.agent_service import _wait_invocation_terminal
from yequ.models.maintenance_plan import (
    MaintenanceArtifact,
    MaintenancePlan,
    MaintenanceRun,
    MaintenanceStep,
    RollbackHint,
)
from yequ.models.invocation import Invocation
from yequ.models.job import Job as JobModel
from yequ.models.timeline import TimelineEvent
from yequ.services.invocation_service import create_invocation, start_invocation
from yequ.services.job_service import create_job


def _make_artifact_id() -> str:
    return f"art_{uuid.uuid4().hex[:16]}"


def _make_hint_id() -> str:
    return f"hint_{uuid.uuid4().hex[:16]}"


async def _write_artifact(
    db: AsyncSession,
    *,
    run_id: str,
    step_id: str | None,
    invocation_id: str | None,
    job_id: str | None,
    kind: str,
    name: str,
    data: dict | None = None,
    summary: dict | None = None,
    content_type: str = "application/json",
    plan_id: str = "",
    target_node_id: str = "",
) -> MaintenanceArtifact:
    """Write a MaintenanceArtifact + timeline event."""
    now = datetime.now(UTC)
    artifact = MaintenanceArtifact(
        artifact_id=_make_artifact_id(),
        run_id=run_id,
        step_id=step_id,
        invocation_id=invocation_id,
        job_id=job_id,
        kind=kind,
        name=name,
        content_type=content_type,
        data=data,
        summary=summary,
        created_at=now,
    )
    db.add(artifact)
    await db.flush()

    # Resolve approval_id from plan for timeline linkage
    resolved_approval_id = ""
    if plan_id:
        plan_result = await db.execute(
            select(MaintenancePlan).where(MaintenancePlan.plan_id == plan_id)
        )
        plan_obj = plan_result.scalar_one_or_none()
        if plan_obj and plan_obj.approval_id:
            resolved_approval_id = plan_obj.approval_id

    # Timeline event
    result = await db.execute(select(func.max(TimelineEvent.global_seq)))
    max_seq = result.scalar() or 0
    event = TimelineEvent(
        global_seq=max_seq + 1,
        event_type="maintenance.artifact.created",
        actor_type="system",
        actor_id="maintenance_executor",
        node_id=target_node_id,
        data={
            "artifact_id": artifact.artifact_id,
            "run_id": run_id,
            "step_id": step_id or "",
            "kind": kind,
            "name": name,
            "plan_id": plan_id,
            "approval_id": resolved_approval_id,
        },
        timestamp=now,
    )
    db.add(event)
    await db.flush()
    return artifact


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
    - Artifacts written at each step boundary
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
        approval_id=plan.approval_id,
    )

    for step in steps:
        # Check depends_on — consider both succeeded AND skipped as "completed"
        if step.depends_on:
            unmet = [s for s in step.depends_on if s not in completed_steps]
            # Also check if dep was skipped (which is acceptable for after_repair)
            dep_steps_r = await db.execute(
                select(MaintenanceStep)
                .where(MaintenanceStep.plan_id == plan.plan_id)
                .where(MaintenanceStep.seq.in_([int(d) for d in step.depends_on]))
            )
            dep_steps_list = list(dep_steps_r.scalars().all())
            all_deps_done = all(
                ds.status in ("succeeded", "skipped")
                for ds in dep_steps_list
            )
            if not all_deps_done:
                step.status = "skipped"
                step.skip_reason = f"Unmet dependencies: {unmet}"
                step.finished_at = datetime.now(UTC)
                await _write_maintenance_timeline(
                    db, "maintenance.step.skipped", run.run_id, plan.plan_id, plan.target_node_id,
                    step_id=step.step_id, function_name=step.function_name,
                    error=step.skip_reason,
                )
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
                step.skip_reason = "previous_healthy"
                step.finished_at = datetime.now(UTC)
                await _write_maintenance_timeline(
                    db, "maintenance.step.skipped", run.run_id, plan.plan_id, plan.target_node_id,
                    step_id=step.step_id, function_name=step.function_name,
                    error=step.skip_reason,
                )
                completed_steps.add(step.step_id)
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
                await _write_maintenance_timeline(
                    db, "maintenance.step.skipped", run.run_id, plan.plan_id, plan.target_node_id,
                    step_id=step.step_id, function_name=step.function_name,
                    error=step.skip_reason,
                )
                completed_steps.add(step.step_id)
                await db.flush()
                continue
        elif step.condition == "manual":
            step.status = "skipped"
            step.skip_reason = "manual step requires operator intervention"
            step.finished_at = datetime.now(UTC)
            await _write_maintenance_timeline(
                db, "maintenance.step.skipped", run.run_id, plan.plan_id, plan.target_node_id,
                step_id=step.step_id, function_name=step.function_name,
                error=step.skip_reason,
            )
            completed_steps.add(step.step_id)
            await db.flush()
            continue

        # --- Write "before" artifact for repair/write steps ---
        if step.kind in ("repair", "write", "rollback"):
            before_data = _build_before_data(steps, step)
            await _write_artifact(
                db,
                run_id=run.run_id,
                step_id=step.step_id,
                invocation_id=None,
                job_id=None,
                kind="before",
                name=f"before_{step.function_name}",
                data=before_data,
                summary={"step_kind": step.kind, "function_name": step.function_name},
                plan_id=plan.plan_id,
                target_node_id=plan.target_node_id,
            )

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
            from yequ.config import get_settings
            from yequ.services.capability_resolver import resolve_function

            resolved = await resolve_function(
                db,
                step.function_name,
                target_node_id=plan.target_node_id,
                settings=get_settings(),
            )
            if resolved is None or not resolved.available:
                reason = (
                    resolved.unavailable_reason
                    if resolved and resolved.unavailable_reason
                    else f"No online node has capability {step.function_name!r}"
                )
                raise RuntimeError(reason)

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

            # Use plan's approval_id if not explicitly passed
            effective_approval_id = approval_id or plan.approval_id or None
            # Repair steps must never be dry_run
            effective_dry_run = False if step.kind == "repair" else dry_run

            job = await create_job(
                db,
                invocation_id=inv.invocation_id,
                node_id=plan.target_node_id,
                runtime_id=resolved.runtime_id,
                function_name=step.function_name,
                input_payload=step.input_data or {},
                execution_requirements_snapshot=resolved.execution_requirements,
                timeout_sec=step.timeout_sec,
                resource_keys=step.resource_keys or [],
                dry_run=effective_dry_run,
                approval_id=effective_approval_id,
            )

            step.invocation_id = inv.invocation_id
            step.job_id = job.job_id
            step.status = "running"
            await db.commit()  # MUST commit before waiting — otherwise poll sees nothing

            # --- Test failure injection ---
            # Allows stable remote testing of rollback_recommended / error artifact paths.
            # Two modes:
            #   1. test.maintenance.repair_fail / test.maintenance.verify_fail functions
            #   2. __test_fail_stage in input_data (only honoured in test mode)
            should_test_fail = False
            test_fail_code = ""
            test_fail_message = ""

            if step.function_name == "test.maintenance.repair_fail":
                should_test_fail = True
                test_fail_code = "TEST_REPAIR_FAILED"
                test_fail_message = "Simulated repair failure for testing"
            elif step.function_name == "test.maintenance.verify_fail":
                should_test_fail = True
                test_fail_code = "TEST_VERIFY_FAILED"
                test_fail_message = "Simulated verify failure for testing"
            else:
                # Check __test_fail_stage (only in test mode)
                from yequ.config import get_settings
                if get_settings().test_mode:
                    fail_stage = (step.input_data or {}).get("__test_fail_stage", "")
                    if fail_stage == "repair" and step.kind == "repair":
                        should_test_fail = True
                        test_fail_code = "TEST_REPAIR_FAILED"
                        test_fail_message = f"Test-injected repair failure at {step.function_name}"
                    elif fail_stage == "verify" and step.kind == "verify":
                        should_test_fail = True
                        test_fail_code = "TEST_VERIFY_FAILED"
                        test_fail_message = f"Test-injected verify failure at {step.function_name}"

            if should_test_fail:
                # Directly mark invocation + job as failed
                inv_final_result = await db.execute(
                    select(Invocation).where(Invocation.invocation_id == inv.invocation_id)
                )
                inv_to_fail = inv_final_result.scalar_one_or_none()
                if inv_to_fail:
                    inv_to_fail.status = "failed"
                    inv_to_fail.finished_at = datetime.now(UTC)
                job_result = await db.execute(
                    select(JobModel).where(JobModel.job_id == step.job_id)
                )
                job_to_fail = job_result.scalar_one_or_none()
                if job_to_fail:
                    job_to_fail.status = "failed"
                    job_to_fail.error_code = test_fail_code
                    job_to_fail.error_message = test_fail_message
                    job_to_fail.finished_at = datetime.now(UTC)
                await db.commit()

                final_status = "failed"
                inv_final = inv_to_fail
            else:
                # Wait for step to reach terminal state
                deadline = datetime.now(UTC) + timedelta(seconds=step.timeout_sec + 30)
                final_status = await _wait_invocation_terminal(inv.invocation_id, deadline)

                # Collect result
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
                    db, "maintenance.step.succeeded", run.run_id, plan.plan_id,
                    plan.target_node_id,
                    step_id=step.step_id, function_name=step.function_name,
                    job_id=step.job_id, invocation_id=step.invocation_id,
                )

                # --- Write success artifacts ---
                if step.kind == "check":
                    # check_result artifact
                    await _write_artifact(
                        db,
                        run_id=run.run_id,
                        step_id=step.step_id,
                        invocation_id=step.invocation_id,
                        job_id=step.job_id,
                        kind="check_result",
                        name=f"check_{step.function_name}",
                        data=step.result,
                        summary=_build_check_summary(step),
                        plan_id=plan.plan_id,
                        target_node_id=plan.target_node_id,
                    )
                elif step.kind in ("repair", "write", "rollback"):
                    # after artifact
                    await _write_artifact(
                        db,
                        run_id=run.run_id,
                        step_id=step.step_id,
                        invocation_id=step.invocation_id,
                        job_id=step.job_id,
                        kind="after",
                        name=f"after_{step.function_name}",
                        data=step.result,
                        summary={"step_kind": step.kind, "function_name": step.function_name,
                                 "status": "succeeded"},
                        plan_id=plan.plan_id,
                        target_node_id=plan.target_node_id,
                    )
                    # rollback_hint artifact if step has one
                    if step.rollback_hint:
                        before_data = _build_before_data(steps, step)
                        hint_data = dict(step.rollback_hint)
                        if before_data:
                            hint_data["before_summary"] = {
                                "available": before_data.get("available", True),
                                "source_step_id": before_data.get("source_step_id"),
                            }
                        await _write_artifact(
                            db,
                            run_id=run.run_id,
                            step_id=step.step_id,
                            invocation_id=step.invocation_id,
                            job_id=step.job_id,
                            kind="rollback_hint",
                            name=f"rollback_{step.function_name}",
                            data=hint_data,
                            summary={"action": hint_data.get("action", "unknown")},
                            plan_id=plan.plan_id,
                            target_node_id=plan.target_node_id,
                        )
                elif step.kind == "verify":
                    # verify_result artifact
                    await _write_artifact(
                        db,
                        run_id=run.run_id,
                        step_id=step.step_id,
                        invocation_id=step.invocation_id,
                        job_id=step.job_id,
                        kind="verify_result",
                        name=f"verify_{step.function_name}",
                        data=step.result,
                        summary=_build_check_summary(step),
                        plan_id=plan.plan_id,
                        target_node_id=plan.target_node_id,
                    )
            else:
                step.status = "failed"
                # Backfill error from Job
                job_result = await db.execute(
                    select(JobModel).where(JobModel.job_id == step.job_id)
                )
                failed_job = job_result.scalar_one_or_none()
                if failed_job and failed_job.error_code:
                    step.error = f"{failed_job.error_code}: {failed_job.error_message or 'no details'}"
                else:
                    step.error = f"Step ended with {final_status}"
                await _write_maintenance_timeline(
                    db, "maintenance.step.failed", run.run_id, plan.plan_id,
                    plan.target_node_id,
                    step_id=step.step_id, function_name=step.function_name,
                    job_id=step.job_id, invocation_id=step.invocation_id,
                    error=step.error,
                )

                # --- Write error artifact ---
                error_data = {
                    "error_code": failed_job.error_code if failed_job else "unknown",
                    "error_message": step.error,
                    "function_name": step.function_name,
                    "job_id": step.job_id or "",
                    "invocation_id": step.invocation_id or "",
                }
                await _write_artifact(
                    db,
                    run_id=run.run_id,
                    step_id=step.step_id,
                    invocation_id=step.invocation_id,
                    job_id=step.job_id,
                    kind="error",
                    name=f"error_{step.function_name}",
                    data=error_data,
                    summary={"step_kind": step.kind, "status": "failed"},
                    plan_id=plan.plan_id,
                    target_node_id=plan.target_node_id,
                )

                # Write rollback_hint artifact from step.rollback_hint on failure
                if step.rollback_hint and step.kind in ("repair", "write", "rollback"):
                    before_data = _build_before_data(steps, step)
                    hint_data = dict(step.rollback_hint)
                    if before_data:
                        hint_data["before_summary"] = {
                            "available": before_data.get("available", True),
                            "source_step_id": before_data.get("source_step_id"),
                        }
                    await _write_artifact(
                        db,
                        run_id=run.run_id,
                        step_id=step.step_id,
                        invocation_id=step.invocation_id,
                        job_id=step.job_id,
                        kind="rollback_hint",
                        name=f"rollback_{step.function_name}",
                        data=hint_data,
                        summary={"action": hint_data.get("action", "unknown")},
                        plan_id=plan.plan_id,
                        target_node_id=plan.target_node_id,
                    )

                # --- Determine run status based on which step failed ---
                if step.kind == "check":
                    # Check failed → run failed, no rollback needed
                    run.status = "failed"
                    run.rollback_recommended = False
                elif step.kind in ("repair", "write", "rollback"):
                    # Repair failed → rollback recommended
                    run.status = "rollback_recommended"
                    run.rollback_recommended = True
                    await _write_rollback_recommended(
                        db, run, step, plan,
                        reason=f"Repair step {step.function_name} failed: {step.error}",
                    )
                elif step.kind == "verify":
                    # Verify failed → rollback recommended (repair may have succeeded)
                    run.status = "rollback_recommended"
                    run.rollback_recommended = True
                    await _write_rollback_recommended(
                        db, run, step, plan,
                        reason=f"Verify step {step.function_name} failed after repair: {step.error}",
                    )

                run.finished_at = datetime.now(UTC)
                # NOTE: do NOT write maintenance.run.failed here —
                # finalize_run() writes the single run terminal event.
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

            # --- Write error artifact for exception ---
            await _write_artifact(
                db,
                run_id=run.run_id,
                step_id=step.step_id,
                invocation_id=step.invocation_id,
                job_id=step.job_id,
                kind="error",
                name=f"error_{step.function_name}",
                data={
                    "error_code": "exception",
                    "error_message": str(e)[:500],
                    "function_name": step.function_name,
                    "job_id": step.job_id or "",
                    "invocation_id": step.invocation_id or "",
                },
                summary={"step_kind": step.kind, "status": "exception"},
                plan_id=plan.plan_id,
                target_node_id=plan.target_node_id,
            )

            # Same rollback logic as above
            if step.kind == "check":
                run.status = "failed"
                run.rollback_recommended = False
            elif step.kind in ("repair", "write", "rollback"):
                run.status = "rollback_recommended"
                run.rollback_recommended = True
                await _write_rollback_recommended(
                    db, run, step, plan,
                    reason=f"Repair step {step.function_name} exception: {str(e)[:200]}",
                )
            elif step.kind == "verify":
                run.status = "rollback_recommended"
                run.rollback_recommended = True
                await _write_rollback_recommended(
                    db, run, step, plan,
                    reason=f"Verify step {step.function_name} exception after repair: {str(e)[:200]}",
                )

            if not step.continue_on_failure:
                run.finished_at = datetime.now(UTC)
                await db.flush()
                # NOTE: do NOT write maintenance.run.failed here —
                # finalize_run() writes the single run terminal event.
                await db.commit()
                return run

            await db.flush()
            continue

    run.current_step_id = None
    run.status = "succeeded"
    run.rollback_recommended = False
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
    # Run status: failed > rollback_recommended > partially_succeeded > succeeded
    # (keep status set by executor if already failed/rollback_recommended)
    if run.status not in ("failed", "rollback_recommended"):
        if any(s.status == "failed" for s in steps):
            # Determine if any failed step was repair/write → rollback_recommended
            failed_repair = any(
                s.status == "failed" and s.kind in ("repair", "write", "rollback")
                for s in steps
            )
            failed_verify = any(
                s.status == "failed" and s.kind == "verify"
                for s in steps
            )
            if failed_repair or failed_verify:
                run.status = "rollback_recommended"
                run.rollback_recommended = True
            else:
                run.status = "failed"
        elif all(s.status in ("succeeded", "skipped") for s in steps):
            run.status = "succeeded"
        else:
            run.status = "partially_succeeded"
    plan.status = run.status

    # Write run completed/failed timeline event
    event_type = (
        "maintenance.run.succeeded"
        if run.status == "succeeded"
        else "maintenance.run.failed"
    )
    await _write_maintenance_timeline(
        db, event_type, run.run_id, plan.plan_id, plan.target_node_id,
        approval_id=plan.approval_id,
    )

    # Write rollback_recommended timeline if applicable and not already written
    # (execute_plan_run may have already written it for early-return failures)
    if run.rollback_recommended:
        existing = await db.execute(
            select(TimelineEvent).where(
                TimelineEvent.event_type == "maintenance.rollback.recommended",
                TimelineEvent.data.op("->>")("run_id") == run.run_id,
            ).limit(1)
        )
        if not existing.scalar_one_or_none():
            await _write_rollback_recommended_timeline(db, run, plan)

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


def _build_before_data(steps: list[MaintenanceStep], current_step: MaintenanceStep) -> dict:
    """Build 'before' data from the previous check step's result."""
    # Find the check step that this step depends on
    if current_step.depends_on:
        for dep_seq in current_step.depends_on:
            try:
                seq_num = int(dep_seq)
            except (ValueError, TypeError):
                continue
            dep_step = next((s for s in steps if s.seq == seq_num), None)
            if dep_step and dep_step.kind == "check" and dep_step.result:
                return {
                    "available": True,
                    "source_step_id": dep_step.step_id,
                    "source_kind": dep_step.kind,
                    "source_function_name": dep_step.function_name,
                    "check_result": dep_step.result,
                }
    # Fallback: search for any preceding check step
    for s in sorted(steps, key=lambda x: x.seq):
        if s.seq < current_step.seq and s.kind == "check" and s.result:
            return {
                "available": True,
                "source_step_id": s.step_id,
                "source_kind": s.kind,
                "source_function_name": s.function_name,
                "check_result": s.result,
            }
    return {"available": False, "reason": "no_previous_check_result"}


def _build_check_summary(step: MaintenanceStep) -> dict:
    """Build a short summary from a check/verify step result."""
    result = step.result or {}
    return {
        "function_name": step.function_name,
        "service_name": step.input_data.get("name", "") if step.input_data else "",
        "status": result.get("status", result.get("state", "unknown")),
        "found": result.get("found", result.get("status") is not None),
    }


async def _write_rollback_recommended(
    db: AsyncSession,
    run: MaintenanceRun,
    step: MaintenanceStep,
    plan: MaintenancePlan,
    reason: str,
) -> None:
    """Write rollback_recommended timeline event during execution."""
    await _write_rollback_recommended_timeline(db, run, plan, step, reason)


async def _write_rollback_recommended_timeline(
    db: AsyncSession,
    run: MaintenanceRun,
    plan: MaintenancePlan,
    step: MaintenanceStep | None = None,
    reason: str = "",
) -> None:
    """Write the maintenance.rollback.recommended timeline event."""
    # Collect rollback hints from artifacts
    art_result = await db.execute(
        select(MaintenanceArtifact)
        .where(MaintenanceArtifact.run_id == run.run_id)
        .where(MaintenanceArtifact.kind == "rollback_hint")
        .order_by(MaintenanceArtifact.created_at)
    )
    artifacts = art_result.scalars().all()
    rollback_hints_data = [
        {
            "artifact_id": a.artifact_id,
            "step_id": a.step_id,
            "name": a.name,
            "data": a.data,
        }
        for a in artifacts
    ]

    result = await db.execute(select(func.max(TimelineEvent.global_seq)))
    max_seq = result.scalar() or 0
    event = TimelineEvent(
        global_seq=max_seq + 1,
        event_type="maintenance.rollback.recommended",
        actor_type="system",
        actor_id="maintenance_executor",
        node_id=plan.target_node_id,
        data={
            "run_id": run.run_id,
            "plan_id": plan.plan_id,
            "step_id": step.step_id if step else "",
            "reason": reason or f"Run {run.run_id} requires rollback",
            "rollback_hints": rollback_hints_data,
            "approval_id": plan.approval_id or "",
        },
        timestamp=datetime.now(UTC),
    )
    db.add(event)
    await db.flush()


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
    approval_id: str | None = None,
) -> None:
    """Write a maintenance timeline event."""
    result = await db.execute(select(func.max(TimelineEvent.global_seq)))
    max_seq = result.scalar() or 0

    # Resolve approval_id from plan if not explicitly passed
    if not approval_id:
        plan_result = await db.execute(
            select(MaintenancePlan).where(MaintenancePlan.plan_id == plan_id)
        )
        plan_obj = plan_result.scalar_one_or_none()
        if plan_obj and plan_obj.approval_id:
            approval_id = plan_obj.approval_id

    data: dict[str, object] = {
        "run_id": run_id,
        "plan_id": plan_id,
        "approval_id": approval_id or "",
    }
    if step_id: data["step_id"] = step_id
    if function_name: data["function_name"] = function_name
    if error: data["error"] = error

    event = TimelineEvent(
        global_seq=max_seq + 1,
        event_type=event_type,
        actor_type="system",
        actor_id="maintenance_executor",
        node_id=target_node_id,
        job_id=job_id,
        invocation_id=invocation_id,
        data=data,
        timestamp=datetime.now(UTC),
    )
    db.add(event)
    await db.flush()
