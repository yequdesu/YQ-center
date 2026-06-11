"""Collector runner — schedules and executes local data collection."""

from __future__ import annotations

import json
import logging
import socket
from datetime import datetime, timezone

from yequ.collector.system import collect_system_metrics
from yequ.registry.store import DeviceStore
from yequ.storage.ingest import ingest_snapshot, ingest_metric

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CollectorRunner:
    """Manages the local device registration and data collection loop."""

    def __init__(self, db_path: str, device_store: DeviceStore):
        self.db_path = db_path
        self.store = device_store

    def ensure_local_device(self):
        """Ensure the local machine is registered as a trusted device.
        Uses hostname as device_id. Creates if not exists.
        """
        hostname = socket.gethostname()
        device = self.store.get_device(hostname)

        if device is None:
            device = self.store.register_device(
                device_id=hostname,
                labels={"role": "host", "location": "local"},
                is_local=True,
            )
            # Register standard capabilities
            self.store.add_capability(hostname, {
                "name": "system_metrics",
                "display": "系统指标",
                "data_type": "snapshot",
                "interval": 60,
                "schema": {
                    "type": "object",
                    "properties": {
                        "cpu_percent": {"type": "number"},
                        "memory_percent": {"type": "number"},
                        "disk_usage_percent": {"type": "number"},
                        "hostname": {"type": "string"},
                    },
                },
            })
            self.store.approve_capability(hostname, "system_metrics")
            logger.info("Registered local device: %s", hostname)

        return device

    def collect_and_ingest(self) -> None:
        """Collect system metrics and ingest as snapshot + individual metric points."""
        hostname = socket.gethostname()
        device = self.ensure_local_device()
        ts = _now()

        try:
            metrics = collect_system_metrics()
        except Exception as e:
            logger.error("Failed to collect system metrics: %s", e)
            return

        # Store as snapshot (latest overall state)
        ingest_snapshot(
            self.db_path,
            device.device_id,
            "system_metrics",
            "v1",
            metrics,
            timestamp=ts,
        )

        # Store key metrics as timeseries for trend analysis
        metric_fields = [
            ("cpu_percent", "%"),
            ("memory_percent", "%"),
            ("disk_usage_percent", "%"),
            ("net_bytes_sent", "bytes"),
            ("net_bytes_recv", "bytes"),
            ("load_avg_1m", ""),
        ]
        for field, unit in metric_fields:
            if field in metrics:
                ingest_metric(
                    self.db_path,
                    device.device_id,
                    "system_metrics",
                    field,
                    metrics[field],
                    unit=unit,
                    timestamp=ts,
                )
