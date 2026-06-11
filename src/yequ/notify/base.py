"""Notification adapters and routing."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Notification:
    severity: Severity
    title: str
    body: str = ""
    device_id: str | None = None
    metadata: dict = field(default_factory=dict)

    def format(self) -> str:
        prefix = {
            Severity.CRITICAL: "🔴",
            Severity.WARNING: "⚠️",
            Severity.INFO: "ℹ️",
        }.get(self.severity, "")

        lines = [f"{prefix} Gateway Alert: {self.title}"]
        if self.device_id:
            lines.append(f"设备: {self.device_id}")
        if self.body:
            lines.append(self.body)
        return "\n".join(lines)


class NotifyAdapter(ABC):
    """Base class for notification adapters."""

    @abstractmethod
    def send(self, notification: Notification) -> None:
        ...


class LogNotifyAdapter(NotifyAdapter):
    """Writes notifications to a log file."""

    def __init__(self, log_path: str):
        self.log_path = log_path

    def send(self, notification: Notification) -> None:
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"[{ts}] [{notification.severity.upper()}] {notification.format()}\n"

        with open(self.log_path, "a") as f:
            f.write(line)


class NotifyRouter:
    """Routes notifications to appropriate adapters based on severity."""

    def __init__(self, log_path: str):
        self._log = LogNotifyAdapter(log_path)
        self._adapters: dict[str, NotifyAdapter] = {"log": self._log}

    def register_adapter(self, name: str, adapter: NotifyAdapter) -> None:
        self._adapters[name] = adapter

    def send(self, notification: Notification) -> None:
        severity = notification.severity

        # Always log
        self._log.send(notification)

        # Route to additional adapters based on severity
        if severity in (Severity.CRITICAL, Severity.WARNING):
            for name, adapter in self._adapters.items():
                if name != "log":
                    try:
                        adapter.send(notification)
                    except Exception:
                        # Don't let one adapter failure break others
                        pass
