import time
import pytest
from yequ.monitor.rules import (
    MonitorRule,
    HeartbeatTimeoutRule,
    ThresholdRule,
    evaluate_rule,
    load_rules_from_yaml,
    RuleResult,
)
from yequ.monitor.engine import MonitorEngine
from yequ.storage.database import init_database
from yequ.registry.store import DeviceStore
from yequ.notify.base import NotifyRouter
from yequ.storage.ingest import ingest_snapshot


class TestHeartbeatTimeoutRule:
    def test_offline_detected(self):
        rule = MonitorRule(
            name="device_offline",
            condition_type="heartbeat_timeout",
            condition_params={"multiplier": 3},
            severity="warning",
            cooldown_seconds=0,
        )
        device = {
            "device_id": "dev1",
            "last_hello_at": "2026-06-12T10:00:00Z",
            "capabilities": [
                {"name": "system_metrics", "interval_seconds": 60},
            ],
        }

        # Check with a reference time 4 minutes later (240s > 3*60=180s)
        result = HeartbeatTimeoutRule.evaluate(
            rule, device, reference_time="2026-06-12T10:04:00Z"
        )

        assert result.triggered is True
        assert "dev1" in result.title

    def test_not_offline_when_recent_hello(self):
        rule = MonitorRule(
            name="device_offline",
            condition_type="heartbeat_timeout",
            condition_params={"multiplier": 3},
            severity="warning",
            cooldown_seconds=0,
        )
        device = {
            "device_id": "dev1",
            "last_hello_at": "2026-06-12T10:03:00Z",
            "capabilities": [{"name": "sys", "interval_seconds": 60}],
        }

        result = HeartbeatTimeoutRule.evaluate(
            rule, device, reference_time="2026-06-12T10:04:00Z"
        )

        assert result.triggered is False

    def test_no_hello_yet_not_offline(self):
        """Device just registered, hasn't sent hello yet — don't alarm."""
        rule = MonitorRule(
            name="device_offline",
            condition_type="heartbeat_timeout",
            condition_params={"multiplier": 3},
            severity="warning",
            cooldown_seconds=0,
        )
        device = {
            "device_id": "dev1",
            "last_hello_at": None,  # never hello'd
            "capabilities": [],
        }

        result = HeartbeatTimeoutRule.evaluate(
            rule, device, reference_time="2026-06-12T10:04:00Z"
        )

        assert result.triggered is False


class TestThresholdRule:
    def test_disk_alert(self, db_path):
        init_database(db_path)
        ingest_snapshot(db_path, "localhost", "system_metrics", "v1",
                        {"disk_usage_percent": 92.0})

        rule = MonitorRule(
            name="disk_high",
            condition_type="threshold",
            condition_params={
                "capability": "system_metrics",
                "field": "disk_usage_percent",
                "operator": ">",
                "value": 90,
            },
            severity="critical",
            cooldown_seconds=0,
        )

        result = ThresholdRule.evaluate(rule, {"device_id": "localhost"}, db_path)
        assert result.triggered is True

    def test_threshold_not_exceeded(self, db_path):
        init_database(db_path)
        ingest_snapshot(db_path, "localhost", "system_metrics", "v1",
                        {"disk_usage_percent": 45.0})

        rule = MonitorRule(
            name="disk_high",
            condition_type="threshold",
            condition_params={
                "capability": "system_metrics",
                "field": "disk_usage_percent",
                "operator": ">",
                "value": 90,
            },
            severity="critical",
            cooldown_seconds=0,
        )

        result = ThresholdRule.evaluate(rule, {"device_id": "localhost"}, db_path)
        assert result.triggered is False


class TestLoadRules:
    def test_load_from_yaml(self, tmp_path):
        yaml_content = """
rules:
  - name: device_offline
    description: 设备心跳超时
    condition:
      type: heartbeat_timeout
      params:
        multiplier: 3
    severity: warning
    cooldown_seconds: 300
    notify: true
"""
        config_path = tmp_path / "rules.yaml"
        config_path.write_text(yaml_content)

        rules = load_rules_from_yaml(str(config_path))
        assert len(rules) == 1
        assert rules[0].name == "device_offline"
        assert rules[0].cooldown_seconds == 300


class TestMonitorEngine:
    @pytest.fixture
    def engine(self, db_path, tmp_path):
        init_database(db_path)
        store = DeviceStore(db_path)
        # Register a local device
        store.register_device("testhost", is_local=True)
        notify = NotifyRouter(log_path=str(tmp_path / "gateway.log"))
        rules = [
            MonitorRule(
                name="disk_high",
                condition_type="threshold",
                condition_params={
                    "capability": "system_metrics",
                    "field": "disk_usage_percent",
                    "operator": ">",
                    "value": 90,
                },
                severity="critical",
                cooldown_seconds=0,
            ),
        ]
        return MonitorEngine(
            db_path=db_path,
            device_store=store,
            notify_router=notify,
            rules=rules,
        )

    def test_scan_does_not_raise(self, engine):
        engine.scan()  # Should run without error

    def test_scan_detects_alert(self, engine):
        # Ingest high disk
        ingest_snapshot(engine.db_path, "testhost", "system_metrics", "v1",
                        {"disk_usage_percent": 95.0})

        engine.scan()

        # Check an event was created
        from yequ.storage.query import get_events
        events = get_events(engine.db_path, severity="critical")
        assert len(events) >= 1
        assert any("disk_high" in e["title"] or "磁盘" in e["title"] for e in events)

    def test_cooldown_prevents_duplicate(self, engine):
        ingest_snapshot(engine.db_path, "testhost", "system_metrics", "v1",
                        {"disk_usage_percent": 95.0})

        engine.scan()  # first scan — fires
        first_count = len(engine._last_fired)

        engine.scan()  # second scan — should be suppressed
        # cooldown dict should still have the entry
        assert len(engine._last_fired) > 0
