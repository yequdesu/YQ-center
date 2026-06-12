"""Monitor engine — periodic scanning and alerting."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from yequ.monitor.rules import MonitorRule, evaluate_rule, RuleResult
from yequ.registry.store import DeviceStore
from yequ.notify.base import NotifyRouter, Severity, Notification
from yequ.storage.ingest import ingest_event

logger = logging.getLogger(__name__)

# Rules that always run even when monitor is OFF
KEEP_ALIVE_RULE_NAMES = {"device_offline", "disk_high"}


class MonitorEngine:
    """Periodic scanner that evaluates rules against all devices."""

    def __init__(
        self,
        db_path: str,
        device_store: DeviceStore,
        notify_router: NotifyRouter,
        rules: list[MonitorRule] | None = None,
        data_dir: str = "",
        rules_path: str = "",
    ):
        self.db_path = db_path
        self.store = device_store
        self.notify = notify_router
        self.rules = rules or []
        self.data_dir = data_dir
        self.rules_path = rules_path
        self._last_fired: dict[str, float] = {}
        self._rules_mtime: float = 0

    @property
    def enabled(self) -> bool:
        """Check if the monitor is enabled via the marker file."""
        if not self.data_dir:
            return True
        marker = os.path.join(self.data_dir, "monitor_enabled")
        if not os.path.exists(marker):
            return True
        return open(marker).read().strip() == "1"

    def _active_rules(self) -> list[MonitorRule]:
        """Return the rules that should be evaluated right now."""
        if self.enabled:
            return list(self.rules)
        # OFF mode: only keep-alive rules
        return [r for r in self.rules if r.name in KEEP_ALIVE_RULE_NAMES]

    def _maybe_reload_rules(self) -> None:
        """Reload rules from YAML if the file has changed since last load."""
        if not self.rules_path or not os.path.exists(self.rules_path):
            return
        mtime = os.path.getmtime(self.rules_path)
        if mtime == self._rules_mtime:
            return
        try:
            from yequ.monitor.rules import load_rules_from_yaml
            self.rules = load_rules_from_yaml(self.rules_path)
            self._rules_mtime = mtime
            logger.info("Monitor rules reloaded from %s (%d rules)",
                        self.rules_path, len(self.rules))
        except Exception as e:
            logger.warning("Failed to reload monitor rules: %s", e)

    def scan(self) -> list[RuleResult]:
        """Run active rules against all devices. Returns triggered results."""
        self._maybe_reload_rules()
        devices = self.store.list_devices()
        results = []
        active = self._active_rules()

        for device in devices:
            caps = self.store.get_capabilities(device.device_id)
            device_info = {
                "device_id": device.device_id,
                "last_hello_at": device.last_hello_at,
                "capabilities": [
                    {"name": c.name, "interval_seconds": c.interval_seconds}
                    for c in caps
                ],
            }

            for rule in active:
                result = evaluate_rule(rule, device_info, self.db_path)

                if result.triggered:
                    cooldown_key = f"{rule.name}:{device.device_id}"
                    now = time.time()
                    last = self._last_fired.get(cooldown_key, 0)

                    if now - last >= rule.cooldown_seconds:
                        self._last_fired[cooldown_key] = now
                        self._handle_alert(result, rule)
                        results.append(result)

        return results

    def _handle_alert(self, result: RuleResult, rule: MonitorRule) -> None:
        severity_map = {
            "info": Severity.INFO,
            "warning": Severity.WARNING,
            "critical": Severity.CRITICAL,
        }
        severity = severity_map.get(result.severity, Severity.WARNING)

        ingest_event(
            self.db_path,
            result.device_id or "gateway",
            rule.name,
            result.severity,
            result.title,
            result.body,
        )

        # Update device status for offline alerts
        if rule.name == "device_offline" and result.device_id:
            self.store.mark_offline(result.device_id)

        if rule.notify:
            self.notify.send(Notification(
                severity=severity,
                title=result.title,
                body=result.body,
                device_id=result.device_id,
            ))

        from yequ.message_queue import mq
        mq.publish("yequ:events", {
            "event_type": rule.name,
            "severity": result.severity,
            "title": result.title,
            "body": result.body,
            "device_id": result.device_id,
        })
