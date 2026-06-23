"""Agent Provider abstract interface.

An Agent Provider wraps an LLM backend and exposes a standard
invocation interface. Providers never call Nodes or Plugins
directly — all execution goes through the Center's standard path.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

# Fields that must be hidden from LLM context (internal execution flags).
# The DB and API retain these for audit; the LLM must not see them.
AGENT_HIDDEN_RESULT_FIELDS = {"approval_id", "dry_run", "approval_enforced"}


def sanitize_tool_payload_for_agent(value: object) -> object:
    """Remove internal execution fields before feeding tool results to an LLM.

    Strips approval_id, dry_run, and approval_enforced recursively from
    dicts and lists. These are implementation details the LLM should not
    reason about.
    """
    if isinstance(value, dict):
        return {
            key: sanitize_tool_payload_for_agent(item)
            for key, item in value.items()
            if key not in AGENT_HIDDEN_RESULT_FIELDS
        }
    if isinstance(value, list):
        return [sanitize_tool_payload_for_agent(item) for item in value]
    return value


@dataclass
class AgentMessage:
    """A single message in a multi-turn conversation.

    role: "system" | "user" | "assistant" | "tool"
    content: text content (may be None for assistant messages with only tool_calls)
    tool_call_id: for "tool" role, the id of the assistant's tool_call this responds to
    tool_calls: for "assistant" role, the tool calls the LLM requested
    """

    role: str
    content: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, object]] | None = None
    message_id: str | None = None


@dataclass
class AgentFunction:
    """Metadata about a function the Agent can reason about."""

    name: str
    description: str = ""
    input_schema: dict[str, object] | None = None
    output_schema: dict[str, object] | None = None
    risk: str = "safe"
    effect: str = "read"
    timeout_sec: int = 30


@dataclass
class AgentResult:
    """Result of an Agent Provider invocation."""

    success: bool
    output: dict[str, object] | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    function_calls: list[dict[str, object]] = field(default_factory=list)
    # Each function_call: {"name": str, "input": dict}


@dataclass
class ProviderInvokeResult:
    """Result of a Provider invocation -- raw, no tool execution.

    The Provider reasons about the prompt and returns:
    - message: Text response from the LLM
    - tool_calls: Raw tool calls the LLM wants to make (not yet executed)
    - usage: Token usage statistics
    - finish_reason: Why the LLM stopped (stop, tool_calls, length, etc.)
    """

    message: str = ""
    tool_calls: list[dict[str, object]] = field(default_factory=list)
    # Each tool_call dict: {"call_id": str, "name": str, "input": dict}
    usage: dict[str, object] = field(default_factory=dict)
    # usage dict: {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}
    finish_reason: str = "stop"
    model: str = ""
    success: bool = True
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False


class AgentProvider(ABC):
    """Abstract base for all Agent Providers (LLM backends).

    Each Provider exposes:
    - A list of functions it can reason about
    - An invoke() method that takes a user prompt and returns
      structured output including any function calls to make
    """

    @abstractmethod
    async def invoke(
        self,
        prompt: str = "",
        *,
        available_functions: list[AgentFunction],
        context: dict[str, object] | None = None,
        messages: list[AgentMessage] | None = None,
    ) -> ProviderInvokeResult:
        """Invoke the agent with a prompt and available functions.

        When messages is provided, it contains the full conversation
        history (system + user + assistant + tool messages). The provider
        should use this as the context. When messages is None, the provider
        should build messages from the prompt and available functions.
        """
        ...

    @abstractmethod
    def list_functions(self) -> list[AgentFunction]:
        """Return the functions this provider can reason about."""
        ...

    @abstractmethod
    def provider_name(self) -> str:
        """Unique provider identifier."""
        ...
