"""Admin endpoints for manual Invocation creation."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.api.deps import get_admin_token, get_db
from yequ.application import ExecuteToolCommand, ExecuteToolResult, ToolInvocationApplicationService

router = APIRouter(prefix="/admin", tags=["admin"])


class CreateInvocationRequest(BaseModel):
    """Request to create an Invocation that spawns a Job on a target node."""

    function_name: str = Field(..., min_length=1, max_length=256)
    target_node_id: str = Field(..., min_length=1, max_length=128)
    input_payload: dict[str, object] = Field(default_factory=dict, validation_alias="input")
    actor_type: str = Field(default="user")
    actor_id: str = Field(default="admin")
    session_id: str | None = Field(default=None)
    execution_mode: str = Field(default="auto")
    timeout_sec: int = Field(default=30, ge=1, le=3600)
    lease_sec: int = Field(default=30, ge=1, le=3600)
    max_depth: int | None = Field(default=None)
    max_steps: int | None = Field(default=None)
    max_total_duration_sec: int | None = Field(default=None)
    approval_id: str | None = Field(default=None)
    dry_run: bool = Field(default=False)


class CreateInvocationResponse(BaseModel):
    invocation_id: str
    job_id: str = ""
    function_name: str
    target_node_id: str
    invocation_status: str
    job_status: str = ""
    approval_id: str = ""
    dry_run: bool = False
    allowed: bool = True
    decision: str = ""
    reason: str = ""
    resource_keys: list[str] = Field(default_factory=list)


def _admin_invocation_response_from_result(
    result: ExecuteToolResult,
    body: CreateInvocationRequest,
) -> CreateInvocationResponse:
    if body.dry_run and result.status == "approval_required" and not result.approval_id:
        return CreateInvocationResponse(
            invocation_id="",
            job_id="",
            function_name=body.function_name,
            target_node_id=result.target_node_id or body.target_node_id,
            invocation_status="dry_run_completed",
            job_status="",
            dry_run=True,
            allowed=False,
            decision="ask",
            reason="approval_required",
            resource_keys=result.approval.resource_keys if result.approval else [],
        )

    if result.status == "approval_required":
        return CreateInvocationResponse(
            invocation_id=result.invocation_id or "",
            job_id="",
            function_name=body.function_name,
            target_node_id=result.target_node_id or body.target_node_id,
            invocation_status="waiting_approval",
            job_status="pending",
            approval_id=result.approval_id or "",
            allowed=False,
            decision="ask",
            reason="approval_required",
            resource_keys=result.approval.resource_keys if result.approval else [],
        )

    return CreateInvocationResponse(
        invocation_id=result.invocation_id or "",
        job_id=result.job_id or "",
        function_name=body.function_name,
        target_node_id=result.target_node_id or body.target_node_id,
        invocation_status="running" if result.status == "created" else result.status,
        job_status="queued" if result.status == "created" else result.status,
    )


@router.post(
    "/invocations",
    response_model=CreateInvocationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invocation_endpoint(
    body: CreateInvocationRequest,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> CreateInvocationResponse:
    """Create an Invocation and fan out to a Job on the target Node."""
    result = await ToolInvocationApplicationService(db).execute(
        ExecuteToolCommand(
            actor_type=body.actor_type,
            actor_id=body.actor_id,
            session_id=body.session_id,
            function_name=body.function_name,
            input_data=body.input_payload,
            target_node_id=body.target_node_id,
            execution_mode=body.execution_mode,
            max_depth=body.max_depth,
            max_steps=body.max_steps,
            max_total_duration_sec=body.max_total_duration_sec,
            approval_id=body.approval_id,
            dry_run=body.dry_run,
            timeout_sec=body.timeout_sec,
            lease_sec=body.lease_sec,
            allow_unregistered_function=True,
        )
    )
    if result.status == "denied":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=result.error_message or "Policy denied",
        )
    if result.status in {"unavailable", "not_found"}:
        error_code = result.error_code or "FUNCTION_NOT_AVAILABLE"
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
                if error_code == "node_not_found"
                else status.HTTP_409_CONFLICT
            ),
            detail={
                "error_code": error_code,
                "error_message": result.error_message or "Function not available",
                "node_id": body.target_node_id,
                "function_name": body.function_name,
            },
        )
    if result.error_code == "resource_lock_conflict":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result.error_message)
    return _admin_invocation_response_from_result(result, body)
