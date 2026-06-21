"""Background liveness scanner — periodically detects stale nodes."""

import asyncio
from contextlib import suppress

from yequ.config import get_settings
from yequ.db import async_session_factory
from yequ.logconfig import get_logger
from yequ.services.node_liveness_service import mark_timed_out_nodes

log = get_logger(__name__)


class NodeLivenessScanner:
    """Background task that scans for stale nodes and marks them offline."""

    def __init__(self, interval_sec: int = 5) -> None:
        self._interval_sec = interval_sec
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background scan loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())
        log.info("node liveness scanner started", interval_sec=self._interval_sec)

    async def stop(self) -> None:
        """Stop the background scan loop."""
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        log.info("node liveness scanner stopped")

    async def _run(self) -> None:
        """Main loop — scan for stale nodes on interval."""
        while True:
            try:
                await asyncio.sleep(self._interval_sec)
                await self._scan()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("node liveness scanner error")

    async def _scan(self) -> None:
        """Scan for stale nodes and mark them offline."""
        settings = get_settings()
        async with async_session_factory() as db:
            timed_out = await mark_timed_out_nodes(db, settings)
            for node in timed_out:
                log.info(
                    "node liveness: marked offline",
                    node_id=node.node_id,
                    last_heartbeat_at=str(node.last_heartbeat_at),
                )


# Module-level singleton
_scanner: NodeLivenessScanner | None = None


def get_liveness_scanner(interval_sec: int = 5) -> NodeLivenessScanner:
    """Return the singleton NodeLivenessScanner instance."""
    global _scanner
    if _scanner is None:
        _scanner = NodeLivenessScanner(interval_sec=interval_sec)
    return _scanner
