"""LLM provider abstraction — multiple backends with presets and streaming."""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator


# ── Presets ──────────────────────────────────────────────────────────

# Each preset: (sdk_style, env_key, default_model, default_base_url)
# sdk_style: "anthropic" or "openai"
PROVIDER_PRESETS: dict[str, dict[str, str]] = {
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
        "env_key": "",  # no key needed for local
        "default_model": "llama3",
        "default_base_url": "http://localhost:11434/v1",
    },
    "custom": {
        "sdk": "openai",
        "env_key": "CUSTOM_API_KEY",
        "default_model": "gpt-4o",
        "default_base_url": "",
    },
}


# ── Data Types ───────────────────────────────────────────────────────

@dataclass
class ToolCall:
    """A tool call the LLM wants to make."""
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResponse:
    """Unified response from any LLM provider."""
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class StreamEvent:
    """One event in a streaming response."""
    type: str  # "token" | "tool_call" | "done" | "error"
    data: Any = None


class LLMProvider(ABC):
    """Abstract LLM provider interface."""

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentResponse:
        """Send messages to the LLM, return unified response."""
        ...

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[StreamEvent]:
        """Stream the LLM response. Falls back to non-stream chat + yield full text."""
        resp = self.chat(messages, tools)
        if resp.tool_calls:
            for tc in resp.tool_calls:
                yield StreamEvent(type="tool_call", data={
                    "name": tc.name, "arguments": tc.arguments, "id": tc.id,
                })
        if resp.text:
            yield StreamEvent(type="token", data=resp.text)
        yield StreamEvent(type="done")


# ── Anthropic-SDK Providers ──────────────────────────────────────────

class AnthropicProvider(LLMProvider):
    """Claude via Anthropic SDK. Also used for DeepSeek (Anthropic-compatible API)."""

    def __init__(self, api_key: str = "", model: str = "claude-sonnet-4-6",
                 base_url: str | None = None, env_key: str = "ANTHROPIC_API_KEY"):
        self.api_key = api_key or os.environ.get(env_key, "")
        self.model = model
        self.base_url = base_url

    def _build_client(self):
        from anthropic import Anthropic
        kwargs = dict(api_key=self.api_key)
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return Anthropic(**kwargs)

    def _prepare_messages(self, messages: list[dict[str, Any]]):
        system_prompt = ""
        api_messages = []
        for m in messages:
            if m["role"] == "system":
                system_prompt = m["content"]
            else:
                api_messages.append({"role": m["role"], "content": m["content"]})
        return system_prompt, api_messages

    def chat(self, messages, tools=None) -> AgentResponse:
        if not self.api_key:
            return AgentResponse(text="未配置 API Key。请在 gateway.yaml 设置 api_key 或对应的环境变量。")

        try:
            client = self._build_client()
        except Exception as e:
            return AgentResponse(text=f"无法初始化客户端: {e}")

        system_prompt, api_messages = self._prepare_messages(messages)

        kwargs = dict(model=self.model, max_tokens=1024, messages=api_messages)
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = tools

        try:
            resp = client.messages.create(**kwargs)
        except Exception as e:
            return AgentResponse(text=f"API 调用失败: {e}")

        text = ""
        tool_calls = []
        for block in resp.content:
            if block.type == "text":
                text += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input if isinstance(block.input, dict) else json.loads(block.input),
                ))

        return AgentResponse(text=text, tool_calls=tool_calls)

    def stream(self, messages, tools=None) -> Iterator[StreamEvent]:
        if not self.api_key:
            yield StreamEvent(type="error", data="未配置 API Key")
            return

        try:
            client = self._build_client()
        except Exception as e:
            yield StreamEvent(type="error", data=f"无法初始化客户端: {e}")
            return

        system_prompt, api_messages = self._prepare_messages(messages)

        kwargs = dict(model=self.model, max_tokens=1024, messages=api_messages)
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = tools

        had_tokens = False
        try:
            with client.messages.stream(**kwargs) as stream:
                for event in stream:
                    if event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            had_tokens = True
                            yield StreamEvent(type="token", data=event.delta.text)
                    elif event.type == "content_block_start":
                        if event.content_block.type == "tool_use":
                            pass
                    elif event.type == "content_block_stop":
                        pass
        except Exception as e:
            yield StreamEvent(type="error", data=str(e))
            return

        final = stream.get_final_message()
        # Yield text from final message if not already streamed (DeepSeek compatibility)
        if not had_tokens:
            for block in final.content:
                if block.type == "text":
                    yield StreamEvent(type="token", data=block.text)
        # Yield tool calls from final message
        for block in final.content:
            if block.type == "tool_use":
                yield StreamEvent(type="tool_call", data={
                    "name": block.name,
                    "arguments": block.input if isinstance(block.input, dict) else json.loads(block.input),
                    "id": block.id,
                })

        yield StreamEvent(type="done")


