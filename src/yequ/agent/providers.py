"""LLM provider abstraction — pi-agent-core inspired StreamFn pattern.

A provider is a simple callable:
    (messages, tools) -> (text, tool_calls)

No class hierarchy needed. Just functions with presets.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── Types ──────────────────────────────────────────────────────────

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_blocks: list[dict] = field(default_factory=list)  # for preserving thinking blocks


# StreamFn: (messages, tools) -> LLMResponse
StreamFn = Callable[[list[dict], list[dict] | None], LLMResponse]

# ── Provider Presets ───────────────────────────────────────────────

PRESETS: dict[str, dict] = {
    "anthropic": {
        "sdk": "anthropic",
        "env_key": "ANTHROPIC_API_KEY",
        "default_model": "claude-sonnet-4-6",
        "default_base_url": "https://api.anthropic.com",
    },
    "deepseek": {
        "sdk": "anthropic",
        "env_key": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
        "default_base_url": "https://api.deepseek.com/anthropic",
    },
    "openai": {
        "sdk": "openai",
        "env_key": "OPENAI_API_KEY",
        "default_model": "gpt-4o",
        "default_base_url": "https://api.openai.com/v1",
    },
    "glm": {
        "sdk": "openai",
        "env_key": "GLM_API_KEY",
        "default_model": "glm-4-plus",
        "default_base_url": "https://open.bigmodel.cn/api/paas/v4",
    },
    "ollama": {
        "sdk": "openai",
        "env_key": "",
        "default_model": "llama3",
        "default_base_url": "http://localhost:11434/v1",
    },
    "custom": {
        "sdk": "openai",
        "env_key": "CUSTOM_API_KEY",
        "default_model": "",
        "default_base_url": "",
    },
}

# ── Anthropic-SDK StreamFn ─────────────────────────────────────────

def _make_anthropic_fn(api_key: str, model: str, base_url: str | None = None,
                       env_key: str = "ANTHROPIC_API_KEY") -> StreamFn:
    """Create a StreamFn using the Anthropic Python SDK."""
    key = api_key or os.environ.get(env_key, "")

    def stream_fn(messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        if not key:
            return LLMResponse(text="未配置 API Key")

        from anthropic import Anthropic

        client_kwargs = dict(api_key=key)
        if base_url:
            client_kwargs["base_url"] = base_url
        client = Anthropic(**client_kwargs)

        # Build Anthropic-format messages
        system_prompt = ""
        api_messages = []
        for m in messages:
            if m["role"] == "system":
                system_prompt = m["content"]
            else:
                api_messages.append({"role": m["role"], "content": m["content"]})

        kwargs = dict(model=model, max_tokens=1024, messages=api_messages)
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = tools

        try:
            resp = client.messages.create(**kwargs)
        except Exception as e:
            logger.error("LLM API error: %s", e)
            return LLMResponse(text=f"API error: {e}")

        text = ""
        tool_calls = []
        raw_blocks = []
        for block in resp.content:
            if block.type == "text":
                text += block.text
                raw_blocks.append({"type": "text", "text": block.text})
            elif block.type == "thinking":
                raw_blocks.append({"type": "thinking", "thinking": getattr(block, 'thinking', '')})
            elif block.type == "tool_use":
                args = block.input if isinstance(block.input, dict) else json.loads(str(block.input))
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=args))
                raw_blocks.append({"type": "tool_use", "id": block.id, "name": block.name, "input": args})

        if not text and not tool_calls:
            # If only thinking blocks, use the last one as fallback text
            thinking_texts = [getattr(b, 'thinking', '') or getattr(b, 'text', '') for b in resp.content if b.type == 'thinking']
            if thinking_texts:
                text = '\n'.join(thinking_texts)
            else:
                block_info = [(b.type, str(getattr(b, 'text', ''))[:80]) for b in resp.content]
                logger.warning("LLM returned no text/tool_use. Blocks: %s", block_info)

        return LLMResponse(text=text, tool_calls=tool_calls, raw_blocks=raw_blocks)

    return stream_fn

# ── OpenAI-SDK StreamFn ────────────────────────────────────────────

def _make_openai_fn(api_key: str, model: str, base_url: str | None = None,
                    env_key: str = "OPENAI_API_KEY") -> StreamFn:
    """Create a StreamFn using the OpenAI Python SDK."""
    key = api_key or os.environ.get(env_key, "")
    # ollama doesn't need a key
    if not key and base_url != "http://localhost:11434/v1":
        pass  # will error on first call

    def stream_fn(messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        try:
            from openai import OpenAI
        except ImportError:
            return LLMResponse(text="openai package not installed")

        client_kwargs = dict(api_key=key or "sk-placeholder")
        if base_url:
            client_kwargs["base_url"] = base_url
        client = OpenAI(**client_kwargs)

        openai_tools = None
        if tools:
            openai_tools = [{"type": "function", "function": t} for t in tools]

        try:
            resp = client.chat.completions.create(
                model=model, messages=messages, tools=openai_tools)
        except Exception as e:
            logger.error("LLM API error: %s", e)
            return LLMResponse(text=f"API error: {e}")

        choice = resp.choices[0]
        text = choice.message.content or ""
        tool_calls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                args = json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        return LLMResponse(text=text, tool_calls=tool_calls)

    return stream_fn

# ── Factory ────────────────────────────────────────────────────────

_SDK_BUILDERS = {
    "anthropic": _make_anthropic_fn,
    "openai": _make_openai_fn,
}


def create_stream_fn(provider: str, api_key: str = "", model: str = "",
                     base_url: str = "") -> StreamFn:
    """Create a StreamFn callable from provider config."""
    preset = PRESETS.get(provider)
    if preset is None:
        # Treat unknown providers as OpenAI-compatible custom
        return _make_openai_fn(api_key=api_key, model=model or "gpt-4o",
                               base_url=base_url or None,
                               env_key="CUSTOM_API_KEY")

    builder = _SDK_BUILDERS[preset["sdk"]]
    return builder(
        api_key=api_key,
        model=model or preset["default_model"],
        base_url=base_url or preset["default_base_url"] or None,
        env_key=preset["env_key"],
    )
