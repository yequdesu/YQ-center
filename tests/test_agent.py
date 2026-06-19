"""Tests for Agent subsystem — Policy, Provider, Session, Invoke, Constraints."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.agent.agent_service import agent_invoke, create_agent_session
from yequ.agent.fake_provider import FakeAgentProvider
from yequ.agent.provider import AgentFunction, AgentResult, ProviderInvokeResult
from yequ.protocol import ErrorCode

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


# ── Agent Service Tests ────────────────────────────────────────────

# Fixture: create a session via create_agent_session so a Session row exists.
# The fixture itself is async; asyncio_mode=auto handles it.
@pytest.fixture
async def agent_session_fixture(db_session: AsyncSession) -> dict[str, object]:
    """Create a real agent session for tests."""
    return await create_agent_session(
        db_session,
        actor_id="test-agent",
        execution_mode="auto",
    )


class TestAgentService:
    """Tests for agent_invoke, create_agent_session, constraints."""

    @pytest.mark.asyncio
    async def test_create_session(self, db_session: AsyncSession):
        result = await create_agent_session(
            db_session,
            actor_id="test-agent",
            execution_mode="auto",
        )
        assert "session_id" in result
        assert result["execution_mode"] == "auto"
        assert result["max_depth"] == 5

    @pytest.mark.asyncio
    async def test_invoke_writes_timeline(
        self, db_session: AsyncSession, agent_session_fixture: dict[str, object]
    ):
        """Agent invoke should write TimelineEvents."""
        from yequ.models.timeline import TimelineEvent

        session_id = str(agent_session_fixture["session_id"])

        p = FakeAgentProvider()
        p.add_response(
            "test",
            AgentResult(
                success=True,
                output={"ok": True},
                function_calls=[{"name": "system.metrics.snapshot", "input": {}}],
            ),
        )
        funcs = [AgentFunction(name="system.metrics.snapshot", risk="safe")]

        result = await agent_invoke(
            db_session,
            p,
            session_id=session_id,
            prompt="test prompt",
            available_functions=funcs,
        )

        assert result.success is True

        # Verify timeline events were written
        events_result = await db_session.execute(
            select(TimelineEvent).where(
                TimelineEvent.session_id == session_id,
            )
        )
        events = events_result.scalars().all()
        event_types = {e.event_type for e in events}
        assert (
            "agent.step.started" in event_types
        ), f"Expected agent.step.started in {event_types}"
        assert (
            "agent.step.finished" in event_types
        ), f"Expected agent.step.finished in {event_types}"

    @pytest.mark.asyncio
    async def test_loop_detection(
        self, db_session: AsyncSession
    ):
        """Calling a function already in call_path must be detected."""
        p = FakeAgentProvider()
        p.add_response(
            "recursive",
            AgentResult(
                success=True,
                function_calls=[
                    {"name": "system.metrics.snapshot", "input": {}},
                    {"name": "system.diag", "input": {}},  # already in call_path!
                ],
            ),
        )
        funcs = [
            AgentFunction(name="system.metrics.snapshot", risk="safe"),
            AgentFunction(name="system.diag", risk="safe"),
        ]

        result = await agent_invoke(
            db_session,
            p,
            session_id="sess_loop",
            prompt="recursive call",
            available_functions=funcs,
            call_path=["system.diag"],  # system.diag already called
        )

        assert result.success is False
        assert result.error_code == ErrorCode.CIRCULAR_DEPENDENCY

    @pytest.mark.asyncio
    async def test_depth_exceeded(
        self, db_session: AsyncSession
    ):
        """Call depth exceeding max_depth must be rejected."""
        p = FakeAgentProvider()
        funcs = [AgentFunction(name="f", risk="safe")]

        result = await agent_invoke(
            db_session,
            p,
            session_id="sess_depth",
            prompt="test",
            available_functions=funcs,
            call_path=["a", "b", "c", "d", "e"],  # depth 5
            max_depth=3,  # only 3 allowed
        )

        assert result.success is False
        assert result.error_code == ErrorCode.CALL_DEPTH_EXCEEDED

    @pytest.mark.asyncio
    async def test_steps_exceeded(
        self, db_session: AsyncSession
    ):
        """Step count exceeding max_steps must be rejected."""
        p = FakeAgentProvider()
        funcs = [AgentFunction(name="f", risk="safe")]

        result = await agent_invoke(
            db_session,
            p,
            session_id="sess_steps",
            prompt="test",
            available_functions=funcs,
            step_count=20,
            max_steps=5,
        )

        assert result.success is False
        assert result.error_code == ErrorCode.MAX_STEPS_EXCEEDED

    @pytest.mark.asyncio
    async def test_duration_exceeded(
        self, db_session: AsyncSession
    ):
        """Elapsed duration exceeding max must be rejected."""
        p = FakeAgentProvider()
        funcs = [AgentFunction(name="f", risk="safe")]

        result = await agent_invoke(
            db_session,
            p,
            session_id="sess_dur",
            prompt="test",
            available_functions=funcs,
            started_at=datetime.now(UTC) - timedelta(minutes=10),
            max_total_duration_sec=60,
        )

        assert result.success is False
        assert result.error_code == ErrorCode.MAX_DURATION_EXCEEDED

    @pytest.mark.asyncio
    async def test_policy_denied_destructive_readonly(
        self, db_session: AsyncSession
    ):
        """Destructive function in readonly mode must be denied."""
        p = FakeAgentProvider()
        p.add_response(
            "dangerous",
            AgentResult(
                success=True,
                function_calls=[{"name": "system.reboot", "input": {}}],
            ),
        )
        funcs = [AgentFunction(name="system.reboot", risk="destructive")]

        result = await agent_invoke(
            db_session,
            p,
            session_id="sess_pol",
            prompt="dangerous action",
            available_functions=funcs,
            execution_mode="readonly",
        )

        assert result.success is False
        assert result.error_code == ErrorCode.POLICY_DENIED

    @pytest.mark.asyncio
    async def test_provider_failure_returns_error(
        self, db_session: AsyncSession
    ):
        """Provider failure must return standard error structure, no retry."""
        p = FakeAgentProvider()
        p.add_response(
            "fail",
            AgentResult(
                success=False,
                error_code="internal_error",
                error_message="LLM unavailable",
                retryable=False,
            ),
        )
        funcs = [AgentFunction(name="test.func", risk="safe")]

        result = await agent_invoke(
            db_session,
            p,
            session_id="sess_fail",
            prompt="fail me",
            available_functions=funcs,
        )

        assert result.success is False
        assert result.error_code == "internal_error"
        assert result.retryable is False  # no auto-retry
