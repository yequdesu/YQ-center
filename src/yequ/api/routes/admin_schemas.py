"""Shared Admin route DTOs and response converters."""

from typing import TypedDict

from pydantic import BaseModel, Field

from yequ.models.capability import Capability
from yequ.models.invocation import Invocation
from yequ.models.job import Job
from yequ.models.node import Node
from yequ.models.runtime_instance import RuntimeInstance
from yequ.models.timeline import TimelineEvent


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
    fresh_signal_count: int = 0
    stale_signal_count: int = 0
    signal_stale: bool = False


class NodeDetail(NodeSummary):
    token_hash: str
    heartbeat_interval_sec: int | None = None
    job_delivery_mode: str | None = None
    created_at: str | None = None


class RuntimeSummary(BaseModel):
    runtime_id: str
    node_id: str
    kind: str
    status: str
    labels: list[str] = Field(default_factory=list)
    owner: str | None = None
    privilege: str | None = None
    interactive: bool = False
    last_seen_at: str | None = None
    metadata: dict[str, object] | None = None


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
    execution_requirements: dict[str, object] | None = None
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
    runtime_id: str | None = None
    execution_requirements: dict[str, object] | None = None
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


# Helper converters


class NodeLivenessFields(TypedDict):
    stored_status: str
    effective_status: str
    heartbeat_age_sec: float | None
    heartbeat_stale: bool
    schedulable: bool
    status: str


def _node_liveness_fields(n: Node) -> NodeLivenessFields:
    """Add liveness snapshot fields for node API responses."""
    from yequ.config import get_settings
    from yequ.services.node_liveness_service import get_node_liveness_snapshot

    snap = get_node_liveness_snapshot(n, get_settings())
    effective_status = str(snap["effective_status"])
    heartbeat_age = snap["heartbeat_age_sec"]
    return {
        "stored_status": n.status,
        "effective_status": effective_status,
        "heartbeat_age_sec": heartbeat_age if isinstance(heartbeat_age, float) else None,
        "heartbeat_stale": bool(snap["heartbeat_stale"]),
        "schedulable": bool(snap["schedulable"]),
        "status": effective_status,  # override: API shows effective status as primary
    }


def _node_summary(n: Node) -> NodeSummary:

    liveness = _node_liveness_fields(n)

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


def _runtime_summary(runtime: RuntimeInstance, node_id: str) -> RuntimeSummary:
    return RuntimeSummary(
        runtime_id=runtime.runtime_id,
        node_id=node_id,
        kind=runtime.kind,
        status=runtime.status,
        labels=list(runtime.labels or []),
        owner=runtime.owner,
        privilege=runtime.privilege,
        interactive=bool(runtime.interactive),
        last_seen_at=runtime.last_seen_at.isoformat() if runtime.last_seen_at else None,
        metadata=runtime.metadata_json,
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

    approval_required = c.effect in ("write", "destructive") or c.risk in (
        "maintenance",
        "destructive",
        "catastrophic",
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
        execution_requirements=c.execution_requirements,
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
        runtime_id=j.runtime_id,
        execution_requirements=j.execution_requirements_snapshot,
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
