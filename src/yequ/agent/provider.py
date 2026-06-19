"""Agent Provider abstract interface.

An Agent Provider wraps an LLM backend and exposes a standard
invocation interface. Providers never call Nodes or Plugins
directly — all execution goes through the Center's standard path.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


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
        prompt: str,
        *,
        available_functions: list[AgentFunction],
        context: dict[str, object] | None = None,
    ) -> AgentResult:
        """Invoke the agent with a prompt and available functions.

        The provider reasons about the prompt, decides which
        functions to call (if any), and returns structured output.
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
