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


import pytest
import json
from yequ.storage.ingest import ingest_snapshot, ingest_metric, ingest_event
from yequ.storage.query import (
    get_latest_snapshot,
    get_metrics,
    get_events,
    query_snapshots_by_device,
)


class TestIngest:
    @pytest.fixture
    def db(self, db_path):
        from yequ.storage.database import init_database
        init_database(db_path)
        return db_path

    def test_ingest_snapshot(self, db):
        ingest_snapshot(db, "dev1", "location", "v1", {"lat": 31.23, "lng": 121.47})

        snap = get_latest_snapshot(db, "dev1", "location")
        assert snap is not None
        assert snap["device_id"] == "dev1"
        assert snap["capability"] == "location"
        payload = json.loads(snap["payload_json"])
        assert payload["lat"] == 31.23

    def test_ingest_snapshot_upsert(self, db):
        ingest_snapshot(db, "dev1", "location", "v1", {"lat": 31.0, "lng": 121.0})
        ingest_snapshot(db, "dev1", "location", "v1", {"lat": 31.5, "lng": 121.5})

        snap = get_latest_snapshot(db, "dev1", "location")
        payload = json.loads(snap["payload_json"])
        assert payload["lat"] == 31.5  # latest value

    def test_ingest_metric(self, db):
        ingest_metric(db, "dev1", "system_metrics", "cpu_percent", 45.2, "%")

        metrics = get_metrics(db, "dev1", "cpu_percent", limit=1)
        assert len(metrics) == 1
        assert metrics[0]["value"] == 45.2
        assert metrics[0]["unit"] == "%"

    def test_get_metrics_time_range(self, db):
        import time
        t1 = "2026-06-12T10:00:00Z"
        t2 = "2026-06-12T10:01:00Z"
        t3 = "2026-06-12T10:02:00Z"

        ingest_metric(db, "dev1", "sys", "cpu", 10.0, "%", t1)
        ingest_metric(db, "dev1", "sys", "cpu", 20.0, "%", t2)
        ingest_metric(db, "dev1", "sys", "cpu", 30.0, "%", t3)

        results = get_metrics(db, "dev1", "cpu", start="2026-06-12T10:00:30Z", end="2026-06-12T10:01:30Z")
        assert len(results) == 1
        assert results[0]["value"] == 20.0

    def test_ingest_event(self, db):
        ingest_event(db, "dev1", "device_offline", "warning", "设备离线", "心跳超时")

        events = get_events(db, device_id="dev1")
        assert len(events) == 1
        assert events[0]["event_type"] == "device_offline"
        assert events[0]["severity"] == "warning"

    def test_get_events_filter_by_severity(self, db):
        ingest_event(db, "dev1", "test", "info", "info event", "")
        ingest_event(db, "dev1", "test", "critical", "critical event", "")
        ingest_event(db, "dev1", "test", "warning", "warning event", "")

        events = get_events(db, severity="critical")
        assert len(events) == 1
        assert events[0]["title"] == "critical event"

    def test_query_snapshots_by_device(self, db):
        ingest_snapshot(db, "dev1", "location", "v1", {"city": "shanghai"})
        ingest_snapshot(db, "dev1", "battery", "v1", {"pct": 80})
        ingest_snapshot(db, "dev2", "location", "v1", {"city": "beijing"})

        snaps = query_snapshots_by_device(db, "dev1")
        assert len(snaps) == 2
        capabilities = {s["capability"] for s in snaps}
        assert capabilities == {"location", "battery"}

    def test_get_latest_snapshot_not_found(self, db):
        assert get_latest_snapshot(db, "nonexistent", "cap") is None
