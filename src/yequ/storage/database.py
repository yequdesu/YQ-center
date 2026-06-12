"""SQLite database setup and connection management."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# Gateway DB schema (device registry, metadata)
GATEWAY_SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    token TEXT UNIQUE NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'device',
    labels_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active',
    is_local INTEGER NOT NULL DEFAULT 0,
    last_hello_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS capabilities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    name TEXT NOT NULL,
    display TEXT NOT NULL,
    schema_version TEXT NOT NULL DEFAULT 'v1',
    data_type TEXT NOT NULL DEFAULT 'snapshot',
    interval_seconds INTEGER NOT NULL DEFAULT 60,
    schema_json TEXT NOT NULL,
    retention_days INTEGER NOT NULL DEFAULT 30,
    is_approved INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (device_id) REFERENCES devices(device_id),
    UNIQUE(device_id, name)
);

CREATE TABLE IF NOT EXISTS pending_registrations (
    device_id TEXT PRIMARY KEY,
    device_info_json TEXT NOT NULL,
    registered_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL DEFAULT (datetime('now', '+1 hour')),
    retry_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pending_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT NOT NULL UNIQUE,
    device_id TEXT NOT NULL,
    action TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT,
    delivered INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    actor TEXT NOT NULL DEFAULT 'system',
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS conversation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    channel TEXT NOT NULL DEFAULT 'cli',
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    tool_calls_json TEXT NOT NULL DEFAULT '[]',
    model TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT ''
);
"""

# Data DB schema (snapshots, metrics, events)
DATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    schema_version TEXT NOT NULL DEFAULT 'v1',
    payload_json TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(device_id, capability)
);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_metrics_device_ts
    ON metrics(device_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_metrics_capability_ts
    ON metrics(capability, timestamp);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_device_ts
    ON events(device_id, timestamp);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    """Get a connection to a SQLite database with WAL mode and foreign keys."""
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_database(db_path: str) -> None:
    """Initialize both gateway and data schemas in the database."""
    conn = get_connection(db_path)
    try:
        conn.executescript(GATEWAY_SCHEMA)
        conn.executescript(DATA_SCHEMA)
        conn.commit()
    finally:
        conn.close()
