"""Redis Stream message queue with memory fallback."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class MessageQueue:
    """Redis Stream producer/consumer with automatic memory fallback."""

    def __init__(self):
        self._redis = None
        self._fallback: list[dict] = []
        self._subscribers: list[asyncio.Queue] = []
        self._config = None

    @property
    def available(self) -> bool:
        return self._redis is not None

    def configure(self, redis_config) -> None:
        """Set Redis config and attempt connection."""
        self._config = redis_config
        if redis_config is None or not getattr(redis_config, 'enabled', True):
            logger.info("MQ: Redis disabled, using memory fallback")
            return
        try:
            import redis
            self._redis = redis.Redis(
                host=redis_config.host,
                port=redis_config.port,
                db=redis_config.db,
                password=redis_config.password or None,
                socket_connect_timeout=2,
                decode_responses=True,
            )
            self._redis.ping()
            logger.info("MQ: Redis connected %s:%d", redis_config.host, redis_config.port)
        except Exception as e:
            logger.warning("MQ: Redis unavailable (%s), using memory fallback", e)
            self._redis = None

    def publish(self, stream: str, event: dict) -> None:
        """Publish an event to a Redis Stream. Falls back to memory if Redis is down."""
        from yequ.utils import now_iso
        event["timestamp"] = event.get("timestamp") or now_iso()
        if self._redis is not None:
            try:
                self._redis.xadd(stream, event, maxlen=10000)
            except Exception as e:
                logger.warning("MQ: Redis publish failed (%s), using fallback", e)
                self._fallback.append({"stream": stream, "event": event})
        else:
            self._fallback.append({"stream": stream, "event": event})
        # Always notify in-memory subscribers (for SSE)
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe(self) -> "asyncio.Queue[dict]":
        """Create a subscriber queue for in-memory SSE delivery."""
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a subscriber queue."""
        try:
            self._subscribers.remove(q)
        except ValueError:
            logger.debug("Unsubscribe: queue not found")

    async def consume(self, stream: str, last_id: str = "$",
                      block_ms: int = 2000, count: int = 10) -> list[dict]:
        """Consume events from a Redis Stream. Falls back to memory."""
        if self._redis is None:
            drained = self._fallback[:]
            self._fallback.clear()
            return [m["event"] for m in drained if m["stream"] == stream]
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._redis.xread(
                    {stream: last_id}, block=block_ms, count=count))
            events = []
            for _stream, entries in result:
                for msg_id, data in entries:
                    events.append(data)
            return events
        except Exception as e:
            logger.warning("MQ: consume error (%s)", e)
            return []


# Singleton
mq = MessageQueue()
