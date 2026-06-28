"""Tests for DeepSeek LLM Provider."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from yequ.agent.provider import AgentFunction, AgentMessage


@pytest.fixture
def deepseek_provider():
    """Create a DeepSeekProvider with mocked AsyncOpenAI client."""
    with patch("yequ.agent.deepseek_provider.AsyncOpenAI") as mock_client:
        mock_client.return_value = MagicMock()
        from yequ.agent.deepseek_provider import DeepSeekProvider

        p = DeepSeekProvider()
        yield p


class TestDeepSeekProvider:
    """Unit tests for DeepSeekProvider (no API calls)."""

    def test_provider_name(self, deepseek_provider):
        p = deepseek_provider
        assert p.provider_name() == "deepseek"

    def test_sanitize_name(self, deepseek_provider):
        p = deepseek_provider
        assert p._sanitize_name("system.metrics.snapshot") == "system_metrics_snapshot"
        assert p._sanitize_name("simple_func") == "simple_func"
        assert p._sanitize_name("a.b.c.d") == "a_b_c_d"

    def test_resolve_name(self, deepseek_provider):
        p = deepseek_provider
        funcs = [
            AgentFunction(name="system.metrics.snapshot"),
            AgentFunction(name="system.info"),
        ]
        assert p._resolve_name("system_metrics_snapshot", funcs) == "system.metrics.snapshot"
        assert p._resolve_name("system_info", funcs) == "system.info"
        with pytest.raises(ValueError, match="Unknown provider tool name"):
            p._resolve_name("unknown_func", funcs)

    def test_functions_to_tools(self, deepseek_provider):
        p = deepseek_provider
        funcs = [
            AgentFunction(
                name="system.metrics.snapshot",
                description="Get metrics",
                input_schema={"type": "object", "properties": {}},
            ),
        ]
        tools = p._functions_to_tools(funcs)
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "system_metrics_snapshot"
        assert tools[0]["function"]["description"] == "Get metrics"
        assert tools[0]["function"]["parameters"] == {
            "type": "object",
            "properties": {},
        }

    def test_add_function(self, deepseek_provider):
        p = deepseek_provider
        p.add_function(AgentFunction(name="test.func"))
        assert len(p.list_functions()) == 1
        assert p.list_functions()[0].name == "test.func"

    def test_add_functions(self, deepseek_provider):
        p = deepseek_provider
        p.add_functions(
            [
                AgentFunction(name="func.a"),
                AgentFunction(name="func.b"),
            ]
        )
        assert len(p.list_functions()) == 2

    def test_default_functions_empty(self, deepseek_provider):
        p = deepseek_provider
        assert p.list_functions() == []

    def test_functions_to_tools_empty(self, deepseek_provider):
        p = deepseek_provider
        tools = p._functions_to_tools([])
        assert tools == []

    def test_resolve_name_no_match(self, deepseek_provider):
        p = deepseek_provider
        funcs = [AgentFunction(name="existing.func")]
        with pytest.raises(ValueError, match="Unknown provider tool name"):
            p._resolve_name("nonexistent_func", funcs)

    @pytest.mark.asyncio
    async def test_invoke_stream_builds_system_prompt_with_functions(self, deepseek_provider):
        p = deepseek_provider

        async def fake_stream():
            yield SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="ok", tool_calls=None),
                        finish_reason="stop",
                    )
                ],
            )

        p._client.chat.completions.create = AsyncMock(return_value=fake_stream())

        chunks = [
            chunk
            async for chunk in p.invoke_stream(
                "inspect files",
                available_functions=[
                    AgentFunction(
                        name="system.file.list",
                        description="List files in a directory",
                        input_schema={
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    )
                ],
            )
        ]

        assert chunks[0] == {"type": "delta", "content": "ok"}
        assert chunks[-1]["type"] == "done"
        assert chunks[-1]["tool_calls"] == []

    @pytest.mark.asyncio
    async def test_invoke_stream_messages_path_builds_system_prompt(self, deepseek_provider):
        p = deepseek_provider

        async def fake_stream():
            yield SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="ok", tool_calls=None),
                        finish_reason="stop",
                    )
                ],
            )

        p._client.chat.completions.create = AsyncMock(return_value=fake_stream())
        functions = [
            AgentFunction(
                name="system.file.list",
                description="List files in a directory",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            )
        ]

        chunks = [
            chunk
            async for chunk in p.invoke_stream(
                available_functions=functions,
                messages=[AgentMessage(role="user", content="list USB files")],
            )
        ]

        assert chunks[0] == {"type": "delta", "content": "ok"}
        call_kwargs = p._client.chat.completions.create.call_args.kwargs
        assert "system.file.list" in call_kwargs["messages"][0]["content"]
