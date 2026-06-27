"""YQP message_id deduplication.

The production path uses the database-backed ``check_and_record_message`` helper so replay
protection survives process restarts and works across multiple Center workers. ``MessageDedup``
is kept for lightweight unit tests and compatibility with older imports.
"""

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.yqp_message import YqpMessage


class MessageDedup:
    """In-memory TTL cache for message_id deduplication.

    On cache hit (duplicate), returns False. On cache miss (new), records
    the message_id and returns True. Expired entries are lazily cleaned up.

    Thread-safe: no. Designed for single-worker async use.
    """

    def __init__(self, ttl_sec: int = 300) -> None:
        self._ttl_sec = ttl_sec
        self._cache: dict[str, float] = {}

    def check_and_record(self, message_id: str) -> bool:
        """Check if message_id is new and record it.

        Returns True if the message_id is new (should process).
        Returns False if the message_id was already seen (should skip).
        """
        now = time.monotonic()
        self._evict_expired(now)

        if message_id in self._cache:
            return False

        self._cache[message_id] = now
        return True

    def _evict_expired(self, now: float) -> None:
        """Remove expired entries from cache."""
        cutoff = now - self._ttl_sec
        expired = [mid for mid, ts in self._cache.items() if ts < cutoff]
        for mid in expired:
            del self._cache[mid]

    def clear(self) -> None:
        """Clear all cached entries (for testing)."""
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)


# Module-level singleton
_dedup: MessageDedup | None = None


def get_dedup() -> MessageDedup:
    """Return the singleton MessageDedup instance."""
    global _dedup
    if _dedup is None:
        from yequ.config import get_settings

        _dedup = MessageDedup(ttl_sec=get_settings().message_dedup_ttl_sec)
    return _dedup


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
    await db.execute(delete(YqpMessage).where(YqpMessage.expires_at <= current))

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
