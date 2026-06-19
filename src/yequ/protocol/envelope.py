"""YQP message envelope — every message is wrapped in this."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from yequ.protocol.enums import MessageType, YqpVersion


class YqpEnvelope(BaseModel):
    """Unified YQP message envelope.

    All messages between Center and Node use this structure.
    Center and Agent communication may also use this in the future.
    """

    yqp_version: YqpVersion = YqpVersion.V0_1
    message_id: str = Field(..., description="Unique message ID for dedup and audit")
    message_type: MessageType = Field(..., description="Message type")
    trace_id: str = Field(..., description="Trace ID for distributed tracing")
    node_id: str | None = Field(default=None, description="Node ID, required for Node messages")
    session_id: str | None = Field(default=None, description="Session ID for user/agent context")
    timestamp: datetime = Field(..., description="Message generation time")
    payload: dict[str, Any] = Field(default_factory=dict, description="Business payload")
