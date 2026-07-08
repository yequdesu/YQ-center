from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

YQP_VERSION = "0.1"


class MessageType(StrEnum):
    NODE_HELLO = "node.hello"
    NODE_ACCEPTED = "node.accepted"
    NODE_REGISTER_CAPABILITIES = "node.register_capabilities"
    REGISTRY_ACCEPTED = "registry.accepted"
    NODE_HEARTBEAT = "node.heartbeat"
    SIGNAL_REPORT = "signal.report"
    JOB_POLL = "job.poll"
    JOB_AVAILABLE = "job.available"
    JOB_EMPTY = "job.empty"
    JOB_DISPATCH = "job.dispatch"
    JOB_ACCEPTED = "job.accepted"
    JOB_EVENT = "job.event"
    JOB_FINISHED = "job.finished"
    JOB_LEASE_RENEW = "job.lease_renew"
    JOB_LEASE_ACCEPTED = "job.lease_accepted"
    JOB_LEASE_DENIED = "job.lease_denied"
    JOB_CANCEL = "job.cancel"
    NODE_RECONCILE_JOBS = "node.reconcile_jobs"
    JOB_RECONCILIATION = "job.reconciliation"
    ARTIFACT_UPLOAD = "artifact.upload"
    ARTIFACT_ACCEPTED = "artifact.accepted"
    ERROR = "error"


class JobStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class JobEventType(StrEnum):
    STARTED = "job.started"
    PROGRESS = "job.progress"
    LOG = "job.log"
    RESULT = "job.result"
    FAILED = "job.failed"
    CANCELLING = "job.cancelling"
    CANCELLED = "job.cancelled"
    TIMEOUT = "job.timeout"


class JobDeliveryMode(StrEnum):
    POLL = "poll"
    WEBSOCKET_PUSH = "websocket_push"


class Envelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    yqp_version: str = YQP_VERSION
    message_id: str = Field(default_factory=lambda: f"msg_{uuid4().hex}")
    message_type: str
    trace_id: str = Field(default_factory=lambda: f"tr_{uuid4().hex}")
    node_id: str | None = None
    session_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any]


class PlatformInfo(BaseModel):
    os: str
    arch: str
    os_version: str | None = None
    hostname: str | None = None


class RuntimeInstancePayload(BaseModel):
    runtime_id: str
    kind: str
    status: Literal["online", "offline", "degraded", "not_installed", "unknown"] = "online"
    labels: list[str] = Field(default_factory=list)
    owner: str | None = None
    privilege: str | None = None
    interactive: bool = False
    last_seen_at: datetime | None = None
    metadata: dict[str, Any] | None = None


class NodeHelloPayload(BaseModel):
    daemon_version: str
    node_name: str
    role: list[str]
    locality: Literal["local", "lan", "wan"]
    platform: PlatformInfo
    runtimes: list[RuntimeInstancePayload] = Field(default_factory=list)


class NodeAcceptedPayload(BaseModel):
    heartbeat_interval_sec: int = 10
    heartbeat_timeout_multiplier: int = 3
    signal_report_interval_sec: int = 5
    signal_stale_multiplier: int = 3
    job_delivery_mode: JobDeliveryMode = JobDeliveryMode.POLL
    job_poll_interval_sec: int | None = 3
    server_time: datetime | None = None


class FunctionManifest(BaseModel):
    name: str
    description: str | None = None
    agent_description: str | None = None
    user_visible_name: str | None = None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    risk: Literal["safe", "maintenance", "destructive", "catastrophic"] = "safe"
    effect: Literal["read", "write", "destructive", "external"] = "read"
    timeout_sec: int = 5
    lease_sec: int = 10
    idempotency: Literal["idempotent", "non_idempotent", "transactional"] = "idempotent"
    resource_keys: list[str] = Field(default_factory=list)
    resource_key_template: list[str] = Field(default_factory=list)
    conflict_policy: Literal["allow_parallel", "serialize", "reject_if_running"] = "allow_parallel"
    approval_required: bool = False
    dry_run_supported: bool = False
    preflight_supported: bool = False
    rollback_supported: bool = False
    supports_progress: bool = False
    supports_cancel: bool = False
    supports_resume: bool = False
    progress_contract: str | None = None
    preconditions: list[dict[str, Any]] = Field(default_factory=list)
    required_intent_slots: list[str] = Field(default_factory=list)
    execution_context: Literal["system", "user", "hybrid"] = "system"
    execution_requirements: dict[str, Any] | None = None
    hidden_input_fields: list[str] = Field(default_factory=list)
    examples: list[dict[str, Any]] = Field(default_factory=list)
    failure_modes: list[dict[str, Any]] = Field(default_factory=list)


class SignalManifest(BaseModel):
    name: str
    scope: Literal["node", "plugin", "resource"] = "node"
    ttl_sec: int
    value_schema: dict[str, Any]


class PluginManifest(BaseModel):
    plugin_id: str
    plugin_version: str
    status: Literal["loaded", "error"] = "loaded"
    error: dict[str, Any] | None = None
    functions: list[FunctionManifest] = Field(default_factory=list)
    signals: list[SignalManifest] = Field(default_factory=list)


class RegisterCapabilitiesPayload(BaseModel):
    plugins: list[PluginManifest]
    runtimes: list[RuntimeInstancePayload] = Field(default_factory=list)


class HeartbeatPayload(BaseModel):
    daemon_uptime_sec: int
    running_jobs: int
    plugin_count: int
    status: Literal["online", "degraded", "offline", "rejoining"] = "online"
    runtimes: list[RuntimeInstancePayload] = Field(default_factory=list)


class SignalValue(BaseModel):
    name: str
    scope: Literal["node", "plugin", "resource"] = "node"
    value: Any
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ttl_sec: int


class SignalReportPayload(BaseModel):
    signals: list[SignalValue]


class JobPayload(BaseModel):
    job_id: str
    invocation_id: str | None = None
    function: str
    input: dict[str, Any] = Field(default_factory=dict)
    runtime_id: str | None = None
    execution_requirements: dict[str, Any] = Field(default_factory=dict)
    approval_id: str | None = None
    dry_run: bool | None = None
    resource_keys: list[str] = Field(default_factory=list)
    timeout_sec: int = 5
    lease_sec: int = 10


class JobPollPayload(BaseModel):
    capacity: int = 1
    running_jobs: list[str] = Field(default_factory=list)


class JobAvailablePayload(BaseModel):
    jobs: list[JobPayload] = Field(default_factory=list)


class JobAcceptedPayload(BaseModel):
    job_id: str
    accepted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class JobEventPayload(BaseModel):
    job_id: str
    event_type: JobEventType
    sequence: int
    data: dict[str, Any] = Field(default_factory=dict)


class JobFinishedPayload(BaseModel):
    job_id: str
    status: Literal["succeeded", "failed", "cancelled", "timeout"]
    output: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class JobLeaseRenewPayload(BaseModel):
    job_id: str
    lease_extend_sec: int = 10


class KnownJob(BaseModel):
    job_id: str
    local_status: JobStatus
    started_at: datetime | None = None
    updated_at: datetime | None = None
    finished_at: datetime | None = None
    output: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class ReconcileJobsPayload(BaseModel):
    known_jobs: list[KnownJob] = Field(default_factory=list)


class ReconciliationAction(BaseModel):
    job_id: str
    action: Literal["continue", "cancel", "accept_result", "discard_result", "forget"]
    lease_sec: int | None = None
    reason: str | None = None
    reconciled: bool | None = None


class JobReconciliationPayload(BaseModel):
    actions: list[ReconciliationAction] = Field(default_factory=list)
