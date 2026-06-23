"""DeepSeek LLM Provider -- OpenAI-compatible API.

Uses the openai SDK pointed at DeepSeek's base URL.
Converts AgentFunction[] to OpenAI tool definitions.
"""

import asyncio
import json
import time as _time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from openai import AsyncOpenAI

from yequ.agent.provider import (
    AgentFunction,
    AgentMessage,
    AgentProvider,
    ProviderInvokeResult,
)
from yequ.config import get_settings
from yequ.logconfig import get_logger

_log = get_logger("deepseek")


class DeepSeekProvider(AgentProvider):
    """LLM Provider backed by DeepSeek API (OpenAI-compatible).

    Converts AgentFunction metadata to OpenAI tool definitions.
    The LLM decides which functions to call based on the prompt.
    Provider never calls functions directly -- it returns function_calls
    for the Center to execute through the standard Invocation path.
    """

    def __init__(self) -> None:
        import httpx

        settings = get_settings()
        self._client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=httpx.Timeout(30.0, connect=10.0, read=30.0, write=30.0, pool=5.0),
            max_retries=0,  # no retry — fail fast, let caller decide
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
        prompt: str = "",
        *,
        available_functions: list[AgentFunction],
        context: dict[str, object] | None = None,
        messages: list[AgentMessage] | None = None,
    ) -> ProviderInvokeResult:
        """Invoke DeepSeek with a prompt and available functions.

        When messages is provided, uses it as the full conversation history.
        Otherwise builds a fresh system + user message pair.
        Returns ProviderInvokeResult with raw tool_calls (no execution).
        """
        functions = available_functions if available_functions else self._functions
        _t0 = _time.monotonic()
        tools = self._functions_to_tools(functions)
        _t1 = _time.monotonic()
        _log.info("deepseek provider build tools: elapsed=%.3fs tool_count=%d",
                  _t1 - _t0, len(tools))

        if messages is not None:
            # Use provided conversation history — inject system prompt at front
            api_messages = self._to_openai_messages(messages, functions)
        else:
            # Build fresh system + user message pair
            api_messages = self._build_fresh_messages(prompt, functions)

        try:
            kwargs: dict = {
                "model": self._model,
                "messages": api_messages,
                "max_tokens": 2048,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            _t_api0 = _time.monotonic()
            _log.info("deepseek api call starting: model=%s tool_count=%d",
                     self._model, len(tools))
            response = await asyncio.wait_for(
                self._client.chat.completions.create(**kwargs),
                timeout=35.0,
            )
            _t_api1 = _time.monotonic()
            _log.info("deepseek api call completed: elapsed=%.1fs",
                     _t_api1 - _t_api0)
            choice = response.choices[0]
            msg = choice.message

            # Build tool_calls from tool_calls in the response
            tool_calls: list[dict[str, object]] = []
            text_output: list[str] = []

            if msg.tool_calls:
                for tc in msg.tool_calls:
                    try:
                        arguments = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, TypeError):
                        arguments = {}
                    original_name = self._resolve_name(
                        tc.function.name, functions
                    )
                    sanitized_name = tc.function.name
                    tool_calls.append({
                        "call_id": tc.id or f"call_{uuid.uuid4().hex}",
                        "name": original_name,
                        "sanitized_name": sanitized_name,
                        "input": arguments,
                    })

            if msg.content:
                text_output.append(msg.content)

            # Check finish_reason
            finish = choice.finish_reason
            if finish == "length":
                return ProviderInvokeResult(
                    success=False,
                    error_code="max_tokens",
                    error_message="Response exceeded max tokens",
                    retryable=False,
                )

            # Extract token usage
            usage_raw = response.usage
            usage: dict[str, object] = {
                "prompt_tokens": (
                    usage_raw.prompt_tokens if usage_raw else None
                ),
                "completion_tokens": (
                    usage_raw.completion_tokens if usage_raw else None
                ),
                "total_tokens": (
                    usage_raw.total_tokens if usage_raw else None
                ),
            }

            return ProviderInvokeResult(
                message="\n".join(text_output) if text_output else "Completed",
                tool_calls=tool_calls,
                usage=usage,
                finish_reason=finish or "stop",
                model=self._model,
                success=True,
            )

        except Exception as e:
            _t_exc = _time.monotonic() - _t0
            error_msg = str(e)
            _log.error("deepseek provider exception: elapsed=%.1fs error=%s",
                      _t_exc, error_msg[:500])
            retryable = "rate" in error_msg.lower() or "timeout" in error_msg.lower()
            return ProviderInvokeResult(
                success=False,
                error_code="llm_error",
                error_message=error_msg,
                retryable=retryable,
            )

    async def invoke_stream(
        self,
        prompt: str,
        *,
        available_functions: list[AgentFunction],
        context: dict[str, object] | None = None,
    ) -> AsyncGenerator[dict[str, object], None]:
        """Invoke DeepSeek with streaming response.

        Yields dicts with keys:
          - type: "delta" (text chunk), "tool_call" (accumulated tool call),
            "done" (streaming complete), "error" (streaming error)
          - content: str (for delta type)
          - tool_calls: list[dict] (for done type)
          - usage: dict (for done type)
          - finish_reason: str (for done type)
        """
        functions = available_functions if available_functions else self._functions
        tools = self._functions_to_tools(functions)

        func_descriptions = "\n".join(
            f"- {f.name}: {f.description}" for f in functions
        )
        messages: list[dict[str, object]] = [
            {
                "role": "system",
                "content": (
                    "You are an infrastructure control agent for a Windows machine. "
                    "You have these tools:\n"
                    f"{func_descriptions}\n\n"
                    "Rules:\n"
                    "1. Choose the right tool(s) for the user's request.\n"
                    "2. For multi-step checks, call multiple tools in one response.\n"
                    "3. Never invent tool names -- only use listed tools.\n"
                    "4. Respond in the user's language.\n"
                    "5. Never mention internal implementation fields such as dry_run or approval_id; call preview-only checks preflight or 预演."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        try:
            kwargs: dict = {
                "model": self._model,
                "messages": messages,
                "max_tokens": 2048,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            stream = await self._client.chat.completions.create(**kwargs)

            # Accumulate streaming content and tool calls
            text_buffer: list[str] = []
            tool_call_buffers: dict[int, dict[str, Any]] = {}
            usage_info: dict[str, object] = {}
            finish_reason = "stop"

            async for chunk in stream:
                if chunk.usage:
                    usage_info = {
                        "prompt_tokens": chunk.usage.prompt_tokens,
                        "completion_tokens": chunk.usage.completion_tokens,
                        "total_tokens": chunk.usage.total_tokens,
                    }

                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue

                # Text content delta
                if delta.content:
                    text_buffer.append(delta.content)
                    yield {"type": "delta", "content": delta.content}

                # Tool call deltas
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_call_buffers:
                            tool_call_buffers[idx] = {
                                "call_id": tc_delta.id or f"call_{uuid.uuid4().hex}",
                                "name": "",
                                "arguments": "",
                            }
                        buf = tool_call_buffers[idx]
                        if tc_delta.id:
                            buf["call_id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                buf["name"] += tc_delta.function.name
                            if tc_delta.function.arguments:
                                buf["arguments"] += tc_delta.function.arguments

                # Finish reason
                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason

            # Parse accumulated tool calls
            raw_tool_calls: list[dict[str, object]] = []
            for buf in sorted(tool_call_buffers.values(), key=lambda b: int(b.get("call_id", "0")[-4:], 16) if b.get("call_id", "") else 0):  # type: ignore[arg-type]
                buf_name = str(buf.get("name", ""))
                try:
                    arguments = json.loads(buf["arguments"]) if buf["arguments"].strip() else {}
                except (json.JSONDecodeError, TypeError):
                    arguments = {}

                original_name = self._resolve_name(buf_name, functions)
                raw_tool_calls.append({
                    "call_id": str(buf.get("call_id", "")),
                    "name": original_name,
                    "sanitized_name": buf_name,
                    "input": arguments,
                })

            yield {
                "type": "done",
                "message": "".join(text_buffer) if text_buffer else "Completed",
                "tool_calls": raw_tool_calls,
                "usage": usage_info,
                "finish_reason": finish_reason,
                "success": finish_reason != "length",
            }

        except Exception as e:
            _log.error("deepseek stream error: %s", str(e)[:500])
            yield {
                "type": "error",
                "error_code": "llm_error",
                "error_message": str(e),
                "retryable": "rate" in str(e).lower() or "timeout" in str(e).lower(),
            }

    def _system_prompt(self, functions: list[AgentFunction]) -> str:
        """Build the system prompt for multi-turn agent conversations."""
        func_descriptions = "\n".join(
            f"- {f.name}: {f.description}" for f in functions
        )
        return (
            "You are an infrastructure control agent. You have these tools:\n"
            f"{func_descriptions}\n\n"
            "Rules:\n"
            "1. Analyze the user's request and choose appropriate tools.\n"
            "2. You may call multiple tools in one response.\n"
            "3. After receiving tool results, assess whether you need more "
            "information or can give the final answer.\n"
            "4. When all needed information is collected, respond with a "
            "clear natural-language summary in the user's language.\n"
            "5. Never invent tool names.\n"
            "6. If a tool fails or is denied, explain the situation to the user.\n"
            "7. Never mention internal implementation fields such as dry_run "
            "or approval_id. If an operation is previewed before execution, "
            "describe it to the user as a preflight check or 预演.\n"
            "8. Do not retry a denied write operation by changing internal "
            "parameters. Ask the user for a new instruction when approval is denied.\n"
        )

    def _build_fresh_messages(
        self, prompt: str, functions: list[AgentFunction]
    ) -> list[dict[str, object]]:
        """Build a fresh system + user message pair."""
        return [
            {"role": "system", "content": self._system_prompt(functions)},
            {"role": "user", "content": prompt},
        ]

    def _to_openai_messages(
        self, messages: list[AgentMessage], functions: list[AgentFunction]
    ) -> list[dict[str, object]]:
        """Convert AgentMessage list to OpenAI-compatible message dicts.

        System prompt is always prepended as the first message.
        AgentMessage content/tool_calls are mapped to the OpenAI format.
        """
        result: list[dict[str, object]] = [
            {"role": "system", "content": self._system_prompt(functions)},
        ]

        for m in messages:
            d: dict[str, object] = {"role": m.role}
            if m.content is not None:
                d["content"] = _sanitize_tool_observation_content(m.content) if m.role == "tool" else m.content
            if m.tool_call_id is not None:
                d["tool_call_id"] = m.tool_call_id
            if m.tool_calls is not None:
                d["tool_calls"] = [
                    {
                        "id": tc.get("call_id", ""),
                        "type": "function",
                        "function": {
                            "name": self._sanitize_name(str(tc.get("name", ""))),
                            "arguments": json.dumps(_sanitize_tool_input_for_provider(tc.get("input", {}))),
                        },
                    }
                    for tc in m.tool_calls
                ]
            result.append(d)

        return result

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


def _sanitize_tool_input_for_provider(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _sanitize_tool_input_for_provider(item)
            for key, item in value.items()
            if key not in {"approval_id", "dry_run"}
        }
    if isinstance(value, list):
        return [_sanitize_tool_input_for_provider(item) for item in value]
    return value


def _sanitize_tool_observation_content(content: str) -> str:
    """Hide internal execution fields from the LLM while preserving DB history.

    Session history keeps approval_id so the console can reconstruct pending
    approvals after refresh. Provider context must not see that internal field.
    """
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return content
    sanitized = _sanitize_tool_input_for_provider(parsed)
    return json.dumps(sanitized, ensure_ascii=False)
