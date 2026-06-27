"""Admin endpoints for API tokens and approval lifecycle."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.api.deps import get_admin_token, get_db
from yequ.application import ExecuteToolCommand, ToolInvocationApplicationService
from yequ.models.api_token import ApiToken
from yequ.models.approval import ApprovalRequest
from yequ.models.invocation import Invocation
from yequ.models.job import Job
from yequ.services.token_auth import hash_token as hash_api_token
from yequ.types import JsonObject

router = APIRouter(prefix="/admin", tags=["admin"])


class CreateTokenRequest(BaseModel):
    token: str = Field(..., min_length=8)
    scope: str = Field(..., min_length=1)  # "admin" or "agent"
    label: str = Field(..., min_length=1)


class CreateTokenResponse(BaseModel):
    token_hash: str
    scope: str
    label: str
    message: str


class CreateApprovalRequest(BaseModel):
    function_name: str = Field(..., min_length=1)
    target_node_id: str = Field(..., min_length=1)
    input_data: JsonObject = Field(default_factory=dict)
    risk: str = Field(default="maintenance")
    effect: str = Field(default="write")
    resource_keys: list[str] = Field(default_factory=list)
    ttl_minutes: int = Field(default=5, ge=1, le=60)
    actor_id: str = Field(default="admin")
    session_id: str | None = None


class ApprovalResponse(BaseModel):
    approval_id: str
    status: str
    function_name: str
    target_node_id: str
    actor_id: str
    expires_at: str
    created_at: str


@router.post("/tokens", status_code=status.HTTP_201_CREATED)
async def create_token(
    body: CreateTokenRequest,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> CreateTokenResponse:
    """Create a new API token (admin or agent scope)."""
    if body.scope not in ("admin", "agent"):
        raise HTTPException(status_code=400, detail="scope must be 'admin' or 'agent'")

    # Check for duplicate (token_hash, scope) pair
    existing = await db.execute(
        select(ApiToken).where(
            ApiToken.token_hash == hash_api_token(body.token),
            ApiToken.scope == body.scope,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Token with this value already exists")

    t = ApiToken(
        token_hash=hash_api_token(body.token),
        scope=body.scope,
        label=body.label,
    )
    db.add(t)
    await db.commit()
    return CreateTokenResponse(
        token_hash=t.token_hash,
        scope=t.scope,
        label=t.label,
        message=f"{body.scope} token created",
    )


# ── Approval endpoints ──


@router.post("/approvals", status_code=201)
async def create_approval_endpoint(
    body: CreateApprovalRequest,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> ApprovalResponse:
    from yequ.services.approval_service import create_approval

    approval = await create_approval(db, **body.model_dump())
    return ApprovalResponse(
        approval_id=approval.approval_id,
        status=approval.status,
        function_name=approval.function_name,
        target_node_id=approval.target_node_id,
        actor_id=approval.actor_id,
        expires_at=approval.expires_at.isoformat(),
        created_at=approval.created_at.isoformat(),
    )


@router.get("/approvals")
async def list_approvals(
    approval_status: str | None = Query(default=None, alias="status"),
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[JsonObject]:
    stmt = select(ApprovalRequest)
    if approval_status:
        stmt = stmt.where(ApprovalRequest.status == approval_status)
    stmt = stmt.order_by(ApprovalRequest.created_at.desc()).limit(min(limit, 200))
    result = await db.execute(stmt)
    return [_approval_dict(a) for a in result.scalars().all()]


@router.get("/approvals/{approval_id}")
async def get_approval(
    approval_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    result = await db.execute(
        select(ApprovalRequest).where(ApprovalRequest.approval_id == approval_id)
    )
    a = result.scalar_one_or_none()
    if a is None:
        raise HTTPException(404, f"Approval {approval_id!r} not found")

    data = _approval_dict(a)

    # Include linked invocation and jobs (the waiting_approval invocation)
    if a.invocation_id:
        inv_result = await db.execute(
            select(Invocation).where(Invocation.invocation_id == a.invocation_id)
        )
        inv = inv_result.scalar_one_or_none()
        if inv:
            data["invocation"] = {
                "invocation_id": inv.invocation_id,
                "status": inv.status,
                "function_name": inv.function_name,
                "started_at": inv.started_at.isoformat() if inv.started_at else None,
                "finished_at": inv.finished_at.isoformat() if inv.finished_at else None,
            }
            jresult = await db.execute(select(Job).where(Job.invocation_id == a.invocation_id))
            jobs = jresult.scalars().all()
            data["invocation"]["jobs"] = [{"job_id": j.job_id, "status": j.status} for j in jobs]

    # Include consumed execution invocation (Bug 2)
    if a.consumed_invocation_id:
        cinv_result = await db.execute(
            select(Invocation).where(Invocation.invocation_id == a.consumed_invocation_id)
        )
        ci = cinv_result.scalar_one_or_none()
        if ci:
            data["consumed_invocation"] = {
                "invocation_id": ci.invocation_id,
                "status": ci.status,
                "function_name": ci.function_name,
                "started_at": ci.started_at.isoformat() if ci.started_at else None,
                "finished_at": ci.finished_at.isoformat() if ci.finished_at else None,
            }
            jresult = await db.execute(select(Job).where(Job.invocation_id == ci.invocation_id))
            data["consumed_invocation"]["jobs"] = [
                {"job_id": j.job_id, "status": j.status} for j in jresult.scalars().all()
            ]

    return data


@router.post("/approvals/{approval_id}/approve")
async def approve_endpoint(
    approval_id: str,
    body: JsonObject | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    result = await db.execute(
        select(ApprovalRequest).where(ApprovalRequest.approval_id == approval_id)
    )
    a = result.scalar_one_or_none()
    if a is None:
        raise HTTPException(404, f"Approval {approval_id!r} not found")
    reason = body.get("reason") if body else None
    from yequ.services.approval_service import approve_approval

    try:
        await approve_approval(db, a, approved_by="admin", reason=reason)
        return _approval_dict(a)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/approvals/{approval_id}/deny")
async def deny_endpoint(
    approval_id: str,
    body: JsonObject | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    result = await db.execute(
        select(ApprovalRequest).where(ApprovalRequest.approval_id == approval_id)
    )
    a = result.scalar_one_or_none()
    if a is None:
        raise HTTPException(404, f"Approval {approval_id!r} not found")
    reason = body.get("reason") if body else None
    from yequ.services.approval_service import deny_approval

    try:
        await deny_approval(db, a, denied_by="admin", reason=reason)
        return _approval_dict(a)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/approvals/{approval_id}/approve-and-run")
async def approve_and_run_endpoint(
    approval_id: str,
    body: JsonObject | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    """Approve a pending approval and immediately execute the tool.

    1. Verify approval is pending
    2. Approve the approval
    3. Consume the approval
    4. Create Invocation + Job from the approval's stored parameters
    5. Return invocation_id, job_id, status
    """
    result = await db.execute(
        select(ApprovalRequest).where(ApprovalRequest.approval_id == approval_id)
    )
    a = result.scalar_one_or_none()
    if a is None:
        raise HTTPException(404, f"Approval {approval_id!r} not found")
    if a.status != "pending":
        raise HTTPException(
            409,
            f"Approval {approval_id!r} is {a.status}, expected pending",
        )

    from yequ.services.approval_service import approve_approval

    reason = body.get("reason") if body else None

    try:
        await approve_approval(db, a, approved_by="admin", reason=reason)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e

    app_result = await ToolInvocationApplicationService(db).execute(
        ExecuteToolCommand(
            actor_type="agent",
            actor_id=a.actor_id,
            session_id=a.session_id,
            function_name=a.function_name,
            input_data=a.input_snapshot or {},
            target_node_id=a.target_node_id,
            execution_mode="auto",
            approval_id=a.approval_id,
            allow_unregistered_function=True,
        )
    )
    if app_result.status in {"denied", "unavailable", "not_found", "failed"}:
        raise HTTPException(
            409,
            {
                "error_code": app_result.error_code or "APPROVAL_EXECUTION_FAILED",
                "error_message": app_result.error_message or "Approval execution failed",
            },
        )

    return {
        "approval_id": a.approval_id,
        "invocation_id": app_result.invocation_id,
        "job_id": app_result.job_id,
        "function_name": a.function_name,
        "target_node_id": a.target_node_id,
        "status": "queued" if app_result.status == "created" else app_result.status,
    }


def _approval_dict(a: ApprovalRequest) -> JsonObject:
    return {
        "approval_id": a.approval_id,
        "status": a.status,
        "actor_id": a.actor_id,
        "session_id": a.session_id,
        "function_name": a.function_name,
        "target_node_id": a.target_node_id,
        "input_hash": a.input_hash,
        "risk": a.risk,
        "effect": a.effect,
        "resource_keys": a.resource_keys,
        "expires_at": a.expires_at.isoformat(),
        "created_at": a.created_at.isoformat(),
        "consumed_at": a.consumed_at.isoformat() if a.consumed_at else None,
        "approved_by": a.approved_by,
        "denied_by": a.denied_by,
        "decision_reason": a.decision_reason,
        "consumed_invocation_id": a.consumed_invocation_id,
    }


# ── Helper converters ──
