from __future__ import annotations

import asyncio
import os
import sys
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any

import webview

from .config import load_settings
from .daemon import WinNodeDaemon
from .gui_bridge import GuiBridge


class DaemonRunner:
    def __init__(self, config_path: str = "config.local.yaml") -> None:
        self.config_path = config_path
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._daemon: WinNodeDaemon | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._set_runner_state("starting", "false")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _set_runner_state(self, status: str, connected: str) -> None:
        try:
            from .state_store import StateStore

            settings = load_settings(self.config_path)
            store = StateStore(f"{settings.paths.data_dir}/node_state.sqlite3")
            try:
                store.set_daemon_state("daemon_status", status)
                store.set_daemon_state("center_connected", connected)
                store.set_daemon_state("daemon_host", "desktop")
            finally:
                store.close()
        except Exception:
            return

    def _run(self) -> None:
        if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._daemon_loop())
        except Exception:
            pass
        finally:
            self._running = False

    async def _daemon_loop(self) -> None:
        from .logging_config import get_logger, setup_logging
        from .state_store import StateStore

        setup_logging()
        logger = get_logger("daemon")

        try:
            settings = load_settings(self.config_path)
        except Exception as e:
            logger.error("Failed to load config: %s", e)
            return

        store = StateStore(f"{settings.paths.data_dir}/node_state.sqlite3")
        store.set_daemon_state("daemon_status", "starting")
        store.set_daemon_state("center_connected", "false")
        store.set_daemon_state("daemon_host", "desktop")

        try:
            self._daemon = WinNodeDaemon(settings, state_store=store)
            await self._daemon.run()

        except Exception as e:
            logger.error("Daemon error: %s", e)
            store.set_daemon_state("daemon_status", "error")
            store.set_daemon_state("center_connected", "false")
        finally:
            if self._daemon:
                await self._daemon.close()
            store.close()

    def stop(self) -> None:
        if not self._running:
            self._set_runner_state("stopped", "false")
            return
        self._set_runner_state("stopping", "false")
        self._running = False
        if self._daemon:
            self._daemon.stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        if not self._thread or not self._thread.is_alive():
            self._set_runner_state("stopped", "false")

    @property
    def is_running(self) -> bool:
        return self._running


class Api:
    def __init__(self, bridge: GuiBridge) -> None:
        self._bridge = bridge
        self._runner = DaemonRunner(str(bridge.config_path))

    def get_config(self) -> dict:
        return self._bridge.get_config()

    def save_config(self, config_text: str) -> dict:
        return self._bridge.save_config(config_text)

    def validate_config(self, config_text: str = "") -> dict:
        return self._bridge.validate_config(config_text or None)

    def get_local_status(self) -> dict:
        return self._bridge.get_local_status()

    def get_service_status(self) -> dict:
        return self._bridge.get_service_status_bridge()

    def get_runtime_status(self) -> dict:
        return self._bridge.get_runtime_status()

    def get_gui_autostart(self) -> dict:
        return self._bridge.get_gui_autostart()

    def set_gui_autostart(self, enabled: bool) -> dict:
        return self._bridge.set_gui_autostart(enabled)

    def install_service(self, account_mode: str = "LocalSystem") -> dict:
        return self._bridge.install_service_bridge(account_mode or "LocalSystem")

    def install_user_worker(self) -> dict:
        return self._bridge.install_user_worker_bridge()

    def start_user_worker(self) -> dict:
        return self._bridge.start_user_worker_bridge()

    def stop_user_worker(self) -> dict:
        return self._bridge.stop_user_worker_bridge()

    def uninstall_service(self) -> dict:
        return self._bridge.uninstall_service_bridge()

    def start_service(self) -> dict:
        return self._bridge.start_service_bridge()

    def stop_service(self) -> dict:
        return self._bridge.stop_service_bridge()

    def restart_service(self) -> dict:
        return self._bridge.restart_service_bridge()

    def start_daemon(self) -> dict:
        allowed, reason = self._bridge.can_start_desktop_daemon()
        if not allowed:
            return {
                "ok": False,
                "data": None,
                "error": {
                    "code": "DESKTOP_DAEMON_DISABLED",
                    "message": reason or "Developer Local Daemon is disabled.",
                },
            }
        self._runner.start()
        return self._bridge.get_daemon_status()

    def stop_daemon(self) -> dict:
        self._runner.stop()
        return self._bridge.get_daemon_status()

    def get_capabilities(self) -> dict:
        return self._bridge.get_capabilities()

    def get_recent_jobs(self, limit: int = 50) -> dict:
        return self._bridge.get_recent_jobs(limit)

    def retry_unreported_jobs(self) -> dict:
        return self._bridge.retry_unreported_jobs()

    def flush_reported_cache(self) -> dict:
        return self._bridge.flush_reported_cache()

    def get_logs(self, limit: int = 500, level: str = "", query: str = "") -> dict:
        return self._bridge.get_logs(
            limit=limit,
            level=level or None,
            query=query or None,
        )

    def export_diagnostics(self) -> dict:
        return self._bridge.export_diagnostics_bridge()

    def open_path(self, path: str) -> dict:
        return self._bridge.open_path(path)

    def test_center_health(self) -> dict:
        return self._bridge.test_center_health()

    def get_daemon_status(self) -> dict:
        return self._bridge.get_daemon_status()


