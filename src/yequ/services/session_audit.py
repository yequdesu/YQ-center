"""Per-session append-only audit logs.

The database remains the source of truth for structured state.  These JSONL
files are a debugging/audit trail tied to one Agent session, including events
that are inconvenient to reconstruct from several tables while investigating
latency or ordering issues.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from yequ.config import PROJECT_ROOT, get_settings

_SESSION_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_LOCK = threading.RLock()
_SESSION_SEQS: dict[str, int] = {}


def session_audit_path(session_id: str) -> Path:
    settings = get_settings()
    base = Path(settings.session_audit_log_dir)
    if not base.is_absolute():
        base = PROJECT_ROOT / base
    safe_session_id = _safe_session_id(session_id)
    return base / f"{safe_session_id}.jsonl"


def record_session_audit_event(
    session_id: str | None,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    turn_id: str | None = None,
    trace_id: str | None = None,
    source: str = "center",
    event_time: datetime | str | None = None,
) -> None:
    if not session_id:
        return
    settings = get_settings()
    if not settings.session_audit_enabled:
        return

    timestamp = _normalize_time(event_time)
    path = session_audit_path(session_id)
    with _LOCK:
        record = {
            "seq": _next_seq_locked(session_id),
            "recorded_at": datetime.now(UTC).isoformat(),
            "event_time": timestamp,
            "perf_counter_ns": perf_counter_ns(),
            "session_id": session_id,
            "turn_id": turn_id,
            "trace_id": trace_id,
            "source": source,
            "event_type": event_type,
            "payload": _jsonable(payload or {}),
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.write("\n")


def read_session_audit_events(session_id: str, *, tail: int | None = None) -> list[dict[str, Any]]:
    path = session_audit_path(session_id)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    if tail is not None and tail > 0:
        lines = lines[-tail:]
    events: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            value = {"event_type": "audit_log.decode_failed", "raw": line}
        if isinstance(value, dict):
            events.append(value)
    return events


def _next_seq_locked(session_id: str) -> int:
    current = _SESSION_SEQS.get(session_id)
    if current is None:
        path = session_audit_path(session_id)
        if path.exists():
            try:
                current = sum(1 for _ in path.open("r", encoding="utf-8"))
            except OSError:
                current = 0
        else:
            current = 0
    current += 1
    _SESSION_SEQS[session_id] = current
    return current


def _safe_session_id(session_id: str) -> str:
    safe = _SESSION_ID_RE.sub("_", session_id).strip("._-")
    return safe or "unknown_session"


def _normalize_time(value: datetime | str | None) -> str:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat()
    if isinstance(value, str) and value:
        return value
    return datetime.now(UTC).isoformat()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return str(value)
