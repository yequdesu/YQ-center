"""Monitor rule definitions and evaluation."""

from __future__ import annotations

import json
import operator
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

import yaml


@dataclass
class MonitorRule:
    name: str
    condition_type: str  # "heartbeat_timeout" | "threshold"
    condition_params: dict[str, Any]
    severity: str  # "info" | "warning" | "critical"
    cooldown_seconds: int = 300
    notify: bool = True
    description: str = ""


@dataclass
class RuleResult:
    rule_name: str
    triggered: bool
    severity: str
    title: str = ""
    body: str = ""
    device_id: str | None = None


def _parse_iso(ts: str) -> datetime:
    """Parse ISO 8601 timestamp string to datetime. Handles Z suffix."""
    ts = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(ts)


class HeartbeatTimeoutRule:
    """Checks if a device has missed its heartbeat window."""

    @staticmethod
    def evaluate(
        rule: MonitorRule,
        device: dict[str, Any],
        reference_time: str | None = None,
        **kwargs,
    ) -> RuleResult:
        last_hello = device.get("last_hello_at")

        if last_hello is None:
            return RuleResult(
                rule_name=rule.name,
                triggered=False,
                severity=rule.severity,
                title="",
                body="",
                device_id=device["device_id"],
            )

        now = _parse_iso(reference_time) if reference_time else datetime.now(timezone.utc)

        # Find the longest hello interval from capabilities
        caps = device.get("capabilities", [])
        max_interval = max((c.get("interval_seconds", 60) for c in caps), default=60)
        multiplier = rule.condition_params.get("multiplier", 3)
        threshold = timedelta(seconds=max_interval * multiplier)

        last = _parse_iso(last_hello)
        if now - last > threshold:
            minutes = int((now - last).total_seconds() / 60)
            return RuleResult(
                rule_name=rule.name,
                triggered=True,
                severity=rule.severity,
                title=f"设备离线: {device['device_id']}",
                body=f"上次心跳: {last_hello} ({minutes}分钟前)",
                device_id=device["device_id"],
            )

        return RuleResult(
            rule_name=rule.name,
            triggered=False,
            severity=rule.severity,
            device_id=device["device_id"],
        )


class ThresholdRule:
    """Checks if a metric value exceeds a threshold."""

    OPS = {
        ">": operator.gt,
        ">=": operator.ge,
        "<": operator.lt,
        "<=": operator.le,
        "==": operator.eq,
        "!=": operator.ne,
    }

    @classmethod
    def evaluate(
        cls,
        rule: MonitorRule,
        device: dict[str, Any],
        db_path: str,
        **kwargs,
    ) -> RuleResult:
        from yequ.storage.query import get_latest_snapshot

        capability = rule.condition_params["capability"]
        field = rule.condition_params["field"]
        op_str = rule.condition_params["operator"]
        threshold_value = rule.condition_params["value"]

        snap = get_latest_snapshot(db_path, device["device_id"], capability)
        if snap is None:
            return RuleResult(
                rule_name=rule.name,
                triggered=False,
                severity=rule.severity,
                device_id=device["device_id"],
            )

        payload = json.loads(snap["payload_json"])
        actual_value = payload.get(field)

        if actual_value is None:
            return RuleResult(
                rule_name=rule.name,
                triggered=False,
                severity=rule.severity,
                device_id=device["device_id"],
            )

        op_func = cls.OPS.get(op_str)
        if op_func is None:
            return RuleResult(
                rule_name=rule.name,
                triggered=False,
                severity=rule.severity,
                device_id=device["device_id"],
            )

        if op_func(actual_value, threshold_value):
            return RuleResult(
                rule_name=rule.name,
                triggered=True,
                severity=rule.severity,
                title=f"{rule.description or rule.name}: {device['device_id']}",
                body=f"{field} = {actual_value} (阈值: {op_str} {threshold_value})",
                device_id=device["device_id"],
            )

        return RuleResult(
            rule_name=rule.name,
            triggered=False,
            severity=rule.severity,
            device_id=device["device_id"],
        )


RULE_EVALUATORS = {
    "heartbeat_timeout": HeartbeatTimeoutRule,
    "threshold": ThresholdRule,
}


def evaluate_rule(
    rule: MonitorRule,
    device: dict[str, Any],
    db_path: str,
    reference_time: str | None = None,
) -> RuleResult:
    """Evaluate a single rule against a device."""
    evaluator_cls = RULE_EVALUATORS.get(rule.condition_type)
    if evaluator_cls is None:
        return RuleResult(
            rule_name=rule.name,
            triggered=False,
            severity=rule.severity,
            device_id=device["device_id"],
        )
    return evaluator_cls.evaluate(
        rule, device, db_path=db_path, reference_time=reference_time
    )


def load_rules_from_yaml(path: str) -> list[MonitorRule]:
    """Load monitor rules from a YAML configuration file."""
    with open(path, "r") as f:
        raw = yaml.safe_load(f)

    rules = []
    for r in raw.get("rules", []):
        condition = r["condition"]
        rules.append(MonitorRule(
            name=r["name"],
            description=r.get("description", ""),
            condition_type=condition["type"],
            condition_params=condition.get("params", {}),
            severity=r.get("severity", "warning"),
            cooldown_seconds=r.get("cooldown_seconds", 300),
            notify=r.get("notify", True),
        ))

    return rules