class TrayController:
    def __init__(self, window: Any, api: Api, close_to_tray: bool) -> None:
        self.window = window
        self.api = api
        self.close_to_tray = close_to_tray
        self._icon: Any = None
        self._thread: threading.Thread | None = None
        self._exiting = False

    def start(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception:
            return

        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, 56, 56), radius=12, fill=(36, 113, 163, 255))
        draw.line((22, 21, 42, 21, 42, 43, 22, 43, 22, 21), fill=(255, 255, 255, 255), width=5)

        self._icon = pystray.Icon(
            "YeQuWinClient",
            image,
            "YeQu Windows Client",
            menu=pystray.Menu(
                pystray.MenuItem("Show", self.show_window, default=True),
                pystray.MenuItem("Start Service", self.start_service),
                pystray.MenuItem("Stop Service", self.stop_service),
                pystray.MenuItem("Restart Service", self.restart_service),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit GUI", self.exit_gui),
            ),
        )
        self._thread = threading.Thread(target=self._icon.run, name="yequ-tray", daemon=True)
        self._thread.start()

    def handle_closing(self, *_args: Any) -> bool:
        if self._exiting or not self.close_to_tray:
            return True
        with suppress(Exception):
            self.window.hide()
        return False

    def show_window(self, *_args: Any) -> None:
        try:
            self.window.show()
            self.window.restore()
        except Exception:
            with suppress(Exception):
                self.window.show()

    def start_service(self, *_args: Any) -> None:
        self.api.start_service()

    def stop_service(self, *_args: Any) -> None:
        self.api.stop_service()

    def restart_service(self, *_args: Any) -> None:
        self.api.restart_service()

    def exit_gui(self, *_args: Any) -> None:
        self._exiting = True
        with suppress(Exception):
            self.api.stop_daemon()
        try:
            if self._icon is not None:
                self._icon.stop()
        except Exception:
            pass
        try:
            self.window.destroy()
        except Exception:
            os._exit(0)


def get_frontend_path() -> Path:
    project_root = Path(__file__).resolve().parent.parent
    dist = project_root / "desktop-ui" / "dist" / "index.html"
    if dist.exists():
        return dist
    raise FileNotFoundError(
        "Frontend not found. Build with: cd desktop-ui && npm run build"
    )


def launch_gui(config_path: str = "config.local.yaml") -> None:
    bridge = GuiBridge(config_path)
    api = Api(bridge)
    frontend_path = get_frontend_path()
    close_to_tray = True
    with suppress(Exception):
        close_to_tray = load_settings(config_path).runtime.close_to_tray

    window = webview.create_window(
        title="YeQu Windows Client",
        url=str(frontend_path),
        js_api=api,
        width=1080,
        height=720,
        min_size=(920, 620),
        resizable=True,
    )

    tray = TrayController(window, api, close_to_tray=close_to_tray)
    with suppress(Exception):
        window.events.closing += tray.handle_closing

    def _on_started() -> None:
        tray.start()

    webview.start(_on_started)
