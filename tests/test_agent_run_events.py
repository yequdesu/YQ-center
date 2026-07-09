import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.session import Session
from yequ.runtime.agent_plan_service import create_agent_plan, get_agent_plan_projection
from yequ.runtime.agent_run_resume import append_event_and_reduce
from yequ.runtime.agent_run_service import create_agent_run
from yequ.runtime.replanner import final_candidate_gate
from yequ.runtime.task_state import get_task_state


@pytest.mark.asyncio
async def test_agent_run_events_drive_task_state_waits(
    db_session: AsyncSession,
) -> None:
    session_id = "sess_agent_run_events"
    db_session.add(
        Session(
            session_id=session_id,
            actor_type="agent",
            actor_id="test-agent",
            status="active",
            execution_mode="auto",
            label="events",
        )
    )
    run = await create_agent_run(
        db_session,
        session_id=session_id,
        provider_name="fake-events",
        execution_mode="auto",
        target_node_id="node-1",
        user_message="read a protected file",
        trace_id="trace-events",
        metadata={},
    )

    await append_event_and_reduce(
        db_session,
        run,
        event_type="approval.waiting",
        source="agent.tool",
        payload={
            "approval_id": "apv_1",
            "function_name": "exec.run",
            "target_node_id": "node-1",
        },
    )
    state = get_task_state(run)
    assert state["completion"]["status"] == "waiting_approval"
    assert state["last_decision"]["action"] == "wait_approval"
    assert state["pending_approvals"][0]["approval_id"] == "apv_1"

    await append_event_and_reduce(
        db_session,
        run,
        event_type="approval.approved",
        source="approval",
        payload={
            "approval_id": "apv_1",
            "operation_id": "op_1",
            "function_name": "exec.run",
            "target_node_id": "node-1",
        },
    )
    state = get_task_state(run)
    assert state["completion"]["status"] == "waiting_operation"
    assert state["last_decision"]["action"] == "wait_operation"
    assert state["pending_approvals"] == []
    assert state["pending_operations"][0]["operation_id"] == "op_1"

    await append_event_and_reduce(
        db_session,
        run,
        event_type="operation.succeeded",
        source="operation",
        payload={"operation_id": "op_1", "status": "succeeded"},
    )
    state = get_task_state(run)
    assert state["completion"]["status"] == "in_progress"
    assert state["last_decision"]["action"] == "continue_llm"
    assert state["pending_operations"] == []


