"""Timeline event insertion helpers.

PostgreSQL production uses a native sequence for global_seq allocation.
SQLite is only used in tests and keeps a deterministic process-local fallback.
"""

import asyncio

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.timeline import TimelineEvent

POSTGRES_TIMELINE_SEQUENCE = "timeline_global_seq"
_sqlite_sequence_lock = asyncio.Lock()


def _dialect_name(db: AsyncSession) -> str:
    bind = db.get_bind()
    return bind.dialect.name if bind is not None else ""


async def allocate_global_seq_values(db: AsyncSession, count: int = 1) -> list[int]:
    """Reserve timeline global_seq values."""
    if count < 1:
        raise ValueError("count must be >= 1")

    if _dialect_name(db) == "postgresql":
        result = await db.execute(
            text(
                f"SELECT nextval('{POSTGRES_TIMELINE_SEQUENCE}') AS global_seq "
                "FROM generate_series(1, :count)"
            ),
            {"count": count},
        )
        values = [int(value) for value in result.scalars().all()]
        if len(values) != count:
            raise RuntimeError("timeline sequence allocation returned an unexpected count")
        return values

    async with _sqlite_sequence_lock:
        result = await db.execute(select(func.max(TimelineEvent.global_seq)))
        max_seq = result.scalar() or 0
        start = int(max_seq) + 1
        return list(range(start, start + count))


async def allocate_global_seq(db: AsyncSession, count: int = 1) -> int:
    """Reserve one timeline global_seq value and return it."""
    if count != 1:
        raise ValueError("use allocate_global_seq_values() for batch allocation")
    return (await allocate_global_seq_values(db, 1))[0]


async def add_timeline_event(db: AsyncSession, event: TimelineEvent) -> TimelineEvent:
    """Assign global_seq and add one TimelineEvent to the current transaction."""
    event.global_seq = await allocate_global_seq(db)
    db.add(event)
    await db.flush()
    return event


async def add_timeline_events(
    db: AsyncSession,
    events: list[TimelineEvent],
) -> list[TimelineEvent]:
    """Assign monotonic global_seq values and add TimelineEvents."""
    if not events:
        return events
    values = await allocate_global_seq_values(db, len(events))
    for event, global_seq in zip(events, values, strict=True):
        event.global_seq = global_seq
    db.add_all(events)
    await db.flush()
    return events
