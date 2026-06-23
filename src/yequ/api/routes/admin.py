"""Admin endpoints for Node management and Invocation creation."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete as sql_delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.api.deps import get_admin_token, get_db
from yequ.models.api_token import ApiToken
from yequ.models.approval import ApprovalRequest
from yequ.models.agent_message import AgentMessage as AgentMessageModel
from yequ.models.capability import Capability
from yequ.models.invocation import Invocation
from yequ.models.job import Job
from yequ.models.node import Node
from yequ.models.resource_lock import ResourceLock
from yequ.models.session import Session
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
    approval_id: str = ""  # set when approval required
    dry_run: bool = False
    allowed: bool = True
    decision: str = ""
    reason: str = ""
    resource_keys: list[str] = Field(default_factory=list)


# ── Read models for GET endpoints ──

class NodeSummary(BaseModel):
    node_id: str
    node_name: str
    role: str
    locality: str
    status: str
    stored_status: str = ""
    effective_status: str = ""
    heartbeat_age_sec: float | None = None
    heartbeat_stale: bool = False
    schedulable: bool = False
    daemon_version: str | None = None
    platform_os: str | None = None
    platform_arch: str | None = None
    last_seen_at: str | None = None
    last_heartbeat_at: str | None = None
    last_capability_register_at: str | None = None
    running_jobs: int = 0
    active_capability_count: int = 0
    executable_capability_count: int = 0

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
    description: str | None = None
    agent_description: str | None = None
    user_visible_name: str | None = None
    status: str
    risk: str | None = None
    effect: str | None = None
    timeout_sec: int | None = None
    idempotency: str | None = None
    execution_context: str | None = None
    hidden_input_fields: list[str] = Field(default_factory=list)
    preflight_supported: bool = False
    scope: str | None = None
    ttl_sec: int | None = None
    is_active: bool
    node_id: str = ""
    node_status: str = ""
    available: bool = True
    unavailable_reason: str | None = None
    executable: bool = True
    inactive_reason: str | None = None
    approval_required: bool = False
    conflict_policy: str | None = None

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
    error_details: dict[str, object] | None = None
    cancel_reason: str | None = None
    attempt: int
    output: dict[str, object] | None = None

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

class RenameSessionRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=128)


@router.get("/sessions")
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[dict]:
    """List all active sessions, sorted by most recent activity."""
    from sqlalchemy import func
    from yequ.agent.agent_stream import is_session_running

    result = await db.execute(
        select(Session)
        .where(Session.status == "active")
        .order_by(Session.updated_at.desc().nullslast())
        .limit(100)
    )
    sessions = result.scalars().all()

    # Batch-fetch last message preview and message counts
    session_ids = [s.session_id for s in sessions]
    previews: dict[str, str] = {}
    counts: dict[str, int] = {}
    if session_ids:
        # Get message counts per session
        count_result = await db.execute(
            select(
                AgentMessageModel.session_id,
                func.count(AgentMessageModel.id).label("cnt"),
            )
            .where(AgentMessageModel.session_id.in_(session_ids))
            .group_by(AgentMessageModel.session_id)
        )
        for row in count_result:
            counts[row.session_id] = row.cnt

        # Get last user message per session (for preview)
        for sid in session_ids:
            preview_result = await db.execute(
                select(AgentMessageModel)
                .where(
                    AgentMessageModel.session_id == sid,
                    AgentMessageModel.role == "user",
                )
                .order_by(AgentMessageModel.created_at.desc())
                .limit(1)
            )
            last_msg = preview_result.scalar_one_or_none()
            if last_msg and last_msg.content:
                previews[sid] = last_msg.content[:80]

    return [
        {
            "session_id": s.session_id,
            "actor_id": s.actor_id,
            "status": s.status,
            "execution_mode": s.execution_mode,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "closed_at": s.closed_at.isoformat() if s.closed_at else None,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            "label": s.label or s.session_id[:8],
            "last_message_preview": previews.get(s.session_id, ""),
            "message_count": counts.get(s.session_id, 0),
            "running": is_session_running(s.session_id),
        }
        for s in sessions
    ]


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> dict:
    """Get a session by ID."""
    result = await db.execute(select(Session).where(Session.session_id == session_id))
    sess = result.scalar_one_or_none()
    if sess is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")
    message_result = await db.execute(
        select(AgentMessageModel)
        .where(AgentMessageModel.session_id == session_id)
        .order_by(AgentMessageModel.created_at.asc())
    )
    messages = [_agent_message_dict(m) for m in message_result.scalars().all()]
    return {
        "session_id": sess.session_id,
        "actor_type": sess.actor_type,
        "actor_id": sess.actor_id,
        "status": sess.status,
        "execution_mode": sess.execution_mode,
        "started_at": sess.started_at.isoformat() if sess.started_at else None,
        "updated_at": sess.updated_at.isoformat() if sess.updated_at else None,
        "closed_at": sess.closed_at.isoformat() if sess.closed_at else None,
        "close_reason": sess.close_reason,
        "metadata": sess.metadata_,
        "label": sess.label or sess.session_id[:8],
        "messages": messages,
    }


@router.get("/sessions/{session_id}/messages")
async def list_session_messages(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[dict]:
    """Return persisted messages for one Agent session."""
    result = await db.execute(select(Session).where(Session.session_id == session_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")

    message_result = await db.execute(
        select(AgentMessageModel)
        .where(AgentMessageModel.session_id == session_id)
        .order_by(AgentMessageModel.created_at.asc())
    )
    return [_agent_message_dict(m) for m in message_result.scalars().all()]


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: str,
    body: RenameSessionRequest,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> dict:
    """Rename a session (updates label field, preserving metadata)."""
    result = await db.execute(select(Session).where(Session.session_id == session_id))
    sess = result.scalar_one_or_none()
    if sess is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")
    sess.label = body.label
    await db.commit()
    return {"session_id": session_id, "label": body.label}


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> None:
    """Delete a session and its message history."""
    result = await db.execute(select(Session).where(Session.session_id == session_id))
    sess = result.scalar_one_or_none()
    if sess is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")

    await db.execute(
        sql_delete(AgentMessageModel).where(AgentMessageModel.session_id == session_id)
    )
    await db.delete(sess)
    await db.commit()


def _agent_message_dict(message: AgentMessageModel) -> dict:
    return {
        "message_id": message.message_id,
        "session_id": message.session_id,
        "role": message.role,
        "content": message.content,
        "tool_call_id": message.tool_call_id,
        "tool_calls": message.tool_calls or [],
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


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


# ── Node management endpoints ──


@router.post("/nodes/{node_id}/capabilities/refresh-state")
async def refresh_node_capability_state(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> dict:
    """Recompute executable state for all capabilities on this node."""
    result = await db.execute(select(Node).where(Node.node_id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(404, f"Node {node_id!r} not found")

    from yequ.services.node_liveness_service import is_node_schedulable
    from yequ.config import get_settings

    settings = get_settings()
    schedulable, reason = is_node_schedulable(node, settings)

    cap_result = await db.execute(
        select(Capability).where(
            Capability.node_record_id == node.id,
            Capability.is_active == True,  # noqa: E712
        )
    )
    caps = cap_result.scalars().all()

    return {
        "node_id": node_id,
        "node_status": node.status,
        "schedulable": schedulable,
        "unavailable_reason": reason if not schedulable else None,
        "active_capability_count": len(caps),
        "executable_capability_count": len(caps) if schedulable else 0,
    }


@router.post("/nodes/{node_id}/mark-offline")
async def mark_node_offline(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> dict:
    """Manually set a node's status to offline."""
    from yequ.protocol import NodeStatus

    result = await db.execute(select(Node).where(Node.node_id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(404, f"Node {node_id!r} not found")

    node.status = NodeStatus.OFFLINE
    await db.commit()
    return {"node_id": node_id, "status": node.status}


@router.delete("/nodes/{node_id}/stale-capabilities", status_code=200)
async def delete_stale_capabilities(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> dict:
    """Delete inactive capabilities for a node."""
    result = await db.execute(select(Node).where(Node.node_id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(404, f"Node {node_id!r} not found")

    from sqlalchemy import delete as sa_delete

    del_result = await db.execute(
        sa_delete(Capability).where(
            Capability.node_record_id == node.id,
            Capability.is_active == False,  # noqa: E712
        )
    )
    await db.commit()
    return {
        "node_id": node_id,
        "deleted_count": del_result.rowcount,
    }


@router.get("/capabilities", response_model=list[CapabilitySummary])
async def list_capabilities(
    node_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[CapabilitySummary]:
    from sqlalchemy.orm import joinedload

    """List capabilities, optionally filtered by node_id."""
    stmt = select(Capability).where(Capability.is_active).options(joinedload(Capability.node))
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
    return _inv_detail(inv, jobs)


@router.get("/invocations", response_model=list[InvocationDetail])
async def list_invocations(
    status: str | None = None,
    actor_id: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[InvocationDetail]:
    """List invocations with optional filters."""
    stmt = select(Invocation)
    if status:
        stmt = stmt.where(Invocation.status == status)
    if actor_id:
        stmt = stmt.where(Invocation.actor_id == actor_id)
    if session_id:
        stmt = stmt.where(Invocation.session_id == session_id)
    stmt = stmt.order_by(Invocation.started_at.desc().nullslast()).limit(min(limit, 200))
    result = await db.execute(stmt)
    invs = result.scalars().all()
    out = []
    for inv in invs:
        jresult = await db.execute(select(Job).where(Job.invocation_id == inv.invocation_id))
        jobs = jresult.scalars().all()
        out.append(_inv_detail(inv, jobs))
    return out


@router.get("/timeline", response_model=list[TimelineSummary])
async def list_timeline(
    node_id: str | None = None,
    job_id: str | None = None,
    invocation_id: str | None = None,
    session_id: str | None = None,
    event_type: str | None = None,
    approval_id: str | None = None,
    trace_id: str | None = None,
    limit: int = 50,
    created_after: str | None = None,
    created_before: str | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[TimelineSummary]:
    """Query timeline events with filters and cursor support."""
    stmt = select(TimelineEvent)
    if trace_id:
        stmt = stmt.where(TimelineEvent.trace_id == trace_id)
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
    if approval_id:
        # Resolve approval to get execution invocation, then include all related events
        apv_result = await db.execute(
            select(ApprovalRequest).where(ApprovalRequest.approval_id == approval_id)
        )
        approval = apv_result.scalar_one_or_none()
        if approval:
            # Find the execution invocation
            exec_inv_id = approval.consumed_invocation_id or approval.invocation_id
            # Get all job_ids for this invocation
            j_result = await db.execute(
                select(Job.job_id).where(Job.invocation_id == exec_inv_id)
            )
            job_ids = [row[0] for row in j_result.fetchall()]
            # Build OR filter
            conditions = [TimelineEvent.data.op('->>')('approval_id') == approval_id]
            if exec_inv_id:
                conditions.append(TimelineEvent.invocation_id == exec_inv_id)
            if job_ids:
                conditions.append(TimelineEvent.job_id.in_(job_ids))
            stmt = stmt.where(or_(*conditions))
        else:
            # Fallback: just filter by approval_id in data
            stmt = stmt.where(TimelineEvent.data.op('->>')('approval_id') == approval_id)
    if created_after:
        stmt = stmt.where(TimelineEvent.timestamp > datetime.fromisoformat(created_after))
    if created_before:
        stmt = stmt.where(TimelineEvent.timestamp < datetime.fromisoformat(created_before))
    stmt = stmt.order_by(TimelineEvent.global_seq.desc()).limit(min(limit, 200))
    result = await db.execute(stmt)
    events = result.scalars().all()
    return [_tl_summary(e) for e in events]


@router.get("/locks")
async def list_locks(
    status: str | None = None,
    node_id: str | None = None,
    job_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict = Depends(get_admin_token),
) -> list[dict]:
    """List resource locks with optional filters."""
    stmt = select(ResourceLock)
    if status:
        stmt = stmt.where(ResourceLock.status == status)
    if node_id:
        stmt = stmt.where(ResourceLock.node_id == node_id)
    if job_id:
        stmt = stmt.where(ResourceLock.job_id == job_id)
    stmt = stmt.order_by(ResourceLock.created_at.desc()).limit(200)
    result = await db.execute(stmt)
    locks = result.scalars().all()
    return [{
        "lock_id": l.lock_id, "resource_key": l.resource_key,
        "job_id": l.job_id, "invocation_id": l.invocation_id,
        "node_id": l.node_id, "status": l.status,
        "expires_at": l.expires_at.isoformat() if l.expires_at else None,
        "created_at": l.created_at.isoformat() if l.created_at else None,
        "released_at": l.released_at.isoformat() if l.released_at else None,
    } for l in locks]


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
    input_data: dict[str, object] = Field(default_factory=dict)
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
    _token: dict = Depends(get_admin_token),
) -> ApprovalResponse:
    from yequ.services.approval_service import create_approval
    approval = await create_approval(db, **body.model_dump())
    return ApprovalResponse(
        approval_id=approval.approval_id, status=approval.status,
        function_name=approval.function_name, target_node_id=approval.target_node_id,
        actor_id=approval.actor_id,
        expires_at=approval.expires_at.isoformat(), created_at=approval.created_at.isoformat(),
    )


@router.get("/approvals")
async def list_approvals(
    approval_status: str | None = Query(default=None, alias="status"),
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _token: dict = Depends(get_admin_token),
) -> list[dict]:
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
    _token: dict = Depends(get_admin_token),
) -> dict:
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
                "invocation_id": inv.invocation_id, "status": inv.status,
                "function_name": inv.function_name,
                "started_at": inv.started_at.isoformat() if inv.started_at else None,
                "finished_at": inv.finished_at.isoformat() if inv.finished_at else None,
            }
            jresult = await db.execute(
                select(Job).where(Job.invocation_id == a.invocation_id)
            )
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
                "invocation_id": ci.invocation_id, "status": ci.status,
                "function_name": ci.function_name,
                "started_at": ci.started_at.isoformat() if ci.started_at else None,
                "finished_at": ci.finished_at.isoformat() if ci.finished_at else None,
            }
            jresult = await db.execute(
                select(Job).where(Job.invocation_id == ci.invocation_id)
            )
            data["consumed_invocation"]["jobs"] = [
                {"job_id": j.job_id, "status": j.status} for j in jresult.scalars().all()
            ]

    return data


@router.post("/approvals/{approval_id}/approve")
async def approve_endpoint(
    approval_id: str,
    body: dict | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict = Depends(get_admin_token),
) -> dict:
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
    body: dict | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict = Depends(get_admin_token),
) -> dict:
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
    body: dict | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict = Depends(get_admin_token),
) -> dict:
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

    from yequ.services.approval_service import approve_approval, consume_approval
    from yequ.services.invocation_service import create_invocation, start_invocation
    from yequ.services.job_service import create_job
    from yequ.services.node_liveness_service import is_node_schedulable
    from yequ.config import get_settings

    reason = body.get("reason") if body else None

    # Liveness gate on target node
    target_node_result = await db.execute(
        select(Node).where(Node.node_id == a.target_node_id)
    )
    target_node = target_node_result.scalar_one_or_none()
    if target_node is None:
        raise HTTPException(404, f"Node {a.target_node_id!r} not found")

    schedulable, unschedulable_reason = is_node_schedulable(target_node, get_settings())
    if not schedulable:
        raise HTTPException(
            409,
            {
                "error_code": "NODE_UNAVAILABLE",
                "error_message": f"Node {a.target_node_id} is not schedulable: {unschedulable_reason}",
            },
        )

    try:
        # Approve + consume
        await approve_approval(db, a, approved_by="admin", reason=reason)
        # Re-query approval to get updated state after approve
        await db.refresh(a)
        # Create invocation
        input_data = a.input_snapshot or {}
        inv = await create_invocation(
            db,
            actor_type="agent",
            actor_id=a.actor_id,
            session_id=a.session_id,
            function_name=a.function_name,
            input_payload=input_data,
            target_node_id=a.target_node_id,
            execution_mode="auto",
        )
        start_invocation(inv)

        # Create job
        job = await create_job(
            db,
            invocation_id=inv.invocation_id,
            node_id=a.target_node_id,
            function_name=a.function_name,
            input_payload=input_data,
            timeout_sec=30,
            resource_keys=list(a.resource_keys) if a.resource_keys else [],
            approval_id=a.approval_id,
        )

        # Consume the approval
        await consume_approval(db, a, invocation_id=inv.invocation_id)
        await db.commit()

        return {
            "approval_id": a.approval_id,
            "invocation_id": inv.invocation_id,
            "job_id": job.job_id,
            "function_name": a.function_name,
            "target_node_id": a.target_node_id,
            "status": job.status,
        }
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


def _approval_dict(a: ApprovalRequest) -> dict:
    return {
        "approval_id": a.approval_id, "status": a.status,
        "actor_id": a.actor_id, "session_id": a.session_id,
        "function_name": a.function_name, "target_node_id": a.target_node_id,
        "input_hash": a.input_hash, "risk": a.risk, "effect": a.effect,
        "resource_keys": a.resource_keys,
        "expires_at": a.expires_at.isoformat(), "created_at": a.created_at.isoformat(),
        "consumed_at": a.consumed_at.isoformat() if a.consumed_at else None,
        "approved_by": a.approved_by, "denied_by": a.denied_by,
        "decision_reason": a.decision_reason,
        "consumed_invocation_id": a.consumed_invocation_id,
    }


# ── Helper converters ──

def _node_liveness_fields(n: Node) -> dict:
    """Add liveness snapshot fields for node API responses."""
    from yequ.config import get_settings
    from yequ.services.node_liveness_service import get_node_liveness_snapshot

    snap = get_node_liveness_snapshot(n, get_settings())
    return {
        "stored_status": n.status,
        "effective_status": snap["effective_status"],
        "heartbeat_age_sec": snap["heartbeat_age_sec"],
        "heartbeat_stale": snap["heartbeat_stale"],
        "schedulable": snap["schedulable"],
        "status": snap["effective_status"],  # override: API shows effective status as primary
    }


def _node_summary(n: Node) -> NodeSummary:
    from sqlalchemy import func, select as sa_select
    from yequ.models.capability import Capability as CapModel
    from yequ.models.job import Job as JobModel

    liveness = _node_liveness_fields(n)

    # Count running jobs for this node
    running_jobs = 0
    active_caps = 0
    executable_caps = 0

    return NodeSummary(
        node_id=n.node_id,
        node_name=n.node_name,
        role=n.role,
        locality=n.locality,
        status=liveness["status"],
        stored_status=liveness["stored_status"],
        effective_status=liveness["effective_status"],
        heartbeat_age_sec=liveness["heartbeat_age_sec"],
        heartbeat_stale=liveness["heartbeat_stale"],
        schedulable=liveness["schedulable"],
        daemon_version=n.daemon_version,
        platform_os=n.platform_os,
        platform_arch=n.platform_arch,
        last_seen_at=n.last_seen_at.isoformat() if n.last_seen_at else None,
        last_heartbeat_at=n.last_heartbeat_at.isoformat() if n.last_heartbeat_at else None,
        last_capability_register_at=None,
        running_jobs=0,
        active_capability_count=0,
        executable_capability_count=0,
    )


def _node_detail(n: Node) -> NodeDetail:
    liveness = _node_liveness_fields(n)
    return NodeDetail(
        node_id=n.node_id,
        node_name=n.node_name,
        role=n.role,
        locality=n.locality,
        status=liveness["status"],
        stored_status=liveness["stored_status"],
        effective_status=liveness["effective_status"],
        heartbeat_age_sec=liveness["heartbeat_age_sec"],
        heartbeat_stale=liveness["heartbeat_stale"],
        schedulable=liveness["schedulable"],
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
    from yequ.config import get_settings
    from yequ.services.node_liveness_service import get_node_liveness_snapshot

    snap = get_node_liveness_snapshot(c.node, get_settings()) if c.node else {}
    node_schedulable = snap.get("schedulable", True)
    node_status = snap.get("effective_status", "")
    is_executable = c.is_active and node_schedulable

    inactive_reason = None
    if not is_executable:
        if not c.is_active:
            inactive_reason = "capability_inactive"
        elif not node_schedulable:
            inactive_reason = snap.get("unavailable_reason") or "node_offline"

    approval_required = (
        c.effect in ("write", "destructive")
        or c.risk in ("maintenance", "destructive", "catastrophic")
    )

    return CapabilitySummary(
        plugin_id=c.plugin_id,
        plugin_version=c.plugin_version,
        capability_type=c.capability_type,
        name=c.name,
        description=c.description,
        agent_description=c.agent_description,
        user_visible_name=c.user_visible_name,
        status=c.status,
        risk=c.risk,
        effect=c.effect,
        timeout_sec=c.timeout_sec,
        idempotency=c.idempotency,
        execution_context=c.execution_context,
        hidden_input_fields=list(c.hidden_input_fields or []),
        preflight_supported=c.preflight_supported,
        scope=c.scope,
        ttl_sec=c.ttl_sec,
        is_active=c.is_active,
        node_id=c.node.node_id if c.node else "",
        node_status=node_status,
        available=node_schedulable and c.is_active,
        unavailable_reason=snap.get("unavailable_reason") if not node_schedulable else None,
        executable=is_executable,
        inactive_reason=inactive_reason,
        approval_required=approval_required,
        conflict_policy=c.conflict_policy,
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
        error_details=j.error_details,
        cancel_reason=j.cancel_reason,
        attempt=j.attempt,
        output=j.output,
    )

def _inv_detail(inv: Invocation, jobs: list[Job]) -> InvocationDetail:
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

    # Liveness gate: reject invocations for offline/degraded nodes
    from yequ.config import get_settings
    from yequ.services.node_liveness_service import is_node_schedulable

    schedulable, reason = is_node_schedulable(node, get_settings())
    if not schedulable:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "NODE_UNAVAILABLE",
                "error_message": f"Node {body.target_node_id} is not schedulable: {reason}",
                "node_id": body.target_node_id,
                "effective_status": reason or "unavailable",
            },
        )

    # Look up the capability to get risk/effect and resource_key_template
    cap_result = await db.execute(
        select(Capability).where(
            Capability.capability_type == "function",
            Capability.name == body.function_name,
            Capability.is_active == True,  # noqa: E712
        ).limit(1)
    )
    capability = cap_result.scalar_one_or_none()
    func_risk = capability.risk if capability else "safe"
    func_effect = capability.effect if capability else "read"
    resource_key_template = capability.resource_keys[0] if (capability and capability.resource_keys) else None

    # If approval_id was provided, verify and consume it (bypassing L2 policy)
    if body.approval_id:
        from yequ.services.approval_service import verify_approval, consume_approval  # noqa: I001
        try:
            approval = await verify_approval(
                db, body.approval_id, actor_id=body.actor_id,
                session_id=body.session_id, function_name=body.function_name,
                target_node_id=body.target_node_id, input_data=body.input_payload,
            )
        except ValueError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
    elif body.dry_run:
        # ── dry_run=true: pre-check only, no job/approval/locks created ──
        from sqlalchemy import func as _f
        max_seq_r = await db.execute(select(_f.max(TimelineEvent.global_seq)))
        _max_seq = max_seq_r.scalar() or 0
        event = TimelineEvent(
            global_seq=_max_seq + 1,
            event_type="l2.dry_run.completed",
            actor_type="admin", actor_id=body.actor_id,
            data={"input": body.input_payload,
                  "function_name": body.function_name,
                  "target_node_id": body.target_node_id},
            timestamp=datetime.now(timezone.utc),
        )
        db.add(event)
        # Compute resource keys for the potential operation
        from yequ.services.resource_lock_service import compute_resource_keys
        res_keys = compute_resource_keys(
            function_name=body.function_name,
            node_id=body.target_node_id,
            input_data=body.input_payload,
            resource_key_template=resource_key_template,
        )
        await db.commit()
        return CreateInvocationResponse(
            invocation_id="", job_id="",
            function_name=body.function_name, target_node_id=body.target_node_id,
            invocation_status="dry_run_completed", job_status="",
            dry_run=True, allowed=False, decision="ask",
            reason="approval_required",
            resource_keys=res_keys if res_keys else [],
        )
    else:
        # L2 policy check for write operations
        from yequ.services.policy import check_policy_l2
        policy_result = check_policy_l2(
            execution_mode=body.execution_mode,
            risk_level=func_risk,
            effect=func_effect,
        )

        if not policy_result.allowed:
            # Approval required — create approval and return waiting_approval
            if policy_result.decision == "ask":
                # Create invocation first
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
                inv.status = "waiting_approval"
                await db.flush()

                from yequ.services.approval_service import create_approval as create_appr
                approval = await create_appr(
                    db, actor_id=body.actor_id, session_id=body.session_id,
                    function_name=body.function_name, target_node_id=body.target_node_id,
                    input_data=body.input_payload, risk="maintenance", effect="write",
                    resource_key_template=resource_key_template,
                    invocation_id=inv.invocation_id,
                )
                await db.commit()
                return CreateInvocationResponse(
                    invocation_id=inv.invocation_id, job_id="",
                    function_name=body.function_name, target_node_id=body.target_node_id,
                    invocation_status="waiting_approval", job_status="pending",
                    approval_id=approval.approval_id,
                )
            else:
                raise HTTPException(status_code=403, detail=policy_result.reason or "Policy denied")

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

    # Consume the approval with the execution invocation_id (Bug 2)
    if body.approval_id:
        from yequ.services.approval_service import consume_approval
        try:
            await consume_approval(db, approval, invocation_id=inv.invocation_id)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    # Use approval's resource keys if available (reuse, don't recompute)
    try:
        res_keys = approval.resource_keys
    except (NameError, AttributeError):
        # Compute resource keys for locking
        from yequ.services.resource_lock_service import compute_resource_keys
        res_keys = compute_resource_keys(
            function_name=body.function_name,
            node_id=body.target_node_id,
            input_data=body.input_payload,
            resource_key_template=resource_key_template,
        )

    # Create Job (with lock acquisition) — explicit dry_run=False for approved invocations
    try:
        dry_run_val = False if body.approval_id else body.dry_run
        job = await create_job(
            db,
            invocation_id=inv.invocation_id,
            node_id=body.target_node_id,
            function_name=body.function_name,
            input_payload=body.input_payload,
            timeout_sec=body.timeout_sec,
            lease_sec=body.lease_sec,
            resource_keys=res_keys,
            dry_run=dry_run_val,
            approval_id=body.approval_id,
        )
    except ValueError as e:
        # Write resource.lock.conflict event before the transaction rolls back
        from sqlalchemy import func as sql_func
        seq_r = await db.execute(select(sql_func.max(TimelineEvent.global_seq)))
        seq_max = seq_r.scalar() or 0
        conflict_event = TimelineEvent(
            global_seq=seq_max + 1,
            event_type="resource.lock.conflict",
            actor_type="system", actor_id=body.actor_id,
            node_id=body.target_node_id,
            invocation_id=inv.invocation_id,
            data={"error": str(e)},
            timestamp=datetime.now(timezone.utc),
        )
        db.add(conflict_event)
        await db.commit()
        raise HTTPException(status_code=409, detail=str(e)) from e

    await db.commit()

    # L2 action started: write timeline event for approved execution
    if body.approval_id and job.approval_id:
        from yequ.models.timeline import TimelineEvent as TlEvent
        from sqlalchemy import func as sql_func, select as sql_select
        l2_seq = await db.execute(sql_select(sql_func.max(TlEvent.global_seq)))
        l2_max = l2_seq.scalar() or 0
        l2_event = TlEvent(
            global_seq=l2_max + 1,
            event_type="l2.action.started",
            actor_type="admin", actor_id=body.actor_id,
            node_id=body.target_node_id, job_id=job.job_id,
            invocation_id=inv.invocation_id,
            data={
                "approval_id": body.approval_id,
                "function_name": body.function_name,
                "input": body.input_payload,
            },
            timestamp=datetime.now(timezone.utc),
        )
        db.add(l2_event)
        await db.commit()

    return CreateInvocationResponse(
        invocation_id=inv.invocation_id,
        job_id=job.job_id,
        function_name=body.function_name,
        target_node_id=body.target_node_id,
        invocation_status=inv.status,
        job_status=job.status,
    )
