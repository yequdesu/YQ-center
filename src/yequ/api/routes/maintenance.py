"""Maintenance Plan API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.api.deps import get_admin_token, get_db
from yequ.models.maintenance_plan import (
    MaintenanceArtifact,
    MaintenancePlan,
    MaintenanceRun,
    MaintenanceStep,
)
from yequ.services.maintenance_executor import execute_plan_run, finalize_run
from yequ.services.maintenance_service import (
    approve_plan,
    create_plan,
    get_steps,
    run_plan,
)

router = APIRouter(prefix="/admin/maintenance", tags=["maintenance"])


class CreatePlanRequest(BaseModel):
    goal: str = Field(..., min_length=1)
    target_node_id: str = Field(..., min_length=1)
    steps: list[dict] = Field(..., min_length=1)
    actor_id: str = Field(default="admin")
    session_id: str | None = None
    risk: str = Field(default="maintenance")
    max_total_duration_sec: int | None = None
    rollback_strategy: str | None = None
    execution_mode: str = Field(default="auto")


@router.post("/plans", status_code=201)
async def create_plan_endpoint(
    body: CreatePlanRequest,
    db: AsyncSession = Depends(get_db),
    _token: dict = Depends(get_admin_token),
) -> dict:
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
    _token: dict = Depends(get_admin_token),
) -> list[dict]:
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
    _token: dict = Depends(get_admin_token),
) -> dict:
    result = await db.execute(
        select(MaintenancePlan).where(MaintenancePlan.plan_id == plan_id)
    )
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
    _token: dict = Depends(get_admin_token),
) -> dict:
    result = await db.execute(
        select(MaintenancePlan).where(MaintenancePlan.plan_id == plan_id)
    )
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
            input_data=repair_steps[0].input_data if repair_steps else {},
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
    _token: dict = Depends(get_admin_token),
) -> dict:
    result = await db.execute(
        select(MaintenancePlan).where(MaintenancePlan.plan_id == plan_id)
    )
    plan = result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(404)
    if plan.status not in ("approved", "draft"):
        raise HTTPException(409, f"Cannot run plan in status {plan.status}")

    run = await run_plan(db, plan)
    run = await execute_plan_run(db, plan, run, approval_id=approval_id, dry_run=dry_run)
    run = await finalize_run(db, plan, run)

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
    }


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict = Depends(get_admin_token),
) -> dict:
    result = await db.execute(
        select(MaintenanceRun).where(MaintenanceRun.run_id == run_id)
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(404)
    data = _run_dict(run)
    data["steps"] = [_step_dict(s) for s in (await get_steps(db, run.plan_id))]

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
    _token: dict = Depends(get_admin_token),
) -> dict:
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


def _plan_dict(p: MaintenancePlan) -> dict:
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


def _step_dict(s: MaintenanceStep) -> dict:
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


def _run_dict(r: MaintenanceRun) -> dict:
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


def _artifact_dict(a: MaintenanceArtifact) -> dict:
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
