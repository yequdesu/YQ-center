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
    await approve_plan(db, plan, approval_id)
    return _plan_dict(plan)


@router.post("/plans/{plan_id}/run")
async def run_plan_endpoint(
    plan_id: str,
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
    return {
        "run_id": run.run_id,
        "plan_id": run.plan_id,
        "status": run.status,
        "started_at": run.started_at.isoformat(),
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
    return data


@router.get("/runs/{run_id}/artifacts")
async def list_artifacts(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict = Depends(get_admin_token),
) -> list[dict]:
    result = await db.execute(
        select(MaintenanceArtifact).where(MaintenanceArtifact.run_id == run_id)
    )
    return [_artifact_dict(a) for a in result.scalars().all()]


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
        "artifact_type": a.artifact_type,
        "name": a.name,
        "content_type": a.content_type,
        "size_bytes": a.size_bytes,
        "sha256": a.sha256,
        "storage_ref": a.storage_ref,
        "created_at": a.created_at.isoformat(),
    }
