"""Standard error codes and error structures for YQP."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCode(StrEnum):
    AUTH_FAILED = "auth_failed"
    SCHEMA_INVALID = "schema_invalid"
    FUNCTION_NOT_AVAILABLE = "function_not_available"
    JOB_NOT_FOUND = "job_not_found"
    LEASE_EXPIRED = "lease_expired"
    CANCEL_REQUESTED = "cancel_requested"
    PLUGIN_LOAD_FAILED = "plugin_load_failed"
    VERSION_INCOMPATIBLE = "version_incompatible"
    INTERNAL_ERROR = "internal_error"
    DUPLICATE_MESSAGE = "duplicate_message"
    TIMESTAMP_OUT_OF_RANGE = "timestamp_out_of_range"
    NODE_NOT_FOUND = "node_not_found"
    NODE_OFFLINE = "node_offline"
    INVALID_STATE_TRANSITION = "invalid_state_transition"
    CALL_DEPTH_EXCEEDED = "call_depth_exceeded"
    MAX_STEPS_EXCEEDED = "max_steps_exceeded"
    MAX_DURATION_EXCEEDED = "max_duration_exceeded"
    CIRCULAR_DEPENDENCY = "circular_dependency"
    POLICY_DENIED = "policy_denied"


class YqpError(BaseModel):
    """Standard YQP error structure."""

    code: ErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
