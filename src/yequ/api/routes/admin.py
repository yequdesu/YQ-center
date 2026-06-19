"""Admin endpoints for Node management and Invocation creation."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.api.deps import get_admin_token, get_db
from yequ.models.api_token import ApiToken
from yequ.models.capability import Capability
from yequ.models.invocation import Invocation
from yequ.models.job import Job
from yequ.models.node import Node
from yequ.models.timeline import TimelineEvent
from yequ.protocol import NodeStatus
from yequ.services.invocation_service import (
    create_invocation,
    start_invocation,
)
from yequ.services.job_service import create_job
from yequ.services.node_auth import hash_token
from yequ.services.token_auth import hash_token as hash_api_token

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


# ── Read models for GET endpoints ──

class NodeSummary(BaseModel):
    node_id: str
    node_name: str
    role: str
    locality: str
    status: str
    daemon_version: str | None = None
    platform_os: str | None = None
    platform_arch: str | None = None
    last_seen_at: str | None = None
    last_heartbeat_at: str | None = None

class NodeDetail(NodeSummary):
    token_hash: str
    heartbeat_interval_sec: int | None = None
    job_delivery_mode: str | None = None
    created_at: str | None = None

class CapabilitySummary(BaseModel):
    plugin_id: str
    plugin_version: str
    capability_type: str
    name: str
    status: str
    risk: str | None = None
    effect: str | None = None
    timeout_sec: int | None = None
    idempotency: str | None = None
    scope: str | None = None
    ttl_sec: int | None = None
    is_active: bool

class JobSummary(BaseModel):
    job_id: str
    invocation_id: str
    node_id: str
    function_name: str
    status: str
    timeout_sec: int
    lease_sec: int
    claimed_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    cancel_reason: str | None = None
    attempt: int

class InvocationDetail(BaseModel):
    invocation_id: str
    actor_type: str
    actor_id: str
    session_id: str | None = None
    function_name: str
    status: str
    execution_mode: str
    target_node_id: str | None = None
    call_path: list[str]
    max_depth: int | None = None
    max_steps: int | None = None
    max_total_duration_sec: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, object] | None = None
    error_code: str | None = None
    error_message: str | None = None
    jobs: list[JobSummary] = []

class TimelineSummary(BaseModel):
    global_seq: int
    event_type: str
    actor_type: str | None = None
    actor_id: str | None = None
    session_id: str | None = None
    invocation_id: str | None = None
    job_id: str | None = None
    node_id: str | None = None
    timestamp: str | None = None
    data: dict[str, object] | None = None


# ── Read endpoints ──

@router.get("/nodes", response_model=list[NodeSummary])
async def list_nodes(
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[NodeSummary]:
    """List all provisioned nodes."""
    result = await db.execute(select(Node).order_by(Node.node_id))
    nodes = result.scalars().all()
    return [_node_summary(n) for n in nodes]


@router.get("/nodes/{node_id}", response_model=NodeDetail)
async def get_node(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> NodeDetail:
    """Get a single node's details."""
    result = await db.execute(select(Node).where(Node.node_id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found")
    return _node_detail(node)


@router.get("/capabilities", response_model=list[CapabilitySummary])
async def list_capabilities(
    node_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[CapabilitySummary]:
    """List capabilities, optionally filtered by node_id."""
    stmt = select(Capability).where(Capability.is_active)
    if node_id:
        sub = select(Node.id).where(Node.node_id == node_id).scalar_subquery()
        stmt = stmt.where(Capability.node_record_id == sub)
    stmt = stmt.order_by(Capability.name)
    result = await db.execute(stmt)
    caps = result.scalars().all()
    return [_cap_summary(c) for c in caps]


@router.get("/jobs", response_model=list[JobSummary])
async def list_jobs(
    node_id: str | None = None,
    status: str | None = None,
    invocation_id: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[JobSummary]:
    """List jobs with optional filters."""
    stmt = select(Job)
    if node_id:
        stmt = stmt.where(Job.node_id == node_id)
    if status:
        stmt = stmt.where(Job.status == status)
    if invocation_id:
        stmt = stmt.where(Job.invocation_id == invocation_id)
    stmt = stmt.order_by(Job.created_at.desc()).limit(min(limit, 200))
    result = await db.execute(stmt)
    jobs = result.scalars().all()
    return [_job_summary(j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=JobSummary)
async def get_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JobSummary:
    """Get a single job's details."""
    result = await db.execute(select(Job).where(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return _job_summary(job)


@router.get("/invocations/{invocation_id}", response_model=InvocationDetail)
async def get_invocation(
    invocation_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> InvocationDetail:
    """Get invocation details including associated jobs."""
    result = await db.execute(
        select(Invocation).where(Invocation.invocation_id == invocation_id)
    )
    inv = result.scalar_one_or_none()
    if inv is None:
        raise HTTPException(status_code=404, detail=f"Invocation {invocation_id!r} not found")

    # Fetch associated jobs
    jresult = await db.execute(
        select(Job).where(Job.invocation_id == invocation_id).order_by(Job.created_at)
    )
    jobs = jresult.scalars().all()

    return InvocationDetail(
        invocation_id=inv.invocation_id,
        actor_type=inv.actor_type,
        actor_id=inv.actor_id,
        session_id=inv.session_id,
        function_name=inv.function_name,
        status=inv.status,
        execution_mode=inv.execution_mode,
        target_node_id=inv.target_node_id,
        call_path=inv.call_path or [],
        max_depth=inv.max_depth,
        max_steps=inv.max_steps,
        max_total_duration_sec=inv.max_total_duration_sec,
        started_at=inv.started_at.isoformat() if inv.started_at else None,
        finished_at=inv.finished_at.isoformat() if inv.finished_at else None,
        result=inv.result,
        error_code=inv.error_code,
        error_message=inv.error_message,
        jobs=[_job_summary(j) for j in jobs],
    )


@router.get("/timeline", response_model=list[TimelineSummary])
async def list_timeline(
    node_id: str | None = None,
    job_id: str | None = None,
    invocation_id: str | None = None,
    session_id: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
    created_after: str | None = None,
    created_before: str | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[TimelineSummary]:
    """Query timeline events with filters and cursor support."""
    stmt = select(TimelineEvent)
    if node_id:
        stmt = stmt.where(TimelineEvent.node_id == node_id)
    if job_id:
        stmt = stmt.where(TimelineEvent.job_id == job_id)
    if invocation_id:
        stmt = stmt.where(TimelineEvent.invocation_id == invocation_id)
    if session_id:
        stmt = stmt.where(TimelineEvent.session_id == session_id)
    if event_type:
        stmt = stmt.where(TimelineEvent.event_type == event_type)
    if created_after:
        stmt = stmt.where(TimelineEvent.timestamp > datetime.fromisoformat(created_after))
    if created_before:
        stmt = stmt.where(TimelineEvent.timestamp < datetime.fromisoformat(created_before))
    stmt = stmt.order_by(TimelineEvent.global_seq.desc()).limit(min(limit, 200))
    result = await db.execute(stmt)
    events = result.scalars().all()
    return [_tl_summary(e) for e in events]


class CreateTokenRequest(BaseModel):
    token: str = Field(..., min_length=8)
    scope: str = Field(..., min_length=1)  # "admin" or "agent"
    label: str = Field(..., min_length=1)


class CreateTokenResponse(BaseModel):
    token_hash: str
    scope: str
    label: str
    message: str


@router.post("/tokens", status_code=status.HTTP_201_CREATED)
async def create_token(
    body: CreateTokenRequest,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> CreateTokenResponse:
    """Create a new API token (admin or agent scope)."""
    if body.scope not in ("admin", "agent"):
        raise HTTPException(status_code=400, detail="scope must be 'admin' or 'agent'")

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


# ── Helper converters ──

def _node_summary(n: Node) -> NodeSummary:
    return NodeSummary(
        node_id=n.node_id,
        node_name=n.node_name,
        role=n.role,
        locality=n.locality,
        status=n.status,
        daemon_version=n.daemon_version,
        platform_os=n.platform_os,
        platform_arch=n.platform_arch,
        last_seen_at=n.last_seen_at.isoformat() if n.last_seen_at else None,
        last_heartbeat_at=n.last_heartbeat_at.isoformat() if n.last_heartbeat_at else None,
    )

def _node_detail(n: Node) -> NodeDetail:
    return NodeDetail(
        node_id=n.node_id,
        node_name=n.node_name,
        role=n.role,
        locality=n.locality,
        status=n.status,
        daemon_version=n.daemon_version,
        platform_os=n.platform_os,
        platform_arch=n.platform_arch,
        last_seen_at=n.last_seen_at.isoformat() if n.last_seen_at else None,
        last_heartbeat_at=n.last_heartbeat_at.isoformat() if n.last_heartbeat_at else None,
        token_hash=n.token_hash,
        heartbeat_interval_sec=n.heartbeat_interval_sec,
        job_delivery_mode=n.job_delivery_mode,
        created_at=n.created_at.isoformat() if n.created_at else None,
    )

def _cap_summary(c: Capability) -> CapabilitySummary:
    return CapabilitySummary(
        plugin_id=c.plugin_id,
        plugin_version=c.plugin_version,
        capability_type=c.capability_type,
        name=c.name,
        status=c.status,
        risk=c.risk,
        effect=c.effect,
        timeout_sec=c.timeout_sec,
        idempotency=c.idempotency,
        scope=c.scope,
        ttl_sec=c.ttl_sec,
        is_active=c.is_active,
    )

def _job_summary(j: Job) -> JobSummary:
    return JobSummary(
        job_id=j.job_id,
        invocation_id=j.invocation_id,
        node_id=j.node_id,
        function_name=j.function_name,
        status=j.status,
        timeout_sec=j.timeout_sec,
        lease_sec=j.lease_sec,
        claimed_at=j.claimed_at.isoformat() if j.claimed_at else None,
        started_at=j.started_at.isoformat() if j.started_at else None,
        finished_at=j.finished_at.isoformat() if j.finished_at else None,
        error_code=j.error_code,
        error_message=j.error_message,
        cancel_reason=j.cancel_reason,
        attempt=j.attempt,
    )

def _tl_summary(e: TimelineEvent) -> TimelineSummary:
    return TimelineSummary(
        global_seq=e.global_seq,
        event_type=e.event_type,
        actor_type=e.actor_type,
        actor_id=e.actor_id,
        session_id=e.session_id,
        invocation_id=e.invocation_id,
        job_id=e.job_id,
        node_id=e.node_id,
        timestamp=e.timestamp.isoformat() if e.timestamp else None,
        data=e.data,
    )


@router.post(
    "/nodes",
    response_model=ProvisionNodeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def provision_node(
    body: ProvisionNodeRequest,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
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
    _token: dict[str, str] = Depends(get_admin_token),
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
