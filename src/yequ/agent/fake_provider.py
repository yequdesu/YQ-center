"""FakeAgentProvider --returns canned responses for testing.

Does NOT connect to a real LLM. Used for integration testing
the Agent->Center pipeline without external dependencies.
"""

from collections.abc import AsyncGenerator

from yequ.agent.provider import (
    AgentFunction,
    AgentMessage,
    AgentProvider,
    AgentResult,
    ProviderInvokeResult,
)


def _message_from_output(output: dict[str, object] | None) -> str:
    if not output:
        return ""
    value = output.get("message", "")
    return value if isinstance(value, str) else str(value)


class FakeAgentProvider(AgentProvider):
    """Test provider that returns pre-configured responses.

    Supports:
    - add_response(pattern, result): match on user message content
    - set_sequence(results): return results in order for multi-turn testing
    - set_default_result(result): default result when no pattern matches
    """

    def __init__(self, provider_name: str = "fake") -> None:
        self._name = provider_name
        self._responses: dict[str, AgentResult] = {}
        self._sequence: list[ProviderInvokeResult] = []
        self._seq_index = 0
        self._functions: list[AgentFunction] = []
        self._invoke_count = 0
        self._default_result = AgentResult(
            success=True,
            output={"message": "default fake response"},
            function_calls=[],
        )
        # Captures the messages list from the last invoke call (for test assertions)
        self.last_messages: list[AgentMessage] | None = None

    @property
    def invoke_count(self) -> int:
        """Number of times invoke() was called."""
        return self._invoke_count

    def add_response(self, prompt_contains: str, result: AgentResult) -> None:
        """Register a response for prompts containing the given string."""
        self._responses[prompt_contains] = result

    def set_default_result(self, result: AgentResult) -> None:
        """Set the default result for unmatched prompts."""
        self._default_result = result

    def set_sequence(self, results: list[ProviderInvokeResult]) -> None:
        """Set a sequence of responses for multi-turn loop testing.

        Each call to invoke() returns the next result in sequence.
        When the sequence is exhausted, returns default_result.
        """
        self._sequence = list(results)
        self._seq_index = 0

    def add_function(self, func: AgentFunction) -> None:
        self._functions.append(func)

    def add_functions(self, funcs: list[AgentFunction]) -> None:
        self._functions.extend(funcs)

    def reset(self) -> None:
        self._invoke_count = 0
        self._seq_index = 0
        self._sequence.clear()
        self._responses.clear()
        self._functions.clear()
        self.last_messages = None

    # --- AgentProvider interface ---

    async def invoke(
        self,
        prompt: str = "",
        *,
        available_functions: list[AgentFunction],
        context: dict[str, object] | None = None,
        messages: list[AgentMessage] | None = None,
    ) -> ProviderInvokeResult:
        """Return a canned response.

        Priority:
        1. Sequence (multi-turn) --next in sequence
        2. Pattern match on last user message
        3. Default result
        """
        self._invoke_count += 1
        self.last_messages = list(messages) if messages else None

        # 1. Sequence mode
        if self._seq_index < len(self._sequence):
            sequence_result = self._sequence[self._seq_index]
            self._seq_index += 1
            return sequence_result

        # 2. Pattern match --look in messages or prompt
        if messages:
            last_non_system = next((m for m in reversed(messages) if m.role != "system"), None)
            if last_non_system and last_non_system.role == "tool":
                return ProviderInvokeResult(
                    message=_message_from_output(self._default_result.output),
                    success=self._default_result.success,
                    tool_calls=list(self._default_result.function_calls),
                )

        search_text = prompt
        if messages:
            for m in reversed(messages):
                if m.role == "user" and m.content:
                    search_text = m.content
                    break

        for pattern, result in self._responses.items():
            if pattern in search_text:
                return ProviderInvokeResult(
                    message=_message_from_output(result.output),
                    tool_calls=result.function_calls,
                    success=result.success,
                    error_code=result.error_code,
                    error_message=result.error_message,
                    retryable=result.retryable,
                )

        # 3. Default
        return ProviderInvokeResult(
            message=_message_from_output(self._default_result.output),
            success=self._default_result.success,
            tool_calls=list(self._default_result.function_calls),
        )

    async def invoke_stream(
        self,
        prompt: str = "",
        *,
        available_functions: list[AgentFunction] | None = None,
        messages: list[AgentMessage] | None = None,
        context: dict[str, object] | None = None,
    ) -> AsyncGenerator[dict[str, object], None]:
        """Simulate streaming --calls invoke() and yields delta + done events."""
        result = await self.invoke(
            prompt,
            available_functions=available_functions or [],
            messages=messages,
            context=context,
        )
        if result.message:
            yield {"type": "delta", "content": result.message}
        yield {
            "type": "done",
            "tool_calls": result.tool_calls,
            "usage": result.usage,
            "finish_reason": result.finish_reason,
        }

    def list_functions(self) -> list[AgentFunction]:
        return list(self._functions)

    def provider_name(self) -> str:
        return self._name
