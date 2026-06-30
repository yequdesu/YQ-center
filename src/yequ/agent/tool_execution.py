"""Agent Tool Execution — complete response structures.

These are the stable contract types for /agent/invoke responses.
Fields can be added but never removed or redefined.
"""

from pydantic import BaseModel, Field

from yequ.agent.limits import (
    DEFAULT_AGENT_MAX_DEPTH,
    DEFAULT_AGENT_MAX_STEPS,
    DEFAULT_AGENT_MAX_TOTAL_DURATION_SEC,
)


class AgentToolPolicySnapshot(BaseModel):
    """Policy decision snapshot for each tool call."""

    decision: str  # allow / deny / ask / conditional
    risk: str
    effect: str
    execution_mode: str


class AgentToolCall(BaseModel):
    """A single tool call planned by the Provider and executed by Center."""

    call_id: str
    name: str  # original function name (e.g. system.metrics.snapshot)
    sanitized_name: str  # Provider-safe name (e.g. system_metrics_snapshot)
    input: dict[str, object] = Field(default_factory=dict)
    target_node_id: str = ""
    policy: AgentToolPolicySnapshot | None = None
    invocation_id: str = ""
    job_ids: list[str] = Field(default_factory=list)
    # pending / executing / succeeded / failed / timeout / cancelled / denied
    status: str = "pending"
    result: dict[str, object] | None = None
    error: dict[str, object] | None = None
    started_at: str | None = None
    finished_at: str | None = None


class AgentInvokeOutput(BaseModel):
    """Agent output — message + structured data."""

    message: str = ""
    summary: str = ""  # human-readable summary
    highlights: list[str] = Field(default_factory=list)  # key findings
    tool_results: dict[str, object] = Field(default_factory=dict)  # tool_name -> result
    data: dict[str, object] = Field(default_factory=dict)


class AgentInvokeError(BaseModel):
    """Standard error for Agent invoke."""

    code: str = ""
    message: str = ""
    retryable: bool = False
    details: dict[str, object] = Field(default_factory=dict)


class AgentInvokeUsage(BaseModel):
    """Token usage and tool call statistics."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    tool_calls: int = 0


class AgentInvokeTrace(BaseModel):
    """Call graph constraint trace."""

    trace_id: str = ""
    call_path: list[str] = Field(default_factory=list)
    step_count: int = 0
    max_depth: int = DEFAULT_AGENT_MAX_DEPTH
    max_steps: int = DEFAULT_AGENT_MAX_STEPS
    max_total_duration_sec: int = DEFAULT_AGENT_MAX_TOTAL_DURATION_SEC


class AgentInvokeResponse(BaseModel):
    """Full /agent/invoke response — stable contract.

    Fields can be added but never removed or redefined.
    """

    success: bool
    status: str = "pending"  # succeeded / failed / partial / timeout / cancelled
    provider_name: str = ""
    session_id: str = ""
    output: AgentInvokeOutput = Field(default_factory=AgentInvokeOutput)
    error: AgentInvokeError | None = None
    tool_calls: list[AgentToolCall] = Field(default_factory=list)
    usage: AgentInvokeUsage = Field(default_factory=AgentInvokeUsage)
    trace: AgentInvokeTrace = Field(default_factory=AgentInvokeTrace)
    metadata: dict[str, object] = Field(default_factory=dict)
