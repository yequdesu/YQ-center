from __future__ import annotations

from datetime import UTC, datetime, timedelta

from yequ.agent.runtime_state import (
    AgentRuntimeController,
    AgentRuntimeFailure,
    AgentRuntimeIteration,
    AgentRuntimeLimits,
    AgentToolObservationCollector,
    status_for_stream_event,
)


def test_runtime_rejects_depth_exceeded():
    runtime = AgentRuntimeController(
        limits=AgentRuntimeLimits(max_depth=2),
        call_path=["root", "child"],
    )

    failure = runtime.check_initial_constraints()

    assert failure is not None
    assert failure.error_code == "call_depth_exceeded"
    assert runtime.status == "failed"


def test_runtime_rejects_elapsed_duration():
    runtime = AgentRuntimeController(
        limits=AgentRuntimeLimits(max_total_duration_sec=5),
        started_at=datetime.now(UTC) - timedelta(seconds=10),
    )

    failure = runtime.check_initial_constraints()

    assert failure is not None
    assert failure.error_code == "max_duration_exceeded"
    assert runtime.status == "failed"


def test_runtime_begin_iteration_increments_until_max_steps():
    runtime = AgentRuntimeController(limits=AgentRuntimeLimits(max_steps=2))

    first = runtime.begin_iteration()
    second = runtime.begin_iteration()
    third = runtime.begin_iteration()

    assert isinstance(first, AgentRuntimeIteration)
    assert first.iteration == 1
    assert isinstance(second, AgentRuntimeIteration)
    assert second.iteration == 2
    assert isinstance(third, AgentRuntimeFailure)
    assert third.error_code == "max_steps_exceeded"


def test_runtime_decides_plain_text_is_final_answer():
    runtime = AgentRuntimeController()

    decision = runtime.decide_provider_output(
        assistant_text="winClient is online.",
        tool_calls=[],
    )

    assert decision.kind == "final"
    assert decision.status == "succeeded"
    assert decision.final_message == "winClient is online."
    assert runtime.status == "succeeded"


def test_runtime_rejects_empty_provider_output():
    runtime = AgentRuntimeController()

    decision = runtime.decide_provider_output(assistant_text="", tool_calls=[])

    assert decision.kind == "failure"
    assert decision.failure is not None
    assert decision.failure.error_code == "agent_protocol_error"
    assert runtime.status == "failed"


def test_runtime_keeps_tool_output_in_continue_state():
    runtime = AgentRuntimeController()

    decision = runtime.decide_provider_output(
        assistant_text="Let me check.",
        tool_calls=[{"name": "linux.system.info"}],
    )

    assert decision.kind == "continue"
    assert decision.status == "validating_tools"
    assert runtime.status == "validating_tools"


def test_runtime_waiting_approval_is_explicit_nonterminal_pause():
    runtime = AgentRuntimeController()

    decision = runtime.waiting_approval()

    assert decision.kind == "waiting_approval"
    assert decision.status == "waiting_approval"
    assert runtime.status == "waiting_approval"


def test_turn_status_mapping_uses_runtime_state_names():
    assert status_for_stream_event("agent.completed") == "succeeded"
    assert status_for_stream_event("agent.tool_call.waiting_approval") == "waiting_approval"
    assert status_for_stream_event("agent.provider.failed") == "failed"


def test_tool_observation_collector_preserves_provider_call_order():
    collector = AgentToolObservationCollector({"call_b": 0, "call_a": 1})

    assert collector.record_event(
        "agent.tool_call.completed",
        {
            "call_id": "call_a",
            "name": "linux.system.info",
            "result": {"ok": True},
            "target_node_id": "linux-node-01",
        },
    )
    assert collector.record_event(
        "agent.tool_call.failed",
        {
            "call_id": "call_b",
            "name": "system.info",
            "message": "node unavailable",
            "error_code": "node_unavailable",
            "target_node_id": "winClient",
        },
    )

    ordered = collector.ordered_results()

    assert [result["call_id"] for result in ordered] == ["call_b", "call_a"]
    assert ordered[0]["status"] == "failed"
    assert ordered[0]["target_node_id"] == "winClient"
    assert ordered[1]["status"] == "succeeded"
    assert ordered[1]["target_node_id"] == "linux-node-01"


def test_tool_observation_collector_tracks_waiting_approval():
    collector = AgentToolObservationCollector({"call_1": 0})

    collector.record_event(
        "agent.tool_call.waiting_approval",
        {
            "call_id": "call_1",
            "name": "system.write",
            "approval_id": "ap_1",
            "target_node_id": "winClient",
        },
    )

    assert collector.has_waiting_approval
    assert collector.ordered_results() == [
        {
            "name": "system.write",
            "call_id": "call_1",
            "status": "waiting_approval",
            "approval_id": "ap_1",
            "target_node_id": "winClient",
        }
    ]
