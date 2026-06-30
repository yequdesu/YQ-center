from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.operation import Operation, OperationEvent
from yequ.services.operation_event_dispatcher import OperationEventDispatcher
from yequ.services.operation_scanner import OperationConsistencyScanner


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
