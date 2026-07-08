from __future__ import annotations

import asyncio
import os
import sys
import winreg
from contextlib import suppress
from pathlib import Path
from typing import Any

from .config import load_settings
from .config import validate_config as validate_config_fn
from .diagnostics import export_diagnostics
from .plugins import FakeSystemPlugin
from .service import (
    get_service_status,
    install_service,
    install_user_worker,
    restart_service,
    start_service,
    start_user_worker,
    stop_service,
    stop_user_worker,
    uninstall_service,
)
from .state_store import StateStore


class GuiBridge:
    def __init__(self, config_path: str = "config.local.yaml") -> None:
        self.config_path = Path(config_path)
        self._project_root = Path(__file__).resolve().parent.parent
        self._data_dir = self._project_root / "data"
        self._log_dir = self._project_root / "logs"
        self._store: StateStore | None = None

    @property
    def store(self) -> StateStore:
        if self._store is None:
            self._store = StateStore(str(self._data_dir / "node_state.sqlite3"))
        return self._store

    def _bridge_response(self, ok: bool, data: Any = None, error: dict | None = None) -> dict:
        return {"ok": ok, "data": data, "error": error}

    def get_config(self) -> dict:
        try:
            if self.config_path.exists():
                raw = self.config_path.read_text(encoding="utf-8")
                return self._bridge_response(True, {"raw": raw, "path": str(self.config_path)})
            return self._bridge_response(True, {"raw": "", "path": str(self.config_path)})
        except Exception as e:
            return self._bridge_response(
                False,
                error={"code": "CONFIG_READ_ERROR", "message": str(e)},
            )

    def save_config(self, config_text: str) -> dict:
        try:
            self.config_path.write_text(config_text, encoding="utf-8")
            return self._bridge_response(True, {"path": str(self.config_path)})
        except Exception as e:
            return self._bridge_response(
                False,
                error={"code": "CONFIG_SAVE_ERROR", "message": str(e)},
            )

    def validate_config(self, config_text: str | None = None) -> dict:
        try:
            path = self.config_path
            if config_text:
                temp_path = self._data_dir / "_temp_validate.yaml"
                temp_path.parent.mkdir(parents=True, exist_ok=True)
                temp_path.write_text(config_text, encoding="utf-8")
                path = temp_path
            validate_config_fn(str(path))
            return self._bridge_response(True, {"valid": True})
        except Exception as e:
            return self._bridge_response(False, error={"code": "CONFIG_INVALID", "message": str(e)})

    def get_local_status(self) -> dict:
        try:
            settings = load_settings(str(self.config_path)) if self.config_path.exists() else None
            svc = get_service_status()
            daemon = self.get_daemon_status_data()
            runtime = self._runtime_status(settings, svc, daemon)
            data = {
                "configured": self.config_path.exists(),
                "config_path": str(self.config_path),
                "node_id": settings.node_id if settings else None,
                "service": svc,
                "daemon_running": daemon.get("daemon_running", False) or svc.get("running", False),
                "desktop_daemon_running": daemon.get("daemon_running", False)
                and daemon.get("daemon_host") == "desktop",
                "runtime": runtime,
                "center_connected": daemon.get("center_connected", False),
                "center_reachable": daemon.get("center_reachable", False),
                "daemon_status": daemon.get("daemon_status", "unknown"),
                "daemon_host": daemon.get("daemon_host", "unknown"),
                "last_heartbeat": daemon.get("last_heartbeat", None),
                "last_capability_register": daemon.get("last_capability_register", None),
                "capability_count": daemon.get("capability_count", None),
                "reconnect_attempts": daemon.get("reconnect_attempts", "0"),
                "last_error": daemon.get("last_error", ""),
            }
            return self._bridge_response(True, data)
        except Exception as e:
            return self._bridge_response(False, error={"code": "STATUS_ERROR", "message": str(e)})

    def get_daemon_status_data(self) -> dict:
        try:
            state = self.store.get_all_daemon_state()
            daemon_status = state.get("daemon_status", "unknown")
            daemon_running = daemon_status in ("starting", "running", "connected")
            reconnect_attempts = state.get("reconnect_attempts", "0") if daemon_running else "0"
            last_error = (
                state.get("last_error", "")
                if daemon_running or daemon_status == "error"
                else ""
            )
            return {
                "daemon_running": daemon_running,
                "daemon_status": daemon_status,
                "daemon_host": state.get("daemon_host", "unknown"),
                "center_connected": daemon_running and state.get("center_connected") == "true",
                "center_reachable": (
                    state.get("center_connected") == "true"
                    or state.get("center_reachable") == "true"
                ),
                "last_heartbeat": state.get("last_heartbeat", None),
                "last_capability_register": state.get("last_capability_register", None),
                "capability_count": state.get("capability_count", None),
                "reconnect_attempts": reconnect_attempts,
                "last_error": last_error,
            }
        except Exception:
            return {
                "daemon_running": False,
                "daemon_status": "unknown",
                "daemon_host": "unknown",
                "center_connected": False,
                "center_reachable": False,
                "last_heartbeat": None,
                "last_capability_register": None,
                "capability_count": None,
                "reconnect_attempts": "0",
                "last_error": "",
            }

    def get_daemon_status(self) -> dict:
        try:
            return self._bridge_response(True, self.get_daemon_status_data())
        except Exception as e:
            return self._bridge_response(False, error={"code": "DAEMON_ERROR", "message": str(e)})

    def get_runtime_status(self) -> dict:
        try:
            settings = load_settings(str(self.config_path)) if self.config_path.exists() else None
            return self._bridge_response(
                True,
                self._runtime_status(settings, get_service_status(), self.get_daemon_status_data()),
            )
        except Exception as e:
            return self._bridge_response(False, error={"code": "RUNTIME_ERROR", "message": str(e)})

    def can_start_desktop_daemon(self) -> tuple[bool, str | None]:
        settings = load_settings(str(self.config_path))
        mode = settings.runtime.mode
        if mode not in {"desktop", "dev"}:
            return (
                False,
                "Developer Local Daemon is only available when runtime.mode is desktop or dev.",
            )
        if get_service_status().get("running"):
            return False, "Stop the Windows Service before starting Developer Local Daemon."
        return True, None

    def get_service_status_bridge(self) -> dict:
        try:
            return self._bridge_response(True, get_service_status())
        except Exception as e:
            return self._bridge_response(False, error={"code": "SERVICE_ERROR", "message": str(e)})

    def get_gui_autostart(self) -> dict:
        try:
            return self._bridge_response(True, {"enabled": self._gui_autostart_enabled()})
        except Exception as e:
            return self._bridge_response(
                False,
                error={"code": "AUTOSTART_ERROR", "message": str(e)},
            )

    def set_gui_autostart(self, enabled: bool) -> dict:
        try:
            self._set_gui_autostart(bool(enabled))
            return self._bridge_response(True, {"enabled": self._gui_autostart_enabled()})
        except Exception as e:
            return self._bridge_response(
                False,
                error={"code": "AUTOSTART_ERROR", "message": str(e)},
            )

    def install_service_bridge(self, account_mode: str = "LocalSystem") -> dict:
        try:
            install_service(account_mode or "LocalSystem")
            return self._bridge_response(True, get_service_status())
        except Exception as e:
            return self._bridge_response(
                False,
                error={"code": "SERVICE_INSTALL_ERROR", "message": str(e)},
            )

    def install_user_worker_bridge(self) -> dict:
        try:
            install_user_worker()
            return self._bridge_response(True, get_service_status())
        except Exception as e:
            return self._bridge_response(
                False,
                error={"code": "USER_WORKER_INSTALL_ERROR", "message": str(e)},
            )

    def start_user_worker_bridge(self) -> dict:
        try:
            start_user_worker()
            return self._bridge_response(True, get_service_status())
        except Exception as e:
            return self._bridge_response(
                False,
                error={"code": "USER_WORKER_START_ERROR", "message": str(e)},
            )

    def stop_user_worker_bridge(self) -> dict:
        try:
            stop_user_worker()
            return self._bridge_response(True, get_service_status())
        except Exception as e:
            return self._bridge_response(
                False,
                error={"code": "USER_WORKER_STOP_ERROR", "message": str(e)},
            )

    def uninstall_service_bridge(self) -> dict:
        try:
            uninstall_service()
            return self._bridge_response(True, get_service_status())
        except Exception as e:
            return self._bridge_response(
                False,
                error={"code": "SERVICE_UNINSTALL_ERROR", "message": str(e)},
            )

    def start_service_bridge(self) -> dict:
        try:
            start_service()
            return self._bridge_response(True, get_service_status())
        except Exception as e:
            return self._bridge_response(
                False,
                error={"code": "SERVICE_START_ERROR", "message": str(e)},
            )

    def stop_service_bridge(self) -> dict:
        try:
            stop_service()
            return self._bridge_response(True, get_service_status())
        except Exception as e:
            return self._bridge_response(
                False,
                error={"code": "SERVICE_STOP_ERROR", "message": str(e)},
            )

    def restart_service_bridge(self) -> dict:
        try:
            restart_service()
            return self._bridge_response(True, get_service_status())
        except Exception as e:
            return self._bridge_response(
                False,
                error={"code": "SERVICE_RESTART_ERROR", "message": str(e)},
            )

    def get_capabilities(self) -> dict:
        try:
            from .config import L2Policy
            plugin = FakeSystemPlugin(L2Policy())
            manifest = plugin.manifest()
            data = manifest.model_dump(mode="json")
            return self._bridge_response(True, data)
        except Exception as e:
            return self._bridge_response(
                False,
                error={"code": "CAPABILITIES_ERROR", "message": str(e)},
            )

    def get_recent_jobs(self, limit: int = 50) -> dict:
        try:
            jobs = self.store.get_recent_jobs(limit)
            unreported = self.store.get_unreported()
            return self._bridge_response(True, {"jobs": jobs, "unreported_count": len(unreported)})
        except Exception as e:
            return self._bridge_response(False, error={"code": "JOBS_ERROR", "message": str(e)})

    def retry_unreported_jobs(self) -> dict:
        async def _retry() -> dict[str, int]:
            settings = load_settings(str(self.config_path))
            from .daemon import WinNodeDaemon

            daemon = WinNodeDaemon(settings)
            daemon.state_store = self.store
            try:
                await daemon.hello()
                return await daemon.report_unreported_results()
            finally:
                await daemon.close()

        try:
            return self._bridge_response(True, asyncio.run(_retry()))
        except Exception as e:
            return self._bridge_response(False, error={"code": "RETRY_ERROR", "message": str(e)})

    def flush_reported_cache(self) -> dict:
        try:
            count = self.store.flush_reported(ttl_hours=0)
            return self._bridge_response(True, {"flushed": count})
        except Exception as e:
            return self._bridge_response(False, error={"code": "FLUSH_ERROR", "message": str(e)})

    def get_logs(
        self,
        limit: int = 500,
        level: str | None = None,
        query: str | None = None,
    ) -> dict:
        try:
            log_file = self._log_dir / "yequ-win-client.log"
            if not log_file.exists():
                return self._bridge_response(True, {"lines": [], "total": 0})

            lines = log_file.read_text(encoding="utf-8").splitlines()
            if level:
                lines = [line for line in lines if f"[{level.upper()}]" in line]
            if query:
                query_lower = query.lower()
                lines = [line for line in lines if query_lower in line.lower()]

            total = len(lines)
            lines = lines[-limit:]
            return self._bridge_response(
                True,
                {"lines": lines, "total": total, "shown": len(lines)},
            )
        except Exception as e:
            return self._bridge_response(False, error={"code": "LOGS_ERROR", "message": str(e)})

    def export_diagnostics_bridge(self) -> dict:
        try:
            zip_path = export_diagnostics(
                config_path=self.config_path,
                log_dir=self._log_dir,
                data_dir=self._data_dir,
            )
            return self._bridge_response(True, {"path": str(zip_path)})
        except Exception as e:
            return self._bridge_response(
                False,
                error={"code": "DIAGNOSTICS_ERROR", "message": str(e)},
            )

    def open_path(self, path_str: str) -> dict:
        try:
            p = Path(path_str)
            if p.is_dir():
                os.startfile(str(p))
            elif p.exists():
                os.startfile(str(p.parent))
            return self._bridge_response(True)
        except Exception as e:
            return self._bridge_response(False, error={"code": "OPEN_ERROR", "message": str(e)})

    def test_center_health(self) -> dict:
        try:
            settings = load_settings(str(self.config_path))
            import httpx
            client = httpx.Client(timeout=10)
            resp = client.get(f"{settings.center_base_url.rstrip('/')}/healthz")
            reachable = 200 <= resp.status_code < 500
            self.store.set_daemon_state("center_reachable", "true" if reachable else "false")
            return self._bridge_response(True, {
                "reachable": reachable,
                "status_code": resp.status_code,
            })
        except Exception as e:
            self.store.set_daemon_state("center_reachable", "false")
            return self._bridge_response(True, {"reachable": False, "error": str(e)})

    def _runtime_status(self, settings: Any, svc: dict, daemon: dict) -> dict:
        configured_mode = settings.runtime.mode if settings else "hybrid"
        service_running = bool(svc.get("running"))
        service_installed = bool(svc.get("installed"))
        worker = svc.get("user_worker") or {}
        desktop_daemon_running = bool(
            daemon.get("daemon_running") and daemon.get("daemon_host") == "desktop"
        )
        conflict = service_running and desktop_daemon_running

        if conflict:
            active_mode = "conflict"
            label = "Conflict"
            message = "Windows Service and Developer Local Daemon both appear active."
        elif service_running and worker.get("running"):
            active_mode = "hybrid"
            label = "Hybrid"
            message = "Service is online and User Worker is available."
        elif service_running:
            active_mode = "service"
            label = "Service"
            message = "Windows Service is the active daemon host."
        elif desktop_daemon_running:
            active_mode = "desktop"
            label = "Developer Local Daemon"
            message = "GUI-bound desktop daemon is active."
        elif service_installed:
            active_mode = "stopped"
            label = "Stopped"
            message = "Windows Service is installed but not running."
        else:
            active_mode = "uninstalled"
            label = "Not Installed"
            message = "Install the Windows Service or switch runtime.mode to desktop/dev."

        return {
            "configured_mode": configured_mode,
            "active_mode": active_mode,
            "label": label,
            "message": message,
            "conflict": conflict,
            "service_installed": service_installed,
            "service_running": service_running,
            "desktop_daemon_running": desktop_daemon_running,
            "gui_start_at_login": bool(settings.runtime.gui_start_at_login) if settings else False,
            "close_to_tray": bool(settings.runtime.close_to_tray) if settings else True,
            "service_startup": settings.runtime.service_startup if settings else "automatic",
        }

    def _gui_autostart_command(self) -> str:
        pythonw = self._project_root / ".venv" / "Scripts" / "pythonw.exe"
        python = (
            pythonw
            if pythonw.exists()
            else self._project_root / ".venv" / "Scripts" / "python.exe"
        )
        if not python.exists():
            python = Path(sys.executable)
        return (
            f'"{python}" -m node_win_client.cli gui '
            f'-c "{self.config_path.resolve()}"'
        )

    def _gui_autostart_enabled(self) -> bool:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, "YeQuWinClient")
                return "node_win_client.cli gui" in str(value)
        except FileNotFoundError:
            return False

    def _set_gui_autostart(self, enabled: bool) -> None:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(
                    key,
                    "YeQuWinClient",
                    0,
                    winreg.REG_SZ,
                    self._gui_autostart_command(),
                )
            else:
                with suppress(FileNotFoundError):
                    winreg.DeleteValue(key, "YeQuWinClient")
