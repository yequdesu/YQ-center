"""Lightweight async pub/sub event bus for SSE streaming."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    """Publish/subscribe event bus. Subscribers get events via async queues."""

    def __init__(self):
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []

    async def publish(self, event: dict[str, Any]) -> None:
        """Push an event to all current subscribers."""
        dead = []
        for i, q in enumerate(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(i)
        # Clean up dead subscribers
        for i in reversed(dead):
            self._subscribers.pop(i)

    async def subscribe(self) -> "asyncio.Queue[dict[str, Any]]":
        """Create a new subscriber queue. Returns immediately."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a subscriber queue."""
        try:
            self._subscribers.remove(q)
        except ValueError:
            logger.debug("Unsubscribe: queue not found in subscribers")

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# Singleton for the Gateway process
bus = EventBus()
