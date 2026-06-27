"""Background scanner for expiring current Signal state."""

import asyncio
from contextlib import suppress

from yequ.db import async_session_factory
from yequ.logconfig import get_logger
from yequ.services.signal_state_service import mark_stale_signals

log = get_logger(__name__)


class SignalStateScanner:
    """Background task that marks expired SignalState rows as stale."""

    def __init__(self, interval_sec: int = 5) -> None:
        self._interval_sec = interval_sec
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background scan loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())
        log.info("signal state scanner started", interval_sec=self._interval_sec)

    async def stop(self) -> None:
        """Stop the background scan loop."""
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        log.info("signal state scanner stopped")

    async def _run(self) -> None:
        """Main loop that scans for expired SignalState rows."""
        while True:
            try:
                await asyncio.sleep(self._interval_sec)
                await self._scan()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("signal state scanner error")

    async def _scan(self) -> None:
        """Scan for expired current Signal states and mark them stale."""
        async with async_session_factory() as db:
            stale_signals = await mark_stale_signals(db)
            for signal in stale_signals:
                log.info(
                    "signal state: marked stale",
                    node_id=signal.node_id,
                    signal_name=signal.signal_name,
                    expires_at=str(signal.expires_at),
                )


_scanner: SignalStateScanner | None = None


def get_signal_state_scanner(interval_sec: int = 5) -> SignalStateScanner:
    """Return the singleton SignalStateScanner instance."""
    global _scanner
    if _scanner is None:
        _scanner = SignalStateScanner(interval_sec=interval_sec)
    return _scanner
