from __future__ import annotations

import json
import platform
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def export_diagnostics(
    config_path: str | Path | None = None,
    log_dir: str | Path = "logs",
    data_dir: str | Path = "data",
    output_dir: str | Path = "data/diagnostics",
) -> Path:
    config_path = Path(config_path) if config_path else Path("config.local.yaml")
    log_dir = Path(log_dir)
    data_dir = Path(data_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC)
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    zip_name = f"yequ-win-client-diagnostics-{timestamp}.zip"
    zip_path = output_dir / zip_name

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        _add_sanitized_config(zf, config_path)
        _add_logs(zf, log_dir)
        _add_system_info(zf)
        _add_service_status(zf)
        _add_capabilities_snapshot(zf)
        _add_state_summary(zf, data_dir)

    return zip_path


def _add_sanitized_config(zf: zipfile.ZipFile, config_path: Path) -> None:
    if not config_path.exists():
        zf.writestr("config.json", json.dumps({"error": "Config file not found"}, indent=2))
        return

    raw = config_path.read_text(encoding="utf-8")
    sanitized = raw
    for key in ("node_token", "token", "api_token"):
        sanitized = _mask_value(sanitized, key)
    zf.writestr("config.txt", sanitized)


def _mask_value(text: str, key: str) -> str:
    import re

    pattern = rf'({re.escape(key)}:?\s*)"?([^"\n\r]+)"?'
    return re.sub(pattern, r'\g<1>"***REDACTED***"', text)


def _add_logs(zf: zipfile.ZipFile, log_dir: Path) -> None:
    if log_dir.exists():
        log_files = sorted(log_dir.glob("*.log*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for lf in log_files[:5]:
            try:
                content = lf.read_text(encoding="utf-8")
                zf.writestr(f"logs/{lf.name}", content)
            except Exception:
                pass

    most_recent_log = log_dir / "yequ-win-client.log"
    if most_recent_log.exists():
        try:
            lines = most_recent_log.read_text(encoding="utf-8").splitlines()
            zf.writestr("logs/recent_500.log", "\n".join(lines[-500:]))
        except Exception:
            pass


def _add_system_info(zf: zipfile.ZipFile) -> None:
    info: dict[str, Any] = {
        "python_version": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "platform_version": platform.version(),
        "processor": platform.processor(),
        "machine": platform.machine(),
        "node": platform.node(),
    }
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-ComputerInfo -Property WindowsVersion,OsName,OsVersion | ConvertTo-Json)",
            ],
            capture_output=True,
            creationflags=_subprocess_creation_flags(),
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            info["windows"] = json.loads(result.stdout)
    except Exception:
        pass
    zf.writestr("system.json", json.dumps(info, indent=2))


def _subprocess_creation_flags() -> int:
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _add_service_status(zf: zipfile.ZipFile) -> None:
    try:
        from .service import get_service_status

        status = get_service_status()
    except Exception:
        status = {"error": "Unable to query service"}
    zf.writestr("service_status.json", json.dumps(status, indent=2))


def _add_capabilities_snapshot(zf: zipfile.ZipFile) -> None:
    try:
        from .config import L2Policy
        from .plugins import FakeSystemPlugin

        plugin = FakeSystemPlugin(L2Policy())
        manifest = plugin.manifest()
        zf.writestr(
            "capabilities.json",
            json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=False),
        )
    except Exception:
        zf.writestr(
            "capabilities.json",
            json.dumps({"error": "Unable to load capabilities"}, indent=2),
        )


def _add_state_summary(zf: zipfile.ZipFile, data_dir: Path) -> None:
    db_path = data_dir / "node_state.sqlite3"
    if not db_path.exists():
        zf.writestr("state_summary.json", json.dumps({"error": "State DB not found"}, indent=2))
        return

    try:
        from .state_store import StateStore

        store = StateStore(db_path)
        summary = store.get_db_summary()
        recent = store.get_recent_jobs(20)
        summary["recent_jobs"] = recent
        store.close()
        zf.writestr("state_summary.json", json.dumps(summary, indent=2))
    except Exception:
        zf.writestr(
            "state_summary.json",
            json.dumps({"error": "Unable to read state DB"}, indent=2),
        )
