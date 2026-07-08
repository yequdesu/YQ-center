from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


CREATE_JOB_RESULTS = """
CREATE TABLE IF NOT EXISTS job_results (
    job_id TEXT PRIMARY KEY,
    invocation_id TEXT,
    function_name TEXT,
    status TEXT NOT NULL,
    result_json TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    reported_at TEXT
)
"""

CREATE_DAEMON_STATE = """
CREATE TABLE IF NOT EXISTS daemon_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

CREATE_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_job_results_status ON job_results(status)",
    "CREATE INDEX IF NOT EXISTS idx_job_results_reported ON job_results(reported_at)",
    "CREATE INDEX IF NOT EXISTS idx_job_results_created ON job_results(created_at)",
]


class StateStore:
    def __init__(self, db_path: str | Path = "data/node_state.sqlite3") -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        self._conn.execute(CREATE_JOB_RESULTS)
        self._conn.execute(CREATE_DAEMON_STATE)
        for idx in CREATE_INDICES:
            self._conn.execute(idx)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert_job_result(
        self,
        job_id: str,
        status: str,
        invocation_id: str | None = None,
        function_name: str | None = None,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """INSERT INTO job_results
               (job_id, invocation_id, function_name, status, result_json,
                error_code, error_message, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET
               status=excluded.status,
               result_json=excluded.result_json,
               error_code=excluded.error_code,
               error_message=excluded.error_message""",
            (
                job_id,
                invocation_id,
                function_name,
                status,
                json.dumps(result, ensure_ascii=False) if result else None,
                error_code,
                error_message,
                now,
            ),
        )
        self._conn.commit()

    def mark_reported(self, job_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "UPDATE job_results SET reported_at=? WHERE job_id=?",
            (now, job_id),
        )
        self._conn.commit()

    def get_unreported(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM job_results WHERE reported_at IS NULL ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_recent_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM job_results ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM job_results WHERE job_id=?", (job_id,)
        ).fetchone()
        return dict(row) if row else None

    def flush_reported(self, ttl_hours: float = 24) -> int:
        cutoff = datetime.now(UTC).timestamp() - ttl_hours * 3600
        cutoff_str = datetime.fromtimestamp(cutoff, UTC).isoformat()
        result = self._conn.execute(
            "DELETE FROM job_results WHERE reported_at IS NOT NULL AND created_at < ?",
            (cutoff_str,),
        )
        self._conn.commit()
        return result.rowcount

    def set_daemon_state(self, key: str, value: str) -> None:
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """INSERT INTO daemon_state (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
               value=excluded.value, updated_at=excluded.updated_at""",
            (key, value, now),
        )
        self._conn.commit()

    def get_daemon_state(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM daemon_state WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def get_all_daemon_state(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT key, value FROM daemon_state").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def get_db_summary(self) -> dict[str, Any]:
        job_count = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM job_results"
        ).fetchone()["cnt"]
        unreported = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM job_results WHERE reported_at IS NULL"
        ).fetchone()["cnt"]
        reported = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM job_results WHERE reported_at IS NOT NULL"
        ).fetchone()["cnt"]
        return {
            "total_jobs": job_count,
            "unreported": unreported,
            "reported": reported,
            "state_keys": self._conn.execute(
                "SELECT COUNT(*) as cnt FROM daemon_state"
            ).fetchone()["cnt"],
        }
