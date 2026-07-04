from __future__ import annotations

from datetime import UTC, datetime, timedelta

from yequ.agent.runtime_state import (
    AgentRunGraph,
    AgentRuntimeController,
    AgentRuntimeFailure,
    AgentRuntimeIteration,
    AgentRuntimeLimits,
    AgentToolObservationCollector,
)
from yequ.runtime.agent_status import status_for_stream_event
from yequ.ycr import project_tool_observation


class FakeYcrClient:
    async def project_tool_observation(self, **payload):
        return project_tool_observation(**payload)


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


def test_agent_run_graph_owns_waiting_and_failure_transitions():
    graph = AgentRunGraph(AgentRuntimeController())

    iteration = graph.begin_iteration()
    assert isinstance(iteration, AgentRuntimeIteration)
    assert graph.loop_state == "model_running"

    decision = graph.decide_provider_output(
        assistant_text="",
        tool_calls=[{"name": "transfer.create"}],
    )
    assert decision.kind == "continue"
    assert graph.loop_state == "validating_tools"

    wait = graph.observe_tool_results(
        [{"status": "waiting_operation", "operation_id": "op_test"}]
    )
    assert wait is not None
    assert wait.status == "waiting_operation"
    assert graph.loop_state == "waiting_operation"

    failure = graph.missing_final_answer()
    assert failure.error_code == "agent_protocol_error"
    assert graph.loop_state == "failed"


def test_runtime_waiting_approval_is_explicit_nonterminal_pause():
    runtime = AgentRuntimeController()

    decision = runtime.waiting_approval()

    assert decision.kind == "waiting_approval"
    assert decision.status == "waiting_approval"
    assert runtime.status == "waiting_approval"


def test_runtime_waiting_operation_is_explicit_nonterminal_pause():
    runtime = AgentRuntimeController()

    decision = runtime.waiting_operation()

    assert decision.kind == "waiting_operation"
    assert decision.status == "waiting_operation"
    assert runtime.status == "waiting_operation"


def test_turn_status_mapping_uses_runtime_state_names():
    assert status_for_stream_event("agent.completed") == "succeeded"
    assert status_for_stream_event("agent.tool_call.waiting_approval") == "waiting_approval"
    assert status_for_stream_event("agent.operation.waiting") == "waiting_operation"
    assert status_for_stream_event("agent.provider.failed") == "failed"


async def test_tool_observation_collector_preserves_provider_call_order():
    collector = AgentToolObservationCollector(
        {"call_b": 0, "call_a": 1},
        ycr_client=FakeYcrClient(),
    )

    assert await collector.record_event(
        "agent.tool_call.completed",
        {
            "call_id": "call_a",
            "name": "linux.system.info",
            "result": {"ok": True},
            "target_node_id": "linux-node-01",
        },
    )
    assert await collector.record_event(
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
    assert ordered[0]["ycr"]["projected"] is True
    assert ordered[0]["result"]["projection_policy"] == "tool_observation_summary_v1"
    assert ordered[1]["status"] == "succeeded"
    assert ordered[1]["target_node_id"] == "linux-node-01"
    assert ordered[1]["ycr"]["projected"] is True
    assert ordered[1]["result"]["facts"] == {"ok": True}


async def test_tool_observation_collector_projects_large_completed_result():
    collector = AgentToolObservationCollector({"call_1": 0}, ycr_client=FakeYcrClient())

    await collector.record_event(
        "agent.tool_call.completed",
        {
            "call_id": "call_1",
            "name": "linux.logs.tail",
            "result": {"stdout": "x" * 5000, "status": "ok"},
            "target_node_id": "linux-node-01",
        },
    )

    ordered = collector.ordered_results()
    result = ordered[0]["result"]
    assert result["truncated"] is True
    assert result["refs"]
    assert result["facts"]["stdout"]["chars"] == 5000
    assert "x" * 2000 not in str(ordered[0])


async def test_tool_observation_collector_tracks_waiting_approval():
    collector = AgentToolObservationCollector({"call_1": 0}, ycr_client=FakeYcrClient())

    await collector.record_event(
        "agent.tool_call.waiting_approval",
        {
            "call_id": "call_1",
            "name": "system.write",
            "approval_id": "ap_1",
            "target_node_id": "winClient",
        },
    )

    assert collector.has_waiting_approval
    result = collector.ordered_results()[0]
    assert result["name"] == "system.write"
    assert result["call_id"] == "call_1"
    assert result["status"] == "waiting_approval"
    assert result["approval_id"] == "ap_1"
    assert result["target_node_id"] == "winClient"
    assert result["ycr"]["projected"] is True


async def test_tool_observation_collector_tracks_waiting_operation():
    collector = AgentToolObservationCollector({"call_1": 0}, ycr_client=FakeYcrClient())

    await collector.record_event(
        "agent.tool_call.waiting_operation",
        {
            "call_id": "call_1",
            "name": "transfer.create",
            "operation_id": "op_1",
            "wait_handle": {"operation_id": "op_1"},
            "target_node_id": None,
        },
    )

    assert collector.has_waiting_operation
    result = collector.ordered_results()[0]
    assert result["name"] == "transfer.create"
    assert result["call_id"] == "call_1"
    assert result["status"] == "waiting_operation"
    assert result["operation_id"] == "op_1"
    assert result["wait_handle"] == {"operation_id": "op_1"}
    assert result["target_node_id"] is None
    assert result["ycr"]["projected"] is True
