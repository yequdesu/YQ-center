import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.session import Session
from yequ.runtime.agent_plan_service import create_agent_plan, get_agent_plan_projection
from yequ.runtime.agent_run_resume import append_event_and_reduce
from yequ.runtime.agent_run_service import create_agent_run
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
