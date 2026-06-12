"""LLM provider abstraction — supports multiple backends."""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


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


class AnthropicProvider(LLMProvider):
    """Claude via Anthropic API."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentResponse:
        from anthropic import Anthropic

        if not self.api_key:
            return AgentResponse(
                text="未配置 Anthropic API Key。请在 config/gateway.yaml 中设置 api_key，或设置环境变量 ANTHROPIC_API_KEY。"
            )

        try:
            client = Anthropic(api_key=self.api_key)
        except Exception as e:
            return AgentResponse(
                text=f"无法初始化 Anthropic 客户端: {e}\n\n可能原因：网络代理配置问题。检查 HTTP_PROXY / ALL_PROXY 环境变量。"
            )

        # Convert generic messages to Anthropic format
        system_prompt = ""
        api_messages = []
        for m in messages:
            if m["role"] == "system":
                system_prompt = m["content"]
            else:
                api_messages.append({"role": m["role"], "content": m["content"]})

        kwargs = dict(
            model=self.model,
            max_tokens=1024,
            messages=api_messages,
        )
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = tools

        resp = client.messages.create(**kwargs)

        # Parse response
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


class OpenAIProvider(LLMProvider):
    """OpenAI / compatible API (GPT-4, etc.)."""

    def __init__(self, api_key: str, model: str = "gpt-4o", base_url: str | None = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model
        self.base_url = base_url

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentResponse:
        try:
            from openai import OpenAI
        except ImportError:
            return AgentResponse(
                text="OpenAI 客户端未安装。请执行: pip install openai"
            )

        if not self.api_key:
            return AgentResponse(
                text="未配置 OpenAI API Key。请在 config/gateway.yaml 中设置 api_key，或设置环境变量 OPENAI_API_KEY。"
            )

        kwargs = dict(model=self.model, messages=messages)
        if self.base_url:
            kwargs["base_url"] = self.base_url

        client = OpenAI(api_key=self.api_key, **kwargs)
        resp = client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools if tools else None,
        )

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


_PROVIDER_REGISTRY = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}


def create_provider(provider: str, api_key: str, model: str, **kwargs) -> LLMProvider:
    """Factory: create an LLM provider from config."""
    provider_cls = _PROVIDER_REGISTRY.get(provider)
    if provider_cls is None:
        raise ValueError(
            f"Unknown provider: {provider}. Available: {list(_PROVIDER_REGISTRY.keys())}"
        )
    return provider_cls(api_key=api_key, model=model, **kwargs)
