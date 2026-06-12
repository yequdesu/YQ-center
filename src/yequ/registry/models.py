"""Device and Capability data models."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class DeviceStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    REVOKED = "revoked"


@dataclass
class Device:
    device_id: str
    token: str
    labels: dict[str, str] = field(default_factory=dict)
    status: DeviceStatus = DeviceStatus.ACTIVE
    is_local: bool = False
    last_hello_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "token": self.token,
            "labels_json": json.dumps(self.labels, ensure_ascii=False),
            "status": self.status.value,
            "is_local": 1 if self.is_local else 0,
            "last_hello_at": self.last_hello_at,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Device":
        return cls(
            device_id=row["device_id"],
            token=row["token"],
            labels=json.loads(row["labels_json"]),
            status=DeviceStatus(row["status"]),
            is_local=bool(row["is_local"]),
            last_hello_at=row.get("last_hello_at"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def touch_hello(self) -> None:
        self.last_hello_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.status = DeviceStatus.ACTIVE

    @property
    def display_status(self) -> str:
        if self.last_hello_at:
            return "online"
        if self.is_local:
            return "local"
        return "unknown"


@dataclass
class Capability:
    device_id: str
    name: str
    display: str
    schema_version: str = "v1"
    data_type: str = "snapshot"
    interval_seconds: int = 60
    schema_json: str = "{}"
    retention_days: int = 30
    is_approved: bool = False

    @classmethod
    def from_declaration(cls, device_id: str, decl: dict[str, Any]) -> "Capability":
        return cls(
            device_id=device_id,
            name=decl["name"],
            display=decl.get("display", decl["name"]),
            schema_version=decl.get("schema_version", "v1"),
            data_type=decl.get("data_type", "snapshot"),
            interval_seconds=decl.get("interval", 60),
            schema_json=json.dumps(decl.get("schema", {}), ensure_ascii=False),
            retention_days=decl.get("retention_days", 30),
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "display": self.display,
            "schema_version": self.schema_version,
            "data_type": self.data_type,
            "interval_seconds": self.interval_seconds,
            "schema_json": self.schema_json,
            "retention_days": self.retention_days,
            "is_approved": 1 if self.is_approved else 0,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Capability":
        return cls(
            device_id=row["device_id"],
            name=row["name"],
            display=row["display"],
            schema_version=row["schema_version"],
            data_type=row["data_type"],
            interval_seconds=row["interval_seconds"],
            schema_json=row["schema_json"],
            retention_days=row["retention_days"],
            is_approved=bool(row["is_approved"]),
        )