# ── OpenAI-SDK Providers ─────────────────────────────────────────────

class OpenAIProvider(LLMProvider):
    """OpenAI / compatible API. Used for OpenAI, GLM, Ollama, custom endpoints."""

    def __init__(self, api_key: str = "", model: str = "gpt-4o",
                 base_url: str | None = None, env_key: str = "OPENAI_API_KEY"):
        self.api_key = api_key or os.environ.get(env_key, "")
        self.model = model
        self.base_url = base_url

    def _build_client(self):
        from openai import OpenAI
        kwargs = dict(api_key=self.api_key or "sk-placeholder")
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return OpenAI(**kwargs)

    def chat(self, messages, tools=None) -> AgentResponse:
        if not self.api_key and self.base_url not in (None, "", "http://localhost:11434/v1"):
            return AgentResponse(text="未配置 API Key。请在 gateway.yaml 设置 api_key 或对应的环境变量。")

        try:
            client = self._build_client()
        except Exception as e:
            return AgentResponse(text=f"无法初始化客户端: {e}")

        openai_tools = None
        if tools:
            openai_tools = [{"type": "function", "function": t} for t in tools]

        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=openai_tools,
            )
        except Exception as e:
            return AgentResponse(text=f"API 调用失败: {e}")

        choice = resp.choices[0]
        text = ""
        tool_calls = []
        if choice.message.content:
            text = choice.message.content
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                args = json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        return AgentResponse(text=text, tool_calls=tool_calls)

    def stream(self, messages, tools=None) -> Iterator[StreamEvent]:
        """OpenAI streaming — falls back to non-streaming chat + yield tokens."""
        resp = self.chat(messages, tools)
        if resp.tool_calls:
            for tc in resp.tool_calls:
                yield StreamEvent(type="tool_call", data={
                    "name": tc.name, "arguments": tc.arguments, "id": tc.id,
                })
        if resp.text:
            yield StreamEvent(type="token", data=resp.text)
        yield StreamEvent(type="done")


# ── Factory ──────────────────────────────────────────────────────────

# Map sdk_style → provider class
_SDK_CLASSES = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}


def get_preset(provider_name: str) -> dict[str, str] | None:
    """Get preset config for a provider, or None if unknown."""
    return PROVIDER_PRESETS.get(provider_name)


def list_presets() -> list[str]:
    """List all available provider presets."""
    return list(PROVIDER_PRESETS.keys())


def create_provider(provider: str, api_key: str = "", model: str = "",
                    base_url: str = "") -> LLMProvider:
    """Factory: create an LLM provider from config.

    For preset providers (anthropic, deepseek, openai, glm, ollama),
    the api_key/model/base_url are optional and fall back to preset defaults.
    For "custom", base_url is required.
    """
    preset = get_preset(provider)
    if preset is None:
        # Treat unknown providers as openai-compatible custom
        sdk_class = OpenAIProvider
        env_key = "CUSTOM_API_KEY"
        default_model = model or "gpt-4o"
        default_base = base_url
    else:
        sdk_class = _SDK_CLASSES[preset["sdk"]]
        env_key = preset["env_key"]
        default_model = model or preset["default_model"]
        default_base = base_url or preset["default_base_url"]

    return sdk_class(
        api_key=api_key,
        model=default_model,
        base_url=default_base or None,
        env_key=env_key,
    )
