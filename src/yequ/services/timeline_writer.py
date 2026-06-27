"""Async Timeline writer -- buffers events and batch-writes in background.

Decouples TimelineEvent insertion from the YQP request path.
Heartbeat and signal.report events are enqueued as fire-and-forget.
Job state transition events remain synchronous for correctness.
"""

import asyncio
import contextlib

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.db import async_session_factory
from yequ.logconfig import get_logger
from yequ.models.timeline import TimelineEvent, TimelineSequence

log = get_logger(__name__)

GLOBAL_TIMELINE_SEQUENCE = "global"
_sequence_lock = asyncio.Lock()


async def next_global_seq(db: AsyncSession) -> int:
    """Return the next available global_seq for a new timeline event."""
    return await allocate_global_seq(db)


async def allocate_global_seq(db: AsyncSession, count: int = 1) -> int:
    """Reserve one or more timeline global_seq values.

    Returns the first reserved sequence number. PostgreSQL uses a row lock;
    the process lock keeps SQLite/test execution deterministic.
    """
    if count < 1:
        raise ValueError("count must be >= 1")

    async with _sequence_lock:
        result = await db.execute(
            select(TimelineSequence)
            .where(TimelineSequence.name == GLOBAL_TIMELINE_SEQUENCE)
            .with_for_update()
        )
        sequence = result.scalar_one_or_none()
        if sequence is None:
            max_result = await db.execute(select(func.max(TimelineEvent.global_seq)))
            max_seq = max_result.scalar() or 0
            sequence = TimelineSequence(
                name=GLOBAL_TIMELINE_SEQUENCE,
                value=max_seq,
            )
            db.add(sequence)
            await db.flush()

        start = sequence.value + 1
        sequence.value += count
        await db.flush()
        return start


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
    """Assign contiguous global_seq values and add TimelineEvents."""
    if not events:
        return events
    start = await allocate_global_seq(db, len(events))
    for offset, event in enumerate(events):
        event.global_seq = start + offset
    db.add_all(events)
    await db.flush()
    return events


# Max batch size for a single DB transaction
BATCH_SIZE = 50
# Flush interval when queue is not full
FLUSH_INTERVAL_SEC = 1.0


class TimelineWriter:
    """Background writer that batches TimelineEvent inserts.

    Events are enqueued via enqueue(). The worker loop dequeues
    and batch-inserts them into the DB. Write failures are logged
    but never propagated back to the caller.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[TimelineEvent] = asyncio.Queue(maxsize=1000)
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background worker."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())
        log.info("timeline writer started")

    async def stop(self) -> None:
        """Stop the background worker, flush remaining events."""
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        # Final flush
        await self._flush(force=True)
        log.info("timeline writer stopped")

    def enqueue(self, event: TimelineEvent) -> None:
        """Enqueue a TimelineEvent for async writing.

        Non-blocking. If the queue is full, logs a warning and
        drops the event (safety valve -- don't block the request path).
        """
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            log.warning(
                "timeline queue full, dropping event",
                event_type=event.event_type,
            )

    async def _run(self) -> None:
        """Main loop -- flush every FLUSH_INTERVAL_SEC or when batch is full."""
        while True:
            try:
                await asyncio.sleep(FLUSH_INTERVAL_SEC)
                await self._flush()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("timeline writer flush error")

    async def _flush(self, force: bool = False) -> None:
        """Drain queue and batch-insert events."""
        batch: list[TimelineEvent] = []
        while len(batch) < BATCH_SIZE:
            try:
                event = self._queue.get_nowait()
                batch.append(event)
            except asyncio.QueueEmpty:
                break

        if not batch:
            return

        try:
            async with async_session_factory() as db:
                await add_timeline_events(db, batch)
                await db.commit()
                log.debug("timeline writer flushed", count=len(batch))
        except Exception:
            log.exception("timeline writer batch insert failed", count=len(batch))


# Module-level singleton
_writer: TimelineWriter | None = None


def get_timeline_writer() -> TimelineWriter:
    """Return the singleton TimelineWriter."""
    global _writer
    if _writer is None:
        _writer = TimelineWriter()
    return _writer
