"""OperationEvent outbox dispatcher.

The database is the source of truth. This dispatcher advances pending
OperationEvent rows through a backend boundary so a real MQ publisher can be
plugged in later without creating a second fact store.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.operation import OperationEvent


class OperationEventBackend(Protocol):
    async def dispatch(self, event: OperationEvent) -> None: ...


class PostgresLocalOperationEventBackend:
    """Local backend used before an external MQ exists.

    Dispatching means the event is durably present in PostgreSQL and available
    for API/UI projections. No network side effect is performed here.
    """

    async def dispatch(self, event: OperationEvent) -> None:
        return None


class OperationEventDispatcher:
    def __init__(
        self,
        db: AsyncSession,
        *,
        backend: OperationEventBackend | None = None,
    ) -> None:
        self.db = db
        self.backend = backend or PostgresLocalOperationEventBackend()

    async def dispatch_pending(self, *, limit: int = 100) -> dict[str, int]:
        result = await self.db.execute(
            select(OperationEvent)
            .where(OperationEvent.dispatch_status.in_(["pending", "retry"]))
            .order_by(OperationEvent.created_at.asc(), OperationEvent.seq.asc())
            .limit(limit)
        )
        events = list(result.scalars().all())
        dispatched = 0
        failed = 0
        now = datetime.now(UTC)
        for event in events:
            event.dispatch_attempts = int(event.dispatch_attempts or 0) + 1
            try:
                await self.backend.dispatch(event)
            except Exception as exc:
                event.dispatch_status = "retry"
                event.last_dispatch_error = str(exc)
                failed += 1
                continue
            event.dispatch_status = "dispatched"
            event.dispatched_at = now
            event.last_dispatch_error = None
            dispatched += 1
        await self.db.flush()
        return {
            "selected": len(events),
            "dispatched": dispatched,
            "failed": failed,
        }
