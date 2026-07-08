from __future__ import annotations

import ctypes
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from node_win_client.models import FunctionManifest

from .base import CapabilityContext, NodeCapability

MAX_STDOUT_BYTES = 65536
MAX_STDERR_BYTES = 8192
MAX_TIMEOUT_SEC = 30
PROFILES = {"user.readonly", "user.write", "admin.readonly", "admin.write"}


def execute(input_data: dict[str, Any], context: CapabilityContext) -> dict[str, Any]:
    del context
    profile = _required_string(input_data.get("profile"), "profile")
    if profile not in PROFILES:
        raise ValueError(f"unsupported exec profile: {profile}")
    if profile.startswith("admin.") and not _is_admin():
        raise PermissionError(f"{profile} is unavailable because this runtime is not elevated")

    command = _required_string(input_data.get("command"), "command")
    reason = _required_string(input_data.get("reason"), "reason")

    cwd = input_data.get("cwd")
    cwd_path = Path(str(cwd)).expanduser() if cwd else None
    if cwd_path is not None and not cwd_path.is_dir():
        raise FileNotFoundError(f"cwd not found: {cwd_path}")

    timeout_sec = min(max(int(input_data.get("timeout_sec") or 10), 1), MAX_TIMEOUT_SEC)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd_path) if cwd_path else None,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "failed",
            "profile": profile,
            "reason": reason,
            "exit_code": None,
            "error_code": "timeout",
            "error_message": f"command timed out after {timeout_sec}s",
            "stdout_preview": _truncate_text(exc.stdout or "", MAX_STDOUT_BYTES)[0],
            "stderr_tail": _tail_text(exc.stderr or "", MAX_STDERR_BYTES),
            "truncated": True,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    stdout_preview, stdout_truncated = _truncate_text(completed.stdout or "", MAX_STDOUT_BYTES)
    stderr_tail = _tail_text(completed.stderr or "", MAX_STDERR_BYTES)
    return {
        "status": "succeeded" if completed.returncode == 0 else "failed",
        "profile": profile,
        "reason": reason,
        "command": command,
        "cwd": str(cwd_path) if cwd_path else os.getcwd(),
        "exit_code": completed.returncode,
        "stdout_preview": stdout_preview,
        "stderr_tail": stderr_tail,
        "truncated": stdout_truncated or len((completed.stderr or "").encode("utf-8")) > MAX_STDERR_BYTES,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


def _required_string(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _truncate_text(value: str, max_bytes: int) -> tuple[str, bool]:
    data = value.encode("utf-8", errors="replace")
    if len(data) <= max_bytes:
        return value, False
    return data[:max_bytes].decode("utf-8", errors="replace"), True


def _tail_text(value: str, max_bytes: int) -> str:
    data = value.encode("utf-8", errors="replace")
    if len(data) <= max_bytes:
        return value
    return data[-max_bytes:].decode("utf-8", errors="replace")


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


CAPABILITY = NodeCapability(
    manifest=FunctionManifest(
        name="windows.exec.run",
        description="Run one Windows command string under a declared execution profile.",
        agent_description=(
            "Use for simple Windows command-line diagnostics or controlled write tasks. "
            "Prefer Product/Core capabilities for files, screenshots, transfer, artifacts, "
            "services, logs, and structured system facts."
        ),
        user_visible_name="Run Windows command",
        input_schema={
            "type": "object",
            "properties": {
                "profile": {"type": "string", "enum": sorted(PROFILES)},
                "command": {"type": "string", "minLength": 1},
                "reason": {"type": "string", "minLength": 1},
                "cwd": {"type": "string"},
                "timeout_sec": {"type": "integer", "minimum": 1, "maximum": MAX_TIMEOUT_SEC, "default": 10},
            },
            "required": ["profile", "command", "reason"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        risk="maintenance",
        effect="write",
        timeout_sec=MAX_TIMEOUT_SEC,
        idempotency="non_idempotent",
        resource_keys=["node.exec"],
        conflict_policy="serialize",
        execution_context="hybrid",
    ),
    handler=execute,
)
