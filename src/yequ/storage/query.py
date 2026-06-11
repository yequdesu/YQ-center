"""Data query interface for snapshots, metrics, and events."""

from __future__ import annotations

from typing import Any

from yequ.storage.database import get_connection


def get_latest_snapshot(
    db_path: str,
    device_id: str,
    capability: str,
) -> dict[str, Any] | None:
    """Get the latest snapshot for a device/capability pair."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            """SELECT * FROM snapshots
               WHERE device_id = ? AND capability = ?
               ORDER BY ingested_at DESC LIMIT 1""",
            (device_id, capability),
        ).fetchone()

    return dict(row) if row else None


def query_snapshots_by_device(
    db_path: str,
    device_id: str,
) -> list[dict[str, Any]]:
    """Get all current snapshots for a device."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM snapshots WHERE device_id = ? ORDER BY capability",
            (device_id,),
        ).fetchall()

    return [dict(r) for r in rows]


def get_metrics(
    db_path: str,
    device_id: str,
    metric_name: str,
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Get metric values for a device/metric, optionally filtered by time range."""
    query = "SELECT * FROM metrics WHERE device_id = ? AND metric_name = ?"
    params: list[Any] = [device_id, metric_name]

    if start:
        query += " AND timestamp >= ?"
        params.append(start)
    if end:
        query += " AND timestamp <= ?"
        params.append(end)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    return [dict(r) for r in rows]


def get_events(
    db_path: str,
    device_id: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Query events with optional filters."""
    query = "SELECT * FROM events WHERE 1=1"
    params: list[Any] = []

    if device_id:
        query += " AND device_id = ?"
        params.append(device_id)
    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)
    if severity:
        query += " AND severity = ?"
        params.append(severity)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    return [dict(r) for r in rows]
