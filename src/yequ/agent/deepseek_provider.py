"""DeepSeek LLM Provider -- OpenAI-compatible API.

Uses the openai SDK pointed at DeepSeek's base URL.
Converts AgentFunction[] to OpenAI tool definitions.
"""

import asyncio
import json
import time as _time
import uuid
from collections.abc import AsyncGenerator
from typing import Any, cast

from openai import AsyncOpenAI

from yequ.agent.context_engine import render_capability_context_prompt
from yequ.agent.provider import (
    AgentFunction,
    AgentMessage,
    AgentProvider,
    ProviderInvokeResult,
    sanitize_tool_payload_for_agent,
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
        read_timeout = float(settings.deepseek_read_timeout)
        self._client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=httpx.Timeout(
                read_timeout,
                connect=10.0,
                read=read_timeout,
                write=30.0,
                pool=5.0,
            ),
            max_retries=0,  # no SDK-level retry --we control retry ourselves
        )
        self._model = settings.deepseek_model
        self._max_retries = int(settings.deepseek_max_retries)
        self._retry_backoff_base = float(settings.deepseek_retry_backoff_base)
        self._read_timeout = read_timeout
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
        _log.info(
            "deepseek provider build tools: elapsed=%.3fs tool_count=%d", _t1 - _t0, len(tools)
        )

        if messages is not None:
            api_messages = self._to_openai_messages(messages, functions, context=context)
        else:
            api_messages = self._build_fresh_messages(prompt, functions, context=context)

        last_error: Exception | None = None
        last_error_msg: str = ""
        for attempt in range(self._max_retries + 1):
            try:
                _t_api0 = _time.monotonic()
                _log.info(
                    "deepseek api call starting: model=%s tool_count=%d attempt=%d/%d",
                    self._model, len(tools), attempt + 1, self._max_retries + 1,
                )
                if tools:
                    response = await asyncio.wait_for(
                        self._client.chat.completions.create(
                            model=self._model,
                            messages=cast(Any, api_messages),
                            max_tokens=2048,
                            tools=cast(Any, tools),
                            tool_choice="auto",
                        ),
                        timeout=self._read_timeout + 5.0,
                    )
                else:
                    response = await asyncio.wait_for(
                        self._client.chat.completions.create(
                            model=self._model,
                            messages=cast(Any, api_messages),
                            max_tokens=2048,
                        ),
                        timeout=self._read_timeout + 5.0,
                    )
                _t_api1 = _time.monotonic()
                _log.info("deepseek api call completed: elapsed=%.1fs", _t_api1 - _t_api0)
                choice = response.choices[0]
                msg = choice.message

                # Build tool_calls from tool_calls in the response
                tool_calls: list[dict[str, object]] = []
                text_output: list[str] = []

                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        function_call = getattr(tc, "function", None)
                        if function_call is None:
                            continue
                        try:
                            arguments = json.loads(function_call.arguments)
                        except (json.JSONDecodeError, TypeError) as exc:
                            raise ValueError(
                                f"Invalid JSON arguments for tool {function_call.name!r}"
                            ) from exc
                        original_name = self._resolve_name(function_call.name, functions)
                        sanitized_name = function_call.name
                        tool_calls.append(
                            {
                                "call_id": tc.id or f"call_{uuid.uuid4().hex}",
                                "name": original_name,
                                "sanitized_name": sanitized_name,
                                "input": arguments,
                            }
                        )

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
                    "prompt_tokens": (usage_raw.prompt_tokens if usage_raw else None),
                    "completion_tokens": (usage_raw.completion_tokens if usage_raw else None),
                    "total_tokens": (usage_raw.total_tokens if usage_raw else None),
                }

                return ProviderInvokeResult(
                    message="\n".join(text_output),
                    tool_calls=tool_calls,
                    usage=usage,
                    finish_reason=finish or "stop",
                    model=self._model,
                    success=True,
                )

            except Exception as e:
                last_error = e
                last_error_msg = str(e)
                retryable = self._is_retryable_error(e)

                if not retryable or attempt >= self._max_retries:
                    break

                backoff = self._retry_backoff_base * (2 ** attempt)
                _log.warning(
                    "deepseek provider retry attempt=%d/%d backoff=%.1fs error=%s",
                    attempt + 1, self._max_retries, backoff, last_error_msg[:200],
                )
                await asyncio.sleep(backoff)

        # All retries exhausted or non-retryable error
        _t_exc = _time.monotonic() - _t0
        _log.error(
            "deepseek provider exception: elapsed=%.1fs retries=%d error=%s",
            _t_exc, self._max_retries, last_error_msg[:500],
        )
        return ProviderInvokeResult(
            success=False,
            error_code="llm_error",
            error_message=last_error_msg,
            retryable=self._is_retryable_error(last_error) if last_error else False,
        )

    async def invoke_stream(
        self,
        prompt: str = "",
        *,
        available_functions: list[AgentFunction] | None = None,
        messages: list[AgentMessage] | None = None,
        context: dict[str, object] | None = None,
    ) -> AsyncGenerator[dict[str, object], None]:
        """Invoke DeepSeek with streaming response.

        When `messages` is provided, it is used as the full conversation
        history (with system prompt prepended if not already present).
        Otherwise, a fresh conversation is built from `prompt` + functions.

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

        if messages:
            oai_messages = self._to_openai_messages(messages, functions, context=context)
            if not oai_messages or oai_messages[0].get("role") != "system":
                oai_messages.insert(
                    0,
                    {"role": "system", "content": self._system_prompt(functions, context=context)},
                )
        else:
            oai_messages = [
                {"role": "system", "content": self._system_prompt(functions, context=context)},
                {"role": "user", "content": prompt},
            ]

        last_error: Exception | None = None
        last_error_msg: str = ""
        for attempt in range(self._max_retries + 1):
            try:
                async for chunk in self._stream_one_attempt(oai_messages, tools, functions):
                    yield chunk
                return  # success --stream completed without error
            except Exception as e:
                last_error = e
                last_error_msg = str(e)
                retryable = self._is_retryable_error(e)

                if not retryable or attempt >= self._max_retries:
                    break

                backoff = self._retry_backoff_base * (2 ** attempt)
                _log.warning(
                    "deepseek stream retry attempt=%d/%d backoff=%.1fs error=%s",
                    attempt + 1, self._max_retries, backoff, last_error_msg[:200],
                )
                await asyncio.sleep(backoff)

        # All retries exhausted or non-retryable error
        _log.error("deepseek stream error: %s", last_error_msg[:500])
        yield {
            "type": "error",
            "error_code": "llm_error",
            "error_message": last_error_msg,
            "retryable": self._is_retryable_error(last_error) if last_error else False,
        }

    async def _stream_one_attempt(
        self,
        oai_messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        functions: list[AgentFunction],
    ) -> AsyncGenerator[dict[str, object], None]:
        """Execute one streaming API call attempt. Yields delta/done chunks or raises."""
        if tools:
            stream = await self._client.chat.completions.create(
                model=self._model,
                messages=cast(Any, oai_messages),
                max_tokens=2048,
                stream=True,
                stream_options={"include_usage": True},
                tools=cast(Any, tools),
                tool_choice="auto",
            )
        else:
            stream = await self._client.chat.completions.create(
                model=self._model,
                messages=cast(Any, oai_messages),
                max_tokens=2048,
                stream=True,
                stream_options={"include_usage": True},
            )

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

        def _call_sort_key(buf: dict[str, Any]) -> int:
            call_id = str(buf.get("call_id", ""))
            return int(call_id[-4:], 16) if call_id else 0

        for buf in sorted(tool_call_buffers.values(), key=_call_sort_key):
            buf_name = str(buf.get("name", ""))
            try:
                arguments = json.loads(buf["arguments"]) if buf["arguments"].strip() else {}
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError(f"Invalid JSON arguments for tool {buf_name!r}") from exc

            original_name = self._resolve_name(buf_name, functions)
            raw_tool_calls.append(
                {
                    "call_id": str(buf.get("call_id", "")),
                    "name": original_name,
                    "sanitized_name": buf_name,
                    "input": arguments,
                }
            )

        yield {
            "type": "done",
            "message": "".join(text_buffer),
            "tool_calls": raw_tool_calls,
            "usage": usage_info,
            "finish_reason": finish_reason,
            "success": finish_reason != "length",
        }

    def _system_prompt(
        self,
        functions: list[AgentFunction],
        *,
        context: dict[str, object] | None = None,
    ) -> str:
        """Build the system prompt for multi-turn agent conversations."""
        capability_context = (
            context.get("capability_context")
            if isinstance(context, dict)
            else None
        )
        grouped_context = render_capability_context_prompt(
            capability_context if isinstance(capability_context, dict) else None,
            functions,
        )
        return _system_prompt_text_enhanced(grouped_context)

    def debug_system_prompt(
        self,
        functions: list[AgentFunction],
        context: dict[str, object] | None = None,
    ) -> str:
        """Return the exact system prompt used for provider calls."""
        return self._system_prompt(functions, context=context)

    def _build_fresh_messages(
        self,
        prompt: str,
        functions: list[AgentFunction],
        *,
        context: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        """Build a fresh system + user message pair."""
        return [
            {"role": "system", "content": self._system_prompt(functions, context=context)},
            {"role": "user", "content": prompt},
        ]

    def _to_openai_messages(
        self,
        messages: list[AgentMessage],
        functions: list[AgentFunction],
        *,
        context: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        """Convert AgentMessage list to OpenAI-compatible message dicts.

        System prompt is always prepended as the first message.
        AgentMessage content/tool_calls are mapped to the OpenAI format.
        """
        result: list[dict[str, object]] = [
            {"role": "system", "content": self._system_prompt(functions, context=context)},
        ]

        for m in messages:
            d: dict[str, object] = {"role": m.role}
            if m.content is not None:
                d["content"] = (
                    _sanitize_tool_observation_content(m.content) if m.role == "tool" else m.content
                )
            if m.tool_call_id is not None:
                d["tool_call_id"] = m.tool_call_id
            if m.tool_calls is not None:
                d["tool_calls"] = [
                    {
                        "id": tc.get("call_id", ""),
                        "type": "function",
                        "function": {
                            "name": self._sanitize_name(str(tc.get("name", ""))),
                            "arguments": json.dumps(
                                sanitize_tool_payload_for_agent(tc.get("input", {}))
                            ),
                        },
                    }
                    for tc in m.tool_calls
                ]
            result.append(d)

        return result

    def _sanitize_name(self, name: str) -> str:
        """Replace dots with underscores for DeepSeek API compatibility."""
        return name.replace(".", "_")

    @staticmethod
    def _is_retryable_error(error: Exception) -> bool:
        """Return True if the error is a transient failure worth retrying."""
        error_msg = str(error).lower()
        return "timeout" in error_msg or "rate" in error_msg

    def _functions_to_tools(self, functions: list[AgentFunction]) -> list[dict[str, object]]:
        """Convert AgentFunction[] to OpenAI tool definitions.

        DeepSeek requires function names matching '^[a-zA-Z0-9_-]+$',
        so dots are replaced with underscores. The mapping is reversed
        when parsing tool_calls back to function_calls.
        """
        tools: list[dict[str, object]] = []
        for func in functions:
            description = func.description or func.name
            if func.source_nodes:
                description = (
                    f"{description}\nAvailable on nodes: {', '.join(func.source_nodes)}."
                )
            tool: dict[str, object] = {
                "type": "function",
                "function": {
                    "name": self._sanitize_name(func.name),
                    "description": description,
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
        raise ValueError(f"Unknown provider tool name: {sanitized!r}")


def _sanitize_tool_observation_content(content: str) -> str:
    """Hide internal execution fields from the LLM while preserving DB history.

    Session history keeps approval_id so the console can reconstruct pending
    approvals after refresh. Provider context must not see that internal field.
    """
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return content
    sanitized = sanitize_tool_payload_for_agent(parsed)
    return json.dumps(sanitized, ensure_ascii=False)


def _system_prompt_text_enhanced(capability_context_text: str) -> str:
    return (
        "You are an infrastructure control agent. Communicate in the "
        "user's language throughout.\n\n"
        "Context:\n"
        "- A Node registers a set of capabilities.\n"
        "- Center routes tool calls to the Node that registered the capability.\n"
        "- Routing truth is node/runtime metadata, not function name prefix.\n"
        "- A tool call fails with \"no online node\" when the target Node is "
        "offline or has not registered that capability.\n\n"
        "Current Center state:\n"
        f"{capability_context_text}\n\n"
        "Rules:\n"
        "1. Analyze the user's request and choose appropriate tools.\n"
        "2. You may call multiple tools in one response.\n"
        "3. After receiving tool results, either call more tools or respond "
        "with the final answer as normal assistant text.\n"
        "4. Never invent tool names.\n"
        "5. If a tool fails or is denied, explain the situation to the user. "
        "Distinguish between: Node offline, capability not registered, "
        "policy denied, or tool execution error.\n"
        "6. Never mention internal implementation fields such as dry_run "
        "or approval_id. If an operation is previewed before execution, "
        "describe it to the user as a preflight check or 预演.\n"
        "7. Do not retry a denied write operation by changing internal "
        "parameters. Ask the user for a new instruction when approval is denied.\n"
        "8. Respect the grouped node capability context. If a user asks for "
        "Linux state, choose an executable Linux node capability. If a user "
        "asks for Windows state, choose an executable Windows node capability. "
        "Do not call a tool that is not listed in the provider tool set.\n"
        "9. When uncertain between a read-only and a write operation, "
        "default to read-only and report what you found.\n"
        "10. Never fabricate tool results. If a tool did not execute, "
        "do not pretend it succeeded.\n"
        "11. Tool results may include an artifacts array. Treat each item as "
        "a Center-managed file/media/report reference. Do not invent file "
        "contents, do not expand binary data into text, and do not claim you "
        "visually inspected an image unless an image-understanding tool was "
        "actually used. Tell the user what artifact was generated, its type, "
        "and that it can be opened or downloaded from the Console.\n\n"
        "Output style:\n"
        "- No emoji or decorative symbols.\n"
        "- Concise and technically precise. Cut filler and hedging.\n"
        "- State facts directly. Do not cheer, congratulate, or over-explain.\n"
        "- Prefer structured output: status first, then details, then recommendations if any.\n"
    )
