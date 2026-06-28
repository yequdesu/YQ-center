"""Shared enums — single source of truth for Center, Daemon, and Agent."""

from enum import StrEnum


class YqpVersion(StrEnum):
    V0_1 = "0.1"


class MessageType(StrEnum):
    """All YQP message types."""

    # Node lifecycle
    NODE_HELLO = "node.hello"
    NODE_ACCEPTED = "node.accepted"
    NODE_REGISTER_CAPABILITIES = "node.register_capabilities"
    REGISTRY_ACCEPTED = "registry.accepted"
    NODE_HEARTBEAT = "node.heartbeat"
    NODE_RECONCILE_JOBS = "node.reconcile_jobs"
    JOB_RECONCILIATION = "job.reconciliation"

    # Signal
    SIGNAL_REPORT = "signal.report"

    # Artifact
    ARTIFACT_UPLOAD = "artifact.upload"
    ARTIFACT_ACCEPTED = "artifact.accepted"

    # Job delivery
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

    # Error
    ERROR = "error"


class NodeStatus(StrEnum):
    PROVISIONED = "provisioned"
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    REJOINING = "rejoining"


class JobStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class JobDeliveryMode(StrEnum):
    POLL = "poll"
    WEBSOCKET_PUSH = "websocket_push"


class RiskLevel(StrEnum):
    SAFE = "safe"
    MAINTENANCE = "maintenance"
    DESTRUCTIVE = "destructive"
    CATASTROPHIC = "catastrophic"


class Effect(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    EXTERNAL = "external"


class Idempotency(StrEnum):
    IDEMPOTENT = "idempotent"
    NON_IDEMPOTENT = "non_idempotent"
    TRANSACTIONAL = "transactional"


class ExecutionMode(StrEnum):
    AUTO = "auto"
    ASSIST = "assist"
    READONLY = "readonly"
    MANUAL = "manual"


class SignalScope(StrEnum):
    NODE = "node"
    PLUGIN = "plugin"
    RESOURCE = "resource"


class ConflictPolicy(StrEnum):
    ALLOW_PARALLEL = "allow_parallel"
    SERIALIZE = "serialize"
    REJECT_IF_RUNNING = "reject_if_running"


class ReconciliationAction(StrEnum):
    CONTINUE = "continue"
    CANCEL = "cancel"
    ACCEPT_RESULT = "accept_result"
    DISCARD_RESULT = "discard_result"
    FORGET = "forget"


class InvocationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    PARTIAL = "partial"
    WAITING_APPROVAL = "waiting_approval"
    REJECTED = "rejected"


class PluginStatus(StrEnum):
    LOADED = "loaded"
    ERROR = "error"


class ActorType(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    INTERRUPTED = "interrupted"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CONSUMED = "consumed"


class LockStatus(StrEnum):
    HELD = "held"
    RELEASED = "released"
    EXPIRED = "expired"
