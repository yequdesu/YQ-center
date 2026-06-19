"""Background timeout scanner — periodically checks for expired job leases."""

import asyncio
from contextlib import suppress

from yequ.db import async_session_factory
from yequ.logconfig import get_logger
from yequ.services.job_service import find_expired_jobs, timeout_job

log = get_logger(__name__)


class TimeoutScanner:
    """Background task that scans for expired job leases and times them out."""

    def __init__(self, interval_sec: int = 5) -> None:
        self._interval_sec = interval_sec
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background scan loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())
        log.info("timeout scanner started", interval_sec=self._interval_sec)

    async def stop(self) -> None:
        """Stop the background scan loop."""
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        log.info("timeout scanner stopped")

    async def _run(self) -> None:
        """Main loop — scan for expired jobs on interval."""
        while True:
            try:
                await asyncio.sleep(self._interval_sec)
                await self._scan()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("timeout scanner error")

    async def _scan(self) -> None:
        """Scan for expired jobs and mark them timeout."""
        async with async_session_factory() as db:
            jobs = await find_expired_jobs(db)
            for job in jobs:
                await timeout_job(db, job, node_id="timeout_scanner")
                log.info(
                    "job timed out",
                    job_id=job.job_id,
                    node_id=job.node_id,
                )
            if jobs:
                await db.commit()


# Module-level singleton
_scanner: TimeoutScanner | None = None


def get_scanner(interval_sec: int = 5) -> TimeoutScanner:
    """Return the singleton TimeoutScanner instance."""
    global _scanner
    if _scanner is None:
        _scanner = TimeoutScanner(interval_sec=interval_sec)
    return _scanner
