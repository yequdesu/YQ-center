"""Configuration loading from YAML."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class GatewayConfig:
    host: str = "127.0.0.1"
    port: int = 9800
    unix_socket: str = "~/.local/share/yequ-gateway/gateway.sock"


@dataclass
class DataConfig:
    dir: str = "~/.local/share/yequ-gateway"


@dataclass
class MonitorConfig:
    scan_interval_seconds: int = 30


@dataclass
class CollectorConfig:
    interval_seconds: int = 60


@dataclass
class AgentConfig:
    anthropic_api_key: str = ""
    model: str = "claude-sonnet-4-6"


@dataclass
class NotifyConfig:
    log_file: str = "~/.local/share/yequ-gateway/gateway.log"


@dataclass
class Config:
    gateway: GatewayConfig
    data: DataConfig
    monitor: MonitorConfig
    collector: CollectorConfig
    agent: AgentConfig
    notify: NotifyConfig


def _expand_path(path: str) -> str:
    """Expand ~ and make absolute path."""
    return str(Path(path).expanduser().resolve())


def load_config(path: str) -> Config:
    """Load configuration from YAML file."""
    with open(path, "r") as f:
        raw = yaml.safe_load(f)

    gateway_raw = raw.get("gateway", {})
    data_raw = raw.get("data", {})
    monitor_raw = raw.get("monitor", {})
    collector_raw = raw.get("collector", {})
    agent_raw = raw.get("agent", {})
    notify_raw = raw.get("notify", {})

    return Config(
        gateway=GatewayConfig(
            host=gateway_raw.get("host", "127.0.0.1"),
            port=gateway_raw.get("port", 9800),
            unix_socket=_expand_path(gateway_raw.get("unix_socket", "~/.local/share/yequ-gateway/gateway.sock")),
        ),
        data=DataConfig(
            dir=_expand_path(data_raw.get("dir", "~/.local/share/yequ-gateway")),
        ),
        monitor=MonitorConfig(
            scan_interval_seconds=monitor_raw.get("scan_interval_seconds", 30),
        ),
        collector=CollectorConfig(
            interval_seconds=collector_raw.get("interval_seconds", 60),
        ),
        agent=AgentConfig(
            anthropic_api_key=agent_raw.get("anthropic_api_key", ""),
            model=agent_raw.get("model", "claude-sonnet-4-6"),
        ),
        notify=NotifyConfig(
            log_file=_expand_path(notify_raw.get("log_file", "~/.local/share/yequ-gateway/gateway.log")),
        ),
    )