@pytest.mark.asyncio
async def test_agent_run_events_bind_plan_step_by_tool_and_operation(
    db_session: AsyncSession,
) -> None:
    session_id = "sess_agent_run_plan_events"
    db_session.add(
        Session(
            session_id=session_id,
            actor_type="agent",
            actor_id="test-agent",
            status="active",
            execution_mode="auto",
            label="plan-events",
        )
    )
    run = await create_agent_run(
        db_session,
        session_id=session_id,
        provider_name="fake-events",
        execution_mode="auto",
        target_node_id="node-1",
        user_message="read a protected file",
        trace_id="trace-plan-events",
        metadata={},
    )
    plan = await create_agent_plan(
        db_session,
        session_id=session_id,
        run_id=run.run_id,
        provider_name="fake-events",
        execution_mode="auto",
        target_node_id="node-1",
        objective="read a protected file",
        metadata={},
    )
    run.metadata_json = {"plan_id": plan.plan_id}

    await append_event_and_reduce(
        db_session,
        run,
        event_type="llm.tool_call_requested",
        source="agent.provider",
        payload={
            "call_id": "call_1",
            "function_name": "capability.invoke",
            "capability_ref": "exec.run",
            "target_node_id": "node-1",
        },
        plan_id=plan.plan_id,
    )
    await append_event_and_reduce(
        db_session,
        run,
        event_type="operation.waiting",
        source="agent.tool",
        payload={
            "call_id": "call_1",
            "operation_id": "op_1",
            "function_name": "capability.invoke",
            "capability_ref": "exec.run",
            "target_node_id": "node-1",
        },
        plan_id=plan.plan_id,
    )
    await append_event_and_reduce(
        db_session,
        run,
        event_type="operation.succeeded",
        source="operation",
        payload={"operation_id": "op_1", "status": "succeeded"},
        plan_id=plan.plan_id,
    )
    projection = await get_agent_plan_projection(db_session, plan.plan_id)
    steps = projection["steps"]
    tool_steps = [step for step in steps if step["kind"] == "tool"]
    assert len(tool_steps) == 1
    assert tool_steps[0]["tool_call_id"] == "call_1"
    assert tool_steps[0]["operation_id"] == "op_1"
    assert tool_steps[0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_agent_run_events_bind_approval_operation_and_artifact_to_one_plan_step(
    db_session: AsyncSession,
) -> None:
    session_id = "sess_agent_run_plan_artifact"
    db_session.add(
        Session(
            session_id=session_id,
            actor_type="agent",
            actor_id="test-agent",
            status="active",
            execution_mode="auto",
            label="plan-artifact",
        )
    )
    run = await create_agent_run(
        db_session,
        session_id=session_id,
        provider_name="fake-events",
        execution_mode="auto",
        target_node_id="node-1",
        user_message="read a protected artifact",
        trace_id="trace-plan-artifact",
        metadata={},
    )
    plan = await create_agent_plan(
        db_session,
        session_id=session_id,
        run_id=run.run_id,
        provider_name="fake-events",
        execution_mode="auto",
        target_node_id="node-1",
        objective="read a protected artifact",
        metadata={},
    )
    run.metadata_json = {"plan_id": plan.plan_id}

    await append_event_and_reduce(
        db_session,
        run,
        event_type="approval.waiting",
        source="agent.tool",
        payload={
            "call_id": "call_1",
            "approval_id": "apv_1",
            "function_name": "capability.invoke",
            "capability_ref": "exec.run",
            "target_node_id": "node-1",
        },
        plan_id=plan.plan_id,
    )
    await append_event_and_reduce(
        db_session,
        run,
        event_type="approval.approved",
        source="approval",
        payload={
            "approval_id": "apv_1",
            "operation_id": "op_1",
            "function_name": "capability.invoke",
            "capability_ref": "exec.run",
            "target_node_id": "node-1",
        },
        plan_id=plan.plan_id,
    )
    await append_event_and_reduce(
        db_session,
        run,
        event_type="artifact.read",
        source="agent.tool",
        payload={
            "artifact_id": "art_1",
            "title": "journal.log",
            "content_type": "text/plain",
        },
        plan_id=plan.plan_id,
        step_id=None,
    )

    projection = await get_agent_plan_projection(db_session, plan.plan_id)
    steps = projection["steps"]
    approval_steps = [step for step in steps if step["kind"] == "approval"]
    assert len(approval_steps) == 1
    assert approval_steps[0]["operation_id"] == "op_1"
    assert approval_steps[0]["status"] == "succeeded"
    artifact_steps = [step for step in steps if step["kind"] == "agent_task"]
    assert artifact_steps
    assert artifact_steps[0]["metadata"]["artifact_ids"] == ["art_1"]


@pytest.mark.asyncio
async def test_replanner_blocks_final_candidate_when_error_is_repairable(
    db_session: AsyncSession,
) -> None:
    session_id = "sess_agent_run_repairable_final"
    db_session.add(
        Session(
            session_id=session_id,
            actor_type="agent",
            actor_id="test-agent",
            status="active",
            execution_mode="auto",
            label="repairable-final",
        )
    )
    run = await create_agent_run(
        db_session,
        session_id=session_id,
        provider_name="fake-events",
        execution_mode="auto",
        target_node_id="node-1",
        user_message="read protected logs autonomously",
        trace_id="trace-repairable-final",
        metadata={},
    )

    await append_event_and_reduce(
        db_session,
        run,
        event_type="tool.failed",
        source="agent.tool",
        payload={
            "call_id": "call_1",
            "function_name": "capability.invoke",
            "capability_ref": "capability.invoke",
            "target_node_id": "node-1",
            "error_code": "unsupported_enum_value",
            "message": "unsupported profile: admin",
            "details": {
                "field": "profile",
                "available_execution_profiles": ["user.readonly", "admin.readonly"],
            },
        },
    )
    await append_event_and_reduce(
        db_session,
        run,
        event_type="llm.final_candidate",
        source="agent.provider",
        payload={"message": "请告诉我要不要使用 admin。"},
    )

    state = get_task_state(run)
    decision = final_candidate_gate(state)
    assert decision.action == "continue_llm"
    assert decision.reason_code == "final_candidate_blocked_by_repairable_error"
    assert state["blockers"][0]["repairable"] is True


def test_replanner_blocks_raw_tool_protocol_final_candidate() -> None:
    decision = final_candidate_gate(
        {
            "completion": {
                "status": "in_progress",
                "criteria": [],
                "satisfied": [],
                "missing": [],
            },
            "blockers": [],
            "pending_operations": [],
            "pending_approvals": [],
        },
        final_message="<｜｜DSML｜｜tool_calls>{\"name\":\"capability.invoke\"}",
    )

    assert decision.action == "continue_llm"
    assert decision.reason_code == "final_candidate_contains_tool_protocol"


@pytest.mark.asyncio
async def test_replanner_allows_final_candidate_after_repairable_error_is_fixed(
    db_session: AsyncSession,
) -> None:
    session_id = "sess_agent_run_repaired_final"
    db_session.add(
        Session(
            session_id=session_id,
            actor_type="agent",
            actor_id="test-agent",
            status="active",
            execution_mode="auto",
            label="repaired-final",
        )
    )
    run = await create_agent_run(
        db_session,
        session_id=session_id,
        provider_name="fake-events",
        execution_mode="auto",
        target_node_id="node-1",
        user_message="capture screen autonomously",
        trace_id="trace-repaired-final",
        metadata={},
    )

    await append_event_and_reduce(
        db_session,
        run,
        event_type="tool.failed",
        source="agent.tool",
        payload={
            "call_id": "call_bad",
            "function_name": "capability.invoke",
            "capability_ref": "capability.invoke",
            "error_code": "invalid_input",
            "message": "capability_ref is required",
        },
    )
    assert get_task_state(run)["blockers"]

    await append_event_and_reduce(
        db_session,
        run,
        event_type="tool.completed",
        source="agent.tool",
        payload={
            "call_id": "call_fixed",
            "function_name": "capability.invoke",
            "capability_ref": "capability.invoke",
            "status": "succeeded",
        },
    )
    await append_event_and_reduce(
        db_session,
        run,
        event_type="llm.final_candidate",
        source="agent.provider",
        payload={"message": "截图已完成。"},
    )

    state = get_task_state(run)
    decision = final_candidate_gate(state)
    assert state["blockers"] == []
    assert decision.action == "complete"
    assert decision.reason_code == "final_candidate_allowed"


@pytest.mark.asyncio
async def test_agent_run_reducer_extracts_transfer_and_artifact_facts(
    db_session: AsyncSession,
) -> None:
    session_id = "sess_agent_run_domain_facts"
    db_session.add(
        Session(
            session_id=session_id,
            actor_type="agent",
            actor_id="test-agent",
            status="active",
            execution_mode="auto",
            label="domain-facts",
        )
    )
    run = await create_agent_run(
        db_session,
        session_id=session_id,
        provider_name="fake-events",
        execution_mode="auto",
        target_node_id="node-1",
        user_message="transfer and read artifact",
        trace_id="trace-domain-facts",
        metadata={},
    )

    await append_event_and_reduce(
        db_session,
        run,
        event_type="operation.succeeded",
        source="operation",
        payload={
            "operation_id": "op_transfer",
            "kind": "transfer",
            "status": "succeeded",
            "transfer": {
                "transfer_id": "trf_1",
                "status": "succeeded",
                "source_node_id": "winClient",
                "target_node_id": "linux-node-01",
                "source_path": "E:\\payload.zip",
                "target_path": "/home/yequdesu/payload.zip",
                "size_bytes": 123,
                "sha256": "abc",
                "resumable": True,
            },
        },
    )
    await append_event_and_reduce(
        db_session,
        run,
        event_type="artifact.read",
        source="agent.tool",
        payload={
            "artifact_id": "art_1",
            "title": "journal.log",
            "artifact_type": "log",
            "content_type": "text/plain",
            "line_range": {"start": 1, "end": 20},
            "matched_lines": {"count": 3},
            "truncated": True,
            "read_ref": "ctxref_1",
        },
    )

    state = get_task_state(run)
    transfer_facts = [item for item in state["facts"] if item["kind"] == "transfer"]
    assert transfer_facts
    assert transfer_facts[0]["data"]["transfer_id"] == "trf_1"
    assert transfer_facts[0]["data"]["target_node_id"] == "linux-node-01"
    artifact = state["artifacts"][0]
    assert artifact["artifact_id"] == "art_1"
    assert artifact["line_range"] == {"start": 1, "end": 20}
    assert artifact["matched_lines"] == {"count": 3}
    assert artifact["truncated"] is True
    assert artifact["read_ref"] == "ctxref_1"


@pytest.mark.asyncio
async def test_agent_run_reducer_uses_ycr_entities_for_artifacts_and_capabilities(
    db_session: AsyncSession,
) -> None:
    session_id = "sess_agent_run_ycr_entities"
    db_session.add(
        Session(
            session_id=session_id,
            actor_type="agent",
            actor_id="test-agent",
            status="active",
            execution_mode="auto",
            label="ycr-entities",
        )
    )
    run = await create_agent_run(
        db_session,
        session_id=session_id,
        provider_name="fake-events",
        execution_mode="auto",
        target_node_id="winClient",
        user_message="capture and show a screenshot",
        trace_id="trace-ycr-entities",
        metadata={},
    )

    await append_event_and_reduce(
        db_session,
        run,
        event_type="tool.completed",
        source="agent.tool",
        payload={
            "call_id": "call_screen",
            "function_name": "capability.invoke",
            "capability_ref": "screen.capture",
            "source_id": "src_screen",
            "target_node_id": "winClient",
            "status": "succeeded",
            "entities": {
                "artifacts": [
                    {
                        "artifact_id": "art_screen",
                        "artifact_type": "screenshot",
                        "title": "windows-screen-capture.png",
                        "content_type": "image/png",
                        "node_id": "winClient",
                        "status": "available",
                        "size_bytes": 1234,
                    }
                ],
                "capabilities": [
                    {
                        "capability_ref": "artifact.present",
                        "canonical_name": "artifact.present",
                        "source_id": "src_present",
                        "dispatchable": True,
                        "effect": "read",
                        "risk": "safe",
                    }
                ],
            },
        },
    )

    state = get_task_state(run)
    assert state["artifacts"][0]["artifact_id"] == "art_screen"
    assert state["working_set"]["artifacts"][0]["artifact_id"] == "art_screen"
    capability_refs = {
        item["capability_ref"] for item in state["working_set"]["capabilities"]
    }
    assert "screen.capture" in capability_refs
    assert "artifact.present" in capability_refs
    assert not [fact for fact in state["facts"] if fact["kind"] == "exec.run"]


@pytest.mark.asyncio
async def test_agent_run_reducer_extracts_exec_refs_tails_and_file_missing_fact(
    db_session: AsyncSession,
) -> None:
    session_id = "sess_agent_run_exec_facts"
    db_session.add(
        Session(
            session_id=session_id,
            actor_type="agent",
            actor_id="test-agent",
            status="active",
            execution_mode="auto",
            label="exec-facts",
        )
    )
    run = await create_agent_run(
        db_session,
        session_id=session_id,
        provider_name="fake-events",
        execution_mode="auto",
        target_node_id="node-1",
        user_message="read a missing file",
        trace_id="trace-exec-facts",
        metadata={},
    )

    await append_event_and_reduce(
        db_session,
        run,
        event_type="tool.failed",
        source="agent.tool",
        payload={
            "call_id": "call_missing",
            "function_name": "capability.invoke",
            "capability_ref": "linux.exec.run",
            "target_node_id": "node-1",
            "error_code": "execution_failed",
            "message": "command failed",
            "raw_ref_id": "ctxref_raw",
            "result": {
                "command": "cat /missing",
                "profile": "user.readonly",
                "exit_code": 1,
                "stdout_ref": "ctxref_stdout",
                "stderr_tail": "cat: /missing: No such file or directory",
            },
        },
    )

    state = get_task_state(run)
    exec_facts = [item for item in state["facts"] if item["kind"] == "exec.run"]
    assert exec_facts
    data = exec_facts[0]["data"]
    assert data["raw_ref_id"] == "ctxref_raw"
    assert data["stdout_ref"] == "ctxref_stdout"
    assert data["stderr_tail"] == "cat: /missing: No such file or directory"
    assert data["file_not_found"] is True
