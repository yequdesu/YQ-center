"""Data ingest routing — stores incoming data into appropriate tables."""

from __future__ import annotations

import json

from yequ.storage.database import get_connection
from yequ.utils import now_iso


def ingest_snapshot(
    db_path: str,
    device_id: str,
    capability: str,
    schema_version: str,
    payload: dict,
    timestamp: str | None = None,
) -> None:
    """Insert or update a snapshot. Upsert on (device_id, capability)."""
    ts = timestamp or now_iso()
    payload_json = json.dumps(payload, ensure_ascii=False)

    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO snapshots (device_id, capability, schema_version, payload_json, timestamp, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(device_id, capability) DO UPDATE SET
               payload_json = excluded.payload_json,
               schema_version = excluded.schema_version,
               timestamp = excluded.timestamp,
               ingested_at = excluded.ingested_at""",
            (device_id, capability, schema_version, payload_json, ts, now_iso()),
        )
        conn.commit()


def ingest_metric(
    db_path: str,
    device_id: str,
    capability: str,
    metric_name: str,
    value: float,
    unit: str = "",
    timestamp: str | None = None,
) -> None:
    """Insert a single metric data point."""
    ts = timestamp or now_iso()

    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO metrics (device_id, capability, metric_name, value, unit, timestamp, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (device_id, capability, metric_name, value, unit, ts, now_iso()),
        )
        conn.commit()


def ingest_event(
    db_path: str,
    device_id: str,
    event_type: str,
    severity: str,
    title: str,
    body: str = "",
    metadata: dict | None = None,
    timestamp: str | None = None,
) -> None:
    """Insert an event record."""
    ts = timestamp or now_iso()
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False)

    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO events (device_id, event_type, severity, title, body, metadata_json, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (device_id, event_type, severity, title, body, metadata_json, ts),
        )
        conn.commit()
