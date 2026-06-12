"""Monitor engine — periodic scanning and alerting."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from yequ.monitor.rules import MonitorRule, evaluate_rule, RuleResult
from yequ.registry.store import DeviceStore
from yequ.notify.base import NotifyRouter, Severity, Notification
from yequ.storage.ingest import ingest_event

logger = logging.getLogger(__name__)


class MonitorEngine:
    """Periodic scanner that evaluates rules against all devices."""

    def __init__(
        self,
        db_path: str,
        device_store: DeviceStore,
        notify_router: NotifyRouter,
        rules: list[MonitorRule] | None = None,
    ):
        self.db_path = db_path
        self.store = device_store
        self.notify = notify_router
        self.rules = rules or []
        self._last_fired: dict[str, float] = {}  # rule_name:device_id -> timestamp
        self._event_bus = None  # set after init for async bus access

    def scan(self) -> list[RuleResult]:
        """Run all rules against all active devices. Returns triggered results."""
        devices = self.store.list_devices()
        results = []

        for device in devices:
            # Enrich device info with capabilities for heartbeat check
            caps = self.store.get_capabilities(device.device_id)
            device_info = {
                "device_id": device.device_id,
                "last_hello_at": device.last_hello_at,
                "capabilities": [
                    {"name": c.name, "interval_seconds": c.interval_seconds}
                    for c in caps
                ],
            }

            for rule in self.rules:
                result = evaluate_rule(rule, device_info, self.db_path)

                if result.triggered:
                    # Check cooldown
                    cooldown_key = f"{rule.name}:{device.device_id}"
                    now = time.time()
                    last = self._last_fired.get(cooldown_key, 0)

                    if now - last >= rule.cooldown_seconds:
                        self._last_fired[cooldown_key] = now
                        self._handle_alert(result, rule)
                        results.append(result)

        return results

    def _handle_alert(self, result: RuleResult, rule: MonitorRule) -> None:
        """Create event and send notification for a triggered rule."""
        severity_map = {
            "info": Severity.INFO,
            "warning": Severity.WARNING,
            "critical": Severity.CRITICAL,
        }
        severity = severity_map.get(result.severity, Severity.WARNING)

        # Store as event
        ingest_event(
            self.db_path,
            result.device_id or "gateway",
            rule.name,
            result.severity,
            result.title,
            result.body,
        )

        # Send notification
        if rule.notify:
            self.notify.send(Notification(
                severity=severity,
                title=result.title,
                body=result.body,
                device_id=result.device_id,
            ))

        # Publish to event bus for SSE streaming
        if self._event_bus is not None:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(self._event_bus.publish({
                        "event_type": rule.name,
                        "severity": result.severity,
                        "title": result.title,
                        "body": result.body,
                        "device_id": result.device_id,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }))
            except RuntimeError:
                pass  # no running event loop (tests, etc.)
