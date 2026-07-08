from __future__ import annotations

import os
import sys
import traceback
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_SITE_PACKAGES = PROJECT_ROOT / ".venv" / "Lib" / "site-packages"

for path in (
    PROJECT_ROOT,
    VENV_SITE_PACKAGES,
    VENV_SITE_PACKAGES / "win32",
    VENV_SITE_PACKAGES / "win32" / "lib",
    VENV_SITE_PACKAGES / "pythonwin",
):
    if path.exists():
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)

import servicemanager  # noqa: E402
import win32event  # noqa: E402
import win32service  # noqa: E402
import win32serviceutil  # noqa: E402


def _service_probe(message: str) -> None:
    try:
        log_dir = PROJECT_ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "service-startup.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now(UTC).isoformat()} {message}\n")
    except Exception:
        pass


class YeQuWinService(win32serviceutil.ServiceFramework):
    _svc_name_ = "YeQuWinClient"
    _svc_display_name_ = "YeQu Windows Client"
    _svc_description_ = "YeQu Windows Client daemon service."

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self._daemon = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)
        if self._daemon:
            self._daemon.stop()

    def SvcDoRun(self):
        import asyncio

        from node_win_client.config import load_settings
        from node_win_client.daemon import WinNodeDaemon
        from node_win_client.logging_config import setup_logging
        from node_win_client.state_store import StateStore

        os.chdir(PROJECT_ROOT)
        _service_probe("SvcDoRun entered")
        if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )

        config_path = PROJECT_ROOT / "config.local.yaml"
        store = None
        try:
            settings = load_settings(str(config_path))
            setup_logging(settings.paths.log_dir)
            store = StateStore(Path(settings.paths.data_dir) / "node_state.sqlite3")
            store.set_daemon_state("daemon_host", "service")
            _service_probe("settings and state store loaded")

            async def _run():
                self._daemon = WinNodeDaemon(settings, state_store=store)
                try:
                    await self._daemon.run()
                except Exception as e:
                    store.set_daemon_state("daemon_status", "error")
                    store.set_daemon_state("center_connected", "false")
                    _service_probe(f"daemon error: {e!r}")
                    servicemanager.LogErrorMsg(f"Daemon error: {e}")
                finally:
                    if self._daemon is not None:
                        await self._daemon.close()
                    store.close()

            asyncio.run(_run())
        except Exception as exc:
            _service_probe(f"SvcDoRun fatal: {exc!r}\n{traceback.format_exc()}")
            servicemanager.LogErrorMsg(f"Service fatal error: {exc}")
            if store is not None:
                with suppress(Exception):
                    store.set_daemon_state("daemon_status", "error")
                    store.set_daemon_state("center_connected", "false")
                with suppress(Exception):
                    store.close()
            raise


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(YeQuWinService)
