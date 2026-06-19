"""Admin endpoints for Node management and Invocation creation."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.api.deps import get_db
from yequ.models.node import Node
from yequ.protocol import NodeStatus
from yequ.services.invocation_service import (
    create_invocation,
    start_invocation,
)
from yequ.services.job_service import create_job
from yequ.services.node_auth import hash_token

router = APIRouter(prefix="/admin", tags=["admin"])


class ProvisionNodeRequest(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=128)
    node_name: str = Field(..., min_length=1, max_length=256)
    token: str = Field(..., min_length=8, description="Plaintext token for this node")
    role: str = Field(default="compute")
    locality: str = Field(default="local")


class ProvisionNodeResponse(BaseModel):
    node_id: str
    node_name: str
    status: str
    message: str


class CreateInvocationRequest(BaseModel):
    """Request to create an Invocation that spawns a Job on a target node."""
    function_name: str = Field(..., min_length=1, max_length=256)
    target_node_id: str = Field(..., min_length=1, max_length=128)
    input_payload: dict[str, object] = Field(default_factory=dict)
    actor_type: str = Field(default="user")
    actor_id: str = Field(default="admin")
    session_id: str | None = Field(default=None)
    execution_mode: str = Field(default="auto")
    timeout_sec: int = Field(default=30, ge=1, le=3600)
    lease_sec: int = Field(default=30, ge=1, le=3600)
    max_depth: int | None = Field(default=None)
    max_steps: int | None = Field(default=None)
    max_total_duration_sec: int | None = Field(default=None)


class CreateInvocationResponse(BaseModel):
    invocation_id: str
    job_id: str
    function_name: str
    target_node_id: str
    invocation_status: str
    job_status: str


@router.post(
    "/nodes",
    response_model=ProvisionNodeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def provision_node(
    body: ProvisionNodeRequest,
    db: AsyncSession = Depends(get_db),
) -> ProvisionNodeResponse:
    """Pre-provision a Node in the system.

    Creates a Node record with hashed token. The node can then
    connect via node.hello using the provided token and node_id.
    """
    # Check if node already exists
    result = await db.execute(
        select(Node).where(Node.node_id == body.node_id)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Node {body.node_id!r} already exists",
        )

    node = Node(
        node_id=body.node_id,
        node_name=body.node_name,
        token_hash=hash_token(body.token),
        role=body.role,
        locality=body.locality,
        status=NodeStatus.PROVISIONED,
    )
    db.add(node)
    await db.commit()

    return ProvisionNodeResponse(
        node_id=body.node_id,
        node_name=body.node_name,
        status=NodeStatus.PROVISIONED,
        message="Node provisioned. Use the token to authenticate via node.hello.",
    )


@router.post(
    "/invocations",
    response_model=CreateInvocationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invocation_endpoint(
    body: CreateInvocationRequest,
    db: AsyncSession = Depends(get_db),
) -> CreateInvocationResponse:
    """Create an Invocation and fan out to a Job on the target Node.

    This is the primary way to trigger work in the system:
    1. Creates an Invocation (PENDING -> RUNNING)
    2. Creates a Job (CREATED -> QUEUED) on the target node
    3. The Daemon polls and picks up the Job
    """
    # Verify the target node exists
    result = await db.execute(
        select(Node).where(Node.node_id == body.target_node_id)
    )
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Node {body.target_node_id!r} not found",
        )

    # Create Invocation
    inv = await create_invocation(
        db,
        actor_type=body.actor_type,
        actor_id=body.actor_id,
        session_id=body.session_id,
        function_name=body.function_name,
        input_payload=body.input_payload,
        target_node_id=body.target_node_id,
        execution_mode=body.execution_mode,
        max_depth=body.max_depth,
        max_steps=body.max_steps,
        max_total_duration_sec=body.max_total_duration_sec,
    )
    start_invocation(inv)

    # Create Job
    job = await create_job(
        db,
        invocation_id=inv.invocation_id,
        node_id=body.target_node_id,
        function_name=body.function_name,
        input_payload=body.input_payload,
        timeout_sec=body.timeout_sec,
        lease_sec=body.lease_sec,
    )

    await db.commit()

    return CreateInvocationResponse(
        invocation_id=inv.invocation_id,
        job_id=job.job_id,
        function_name=body.function_name,
        target_node_id=body.target_node_id,
        invocation_status=inv.status,
        job_status=job.status,
    )
