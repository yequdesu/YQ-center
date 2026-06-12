"""Data retention and archival cleanup.

Policy (from spec 4.2):
  - Metrics: raw points older than 7 days are pruned
  - Snapshots: removed after capability's retention_days (default 30)
  - Events: kept permanently
"""

from __future__ import annotations

import logging

from yequ.storage.database import get_connection
from yequ.utils import now_iso

logger = logging.getLogger(__name__)

# Default retention (days)
DEFAULT_METRIC_RETENTION = 7
DEFAULT_SNAPSHOT_RETENTION = 30


def prune_old_metrics(db_path: str, retention_days: int = DEFAULT_METRIC_RETENTION) -> int:
    """Remove metric data points older than retention_days."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM metrics WHERE timestamp < datetime('now', ?)",
            (f"-{retention_days} days",),
        )
        conn.commit()
        deleted = cursor.rowcount
    if deleted:
        logger.info("Pruned %d metric points older than %d days", deleted, retention_days)
    return deleted


def prune_expired_snapshots(db_path: str) -> dict[str, int]:
    """Remove snapshots whose capability retention has expired.

    Returns counts per capability of deleted snapshots.
    """
    counts: dict[str, int] = {}
    with get_connection(db_path) as conn:
        # Find capabilities with defined retention
        rows = conn.execute(
            "SELECT device_id, name, retention_days FROM capabilities WHERE retention_days > 0"
        ).fetchall()

        for row in rows:
            device_id = row["device_id"]
            cap_name = row["name"]
            retention = row["retention_days"]

            cursor = conn.execute(
                """DELETE FROM snapshots
                   WHERE device_id = ? AND capability = ?
                   AND timestamp < datetime('now', ?)""",
                (device_id, cap_name, f"-{retention} days"),
            )
            if cursor.rowcount:
                key = f"{device_id}/{cap_name}"
                counts[key] = cursor.rowcount
        conn.commit()

    if counts:
        logger.info("Pruned expired snapshots: %s", counts)
    return counts


def run_archive_cycle(db_path: str) -> dict:
    """Run a full archive cycle: prune metrics and snapshots.

    Called daily. Returns summary of what was cleaned.
    """
    metrics_deleted = prune_old_metrics(db_path)
    snapshots_deleted = prune_expired_snapshots(db_path)
    return {
        "timestamp": now_iso(),
        "metrics_pruned": metrics_deleted,
        "snapshots_pruned": sum(snapshots_deleted.values()),
    }
