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
from yequ.models.timeline import TimelineEvent

log = get_logger(__name__)


async def next_global_seq(db: AsyncSession) -> int:
    """Return the next available global_seq for a new timeline event."""
    result = await db.execute(select(func.max(TimelineEvent.global_seq)))
    return (result.scalar() or 0) + 1

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
                # Compute global_seq range for this batch
                from sqlalchemy import func, select

                result = await db.execute(select(func.max(TimelineEvent.global_seq)))
                max_seq = result.scalar() or 0

                for i, event in enumerate(batch):
                    event.global_seq = max_seq + i + 1

                db.add_all(batch)
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
