import json
import pytest
from yequ.collector.system import collect_system_metrics
from yequ.collector.runner import CollectorRunner
from yequ.storage.database import init_database
from yequ.registry.store import DeviceStore


class TestSystemCollector:
    def test_collect_returns_metrics(self):
        metrics = collect_system_metrics()

        # Check structure
        assert "cpu_percent" in metrics
        assert "memory_percent" in metrics
        assert "disk_usage_percent" in metrics

        # Check types
        assert isinstance(metrics["cpu_percent"], float)
        assert isinstance(metrics["memory_percent"], float)
        assert isinstance(metrics["disk_usage_percent"], float)

        # Check reasonable ranges
        assert 0 <= metrics["cpu_percent"] <= 100
        assert 0 <= metrics["memory_percent"] <= 100
        assert 0 <= metrics["disk_usage_percent"] <= 100

    def test_collect_includes_network(self):
        metrics = collect_system_metrics()

        assert "hostname" in metrics
        assert isinstance(metrics["hostname"], str)
        assert len(metrics["hostname"]) > 0


class TestCollectorRunner:
    @pytest.fixture
    def runner(self, db_path):
        init_database(db_path)
        store = DeviceStore(db_path)
        return CollectorRunner(db_path=db_path, device_store=store)

    def test_ensure_local_device(self, runner):
        device = runner.ensure_local_device()

        assert device is not None
        assert device.is_local is True
        # local device should have a predictable device_id based on hostname
        assert len(device.device_id) > 0

    def test_collect_and_ingest(self, runner):
        device = runner.ensure_local_device()
        runner.collect_and_ingest()

        # After collection, there should be snapshots
        from yequ.storage.query import get_latest_snapshot, get_metrics

        snap = get_latest_snapshot(runner.db_path, device.device_id, "system_metrics")
        assert snap is not None
        payload = json.loads(snap["payload_json"])
        assert "cpu_percent" in payload
