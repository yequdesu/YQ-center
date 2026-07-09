from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.agent_turn import AgentTurn
from yequ.models.operation import Operation, OperationEvent
from yequ.models.ycr import YcrSessionState
from yequ.runtime.agent_plan_service import create_agent_plan, update_agent_plan_status
from yequ.runtime.agent_run_service import create_agent_run, update_agent_run_status
from yequ.runtime.task_state import get_task_state
from yequ.services.agent_operation_notifications import AgentOperationNotificationService
from yequ.services.agent_operation_reporter import (
    _operation_report_prompt,
    reconcile_waiting_operation_agent_state,
)
from yequ.services.operation_event_dispatcher import OperationEventDispatcher
from yequ.services.operation_scanner import OperationConsistencyScanner
from yequ.services.operation_service import OperationService


@pytest.mark.asyncio
async def test_operation_event_dispatcher_marks_pending_events_dispatched(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    db_session.add(
        Operation(
            operation_id="op_dispatch_test",
            kind="job",
            status="running",
            ref_type="job",
            ref_id="job_dispatch_test",
            actor_type="agent",
            actor_id="tester",
            started_at=now,
        )
    )
    db_session.add(
        OperationEvent(
            event_id="opevt_dispatch_test",
            operation_id="op_dispatch_test",
            seq=1,
            event_type="operation.created",
            status="running",
            data={},
            dispatch_status="pending",
            created_at=now,
        )
    )
    await db_session.commit()

    result = await OperationEventDispatcher(db_session).dispatch_pending()
    await db_session.commit()

    assert result == {"selected": 1, "dispatched": 1, "failed": 0}
    event = (
        await db_session.execute(
            select(OperationEvent).where(OperationEvent.event_id == "opevt_dispatch_test")
        )
    ).scalar_one()
    assert event.dispatch_status == "dispatched"
    assert event.dispatch_attempts == 1
    assert event.dispatched_at is not None


@pytest.mark.asyncio
async def test_operation_scanner_reports_and_cancels_stale_cancelling_operations(
    db_session: AsyncSession,
) -> None:
    old = datetime.now(UTC) - timedelta(hours=2)
    db_session.add(
        Operation(
            operation_id="op_cancel_timeout_test",
            kind="job",
            status="cancelling",
            ref_type="job",
            ref_id="job_cancel_timeout_test",
            actor_type="agent",
            actor_id="tester",
            started_at=old,
            updated_at=old,
        )
    )
    await db_session.commit()

    scanner = OperationConsistencyScanner(
        cancelling_timeout_sec=1,
        active_stuck_report_sec=3600,
    )
    report = await scanner.startup_recovery_report()
    await scanner._handle_stale_operations()

    assert report["active_operations_by_status"] == {"cancelling": 1}
    operation = (
        await db_session.execute(
            select(Operation).where(Operation.operation_id == "op_cancel_timeout_test")
        )
    ).scalar_one()
    assert operation.status == "cancelled"
    assert operation.error_code == "operation_cancel_timeout"
    event = (
        await db_session.execute(
            select(OperationEvent).where(
                OperationEvent.operation_id == "op_cancel_timeout_test"
            )
        )
    ).scalar_one()
    assert event.event_type == "operation.cancel_timeout"


@pytest.mark.asyncio
async def test_terminal_operation_event_enqueues_agent_notification_once(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    operation = Operation(
        operation_id="op_agent_notify_test",
        kind="transfer",
        status="running",
        ref_type="transfer_session",
        ref_id="trf_agent_notify_test",
        actor_type="agent",
        actor_id="tester",
        session_id="sess_agent_notify",
        started_at=now,
    )
    db_session.add(operation)
    await db_session.flush()

    service = OperationService(db_session)
    operation.status = "succeeded"
    operation.completed_at = now
    await service.append_event(
        operation,
        "operation.succeeded",
        {"ref_type": operation.ref_type, "ref_id": operation.ref_id},
    )
    await service.append_event(
        operation,
        "operation.succeeded",
        {"ref_type": operation.ref_type, "ref_id": operation.ref_id},
    )
    await db_session.commit()

    pending = await AgentOperationNotificationService(db_session).list_pending(
        session_id="sess_agent_notify"
    )
    assert len(pending) == 1
    assert pending[0]["operation_id"] == "op_agent_notify_test"
    assert pending[0]["operation_status"] == "succeeded"
    assert pending[0]["status"] == "pending"
    state = (
        await db_session.execute(
            select(YcrSessionState).where(
                YcrSessionState.session_id == "sess_agent_notify",
                YcrSessionState.entity_type == "operation",
                YcrSessionState.entity_key == "op_agent_notify_test",
            )
        )
    ).scalar_one()
    assert state.status == "succeeded"
    assert state.source_type == "transfer_session"
    assert state.source_id == "trf_agent_notify_test"

    claimed = await AgentOperationNotificationService(db_session).claim_next(
        session_id="sess_agent_notify"
    )
    assert claimed is not None
    assert claimed["notification_id"] == pending[0]["notification_id"]
    assert claimed["status"] == "processing"

    reported = await AgentOperationNotificationService(db_session).mark_reported(
        notification_id=str(claimed["notification_id"]),
        turn_id="turn_reported",
    )
    await db_session.commit()
    assert reported["status"] == "reported"
    assert reported["report_turn_id"] == "turn_reported"
    assert (
        await AgentOperationNotificationService(db_session).claim_next(
            session_id="sess_agent_notify"
        )
        is None
    )


@pytest.mark.asyncio
async def test_agent_operation_notification_claim_api(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    operation = Operation(
        operation_id="op_agent_notify_api",
        kind="transfer",
        status="succeeded",
        ref_type="transfer_session",
        ref_id="trf_agent_notify_api",
        actor_type="agent",
        actor_id="tester",
        session_id="sess_agent_notify_api",
        started_at=now,
        completed_at=now,
    )
    db_session.add(operation)
    await db_session.flush()
    await OperationService(db_session).append_event(
        operation,
        "operation.succeeded",
        {"ref_type": operation.ref_type, "ref_id": operation.ref_id},
    )
    await db_session.commit()

    claim = await client.post(
        "/agent/sessions/sess_agent_notify_api/operation-notifications/claim"
    )
    assert claim.status_code == 200
    notification = claim.json()["notification"]
    assert notification["operation_id"] == "op_agent_notify_api"
    assert notification["status"] == "processing"

    reported = await client.post(
        f"/agent/operation-notifications/{notification['notification_id']}/reported",
        json={"turn_id": "turn_api_reported"},
    )
    assert reported.status_code == 200
    assert reported.json()["notification"]["status"] == "reported"

    empty = await client.post(
        "/agent/sessions/sess_agent_notify_api/operation-notifications/claim"
    )
    assert empty.status_code == 200
    assert empty.json()["notification"] is None


@pytest.mark.asyncio
async def test_operation_report_reconciles_waiting_agent_state(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    waiting_run = await create_agent_run(
        db_session,
        session_id="sess_operation_reconcile",
        provider_name="fake",
        execution_mode="auto",
        target_node_id=None,
        user_message="start operation",
        trace_id="tr_waiting",
        metadata={"source": "test"},
    )
    waiting_plan = await create_agent_plan(
        db_session,
        session_id="sess_operation_reconcile",
        run_id=waiting_run.run_id,
        provider_name="fake",
        execution_mode="auto",
        target_node_id=None,
        objective="start operation",
    )
    waiting_run.turn_id = "turn_waiting"
    waiting_plan.turn_id = "turn_waiting"
    db_session.add(
        AgentTurn(
            turn_id="turn_waiting",
            session_id="sess_operation_reconcile",
            trace_id="tr_waiting",
            provider_name="fake",
            target_node_id=None,
            execution_mode="auto",
            status="waiting_operation",
            prompt="start operation",
            started_at=now,
            updated_at=now,
            metadata_={},
        )
    )
    await update_agent_run_status(
        db_session,
        waiting_run,
        status="waiting_operation",
        metadata={
            "plan_id": waiting_plan.plan_id,
            "waiting": {
                "status": "waiting_operation",
                "operation_id": "op_reconcile",
            },
        },
    )
    await update_agent_plan_status(
        db_session,
        waiting_plan.plan_id,
        status="waiting_operation",
        operation_id="op_reconcile",
    )
    report_run = await create_agent_run(
        db_session,
        session_id="sess_operation_reconcile",
        provider_name="fake",
        execution_mode="auto",
        target_node_id=None,
        user_message=None,
        trace_id="tr_report",
        metadata={"source": "agent.operation_reporter", "internal": True},
    )
    report_run.turn_id = "turn_report"
    await update_agent_run_status(
        db_session,
        report_run,
        status="succeeded",
        final_message="operation completed",
    )
    await db_session.flush()

    await reconcile_waiting_operation_agent_state(
        db_session,
        session_id="sess_operation_reconcile",
        operation_id="op_reconcile",
        report_turn_id="turn_report",
        operation_observation={
            "operation": {
                "operation_id": "op_reconcile",
                "status": "succeeded",
            }
        },
    )
    await db_session.flush()

    assert waiting_run.status == "succeeded"
    assert waiting_run.final_message == "operation completed"
    assert waiting_run.completed_at is not None
    state = get_task_state(waiting_run)
    assert state["completion"]["status"] == "complete"
    assert state["last_decision"]["action"] == "complete"
    assert waiting_plan.status == "succeeded"
    assert waiting_plan.completed_at is not None
    turn = (
        await db_session.execute(
            select(AgentTurn).where(AgentTurn.turn_id == "turn_waiting")
        )
    ).scalar_one()
    assert turn.status == "succeeded"
    assert turn.completed_at is not None


def test_operation_report_prompt_is_resume_prompt_not_report_only() -> None:
    prompt = _operation_report_prompt(
        {"operation": {"operation_id": "op_resume_prompt", "status": "succeeded"}},
        preferred_language="zh",
    )

    assert "恢复并推进用户任务" in prompt
    assert "继续使用当前可用工具完成任务" in prompt
    assert "不要调用工具" not in prompt
