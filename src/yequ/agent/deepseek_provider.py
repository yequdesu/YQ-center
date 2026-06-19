"""DeepSeek LLM Provider -- OpenAI-compatible API.

Uses the openai SDK pointed at DeepSeek's base URL.
Converts AgentFunction[] to OpenAI tool definitions.
"""

import json

from openai import AsyncOpenAI

from yequ.agent.provider import AgentFunction, AgentProvider, AgentResult
from yequ.config import get_settings


class DeepSeekProvider(AgentProvider):
    """LLM Provider backed by DeepSeek API (OpenAI-compatible).

    Converts AgentFunction metadata to OpenAI tool definitions.
    The LLM decides which functions to call based on the prompt.
    Provider never calls functions directly -- it returns function_calls
    for the Center to execute through the standard Invocation path.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
        self._model = settings.deepseek_model
        self._functions: list[AgentFunction] = []

    def provider_name(self) -> str:
        return "deepseek"

    def list_functions(self) -> list[AgentFunction]:
        return list(self._functions)

    def add_function(self, func: AgentFunction) -> None:
        """Register a function the agent can reason about."""
        self._functions.append(func)

    def add_functions(self, funcs: list[AgentFunction]) -> None:
        self._functions.extend(funcs)

    async def invoke(
        self,
        prompt: str,
        *,
        available_functions: list[AgentFunction],
        context: dict[str, object] | None = None,
    ) -> AgentResult:
        """Invoke DeepSeek with a prompt and available functions.

        Converts available_functions to OpenAI tool format,
        sends to DeepSeek, parses the response.
        """
        functions = available_functions if available_functions else self._functions
        tools = self._functions_to_tools(functions)

        messages: list[dict[str, object]] = [
            {
                "role": "system",
                "content": (
                    "You are an infrastructure control agent. "
                    "You have access to tools on a connected node. "
                    "When asked, call the appropriate tools to gather information. "
                    "If no tool is relevant, respond with a brief message. "
                    "Never call destructive tools unless explicitly asked. "
                    "Always read current state before making changes."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        try:
            kwargs: dict = {
                "model": self._model,
                "messages": messages,
                "max_tokens": 1024,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            response = await self._client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            msg = choice.message

            # Build function_calls from tool_calls in the response
            function_calls: list[dict[str, object]] = []
            text_output: list[str] = []

            if msg.tool_calls:
                for tc in msg.tool_calls:
                    try:
                        arguments = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, TypeError):
                        arguments = {}
                    function_calls.append({
                        "name": self._resolve_name(tc.function.name, functions),
                        "input": arguments,
                    })

            if msg.content:
                text_output.append(msg.content)

            # Check finish_reason
            finish = choice.finish_reason
            if finish == "length":
                return AgentResult(
                    success=False,
                    error_code="max_tokens",
                    error_message="Response exceeded max tokens",
                    retryable=False,
                )

            return AgentResult(
                success=True,
                output={
                    "message": "\n".join(text_output) if text_output else "Completed",
                    "model": self._model,
                    "finish_reason": finish or "stop",
                },
                function_calls=function_calls,
            )

        except Exception as e:
            error_msg = str(e)
            retryable = "rate" in error_msg.lower() or "timeout" in error_msg.lower()
            return AgentResult(
                success=False,
                error_code="llm_error",
                error_message=error_msg,
                retryable=retryable,
            )

    def _sanitize_name(self, name: str) -> str:
        """Replace dots with underscores for DeepSeek API compatibility."""
        return name.replace(".", "_")

    def _functions_to_tools(
        self, functions: list[AgentFunction]
    ) -> list[dict[str, object]]:
        """Convert AgentFunction[] to OpenAI tool definitions.

        DeepSeek requires function names matching '^[a-zA-Z0-9_-]+$',
        so dots are replaced with underscores. The mapping is reversed
        when parsing tool_calls back to function_calls.
        """
        tools: list[dict[str, object]] = []
        for func in functions:
            tool = {
                "type": "function",
                "function": {
                    "name": self._sanitize_name(func.name),
                    "description": func.description or func.name,
                    "parameters": func.input_schema or {"type": "object", "properties": {}},
                },
            }
            tools.append(tool)
        return tools

    def _resolve_name(self, sanitized: str, functions: list[AgentFunction]) -> str:
        """Map a sanitized function name back to the original dotted name."""
        for f in functions:
            if self._sanitize_name(f.name) == sanitized:
                return f.name
        return sanitized  # fallback
