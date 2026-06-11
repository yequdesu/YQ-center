import sqlite3
import pytest
from yequ.storage.database import (
    get_connection,
    init_database,
    GATEWAY_SCHEMA,
    DATA_SCHEMA,
)


class TestGatewayDB:
    def test_init_creates_tables(self, db_path):
        init_database(db_path)

        conn = sqlite3.connect(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [t[0] for t in tables]

        assert "devices" in table_names
        assert "capabilities" in table_names
        assert "pending_registrations" in table_names
        conn.close()

    def test_devices_table_schema(self, db_path):
        init_database(db_path)

        conn = sqlite3.connect(db_path)
        cols = conn.execute("PRAGMA table_info(devices)").fetchall()
        col_names = [c[1] for c in cols]

        assert "device_id" in col_names
        assert "token" in col_names
        assert "labels_json" in col_names
        assert "status" in col_names
        assert "last_hello_at" in col_names
        assert "created_at" in col_names
        conn.close()

    def test_idempotent_init(self, db_path):
        init_database(db_path)
        init_database(db_path)  # should not raise

        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
        assert count == 0
        conn.close()


class TestDataDB:
    def test_init_creates_tables(self, db_path):
        init_database(db_path)

        conn = sqlite3.connect(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [t[0] for t in tables]

        assert "snapshots" in table_names
        assert "metrics" in table_names
        assert "events" in table_names
        conn.close()

    def test_snapshots_table_schema(self, db_path):
        init_database(db_path)

        conn = sqlite3.connect(db_path)
        cols = conn.execute("PRAGMA table_info(snapshots)").fetchall()
        col_names = [c[1] for c in cols]

        assert "device_id" in col_names
        assert "capability" in col_names
        assert "payload_json" in col_names
        assert "timestamp" in col_names
        conn.close()

    def test_metrics_table_schema(self, db_path):
        init_database(db_path)

        conn = sqlite3.connect(db_path)
        cols = conn.execute("PRAGMA table_info(metrics)").fetchall()
        col_names = [c[1] for c in cols]

        assert "device_id" in col_names
        assert "capability" in col_names
        assert "metric_name" in col_names
        assert "value" in col_names
        assert "timestamp" in col_names
        conn.close()
