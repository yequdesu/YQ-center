"""Tests for durable Agent turn event streams."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_agent_stream_persists_turn_and_events(client: AsyncClient, db_session):
    from sqlalchemy import select

    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import ProviderInvokeResult
    from yequ.api.routes.agent import register_provider
    from yequ.models.agent_plan import AgentPlan
    from yequ.models.agent_run import AgentRun

    provider = FakeAgentProvider("turn-events-test")
    provider.set_sequence(
        [
            ProviderInvokeResult(
                message="System check completed.",
                tool_calls=[],
                success=True,
            ),
        ]
    )
    register_provider(provider)

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "turn-events-test", "execution_mode": "auto"},
    )
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    stream_resp = await client.post(
        "/agent/invoke/stream",
        json={
            "session_id": session_id,
            "provider_name": "turn-events-test",
            "prompt": "check the machine",
            "execution_mode": "auto",
        },
    )
    assert stream_resp.status_code == 200

    events = _parse_sse_events(stream_resp.text)
    assert events
    turn_ids = {event.get("turn_id") for event in events}
    assert len(turn_ids) == 1
    turn_id = next(iter(turn_ids))
    assert isinstance(turn_id, str)
    assert turn_id.startswith("turn_")
    assert all(event.get("data", {}).get("turn_id") == turn_id for event in events)

    detail_resp = await client.get(f"/admin/sessions/{session_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["turns"][0]["turn_id"] == turn_id
    assert detail["turns"][0]["status"] == "succeeded"
    assert detail["turns"][0]["events"]
    assert detail["turns"][0]["prompt"] == "check the machine"

    turns_resp = await client.get(f"/admin/sessions/{session_id}/turns")
    assert turns_resp.status_code == 200
    assert turns_resp.json()[0]["turn_id"] == turn_id

    event_resp = await client.get(f"/admin/agent-turns/{turn_id}/events")
    assert event_resp.status_code == 200
    persisted = event_resp.json()
    persisted_types = [event["event_type"] for event in persisted]
    assert persisted_types[0] == "stream.open"
    assert "agent.prompt.received" in persisted_types
    assert "agent.completed" in persisted_types
    assert persisted_types[-1] == "stream.close"
    assert [event["seq"] for event in persisted] == list(range(1, len(persisted) + 1))

    run_result = await db_session.execute(select(AgentRun).where(AgentRun.session_id == session_id))
    run = run_result.scalar_one()
    assert run.turn_id == turn_id
    assert run.status == "succeeded"
    plan_result = await db_session.execute(select(AgentPlan).where(AgentPlan.run_id == run.run_id))
    plan = plan_result.scalar_one()
    assert plan.turn_id == turn_id
    assert plan.status == "succeeded"
    assert plan.objective == "check the machine"
    plan_resp = await client.get(f"/agent/sessions/{session_id}/plan")
    assert plan_resp.status_code == 200
    plan_projection = plan_resp.json()["plan"]
    assert plan_projection["plan_id"] == plan.plan_id
    assert plan_projection["status"] == "succeeded"
    assert plan_projection["steps"][0]["status"] == "succeeded"
    runtime_resp = await client.get(f"/agent/sessions/{session_id}/runtime-state")
    assert runtime_resp.status_code == 200
    runtime_state = runtime_resp.json()
    assert runtime_state["plan"]["plan_id"] == plan.plan_id
    assert runtime_state["run"]["run_id"] == run.run_id
    assert runtime_state["run"]["status"] == "succeeded"
    assert runtime_state["run"]["task_state"]["objective"]["text"] == "check the machine"
    assert runtime_state["run"]["events"]
    assert provider.last_messages is not None
    assert any(
        message.role == "system"
        and message.content
        and "YCR Agent Plan" in message.content
        for message in provider.last_messages
    )


@pytest.mark.asyncio
async def test_agent_session_audit_log_persists_lifecycle_events(
    client: AsyncClient,
    tmp_path,
    monkeypatch,
):
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import ProviderInvokeResult
    from yequ.api.routes.agent import register_provider
    from yequ.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "session_audit_enabled", True)
    monkeypatch.setattr(settings, "session_audit_log_dir", str(tmp_path / "session-audit"))

    provider = FakeAgentProvider("session-audit-test")
    provider.set_sequence(
        [
            ProviderInvokeResult(
                message="Audit trace visible.",
                tool_calls=[],
                success=True,
            ),
        ]
    )
    register_provider(provider)

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "session-audit-test", "execution_mode": "auto"},
    )
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    stream_resp = await client.post(
        "/agent/invoke/stream",
        json={
            "session_id": session_id,
            "provider_name": "session-audit-test",
            "prompt": "write a full audit trail",
            "execution_mode": "auto",
        },
    )
    assert stream_resp.status_code == 200

    audit_resp = await client.get(f"/admin/sessions/{session_id}/audit-log?tail=200")
    assert audit_resp.status_code == 200
    audit = audit_resp.json()
    assert audit["exists"] is True
    events = audit["events"]
    event_types = [event["event_type"] for event in events]
    assert event_types[0] == "session.created"
    assert "agent.invoke.request_received" in event_types
    assert "agent.invoke.context_loaded" in event_types
    assert "agent.ycr.build_turn.completed" in event_types
    assert "agent.provider.completed" in event_types
    assert "agent.turn.created" in event_types
    assert "stream.open" in event_types
    assert "agent.prompt.received" in event_types
    assert "agent.completed" in event_types
    assert "stream.close" in event_types
    assert "agent.message.persisted" in event_types
    assert all(event["session_id"] == session_id for event in events)
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert all(event.get("recorded_at") for event in events)
    assert all(event.get("event_time") for event in events)
    context_loaded = next(
        event for event in events if event["event_type"] == "agent.invoke.context_loaded"
    )
    assert context_loaded["payload"]["elapsed_ms"] >= 0
    assert context_loaded["payload"]["spans"]["capability_context_ms"] >= 0
    ycr_span = next(
        event for event in events if event["event_type"] == "agent.ycr.build_turn.completed"
    )
    assert ycr_span["payload"]["elapsed_ms"] >= 0
    assert "context_estimate" in ycr_span["payload"]
    provider_span = next(
        event for event in events if event["event_type"] == "agent.provider.completed"
    )
    assert provider_span["payload"]["elapsed_ms"] >= 0


@pytest.mark.asyncio
async def test_runtime_state_timing_summary_reports_operation_wait_and_db_errors(
    client: AsyncClient,
    tmp_path,
    monkeypatch,
):
    from yequ.config import get_settings
    from yequ.services.session_audit import record_session_audit_event

    settings = get_settings()
    monkeypatch.setattr(settings, "session_audit_enabled", True)
    monkeypatch.setattr(settings, "session_audit_log_dir", str(tmp_path / "session-audit"))

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "timing-summary-test", "execution_mode": "auto"},
    )
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    record_session_audit_event(
        session_id,
        "operation.created",
        {
            "operation_id": "op_timing_1",
            "operation_kind": "job",
            "operation_status": "running",
            "title": "exec.run",
        },
        event_time="2026-07-09T00:00:00+00:00",
        source="operation",
    )
    record_session_audit_event(
        session_id,
        "operation.succeeded",
        {
            "operation_id": "op_timing_1",
            "operation_kind": "job",
            "operation_status": "succeeded",
        },
        event_time="2026-07-09T00:00:02+00:00",
        source="operation",
    )
    record_session_audit_event(
        session_id,
        "agent.operation_report.completed",
        {
            "operation_id": "op_timing_1",
            "notification_id": "noti_1",
            "turn_id": "turn_1",
            "elapsed_ms": 123.4,
        },
        source="agent.operation_reporter",
    )
    record_session_audit_event(
        session_id,
        "agent.db.error",
        {
            "phase": "execute_serial",
            "name": "capability.invoke",
            "target_node_id": "linux-node-01",
            "error_code": "db_lock_timeout",
            "message": "canceling statement due to lock timeout",
        },
        source="agent.tools",
    )

    runtime_resp = await client.get(f"/agent/sessions/{session_id}/runtime-state")
    assert runtime_resp.status_code == 200
    timing = runtime_resp.json()["timing"]
    assert timing["db_error_count"] == 1
    assert timing["db_errors"][0]["error_code"] == "db_lock_timeout"
    assert timing["category_totals_ms"]["operation_report"] == 123.4
    assert timing["operation_wait_segments"][0]["operation_id"] == "op_timing_1"
    assert timing["operation_wait_segments"][0]["elapsed_ms"] == 2000


@pytest.mark.asyncio
async def test_agent_turn_stream_close_fails_open_turn(db_session):
    from sqlalchemy import select

    from yequ.models.agent_turn import AgentTurn
    from yequ.services.agent_turn_service import create_agent_turn, record_agent_turn_event

    turn_id = await create_agent_turn(
        session_id="sess_turn_close",
        prompt="internal report",
        provider_name="fake",
        target_node_id=None,
        execution_mode="auto",
        trace_id="tr_turn_close",
        metadata={"suppress_user_message": True},
    )
    await record_agent_turn_event(
        turn_id,
        {
            "event_id": "evt_turn_close_prompt_context",
            "event_type": "agent.prompt_context",
            "session_id": "sess_turn_close",
            "trace_id": "tr_turn_close",
            "data": {},
        },
    )
    await record_agent_turn_event(
        turn_id,
        {
            "event_id": "evt_turn_close_stream_close",
            "event_type": "stream.close",
            "session_id": "sess_turn_close",
            "trace_id": "tr_turn_close",
            "data": {},
        },
    )

    result = await db_session.execute(select(AgentTurn).where(AgentTurn.turn_id == turn_id))
    turn = result.scalar_one()
    assert turn.status == "failed"
    assert turn.error_code == "stream_closed_before_terminal"
    assert turn.completed_at is not None


@pytest.mark.asyncio
async def test_agent_turn_stream_close_preserves_waiting_approval(db_session):
    from sqlalchemy import select

    from yequ.models.agent_turn import AgentTurn
    from yequ.services.agent_turn_service import create_agent_turn, record_agent_turn_event

    turn_id = await create_agent_turn(
        session_id="sess_turn_waiting_close",
        prompt="write action",
        provider_name="fake",
        target_node_id=None,
        execution_mode="auto",
        trace_id="tr_turn_waiting_close",
        metadata={},
    )
    for event_type, event_id in [
        ("agent.tool_call.waiting_approval", "evt_turn_waiting_approval"),
        ("agent.observing", "evt_turn_waiting_observing"),
        ("stream.close", "evt_turn_waiting_stream_close"),
    ]:
        await record_agent_turn_event(
            turn_id,
            {
                "event_id": event_id,
                "event_type": event_type,
                "session_id": "sess_turn_waiting_close",
                "trace_id": "tr_turn_waiting_close",
                "data": {},
            },
        )

    result = await db_session.execute(select(AgentTurn).where(AgentTurn.turn_id == turn_id))
    turn = result.scalar_one()
    assert turn.status == "waiting_approval"
    assert turn.error_code is None
    assert turn.completed_at is None


@pytest.mark.asyncio
async def test_create_internal_turn_closes_stale_internal_turn(db_session):
    from sqlalchemy import select

    from yequ.models.agent_turn import AgentTurn
    from yequ.services.agent_turn_service import create_agent_turn, record_agent_turn_event

    first_turn_id = await create_agent_turn(
        session_id="sess_internal_replace",
        prompt="first internal report",
        provider_name="fake",
        target_node_id=None,
        execution_mode="auto",
        trace_id="tr_internal_first",
        metadata={"suppress_user_message": True},
    )
    await record_agent_turn_event(
        first_turn_id,
        {
            "event_id": "evt_internal_first_context",
            "event_type": "agent.prompt_context",
            "session_id": "sess_internal_replace",
            "trace_id": "tr_internal_first",
            "data": {},
        },
    )

    second_turn_id = await create_agent_turn(
        session_id="sess_internal_replace",
        prompt="second internal report",
        provider_name="fake",
        target_node_id=None,
        execution_mode="auto",
        trace_id="tr_internal_second",
        metadata={"suppress_user_message": True},
    )

    result = await db_session.execute(
        select(AgentTurn).where(AgentTurn.turn_id.in_([first_turn_id, second_turn_id]))
    )
    turns = {turn.turn_id: turn for turn in result.scalars().all()}
    assert turns[first_turn_id].status == "failed"
    assert turns[first_turn_id].error_code == "internal_turn_replaced"
    assert turns[first_turn_id].metadata_["replaced_by_turn_id"] == second_turn_id
    assert turns[second_turn_id].status == "created"


@pytest.mark.asyncio
async def test_agent_stream_emits_ycr_context_budget_events(client: AsyncClient, monkeypatch):
    from yequ.agent.agent_stream import agent_invoke_stream
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import ProviderInvokeResult
    from yequ.config import get_settings
    from yequ.db import async_session_factory
    from yequ.ycr.budget import projection_profile_from_settings
    from yequ.ycr.context_packet import build_agent_context_packet

    provider = FakeAgentProvider("turn-ycr-budget-test")
    provider.set_sequence(
        [
            ProviderInvokeResult(
                message="Budget visible.",
                usage={
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                    "total_tokens": 15,
                },
            ),
        ]
    )

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "turn-ycr-budget-test", "execution_mode": "auto"},
    )
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    class FakeYcrClient:
        async def build_turn(self, **payload):
            async with async_session_factory() as session:
                return await build_agent_context_packet(
                    session,
                    session_id=str(payload["session_id"]),
                    actor_id=str(payload.get("actor_id") or ""),
                    provider=str(payload["provider"]),
                    model=str(payload.get("model") or ""),
                    messages=list(payload.get("messages") or []),
                    available_functions=list(payload.get("available_functions") or []),
                    capability_context=dict(payload.get("capability_context") or {}),
                    profile=projection_profile_from_settings(get_settings()),
                    agent_plan=dict(payload.get("agent_plan") or {}),
                    step=int(payload.get("step") or 1),
                )

    import yequ.agent.agent_stream as agent_stream_module

    monkeypatch.setattr(agent_stream_module, "get_ycr_client", lambda: FakeYcrClient())

    events = [
        event
        async for event in agent_invoke_stream(
            provider,
            session_id=session_id,
            prompt="show ycr budget",
            available_functions=[],
            execution_mode="auto",
        )
    ]

    ycr_events = [event for event in events if event["event_type"] == "agent.ycr.context"]
    assert [event["data"]["phase"] for event in ycr_events] == [
        "provider_input",
        "provider_output",
    ]
    assert ycr_events[0]["data"]["tokens"]["upload_estimated"] > 0
    assert ycr_events[1]["data"]["tokens"]["download_estimated"] > 0
    assert ycr_events[1]["data"]["tokens"]["upload_actual"] == 12
    assert ycr_events[1]["data"]["tokens"]["download_actual"] == 3


@pytest.mark.asyncio
async def test_agent_stream_fails_closed_when_ycr_build_turn_fails(
    client: AsyncClient, monkeypatch
):
    from yequ.agent.agent_stream import agent_invoke_stream
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.ycr.client import YcrError

    provider = FakeAgentProvider("turn-ycr-fail-closed-test")

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "turn-ycr-fail-closed-test", "execution_mode": "auto"},
    )
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    class FailingYcrClient:
        async def build_turn(self, **payload):
            raise YcrError("context_router_unavailable", "YCR unavailable")

    import yequ.agent.agent_stream as agent_stream_module

    monkeypatch.setattr(agent_stream_module, "get_ycr_client", lambda: FailingYcrClient())

    events = [
        event
        async for event in agent_invoke_stream(
            provider,
            session_id=session_id,
            prompt="do not reach provider",
            available_functions=[],
            execution_mode="auto",
        )
    ]

    event_types = [event["event_type"] for event in events]
    failed_events = [event for event in events if event["event_type"] == "agent.failed"]
    assert "agent.provider.started" not in event_types
    assert len(failed_events) == 1
    assert failed_events[0]["data"]["error_code"] == "context_router_unavailable"


@pytest.mark.asyncio
async def test_delete_session_deletes_turn_events(client: AsyncClient):
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import ProviderInvokeResult
    from yequ.api.routes.agent import register_provider

    provider = FakeAgentProvider("turn-delete-test")
    provider.set_sequence(
        [
            ProviderInvokeResult(message="Done.", tool_calls=[], success=True),
        ]
    )
    register_provider(provider)

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "turn-delete-test", "execution_mode": "auto"},
    )
    session_id = session_resp.json()["session_id"]
    stream_resp = await client.post(
        "/agent/invoke/stream",
        json={
            "session_id": session_id,
            "provider_name": "turn-delete-test",
            "prompt": "hello",
        },
    )
    turn_id = _parse_sse_events(stream_resp.text)[0]["turn_id"]

    delete_resp = await client.delete(f"/admin/sessions/{session_id}")
    assert delete_resp.status_code == 204

    events_resp = await client.get(f"/admin/agent-turns/{turn_id}/events")
    assert events_resp.status_code == 404


@pytest.mark.asyncio
async def test_agent_plan_stream_reaches_provider_for_existing_session(client: AsyncClient):
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.api.routes.agent import register_provider

    provider = FakeAgentProvider("plan-stream-session-test")
    register_provider(provider)

    session_resp = await client.post(
        "/agent/sessions",
        json={"actor_id": "plan-stream-session-test", "execution_mode": "auto"},
    )
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    stream_resp = await client.post(
        "/agent/plan/stream",
        json={
            "session_id": session_id,
            "provider_name": "plan-stream-session-test",
            "prompt": "check status",
            "target_node_id": "test-node",
            "execution_mode": "auto",
        },
    )

    assert stream_resp.status_code == 200
    event_types = [event["event_type"] for event in _parse_sse_events(stream_resp.text)]
    assert "agent.session.resolved" in event_types
    assert "agent.prompt.received" in event_types
    assert "agent.provider.started" in event_types


def _parse_sse_events(text: str) -> list[dict]:
    events: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data: "):
            continue
        events.append(json.loads(line[len("data: ") :]))
    return events
