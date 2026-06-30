"""Database-backed YQP message_id deduplication."""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yequ import db as yequ_db
from yequ.logconfig import get_logger
from yequ.models.yqp_message import YqpMessage

log = get_logger(__name__)
_cleanup_interval_sec = 60.0
_cleanup_batch_size = 1000


async def check_and_record_message(
    db: AsyncSession,
    *,
    message_id: str,
    node_id: str,
    message_type: str,
    trace_id: str | None,
    ttl_sec: int,
    now: datetime | None = None,
) -> bool:
    """Return True when a YQP message is new, False when it is a duplicate."""
    current = now or datetime.now(UTC)

    db.add(
        YqpMessage(
            message_id=message_id,
            node_id=node_id,
            message_type=message_type,
            trace_id=trace_id,
            received_at=current,
            expires_at=current + timedelta(seconds=ttl_sec),
        )
    )
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return False
    return True


class YqpMessageCleanupScanner:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="yqp-message-cleanup-scanner")
        log.info("yqp message cleanup scanner started")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        log.info("yqp message cleanup scanner stopped")

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.sleep(_cleanup_interval_sec)
                await cleanup_expired_messages_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("yqp message cleanup scanner error")


async def cleanup_expired_messages_once(
    current: datetime | None = None,
    *,
    batch_size: int = _cleanup_batch_size,
) -> int:
    cutoff = current or datetime.now(UTC)
    async with yequ_db.async_session_factory() as db:
        expired_ids = (
            select(YqpMessage.message_id)
            .where(YqpMessage.expires_at <= cutoff)
            .order_by(YqpMessage.expires_at.asc())
            .limit(batch_size)
        )
        result = await db.execute(delete(YqpMessage).where(YqpMessage.message_id.in_(expired_ids)))
        await db.commit()
        deleted = int(result.rowcount or 0)
        if deleted:
            log.info("expired yqp message dedup records deleted", count=deleted)
        return deleted


_cleanup_scanner: YqpMessageCleanupScanner | None = None


def get_yqp_message_cleanup_scanner() -> YqpMessageCleanupScanner:
    global _cleanup_scanner
    if _cleanup_scanner is None:
        _cleanup_scanner = YqpMessageCleanupScanner()
    return _cleanup_scanner
