"""Tests for Agent policy, provider fakes, and session creation."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.agent.agent_service import create_agent_session
from yequ.agent.fake_provider import FakeAgentProvider
from yequ.agent.provider import AgentFunction, AgentResult

# ── Policy Engine Tests ────────────────────────────────────────────


class TestPolicyEngine:
    """Policy engine risk/mode matrix tests."""

    def test_auto_safe_allowed(self):
        from yequ.services.policy import check_policy

        r = check_policy("auto", "safe")
        assert r.allowed is True

    def test_auto_destructive_ask(self):
        from yequ.services.policy import check_policy

        r = check_policy("auto", "destructive")
        assert r.allowed is False
        assert r.decision == "ask"

    def test_readonly_maint_denied(self):
        from yequ.services.policy import check_policy

        r = check_policy("readonly", "maintenance")
        assert r.allowed is False
        assert r.decision == "deny"

    def test_manual_catastrophic_denied(self):
        from yequ.services.policy import check_policy

        r = check_policy("manual", "catastrophic")
        assert r.allowed is False

    def test_all_modes_allow_safe(self):
        from yequ.services.policy import check_policy

        for mode in ("auto", "assist", "readonly", "manual"):
            assert check_policy(mode, "safe").allowed is True

    def test_all_modes_deny_catastrophic(self):
        from yequ.services.policy import check_policy

        for mode in ("auto", "assist", "readonly", "manual"):
            assert check_policy(mode, "catastrophic").allowed is False

    def test_conditional_without_whitelist_denied(self):
        from yequ.services.policy import check_policy

        r = check_policy("assist", "maintenance", function_name="system.diag")
        assert r.allowed is False

    def test_conditional_with_whitelist_allowed(self):
        from yequ.services.policy import check_policy

        r = check_policy(
            "assist",
            "maintenance",
            function_name="system.diag",
            whitelist={"system.diag"},
        )
        assert r.allowed is True


# ── FakeAgentProvider Tests ────────────────────────────────────────


class TestFakeAgentProvider:
    """Tests for FakeAgentProvider — canned responses."""

    @pytest.mark.asyncio
    async def test_default_response(self):
        p = FakeAgentProvider()
        r = await p.invoke("hello", available_functions=[])
        assert r.success is True
        assert r.message == "default fake response"
        assert r.tool_calls == []

    @pytest.mark.asyncio
    async def test_canned_response(self):
        p = FakeAgentProvider()
        p.add_response(
            "metrics",
            AgentResult(
                success=True,
                output={"action": "get_metrics"},
                function_calls=[{"name": "system.metrics.snapshot", "input": {}}],
            ),
        )
        r = await p.invoke("get me metrics", available_functions=[])
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0]["name"] == "system.metrics.snapshot"

    @pytest.mark.asyncio
    async def test_invoke_count(self):
        p = FakeAgentProvider()
        await p.invoke("a", available_functions=[])
        await p.invoke("b", available_functions=[])
        assert p.invoke_count == 2

    @pytest.mark.asyncio
    async def test_reset(self):
        p = FakeAgentProvider()
        p.add_response("x", AgentResult(success=True))
        await p.invoke("x", available_functions=[])
        p.reset()
        assert p.invoke_count == 0
        assert len(p.list_functions()) == 0

    def test_list_functions(self):
        p = FakeAgentProvider()
        f = AgentFunction(name="test.func", description="test")
        p.add_function(f)
        assert len(p.list_functions()) == 1
        assert p.list_functions()[0].name == "test.func"

    def test_provider_name(self):
        p = FakeAgentProvider("my-agent")
        assert p.provider_name() == "my-agent"


# ── Agent Session Tests ────────────────────────────────────────────


class TestAgentService:
    """Tests for create_agent_session.

    ReAct execution is stream-only and is covered by stream/runtime tests.
    """

    @pytest.mark.asyncio
    async def test_create_session(self, db_session: AsyncSession):
        result = await create_agent_session(
            # db_session removed — agent functions self-manage DB sessions now

            actor_id="test-agent",
            execution_mode="auto",
        )
        assert "session_id" in result
        assert result["execution_mode"] == "auto"
        assert result["max_depth"] == 5
