"""Agent Provider abstract interface.

An Agent Provider wraps an LLM backend and exposes a standard
invocation interface. Providers never call Nodes or Plugins
directly — all execution goes through the Center's standard path.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from yequ.types import JsonObject

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


def is_task_completed(tool_call: dict[str, object]) -> bool:
    """Return True if this tool call is the task_completed signal."""
    return tool_call.get("name") == "task_completed"


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
    input_schema: JsonObject | None = None
    output_schema: JsonObject | None = None
    risk: str = "safe"
    effect: str = "read"
    timeout_sec: int = 30


# The explicit termination signal for the ReAct loop.
# When the LLM decides the task is complete, it MUST call this tool instead
# of returning an empty tool_calls list. The orchestrator recognizes this
# function name and treats it as the loop exit condition.
TASK_COMPLETED_FUNCTION = AgentFunction(
    name="task_completed",
    description=(
        "Call this tool when the task is complete. Include a final summary "
        "in the user's language explaining what was done and what was found. "
        "You MUST call this tool as the last action — do not just stop "
        "without calling it."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Final summary in the user's language",
            },
        },
        "required": ["message"],
    },
    risk="safe",
    effect="read",
)


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

    def invoke_stream(
        self,
        prompt: str = "",
        *,
        available_functions: list[AgentFunction] | None = None,
        messages: list[AgentMessage] | None = None,
        context: dict[str, object] | None = None,
    ) -> AsyncGenerator[dict[str, object], None]:
        """Optionally stream provider output as delta/done/error events."""

        async def _raise() -> AsyncGenerator[dict[str, object], None]:
            raise NotImplementedError(f"{self.provider_name()} does not support streaming")
            yield {}

        return _raise()

    @abstractmethod
    def list_functions(self) -> list[AgentFunction]:
        """Return the functions this provider can reason about."""
        ...

    @abstractmethod
    def provider_name(self) -> str:
        """Unique provider identifier."""
        ...
