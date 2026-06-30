"""MaintenanceRun Operation projection handler."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.maintenance_plan import MaintenancePlan, MaintenanceRun, MaintenanceStep
from yequ.models.operation import Operation
from yequ.models.timeline import TimelineEvent
from yequ.services.timeline_writer import add_timeline_event

TERMINAL_MAINTENANCE_STATUSES = {
    "succeeded",
    "failed",
    "rollback_recommended",
    "partially_succeeded",
    "cancelled",
    "timeout",
}


class MaintenanceOperationHandler:
    """Project one MaintenanceRun into an Operation shell."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def project(self, operation: Operation) -> dict[str, object]:
        run = await self._get_run(operation.ref_id)
        plan = await self._get_plan(run.plan_id)
        steps = await self._get_steps(run.plan_id)
        self.sync_operation(operation, run, plan, steps)
        return {"maintenance_run": maintenance_run_dict(run, plan, steps)}

    async def cancel(self, operation: Operation, *, reason: str) -> dict[str, object]:
        run = await self._get_run(operation.ref_id)
        plan = await self._get_plan(run.plan_id)
        steps = await self._get_steps(run.plan_id)
        if run.status not in TERMINAL_MAINTENANCE_STATUSES:
            now = datetime.now(UTC)
            run.status = "cancelled"
            run.finished_at = now
            run.summary = {
                "reason": "operation_cancelled",
                "message": reason,
            }
            plan.status = "cancelled"
            plan.finished_at = now
            for step in steps:
                if step.status in {"pending", "running", "requires_approval"}:
                    step.status = "failed"
                    step.error = reason
                    step.finished_at = now
            await add_timeline_event(
                self.db,
                TimelineEvent(
                    global_seq=0,
                    event_type="maintenance.run.cancelled",
                    actor_type="operation",
                    actor_id=operation.operation_id,
                    node_id=plan.target_node_id,
                    data={
                        "run_id": run.run_id,
                        "plan_id": plan.plan_id,
                        "operation_id": operation.operation_id,
                        "reason": reason,
                    },
                    timestamp=now,
                ),
            )
        self.sync_operation(operation, run, plan, steps)
        return {"maintenance_run": maintenance_run_dict(run, plan, steps)}

    async def _get_run(self, run_id: str) -> MaintenanceRun:
        result = await self.db.execute(
            select(MaintenanceRun).where(MaintenanceRun.run_id == run_id)
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise ValueError(f"MaintenanceRun {run_id!r} not found")
        return run

    async def _get_plan(self, plan_id: str) -> MaintenancePlan:
        result = await self.db.execute(
            select(MaintenancePlan).where(MaintenancePlan.plan_id == plan_id)
        )
        plan = result.scalar_one_or_none()
        if plan is None:
            raise ValueError(f"MaintenancePlan {plan_id!r} not found")
        return plan

    async def _get_steps(self, plan_id: str) -> list[MaintenanceStep]:
        result = await self.db.execute(
            select(MaintenanceStep)
            .where(MaintenanceStep.plan_id == plan_id)
            .order_by(MaintenanceStep.seq)
        )
        return list(result.scalars().all())

    @staticmethod
    def sync_operation(
        operation: Operation,
        run: MaintenanceRun,
        plan: MaintenancePlan,
        steps: list[MaintenanceStep],
    ) -> None:
        operation.status = operation_status_from_maintenance(run.status)
        operation.progress_pct = maintenance_progress_pct(steps)
        operation.progress_message = maintenance_progress_message(run, steps)
        operation.output_data = {
            "maintenance_run": maintenance_run_dict(run, plan, steps),
        }
        if operation.status in {"succeeded", "failed", "cancelled", "timeout"}:
            operation.completed_at = operation.completed_at or run.finished_at or datetime.now(UTC)


def operation_status_from_maintenance(status: str) -> str:
    if status in {"running", "waiting_approval"}:
        return "running"
    if status == "succeeded":
        return "succeeded"
    if status == "cancelled":
        return "cancelled"
    if status == "timeout":
        return "timeout"
    if status in {"failed", "rollback_recommended", "partially_succeeded"}:
        return "failed"
    return "running"


def maintenance_title(run: MaintenanceRun, plan: MaintenancePlan | None = None) -> str:
    if plan is not None:
        return f"Maintenance {plan.goal} @ {plan.target_node_id}"
    return f"Maintenance run {run.run_id}"


def maintenance_progress_pct(steps: list[MaintenanceStep]) -> int | None:
    if not steps:
        return None
    completed = len([s for s in steps if s.status in {"succeeded", "failed", "skipped"}])
    return int((completed / len(steps)) * 100)


def maintenance_progress_message(run: MaintenanceRun, steps: list[MaintenanceStep]) -> str | None:
    if run.status == "waiting_approval":
        return "waiting_approval"
    current = next((s for s in steps if s.step_id == run.current_step_id), None)
    if current is not None:
        return f"{current.status}: {current.function_name}"
    return run.status


def maintenance_run_dict(
    run: MaintenanceRun,
    plan: MaintenancePlan,
    steps: list[MaintenanceStep],
) -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "plan_id": run.plan_id,
        "goal": plan.goal,
        "target_node_id": plan.target_node_id,
        "status": run.status,
        "rollback_recommended": run.rollback_recommended,
        "current_step_id": run.current_step_id,
        "summary": run.summary,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "steps": [
            {
                "step_id": s.step_id,
                "seq": s.seq,
                "function_name": s.function_name,
                "kind": s.kind,
                "status": s.status,
                "job_id": s.job_id,
                "invocation_id": s.invocation_id,
                "error": s.error,
            }
            for s in steps
        ],
    }
