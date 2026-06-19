"""FakeAgentProvider — returns canned responses for testing.

Does NOT connect to a real LLM. Used for integration testing
the Agent->Center pipeline without external dependencies.
"""

from yequ.agent.provider import (
    AgentFunction,
    AgentProvider,
    AgentResult,
    ProviderInvokeResult,
)


class FakeAgentProvider(AgentProvider):
    """Test provider that returns pre-configured responses.

    Configure with add_response() to set up expected behavior
    for specific prompt patterns. Unmatched prompts return
    a default success with no function calls.
    """

    def __init__(self, provider_name: str = "fake") -> None:
        self._name = provider_name
        self._responses: dict[str, AgentResult] = {}
        self._functions: list[AgentFunction] = []
        self._invoke_count = 0
        self._default_result = AgentResult(
            success=True,
            output={"message": "default fake response"},
            function_calls=[],
        )

    @property
    def invoke_count(self) -> int:
        """Number of times invoke() was called."""
        return self._invoke_count

    def add_response(self, prompt_contains: str, result: AgentResult) -> None:
        """Register a response for prompts containing the given string.

        First match wins. Patterns are checked in insertion order.
        """
        self._responses[prompt_contains] = result

    def set_default_result(self, result: AgentResult) -> None:
        """Set the default result for unmatched prompts."""
        self._default_result = result

    def add_function(self, func: AgentFunction) -> None:
        """Register a function this provider can reason about."""
        self._functions.append(func)

    def add_functions(self, funcs: list[AgentFunction]) -> None:
        """Register multiple functions at once."""
        self._functions.extend(funcs)

    def reset(self) -> None:
        """Reset invoke count and clear responses (for test isolation)."""
        self._invoke_count = 0
        self._responses.clear()
        self._functions.clear()

    # --- AgentProvider interface ---

    async def invoke(
        self,
        prompt: str,
        *,
        available_functions: list[AgentFunction],
        context: dict[str, object] | None = None,
    ) -> ProviderInvokeResult:
        """Return a canned response based on prompt content.

        Matches prompt against registered response patterns
        using substring match. Falls back to default_result.
        """
        self._invoke_count += 1

        for pattern, result in self._responses.items():
            if pattern in prompt:
                return ProviderInvokeResult(
                    message=str(result.output.get("message", "")) if result.output else "",
                    tool_calls=result.function_calls,
                    success=result.success,
                    error_code=result.error_code,
                    error_message=result.error_message,
                    retryable=result.retryable,
                )

        return ProviderInvokeResult(
            message=(
                self._default_result.output.get("message", "")
                if self._default_result.output
                else ""
            ),
            success=self._default_result.success,
            tool_calls=list(self._default_result.function_calls),
        )

    def list_functions(self) -> list[AgentFunction]:
        """Return registered functions."""
        return list(self._functions)

    def provider_name(self) -> str:
        """Return the provider's name."""
        return self._name
