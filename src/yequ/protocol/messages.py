"""YQP v1.0 消息类型定义."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any


PROTOCOL_VERSION = "yqp/1.0"


def _new_uuid() -> str:
    return str(uuid.uuid4())


@dataclass
class HelloRegistration:
    """首次注册 Hello，不带 token.

    capabilities 字段：设备声明自己可以提供的数据类型。
    格式: [{"name": "...", "display": "...", "data_type": "snapshot"|"metric", ...}, ...]
    """
    device_id: str
    device_info: dict[str, Any] = field(default_factory=dict)
    capabilities: list[dict[str, Any]] = field(default_factory=list)
    hello_type: str = "registration"
    protocol: str = PROTOCOL_VERSION
    message_type: str = "hello"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: str) -> "HelloRegistration":
        data = json.loads(raw)
        return cls(
            device_id=data["device_id"],
            device_info=data.get("device_info", {}),
            capabilities=data.get("capabilities", []),
        )


@dataclass
class HelloHeartbeat:
    """已注册设备心跳 Hello."""
    device_id: str
    token: str
    hello_type: str = "heartbeat"
    protocol: str = PROTOCOL_VERSION
    message_type: str = "hello"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: str) -> "HelloHeartbeat":
        data = json.loads(raw)
        return cls(
            device_id=data["device_id"],
            token=data["token"],
        )


@dataclass
class Goodbye:
    """设备主动通知 Gateway 即将离线."""
    device_id: str
    token: str
    hello_type: str = "goodbye"
    protocol: str = PROTOCOL_VERSION
    message_type: str = "hello"

    @classmethod
    def from_json(cls, raw: str) -> "Goodbye":
        data = json.loads(raw)
        return cls(device_id=data["device_id"], token=data["token"])


@dataclass
class Command:
    """Gateway 下发给设备的指令."""
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    command_id: str = field(default_factory=_new_uuid)
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


@dataclass
class Ingest:
    """设备推送数据."""
    device_id: str
    token: str
    timestamp: str
    capability: str
    schema_version: str
    payload: dict[str, Any]
    message_id: str = field(default_factory=_new_uuid)
    protocol: str = PROTOCOL_VERSION
    message_type: str = "ingest"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: str) -> "Ingest":
        data = json.loads(raw)
        return cls(
            message_id=data.get("message_id", _new_uuid()),
            device_id=data["device_id"],
            token=data["token"],
            timestamp=data["timestamp"],
            capability=data["capability"],
            schema_version=data.get("schema_version", "v1"),
            payload=data["payload"],
        )


@dataclass
class Ack:
    """Gateway 对 Ingest/Hello 的确认回执."""
    message_id: str
    status: str  # "ok" | "error"
    pending_commands: list[Command] = field(default_factory=list)
    error: str | None = None
    protocol: str = PROTOCOL_VERSION
    message_type: str = "ack"

    def to_dict(self) -> dict[str, Any]:
        d = {
            "protocol": self.protocol,
            "message_type": self.message_type,
            "message_id": self.message_id,
            "status": self.status,
            "pending_commands": [c.to_dict() for c in self.pending_commands],
        }
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class HelloResponse:
    """响应 Registration Hello（pending 状态）."""
    status: str = "pending"
    retry_after: int = 30
    note: str | None = None
    protocol: str = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


@dataclass
class RegistrationResponse:
    """响应 Registration Hello（approved 状态）."""
    status: str = "approved"
    token: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    protocol: str = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Query:
    """Gateway 主动向设备拉取数据."""
    query_id: str = field(default_factory=_new_uuid)
    capability: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    protocol: str = PROTOCOL_VERSION
    message_type: str = "query"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v}


@dataclass
class Alert:
    """Gateway 向设备推送异常通知."""
    device_id: str
    severity: str  # info | warning | critical
    title: str
    body: str = ""
    alert_id: str = field(default_factory=_new_uuid)
    protocol: str = PROTOCOL_VERSION
    message_type: str = "alert"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_hello(raw: str) -> HelloRegistration | HelloHeartbeat | Goodbye:
    """根据 hello_type 自动解析 Hello 消息."""
    data = json.loads(raw)
    hello_type = data.get("hello_type", "")
    if hello_type == "registration":
        return HelloRegistration.from_json(raw)
    elif hello_type == "heartbeat":
        return HelloHeartbeat.from_json(raw)
    elif hello_type == "goodbye":
        return Goodbye.from_json(raw)
    else:
        raise ValueError(f"Unknown hello_type: {hello_type}")


