"""Background scanner for Operation projection and runtime cleanup."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

import yequ.db as yequ_db
from yequ.logconfig import get_logger
from yequ.models.operation import Operation, OperationEvent
from yequ.services.operation_event_dispatcher import OperationEventDispatcher
from yequ.services.operation_service import TERMINAL_OPERATION_STATUSES, OperationService
from yequ.services.resource_lock_service import release_locks_for_terminal_jobs

log = get_logger(__name__)


class OperationConsistencyScanner:
    """Keep Operation projections and terminal job locks consistent."""

    def __init__(
        self,
        interval_sec: int = 10,
        batch_size: int = 50,
        *,
        cancelling_timeout_sec: int = 600,
        active_stuck_report_sec: int = 3600,
    ) -> None:
        self._interval_sec = interval_sec
        self._batch_size = batch_size
        self._cancelling_timeout_sec = cancelling_timeout_sec
        self._active_stuck_report_sec = active_stuck_report_sec
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        report = await self.startup_recovery_report()
        self._task = asyncio.create_task(self._run(), name="operation-consistency-scanner")
        log.info(
            "operation consistency scanner started",
            interval_sec=self._interval_sec,
            startup_report=report,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        log.info("operation consistency scanner stopped")

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval_sec)
                await self._scan()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("operation consistency scanner error")

    async def _scan(self) -> None:
        await self._release_terminal_job_locks()
        await self._sync_active_operations()
        await self._handle_stale_operations()
        await self._dispatch_operation_events()

    async def startup_recovery_report(self) -> dict[str, object]:
        async with yequ_db.async_session_factory() as db:
            active_result = await db.execute(
                select(Operation.status, func.count())
                .where(Operation.status.notin_(TERMINAL_OPERATION_STATUSES))
                .group_by(Operation.status)
                .order_by(Operation.status.asc())
            )
            pending_events_result = await db.execute(
                select(func.count()).where(
                    OperationEvent.dispatch_status.in_(["pending", "retry"])
                )
            )
            return {
                "active_operations_by_status": {
                    str(status): int(count) for status, count in active_result.all()
                },
                "pending_operation_events": int(pending_events_result.scalar() or 0),
            }

    async def _release_terminal_job_locks(self) -> None:
        async with yequ_db.async_session_factory() as db:
            released = await release_locks_for_terminal_jobs(db)
            if released:
                await db.commit()
                log.info("released stale terminal job locks", count=released)

    async def _sync_active_operations(self) -> None:
        async with yequ_db.async_session_factory() as db:
            result = await db.execute(
                select(Operation.operation_id)
                .where(Operation.status.notin_(TERMINAL_OPERATION_STATUSES))
                .order_by(Operation.created_at.asc())
                .limit(self._batch_size)
            )
            operation_ids = [str(row[0]) for row in result.all()]

        for operation_id in operation_ids:
            try:
                async with yequ_db.async_session_factory() as db:
                    await OperationService(db).status(operation_id)
            except Exception:
                log.exception("operation projection sync failed", operation_id=operation_id)

    async def _handle_stale_operations(self) -> None:
        now = datetime.now(UTC)
        cancelling_cutoff = now - timedelta(seconds=self._cancelling_timeout_sec)
        active_cutoff = now - timedelta(seconds=self._active_stuck_report_sec)
        async with yequ_db.async_session_factory() as db:
            service = OperationService(db)
            cancelling_result = await db.execute(
                select(Operation)
                .where(Operation.status == "cancelling")
                .where(Operation.updated_at < cancelling_cutoff)
                .order_by(Operation.updated_at.asc())
                .limit(self._batch_size)
            )
            cancelling = list(cancelling_result.scalars().all())
            for operation in cancelling:
                operation.status = "cancelled"
                operation.completed_at = now
                operation.error_code = "operation_cancel_timeout"
                operation.error_message = (
                    f"Operation stayed cancelling for more than "
                    f"{self._cancelling_timeout_sec} seconds."
                )
                await service.append_event(
                    operation,
                    "operation.cancel_timeout",
                    {"timeout_sec": self._cancelling_timeout_sec},
                )

            active_result = await db.execute(
                select(Operation)
                .where(Operation.status.in_(["queued", "running"]))
                .where(Operation.updated_at < active_cutoff)
                .order_by(Operation.updated_at.asc())
                .limit(self._batch_size)
            )
            active = list(active_result.scalars().all())
            for operation in active:
                metadata = dict(operation.metadata_json or {})
                if metadata.get("stuck_reported_at"):
                    continue
                metadata["stuck_reported_at"] = now.isoformat()
                operation.metadata_json = metadata
                await service.append_event(
                    operation,
                    "operation.stuck_detected",
                    {"stuck_after_sec": self._active_stuck_report_sec},
                )

            if cancelling or active:
                await db.commit()
                log.warning(
                    "operation scanner detected stale operations",
                    cancelling_count=len(cancelling),
                    stuck_count=len(active),
                )

    async def _dispatch_operation_events(self) -> None:
        async with yequ_db.async_session_factory() as db:
            result = await OperationEventDispatcher(db).dispatch_pending(
                limit=self._batch_size * 2
            )
            if result["selected"]:
                await db.commit()
                log.info("operation events dispatched", **result)


_scanner: OperationConsistencyScanner | None = None


def get_operation_consistency_scanner(
    interval_sec: int = 10,
) -> OperationConsistencyScanner:
    global _scanner
    if _scanner is None:
        _scanner = OperationConsistencyScanner(interval_sec=interval_sec)
    return _scanner
