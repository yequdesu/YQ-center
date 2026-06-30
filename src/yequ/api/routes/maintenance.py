"""Maintenance Plan API endpoints."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.api.deps import get_admin_token, get_db
from yequ.models.approval import ApprovalRequest
from yequ.models.maintenance_plan import (
    MaintenanceArtifact,
    MaintenancePlan,
    MaintenanceRun,
    MaintenanceStep,
)
from yequ.models.node import Node
from yequ.models.operation import Operation
from yequ.models.timeline import TimelineEvent
from yequ.services.maintenance_executor import execute_plan_run, finalize_run
from yequ.services.maintenance_service import (
    approve_plan,
    create_plan,
    get_steps,
    run_plan,
)
from yequ.services.operation_service import OperationService
from yequ.shared_types import JsonObject

router = APIRouter(prefix="/admin/maintenance", tags=["maintenance"])


class CreatePlanRequest(BaseModel):
    goal: str = Field(..., min_length=1)
    target_node_id: str = Field(..., min_length=1)
    steps: list[JsonObject] = Field(..., min_length=1)
    actor_id: str = Field(default="admin")
    session_id: str | None = None
    risk: str = Field(default="maintenance")
    max_total_duration_sec: int | None = None
    rollback_strategy: str | None = None
    execution_mode: str = Field(default="auto")


class ResumeRunRequest(BaseModel):
    approval_id: str | None = None


class RejectRunRequest(BaseModel):
    approval_id: str | None = None
    reason: str | None = None


@router.post("/plans", status_code=201)
async def create_plan_endpoint(
    body: CreatePlanRequest,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    plan = await create_plan(db, **body.model_dump())
    steps = await get_steps(db, plan.plan_id)
    return {
        "plan_id": plan.plan_id,
        "goal": plan.goal,
        "status": plan.status,
        "target_node_id": plan.target_node_id,
        "step_count": len(steps),
        "steps": [
            {
                "step_id": s.step_id,
                "seq": s.seq,
                "function_name": s.function_name,
                "status": s.status,
            }
            for s in steps
        ],
    }


@router.get("/plans")
async def list_plans(
    plan_status: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[JsonObject]:
    stmt = select(MaintenancePlan)
    if plan_status:
        stmt = stmt.where(MaintenancePlan.status == plan_status)
    stmt = stmt.order_by(MaintenancePlan.created_at.desc()).limit(min(limit, 200))
    result = await db.execute(stmt)
    return [_plan_dict(p) for p in result.scalars().all()]


@router.get("/plans/{plan_id}")
async def get_plan(
    plan_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    result = await db.execute(select(MaintenancePlan).where(MaintenancePlan.plan_id == plan_id))
    plan = result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(404, f"Plan {plan_id!r} not found")
    steps = await get_steps(db, plan_id)
    data = _plan_dict(plan)
    data["steps"] = [_step_dict(s) for s in steps]
    return data


@router.post("/plans/{plan_id}/approve")
async def approve_plan_endpoint(
    plan_id: str,
    approval_id: str = "",
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    result = await db.execute(select(MaintenancePlan).where(MaintenancePlan.plan_id == plan_id))
    plan = result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(404)

    # Auto-create and approve if none provided
    if not approval_id:
        from yequ.services.approval_service import approve_approval, create_approval

        steps_data = await get_steps(db, plan_id)
        repair_steps = [s for s in steps_data if s.requires_approval]
        approval = await create_approval(
            db,
            actor_id=plan.actor_id,
            session_id=plan.session_id,
            function_name=repair_steps[0].function_name if repair_steps else plan.goal,
            target_node_id=plan.target_node_id,
            input_data=(repair_steps[0].input_data or {}) if repair_steps else {},
            risk=plan.risk,
            effect="write",
            resource_keys=plan.resource_keys or [],
        )
        approval = await approve_approval(db, approval, approved_by="admin")
        approval_id = approval.approval_id

    await approve_plan(db, plan, approval_id)
    return _plan_dict(plan)


@router.post("/plans/{plan_id}/run")
async def run_plan_endpoint(
    plan_id: str,
    approval_id: str = "",
    dry_run: bool = False,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    result = await db.execute(select(MaintenancePlan).where(MaintenancePlan.plan_id == plan_id))
    plan = result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(404)
    if plan.status not in ("approved", "draft"):
        raise HTTPException(409, f"Cannot run plan in status {plan.status}")

    # Liveness gate: reject run if target node is offline/degraded
    from yequ.config import get_settings
    from yequ.services.node_liveness_service import is_node_schedulable

    node_result = await db.execute(select(Node).where(Node.node_id == plan.target_node_id))
    target_node = node_result.scalar_one_or_none()
    if target_node is None:
        raise HTTPException(404, f"Target node {plan.target_node_id!r} not found")

    schedulable, reason = is_node_schedulable(target_node, get_settings())
    if not schedulable:
        raise HTTPException(
            409,
            detail={
                "error_code": "NODE_UNAVAILABLE",
                "error_message": f"Node {plan.target_node_id} is not schedulable: {reason}",
                "node_id": plan.target_node_id,
                "effective_status": reason or "unavailable",
            },
        )

    run = await run_plan(db, plan)
    operation = await OperationService(db).create_for_maintenance(
        run,
        plan,
        actor_type="admin",
        actor_id=plan.actor_id,
        session_id=plan.session_id,
    )
    run = await execute_plan_run(db, plan, run, approval_id=approval_id, dry_run=dry_run)
    run = await finalize_run(db, plan, run)
    operation_status = await OperationService(db).status(str(operation["operation_id"]))

    steps_result = await db.execute(
        select(MaintenanceStep)
        .where(MaintenanceStep.plan_id == plan_id)
        .order_by(MaintenanceStep.seq)
    )
    steps = steps_result.scalars().all()

    return {
        "run_id": run.run_id,
        "plan_id": run.plan_id,
        "status": run.status,
        "summary": run.summary,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "steps": [_step_dict(s) for s in steps],
        "operation": operation_status["operation"],
        "wait_handle": {
            "type": "operation",
            "operation_id": operation_status["operation"]["operation_id"],
            "kind": operation_status["operation"]["kind"],
            "status": operation_status["operation"]["status"],
            "resume_policy": operation_status["operation"].get("resume_policy") or "manual",
            "cancel_supported": bool(operation_status["operation"].get("cancel_supported")),
        },
    }


@router.post("/runs/{run_id}/resume")
async def resume_run_endpoint(
    run_id: str,
    body: ResumeRunRequest | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    result = await db.execute(select(MaintenanceRun).where(MaintenanceRun.run_id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(404, f"Run {run_id!r} not found")
    if run.status != "waiting_approval":
        raise HTTPException(409, f"Cannot resume run in status {run.status}")

    plan_result = await db.execute(
        select(MaintenancePlan).where(MaintenancePlan.plan_id == run.plan_id)
    )
    plan = plan_result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(404, f"Plan {run.plan_id!r} not found")

    approval_id = (body.approval_id if body else None) or plan.approval_id
    if not approval_id and isinstance(run.summary, dict):
        approval_id = run.summary.get("approval_id")
    if not approval_id:
        raise HTTPException(409, "Run is waiting for approval but no approval_id is linked")

    approval_result = await db.execute(
        select(ApprovalRequest).where(ApprovalRequest.approval_id == approval_id)
    )
    approval = approval_result.scalar_one_or_none()
    if approval is None:
        raise HTTPException(404, f"Approval {approval_id!r} not found")
    if approval.status != "approved":
        raise HTTPException(409, f"Approval {approval_id!r} is {approval.status}")

    if run.current_step_id:
        step_result = await db.execute(
            select(MaintenanceStep).where(MaintenanceStep.step_id == run.current_step_id)
        )
        step = step_result.scalar_one_or_none()
        if step and step.status == "requires_approval":
            step.status = "pending"
            step.error = None
            step.started_at = None
            step.finished_at = None

    run.status = "running"
    run.finished_at = None
    plan.status = "running"
    plan.finished_at = None
    await db.flush()

    run = await execute_plan_run(db, plan, run, approval_id=approval_id)
    run = await finalize_run(db, plan, run)
    steps = await get_steps(db, plan.plan_id)
    data = {
        "run_id": run.run_id,
        "plan_id": run.plan_id,
        "status": run.status,
        "summary": run.summary,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "steps": [_step_dict(s) for s in steps],
    }
    operation_status = await _operation_status_for_ref(db, "maintenance_run", run.run_id)
    if operation_status is not None:
        data["operation"] = operation_status["operation"]
    return data


@router.post("/runs/{run_id}/reject")
async def reject_run_endpoint(
    run_id: str,
    body: RejectRunRequest | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    result = await db.execute(select(MaintenanceRun).where(MaintenanceRun.run_id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(404, f"Run {run_id!r} not found")
    if run.status != "waiting_approval":
        raise HTTPException(409, f"Cannot reject run in status {run.status}")

    plan_result = await db.execute(
        select(MaintenancePlan).where(MaintenancePlan.plan_id == run.plan_id)
    )
    plan = plan_result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(404, f"Plan {run.plan_id!r} not found")

    approval_id = (body.approval_id if body else None) or plan.approval_id
    if not approval_id and isinstance(run.summary, dict):
        approval_id = run.summary.get("approval_id")
    reason = (body.reason if body else None) or "maintenance_run_rejected"

    if approval_id:
        approval_result = await db.execute(
            select(ApprovalRequest).where(ApprovalRequest.approval_id == approval_id)
        )
        approval = approval_result.scalar_one_or_none()
        if approval and approval.status == "pending":
            from yequ.services.approval_service import deny_approval

            await deny_approval(db, approval, denied_by="admin", reason=reason)

    now = datetime.now(UTC)
    if run.current_step_id:
        step_result = await db.execute(
            select(MaintenanceStep).where(MaintenanceStep.step_id == run.current_step_id)
        )
        step = step_result.scalar_one_or_none()
        if step:
            step.status = "failed"
            step.error = "approval_denied"
            step.finished_at = now

    run.status = "cancelled"
    run.finished_at = now
    run.summary = {
        "reason": "approval_denied",
        "approval_id": approval_id,
        "message": reason,
    }
    plan.status = "cancelled"
    plan.finished_at = now

    from yequ.models.timeline import TimelineEvent
    from yequ.services.timeline_writer import add_timeline_event

    await add_timeline_event(
        db,
        TimelineEvent(
            global_seq=0,
            event_type="maintenance.run.cancelled",
            actor_type="admin",
            actor_id="admin",
            node_id=plan.target_node_id,
            data={
                "run_id": run.run_id,
                "plan_id": plan.plan_id,
                "approval_id": approval_id or "",
                "reason": reason,
            },
            timestamp=now,
        ),
    )
    await db.commit()
    data = _run_dict(run)
    operation_status = await _operation_status_for_ref(db, "maintenance_run", run.run_id)
    if operation_status is not None:
        data["operation"] = operation_status["operation"]
    return data


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    result = await db.execute(select(MaintenanceRun).where(MaintenanceRun.run_id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(404)
    data = _run_dict(run)
    data["steps"] = [_step_dict(s) for s in (await get_steps(db, run.plan_id))]
    operation_status = await _operation_status_for_ref(db, "maintenance_run", run.run_id)
    if operation_status is not None:
        data["operation"] = operation_status["operation"]

    # Build artifact summary
    art_result = await db.execute(
        select(MaintenanceArtifact).where(MaintenanceArtifact.run_id == run_id)
    )
    all_artifacts = art_result.scalars().all()
    by_kind: dict[str, int] = {}
    for a in all_artifacts:
        by_kind[a.kind] = by_kind.get(a.kind, 0) + 1
    data["artifact_summary"] = {
        "total": len(all_artifacts),
        "by_kind": by_kind,
    }

    # Collect rollback hints from rollback_hint artifacts
    rollback_artifacts = [a for a in all_artifacts if a.kind == "rollback_hint"]
    data["rollback_recommended"] = run.rollback_recommended
    data["rollback_hints"] = [
        {
            "artifact_id": a.artifact_id,
            "step_id": a.step_id,
            "name": a.name,
            "data": a.data,
        }
        for a in rollback_artifacts
    ]

    return data


@router.get("/runs/{run_id}/artifacts")
async def list_artifacts(
    run_id: str,
    kind: str | None = None,
    step_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    stmt = select(MaintenanceArtifact).where(MaintenanceArtifact.run_id == run_id)
    if kind:
        stmt = stmt.where(MaintenanceArtifact.kind == kind)
    if step_id:
        stmt = stmt.where(MaintenanceArtifact.step_id == step_id)
    stmt = stmt.order_by(MaintenanceArtifact.created_at.asc())
    result = await db.execute(stmt)
    artifacts = [_artifact_dict(a) for a in result.scalars().all()]
    return {
        "run_id": run_id,
        "artifacts": artifacts,
    }


# ── Run events stream ──


@router.get("/runs/{run_id}/events/stream")
async def run_events_stream(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> StreamingResponse:
    """Stream maintenance run timeline events as SSE.

    Sends all existing events for the run, then polls for new events
    every 2 seconds. Closes when the run reaches terminal status.
    """
    # Verify run exists
    run_result = await db.execute(select(MaintenanceRun).where(MaintenanceRun.run_id == run_id))
    r = run_result.scalar_one_or_none()
    if r is None:
        raise HTTPException(404, f"Run {run_id!r} not found")

    async def event_generator() -> AsyncIterator[str]:
        from yequ.db import async_session_factory

        last_seq = 0
        terminal_statuses = {"succeeded", "failed", "rollback_recommended", "cancelled"}

        # Send initial events
        async with async_session_factory() as s:
            result = await s.execute(
                select(TimelineEvent)
                .where(TimelineEvent.data.op("->>")("run_id") == run_id)
                .order_by(TimelineEvent.global_seq.asc())
            )
            for ev in result.scalars().all():
                last_seq = max(last_seq, ev.global_seq)
                yield f"data: {json.dumps(_tl_event(ev), ensure_ascii=False)}\n\n"

        # Check if run already terminal
        if r.status in terminal_statuses:
            close_event = {"event_type": "stream.close", "run_id": run_id}
            yield f"data: {json.dumps(close_event, ensure_ascii=False)}\n\n"
            return

        # Poll for new events
        while True:
            await asyncio.sleep(2)

            async with async_session_factory() as s:
                # Check for new timeline events
                result = await s.execute(
                    select(TimelineEvent)
                    .where(
                        TimelineEvent.data.op("->>")("run_id") == run_id,
                        TimelineEvent.global_seq > last_seq,
                    )
                    .order_by(TimelineEvent.global_seq.asc())
                )
                new_events = list(result.scalars().all())
                for ev in new_events:
                    last_seq = max(last_seq, ev.global_seq)
                    yield f"data: {json.dumps(_tl_event(ev), ensure_ascii=False)}\n\n"

                # Check run status
                run_check = await s.execute(
                    select(MaintenanceRun).where(MaintenanceRun.run_id == run_id)
                )
                run = run_check.scalar_one_or_none()
                if run and run.status in terminal_statuses:
                    close_event = {"event_type": "stream.close", "run_id": run_id}
                    yield f"data: {json.dumps(close_event, ensure_ascii=False)}\n\n"
                    return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _tl_event(e: TimelineEvent) -> JsonObject:
    """Convert TimelineEvent to a dict suitable for SSE."""
    return {
        "event_type": e.event_type,
        "global_seq": e.global_seq,
        "timestamp": e.timestamp.isoformat() if e.timestamp else None,
        "actor_type": e.actor_type,
        "actor_id": e.actor_id,
        "session_id": e.session_id,
        "invocation_id": e.invocation_id,
        "job_id": e.job_id,
        "node_id": e.node_id,
        "data": e.data,
    }


def _plan_dict(p: MaintenancePlan) -> JsonObject:
    return {
        "plan_id": p.plan_id,
        "goal": p.goal,
        "actor_id": p.actor_id,
        "target_node_id": p.target_node_id,
        "risk": p.risk,
        "status": p.status,
        "approval_id": p.approval_id,
        "resource_keys": p.resource_keys,
        "max_total_duration_sec": p.max_total_duration_sec,
        "rollback_strategy": p.rollback_strategy,
        "created_at": p.created_at.isoformat(),
        "approved_at": p.approved_at.isoformat() if p.approved_at else None,
        "started_at": p.started_at.isoformat() if p.started_at else None,
        "finished_at": p.finished_at.isoformat() if p.finished_at else None,
    }


def _step_dict(s: MaintenanceStep) -> JsonObject:
    return {
        "step_id": s.step_id,
        "plan_id": s.plan_id,
        "seq": s.seq,
        "function_name": s.function_name,
        "input_data": s.input_data,
        "depends_on": s.depends_on,
        "continue_on_failure": s.continue_on_failure,
        "timeout_sec": s.timeout_sec,
        "resource_keys": s.resource_keys,
        "kind": s.kind,
        "condition": s.condition,
        "requires_approval": s.requires_approval,
        "skip_reason": s.skip_reason,
        "risk": s.risk,
        "rollback_hint": s.rollback_hint,
        "status": s.status,
        "job_id": s.job_id,
        "invocation_id": s.invocation_id,
        "result": s.result,
        "error": s.error,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "finished_at": s.finished_at.isoformat() if s.finished_at else None,
    }


def _run_dict(r: MaintenanceRun) -> JsonObject:
    return {
        "run_id": r.run_id,
        "plan_id": r.plan_id,
        "status": r.status,
        "rollback_recommended": r.rollback_recommended,
        "started_at": r.started_at.isoformat(),
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "current_step_id": r.current_step_id,
        "summary": r.summary,
    }


def _artifact_dict(a: MaintenanceArtifact) -> JsonObject:
    return {
        "artifact_id": a.artifact_id,
        "run_id": a.run_id,
        "step_id": a.step_id,
        "invocation_id": a.invocation_id,
        "job_id": a.job_id,
        "kind": a.kind,
        "name": a.name,
        "content_type": a.content_type,
        "summary": a.summary,
        "data": a.data,
        "created_at": a.created_at.isoformat(),
    }


async def _operation_status_for_ref(
    db: AsyncSession,
    ref_type: str,
    ref_id: str,
) -> dict[str, object] | None:
    result = await db.execute(
        select(Operation).where(
            Operation.ref_type == ref_type,
            Operation.ref_id == ref_id,
        )
    )
    operation = result.scalar_one_or_none()
    if operation is None:
        return None
    return await OperationService(db).status(operation.operation_id)
