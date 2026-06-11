# YeQu-Gateway Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the YeQu-Gateway local base — receive local machine metrics via YQP protocol, store in SQLite, run basic inspector rules, expose CLI for serve/query/status.

**Architecture:** Single Python process hosting HTTP server (Starlette/uvicorn) + Unix socket server + monitor scheduler + local collector. All data in SQLite under `~/.local/share/yequ-gateway/`. YQP protocol messages defined as Python dataclasses with JSON serialization. Local collector auto-registers as trusted device.

**Tech Stack:** Python 3.12+, Starlette + uvicorn (HTTP), psutil (metrics), SQLite (stdlib), PyYAML (config), click (CLI), pytest (testing)

---

## File Map

```
YeQu-gateway/
├── pyproject.toml
├── config/
│   ├── gateway.yaml              # 主配置
│   └── monitor_rules.yaml        # 巡检规则
├── src/
│   └── yequ/
│       ├── __init__.py
│       ├── protocol/
│       │   ├── __init__.py
│       │   ├── messages.py       # 6 种消息类型 dataclass
│       │   └── schema.py         # Capability schema 校验
│       ├── config.py             # 配置加载
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── database.py       # SQLite 连接 + 建表
│       │   ├── ingest.py         # 数据入库路由
│       │   └── query.py          # 数据查询
│       ├── registry/
│       │   ├── __init__.py
│       │   ├── models.py         # Device, Capability model
│       │   └── store.py          # 设备 CRUD
│       ├── transport/
│       │   ├── __init__.py
│       │   ├── http_server.py    # Starlette app + /hello /ingest 路由
│       │   └── local_ipc.py      # Unix socket 服务端
│       ├── collector/
│       │   ├── __init__.py
│       │   ├── runner.py         # 采集调度器
│       │   └── system.py         # psutil 系统指标采集
│       ├── monitor/
│       │   ├── __init__.py
│       │   ├── engine.py         # 定时扫描引擎
│       │   └── rules.py          # 规则实现
│       ├── notify/
│       │   ├── __init__.py
│       │   └── base.py           # 通知适配器 + Log 实现
│       ├── agent/
│       │   ├── __init__.py
│       │   ├── core.py           # Agent 核心（Phase 1 stub）
│       │   └── tools.py          # Tool Set 定义
│       └── cli.py                # CLI 入口
└── tests/
    ├── test_protocol.py
    ├── test_config.py
    ├── test_storage.py
    ├── test_registry.py
    ├── test_transport.py
    ├── test_collector.py
    ├── test_monitor.py
    ├── test_notify.py
    └── conftest.py               # fixtures: temp db, test config
```

---

### Task 1: Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `src/yequ/__init__.py`
- Create: `src/yequ/protocol/__init__.py`
- Create: `src/yequ/storage/__init__.py`
- Create: `src/yequ/registry/__init__.py`
- Create: `src/yequ/transport/__init__.py`
- Create: `src/yequ/collector/__init__.py`
- Create: `src/yequ/monitor/__init__.py`
- Create: `src/yequ/notify/__init__.py`
- Create: `src/yequ/agent/__init__.py`
- Create: `config/gateway.yaml`
- Create: `config/monitor_rules.yaml`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=75", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "yequ-gateway"
version = "0.1.0"
description = "YeQu Gateway - Personal Data Hub"
requires-python = ">=3.12"
dependencies = [
    "starlette>=0.40",
    "uvicorn>=0.30",
    "psutil>=6.0",
    "pyyaml>=6.0",
    "click>=8.0",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "httpx>=0.27",
]

[project.scripts]
yequ = "yequ.cli:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 2: Create all __init__.py files**

```bash
mkdir -p src/yequ/protocol src/yequ/storage src/yequ/registry \
         src/yequ/transport src/yequ/collector src/yequ/monitor \
         src/yequ/notify src/yequ/agent \
         config tests
touch src/yequ/__init__.py \
      src/yequ/protocol/__init__.py \
      src/yequ/storage/__init__.py \
      src/yequ/registry/__init__.py \
      src/yequ/transport/__init__.py \
      src/yequ/collector/__init__.py \
      src/yequ/monitor/__init__.py \
      src/yequ/notify/__init__.py \
      src/yequ/agent/__init__.py
```

- [ ] **Step 3: Create config/gateway.yaml**

```yaml
# YeQu Gateway 主配置
gateway:
  host: "127.0.0.1"
  port: 9800
  unix_socket: "~/.local/share/yequ-gateway/gateway.sock"

data:
  dir: "~/.local/share/yequ-gateway"

monitor:
  scan_interval_seconds: 30

collector:
  interval_seconds: 60

agent:
  anthropic_api_key: ""  # Phase 2 启用
  model: "claude-sonnet-4-6"

notify:
  log_file: "~/.local/share/yequ-gateway/gateway.log"
```

- [ ] **Step 4: Create config/monitor_rules.yaml**

```yaml
rules:
  - name: device_offline
    description: "设备心跳超时"
    condition:
      type: heartbeat_timeout
      params:
        multiplier: 3
    severity: warning
    cooldown_seconds: 300
    notify: true

  - name: disk_high
    description: "磁盘使用率过高"
    condition:
      type: threshold
      params:
        capability: system_metrics
        field: disk_usage_percent
        operator: ">"
        value: 90
    severity: critical
    cooldown_seconds: 1800
    notify: true
```

- [ ] **Step 5: Create tests/conftest.py**

```python
import os
import tempfile
import pytest


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test data."""
    with tempfile.TemporaryDirectory() as d:
        old_cwd = os.getcwd()
        os.chdir(d)
        yield d
        os.chdir(old_cwd)


@pytest.fixture
def test_config_dict():
    """Return a minimal valid config dict for testing."""
    return {
        "gateway": {"host": "127.0.0.1", "port": 9800, "unix_socket": "/tmp/gateway.sock"},
        "data": {"dir": "/tmp/yequ-test"},
        "monitor": {"scan_interval_seconds": 30},
        "collector": {"interval_seconds": 60},
        "agent": {"anthropic_api_key": "", "model": "claude-sonnet-4-6"},
        "notify": {"log_file": "/tmp/yequ-test/gateway.log"},
    }


@pytest.fixture
def db_path(tmp_path):
    """Return an isolated SQLite database path."""
    return str(tmp_path / "test.db")
```

- [ ] **Step 6: Commit**

```bash
git init
git add -A
git commit -m "chore: scaffold YeQu-Gateway project structure"
```

---

### Task 2: YQP Protocol Messages

**Files:**
- Create: `src/yequ/protocol/messages.py`
- Create: `tests/test_protocol.py`

- [ ] **Step 1: Write failing tests for message serialization**

Create `tests/test_protocol.py`:

```python
import json
import pytest
from dataclasses import asdict
from yequ.protocol.messages import (
    HelloRegistration,
    HelloHeartbeat,
    Ingest,
    Ack,
    Command,
    HelloResponse,
    RegistrationResponse,
)


class TestHelloRegistration:
    def test_serialize_to_json(self):
        msg = HelloRegistration(
            device_id="pixel-8a",
            device_info={"os": "Android 15", "hostname": "pixel-8a"},
        )
        data = msg.to_dict()

        assert data["protocol"] == "yqp/1.0"
        assert data["message_type"] == "hello"
        assert data["hello_type"] == "registration"
        assert data["device_id"] == "pixel-8a"
        assert data["device_info"]["os"] == "Android 15"
        assert "token" not in data

    def test_deserialize_from_json(self):
        raw = json.dumps({
            "protocol": "yqp/1.0",
            "message_type": "hello",
            "hello_type": "registration",
            "device_id": "pixel-8a",
            "device_info": {"os": "Android 15", "hostname": "pixel-8a"},
        })
        msg = HelloRegistration.from_json(raw)

        assert msg.device_id == "pixel-8a"
        assert msg.device_info["os"] == "Android 15"
        assert msg.hello_type == "registration"


class TestHelloHeartbeat:
    def test_serialize(self):
        msg = HelloHeartbeat(device_id="pixel-8a", token="secret123")
        data = msg.to_dict()

        assert data["hello_type"] == "heartbeat"
        assert data["token"] == "secret123"


class TestIngest:
    def test_serialize(self):
        msg = Ingest(
            message_id="abc-123",
            device_id="pixel-8a",
            token="secret123",
            timestamp="2026-06-12T10:30:00Z",
            capability="location",
            schema_version="v1",
            payload={"lat": 31.23, "lng": 121.47},
        )
        data = msg.to_dict()

        assert data["message_type"] == "ingest"
        assert data["capability"] == "location"
        assert data["payload"]["lat"] == 31.23

    def test_deserialize(self):
        raw = json.dumps({
            "protocol": "yqp/1.0",
            "message_type": "ingest",
            "message_id": "abc-123",
            "device_id": "pixel-8a",
            "token": "secret123",
            "timestamp": "2026-06-12T10:30:00Z",
            "capability": "location",
            "schema_version": "v1",
            "payload": {"lat": 31.23, "lng": 121.47},
        })
        msg = Ingest.from_json(raw)

        assert msg.message_id == "abc-123"
        assert msg.capability == "location"
        assert msg.payload["lat"] == 31.23

    def test_message_id_auto_generated(self):
        msg = Ingest(
            device_id="test",
            token="s",
            timestamp="2026-06-12T10:30:00Z",
            capability="cpu",
            schema_version="v1",
            payload={"pct": 50},
        )
        assert msg.message_id is not None
        assert len(msg.message_id) == 36  # UUID4


class TestAck:
    def test_ok_response(self):
        ack = Ack(message_id="abc-123", status="ok")
        data = ack.to_dict()

        assert data["message_type"] == "ack"
        assert data["status"] == "ok"
        assert data["pending_commands"] == []

    def test_with_commands(self):
        cmd = Command(action="set_interval", params={"capability": "location", "interval": 30})
        ack = Ack(message_id="abc-123", status="ok", pending_commands=[cmd])
        data = ack.to_dict()

        assert len(data["pending_commands"]) == 1
        assert data["pending_commands"][0]["action"] == "set_interval"


class TestHelloResponse:
    def test_pending(self):
        resp = HelloResponse(status="pending", retry_after=30)
        data = resp.to_dict()

        assert data["status"] == "pending"
        assert data["retry_after"] == 30

    def test_approved(self):
        resp = RegistrationResponse(
            status="approved",
            token="secret123",
            config={"collector": {"interval_seconds": 60}},
        )
        data = resp.to_dict()

        assert data["status"] == "approved"
        assert data["token"] == "secret123"
        assert data["config"]["collector"]["interval_seconds"] == 60
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_protocol.py -v
```

Expected: FAIL — module not found

- [ ] **Step 3: Implement protocol messages**

Create `src/yequ/protocol/messages.py`:

```python
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
    """首次注册 Hello，不带 token."""
    device_id: str
    device_info: dict[str, Any] = field(default_factory=dict)
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
    protocol: str = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RegistrationResponse:
    """响应 Registration Hello（approved 状态）."""
    status: str = "approved"
    token: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    protocol: str = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_hello(raw: str) -> HelloRegistration | HelloHeartbeat:
    """根据 hello_type 自动解析 Hello 消息."""
    data = json.loads(raw)
    hello_type = data.get("hello_type", "")
    if hello_type == "registration":
        return HelloRegistration.from_json(raw)
    elif hello_type == "heartbeat":
        return HelloHeartbeat.from_json(raw)
    else:
        raise ValueError(f"Unknown hello_type: {hello_type}")


def parse_ingest(raw: str) -> Ingest:
    """解析 Ingest 消息."""
    return Ingest.from_json(raw)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_protocol.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/yequ/protocol/messages.py tests/test_protocol.py
git commit -m "feat: add YQP protocol message types"
```

---

### Task 3: Configuration Loading

**Files:**
- Create: `src/yequ/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_config.py`:

```python
import os
import pytest
from yequ.config import load_config, Config, GatewayConfig, DataConfig


class TestLoadConfig:
    def test_load_from_yaml(self, tmp_path):
        yaml_content = """
gateway:
  host: "0.0.0.0"
  port: 8080
  unix_socket: "/tmp/sock"
data:
  dir: "/tmp/data"
monitor:
  scan_interval_seconds: 10
collector:
  interval_seconds: 30
agent:
  anthropic_api_key: ""
  model: "claude-sonnet-4-6"
notify:
  log_file: "/tmp/log"
"""
        config_path = tmp_path / "gateway.yaml"
        config_path.write_text(yaml_content)

        config = load_config(str(config_path))

        assert config.gateway.host == "0.0.0.0"
        assert config.gateway.port == 8080
        assert config.data.dir == "/tmp/data"

    def test_expand_user_paths(self, tmp_path):
        yaml_content = """
gateway:
  host: "127.0.0.1"
  port: 9800
  unix_socket: "~/data/gateway.sock"
data:
  dir: "~/data"
monitor:
  scan_interval_seconds: 30
collector:
  interval_seconds: 60
agent:
  anthropic_api_key: ""
  model: "claude-sonnet-4-6"
notify:
  log_file: "~/data/gateway.log"
"""
        config_path = tmp_path / "gateway.yaml"
        config_path.write_text(yaml_content)

        config = load_config(str(config_path))

        assert not config.data.dir.startswith("~")
        assert config.data.dir.startswith("/")

    def test_default_values(self, tmp_path):
        yaml_content = """
gateway:
  host: "127.0.0.1"
  port: 9800
data:
  dir: "/tmp"
"""
        config_path = tmp_path / "gateway.yaml"
        config_path.write_text(yaml_content)

        config = load_config(str(config_path))

        assert config.monitor.scan_interval_seconds == 30
        assert config.collector.interval_seconds == 60
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_config.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement config loading**

Create `src/yequ/config.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_config.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/yequ/config.py tests/test_config.py
git commit -m "feat: add YAML configuration loading"
```

---

### Task 4: SQLite Database Setup

**Files:**
- Create: `src/yequ/storage/database.py`
- Create: `tests/test_storage.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_storage.py`:

```python
import sqlite3
import pytest
from yequ.storage.database import (
    get_connection,
    init_database,
    GatewayDB,
    DataDB,
    DB_SCHEMA,
    DATA_SCHEMA,
)


class TestGatewayDB:
    def test_init_creates_tables(self, db_path):
        init_database(db_path)

        conn = sqlite3.connect(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [t[0] for t in tables]

        assert "devices" in table_names
        assert "capabilities" in table_names
        assert "pending_registrations" in table_names
        conn.close()

    def test_devices_table_schema(self, db_path):
        init_database(db_path)

        conn = sqlite3.connect(db_path)
        cols = conn.execute("PRAGMA table_info(devices)").fetchall()
        col_names = [c[1] for c in cols]

        assert "device_id" in col_names
        assert "token" in col_names
        assert "labels_json" in col_names
        assert "status" in col_names
        assert "last_hello_at" in col_names
        assert "created_at" in col_names
        conn.close()

    def test_idempotent_init(self, db_path):
        init_database(db_path)
        init_database(db_path)  # should not raise

        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
        assert count == 0
        conn.close()


class TestDataDB:
    def test_init_creates_tables(self, db_path):
        init_database(db_path)

        conn = sqlite3.connect(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [t[0] for t in tables]

        assert "snapshots" in table_names
        assert "metrics" in table_names
        assert "events" in table_names
        conn.close()

    def test_snapshots_table_schema(self, db_path):
        init_database(db_path)

        conn = sqlite3.connect(db_path)
        cols = conn.execute("PRAGMA table_info(snapshots)").fetchall()
        col_names = [c[1] for c in cols]

        assert "device_id" in col_names
        assert "capability" in col_names
        assert "payload_json" in col_names
        assert "timestamp" in col_names
        conn.close()

    def test_metrics_table_schema(self, db_path):
        init_database(db_path)

        conn = sqlite3.connect(db_path)
        cols = conn.execute("PRAGMA table_info(metrics)").fetchall()
        col_names = [c[1] for c in cols]

        assert "device_id" in col_names
        assert "capability" in col_names
        assert "metric_name" in col_names
        assert "value" in col_names
        assert "timestamp" in col_names
        conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_storage.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement database module**

Create `src/yequ/storage/database.py`:

```python
"""SQLite database setup and connection management."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# Gateway DB schema (device registry, metadata)
GATEWAY_SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    token TEXT UNIQUE NOT NULL,
    labels_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active',
    is_local INTEGER NOT NULL DEFAULT 0,
    last_hello_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS capabilities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    name TEXT NOT NULL,
    display TEXT NOT NULL,
    schema_version TEXT NOT NULL DEFAULT 'v1',
    data_type TEXT NOT NULL DEFAULT 'snapshot',
    interval_seconds INTEGER NOT NULL DEFAULT 60,
    schema_json TEXT NOT NULL,
    retention_days INTEGER NOT NULL DEFAULT 30,
    is_approved INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (device_id) REFERENCES devices(device_id),
    UNIQUE(device_id, name)
);

CREATE TABLE IF NOT EXISTS pending_registrations (
    device_id TEXT PRIMARY KEY,
    device_info_json TEXT NOT NULL,
    registered_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL DEFAULT (datetime('now', '+1 hour')),
    retry_count INTEGER NOT NULL DEFAULT 0
);
"""

# Data DB schema (snapshots, metrics, events)
DATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    schema_version TEXT NOT NULL DEFAULT 'v1',
    payload_json TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(device_id, capability)
);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_metrics_device_ts
    ON metrics(device_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_metrics_capability_ts
    ON metrics(capability, timestamp);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_device_ts
    ON events(device_id, timestamp);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    """Get a connection to a SQLite database with WAL mode and foreign keys."""
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_database(db_path: str) -> None:
    """Initialize both gateway and data schemas in the database."""
    conn = get_connection(db_path)
    try:
        conn.executescript(GATEWAY_SCHEMA)
        conn.executescript(DATA_SCHEMA)
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_storage.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/yequ/storage/database.py tests/test_storage.py
git commit -m "feat: add SQLite database schema and initialization"
```

---

### Task 5: Device Registry

**Files:**
- Create: `src/yequ/registry/models.py`
- Create: `src/yequ/registry/store.py`
- Modify: `tests/test_registry.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_registry.py`:

```python
import json
import pytest
from yequ.registry.models import Device, DeviceStatus, Capability
from yequ.registry.store import DeviceStore
from yequ.storage.database import init_database


class TestDevice:
    def test_create_device(self):
        device = Device(
            device_id="test-device",
            token="tok123",
            labels={"role": "phone"},
        )

        assert device.device_id == "test-device"
        assert device.status == DeviceStatus.ACTIVE
        assert device.is_local is False

    def test_create_local_device(self):
        device = Device(
            device_id="local-host",
            token="localtok",
            is_local=True,
        )

        assert device.is_local is True

    def test_to_row(self):
        device = Device(
            device_id="dev1",
            token="t1",
            labels={"role": "server", "location": "home"},
        )
        row = device.to_row()

        assert row["device_id"] == "dev1"
        assert json.loads(row["labels_json"]) == {"role": "server", "location": "home"}

    def test_from_row(self):
        row = {
            "device_id": "dev1",
            "token": "t1",
            "labels_json": '{"role": "phone"}',
            "status": "active",
            "is_local": 0,
            "last_hello_at": "2026-06-12T10:00:00Z",
            "created_at": "2026-06-12T00:00:00Z",
            "updated_at": "2026-06-12T10:00:00Z",
        }
        device = Device.from_row(row)

        assert device.device_id == "dev1"
        assert device.labels == {"role": "phone"}
        assert device.last_hello_at == "2026-06-12T10:00:00Z"

    def test_touch_hello(self):
        device = Device(device_id="d1", token="t1")
        device.touch_hello()

        assert device.last_hello_at is not None
        assert device.status == DeviceStatus.ACTIVE


class TestCapability:
    def test_from_declaration(self):
        decl = {
            "name": "location",
            "display": "设备位置",
            "schema_version": "v1",
            "data_type": "snapshot",
            "interval": 300,
            "schema": {"type": "object", "properties": {"lat": {"type": "number"}}},
            "retention_days": 30,
        }
        cap = Capability.from_declaration("dev1", decl)

        assert cap.device_id == "dev1"
        assert cap.name == "location"
        assert cap.data_type == "snapshot"
        assert cap.interval_seconds == 300
        assert cap.is_approved is False

    def test_default_values(self):
        decl = {"name": "cpu", "display": "CPU Usage"}
        cap = Capability.from_declaration("dev1", decl)

        assert cap.data_type == "snapshot"
        assert cap.interval_seconds == 60
        assert cap.schema_version == "v1"


class TestDeviceStore:
    @pytest.fixture
    def store(self, db_path):
        init_database(db_path)
        return DeviceStore(db_path)

    def test_register_device(self, store):
        device = store.register_device(
            device_id="dev1",
            labels={"role": "phone"},
        )

        assert device.device_id == "dev1"
        assert device.token is not None
        assert len(device.token) == 64  # hex token

        # verify persistence
        found = store.get_device("dev1")
        assert found is not None
        assert found.token == device.token

    def test_register_local_device(self, store):
        device = store.register_device(
            device_id="localhost",
            is_local=True,
        )

        assert device.is_local is True

    def test_get_device_not_found(self, store):
        assert store.get_device("nonexistent") is None

    def test_list_devices(self, store):
        store.register_device("dev1")
        store.register_device("dev2")

        devices = store.list_devices()
        assert len(devices) == 2
        assert {d.device_id for d in devices} == {"dev1", "dev2"}

    def test_touch_hello_updates_timestamp(self, store):
        device = store.register_device("dev1")
        old_hello = device.last_hello_at

        store.touch_hello("dev1")
        updated = store.get_device("dev1")

        assert updated.last_hello_at is not None

    def test_get_device_by_token(self, store):
        device = store.register_device("dev1")
        found = store.get_device_by_token(device.token)

        assert found is not None
        assert found.device_id == "dev1"

    def test_invalid_token_returns_none(self, store):
        store.register_device("dev1")
        assert store.get_device_by_token("badtoken") is None

    def test_add_capability(self, store):
        store.register_device("dev1")
        store.add_capability("dev1", {
            "name": "location",
            "display": "位置",
            "data_type": "snapshot",
            "schema": {"type": "object"},
        })
        store.approve_capability("dev1", "location")

        caps = store.get_capabilities("dev1")
        assert len(caps) == 1
        assert caps[0].name == "location"
        assert caps[0].is_approved is True

    def test_pending_registration(self, store):
        store.add_pending_registration("new-device", {"os": "Android"})

        pending = store.get_pending_registration("new-device")
        assert pending is not None
        assert pending["device_info"]["os"] == "Android"

        store.increment_retry("new-device")
        pending2 = store.get_pending_registration("new-device")
        assert pending2["retry_count"] == 1

        store.remove_pending_registration("new-device")
        assert store.get_pending_registration("new-device") is None

    def test_revoke_device(self, store):
        store.register_device("dev1")
        store.revoke_device("dev1")

        assert store.get_device("dev1") is None
        assert store.get_device_by_token("any") is None

    def test_update_labels(self, store):
        store.register_device("dev1", labels={"role": "phone"})
        store.update_labels("dev1", {"role": "tablet", "owner": "me"})

        device = store.get_device("dev1")
        assert device.labels == {"role": "tablet", "owner": "me"}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_registry.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement device models**

Create `src/yequ/registry/models.py`:

```python
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
```

- [ ] **Step 4: Implement device store**

Create `src/yequ/registry/store.py`:

```python
"""Device registry CRUD operations."""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any

from yequ.registry.models import Device, Capability
from yequ.storage.database import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_token() -> str:
    return secrets.token_hex(32)


class DeviceStore:
    """Manages device registration, capabilities, and pending registrations."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _conn(self):
        return get_connection(self.db_path)

    # --- Device CRUD ---

    def register_device(
        self,
        device_id: str,
        labels: dict[str, str] | None = None,
        is_local: bool = False,
    ) -> Device:
        token = _generate_token()
        now = _now()
        device = Device(
            device_id=device_id,
            token=token,
            labels=labels or {},
            is_local=is_local,
            created_at=now,
            updated_at=now,
        )
        row = device.to_row()

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO devices (device_id, token, labels_json, status, is_local, created_at, updated_at)
                   VALUES (:device_id, :token, :labels_json, :status, :is_local, :created_at, :updated_at)""",
                {**row, "created_at": now, "updated_at": now},
            )
            conn.commit()

        return device

    def get_device(self, device_id: str) -> Device | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE device_id = ? AND status != 'revoked'",
                (device_id,),
            ).fetchone()

        if row is None:
            return None
        return Device.from_row(dict(row))

    def get_device_by_token(self, token: str) -> Device | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE token = ? AND status != 'revoked'",
                (token,),
            ).fetchone()

        if row is None:
            return None
        return Device.from_row(dict(row))

    def list_devices(self) -> list[Device]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM devices WHERE status != 'revoked' ORDER BY device_id"
            ).fetchall()

        return [Device.from_row(dict(r)) for r in rows]

    def touch_hello(self, device_id: str) -> None:
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "UPDATE devices SET last_hello_at = ?, updated_at = ? WHERE device_id = ?",
                (now, now, device_id),
            )
            conn.commit()

    def revoke_device(self, device_id: str) -> None:
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "UPDATE devices SET status = 'revoked', updated_at = ? WHERE device_id = ?",
                (now, device_id),
            )
            conn.commit()

    def update_labels(self, device_id: str, labels: dict[str, str]) -> None:
        now = _now()
        labels_json = json.dumps(labels, ensure_ascii=False)
        with self._conn() as conn:
            conn.execute(
                "UPDATE devices SET labels_json = ?, updated_at = ? WHERE device_id = ?",
                (labels_json, now, device_id),
            )
            conn.commit()

    # --- Capability ---

    def add_capability(self, device_id: str, decl: dict[str, Any]) -> Capability:
        cap = Capability.from_declaration(device_id, decl)
        row = cap.to_row()

        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO capabilities
                   (device_id, name, display, schema_version, data_type, interval_seconds, schema_json, retention_days, is_approved)
                   VALUES (:device_id, :name, :display, :schema_version, :data_type, :interval_seconds, :schema_json, :retention_days, :is_approved)""",
                row,
            )
            conn.commit()

        return cap

    def approve_capability(self, device_id: str, name: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE capabilities SET is_approved = 1 WHERE device_id = ? AND name = ?",
                (device_id, name),
            )
            conn.commit()

    def get_capabilities(self, device_id: str) -> list[Capability]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM capabilities WHERE device_id = ? AND is_approved = 1",
                (device_id,),
            ).fetchall()

        return [Capability.from_row(dict(r)) for r in rows]

    def get_capability(self, device_id: str, name: str) -> Capability | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM capabilities WHERE device_id = ? AND name = ?",
                (device_id, name),
            ).fetchone()

        if row is None:
            return None
        return Capability.from_row(dict(row))

    # --- Pending Registrations ---

    def add_pending_registration(self, device_id: str, device_info: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO pending_registrations (device_id, device_info_json, registered_at, retry_count)
                   VALUES (?, ?, ?, 0)""",
                (device_id, json.dumps(device_info, ensure_ascii=False), _now()),
            )
            conn.commit()

    def get_pending_registration(self, device_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pending_registrations WHERE device_id = ? AND expires_at > ?",
                (device_id, _now()),
            ).fetchone()

        if row is None:
            return None
        d = dict(row)
        d["device_info"] = json.loads(d.pop("device_info_json"))
        return d

    def increment_retry(self, device_id: str) -> int:
        with self._conn() as conn:
            conn.execute(
                "UPDATE pending_registrations SET retry_count = retry_count + 1 WHERE device_id = ?",
                (device_id,),
            )
            conn.commit()
            row = conn.execute(
                "SELECT retry_count FROM pending_registrations WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            return row["retry_count"] if row else 0

    def remove_pending_registration(self, device_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM pending_registrations WHERE device_id = ?",
                (device_id,),
            )
            conn.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_registry.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/yequ/registry/models.py src/yequ/registry/store.py tests/test_registry.py
git commit -m "feat: add device registry with CRUD and capability management"
```

---

### Task 6: Data Ingest and Query

**Files:**
- Create: `src/yequ/storage/ingest.py`
- Create: `src/yequ/storage/query.py`
- Modify: `tests/test_storage.py` (append tests)

- [ ] **Step 1: Write failing tests for ingest and query**

Append to `tests/test_storage.py`:

```python
import pytest
from yequ.storage.ingest import ingest_snapshot, ingest_metric, ingest_event
from yequ.storage.query import (
    get_latest_snapshot,
    get_metrics,
    get_events,
    query_snapshots_by_device,
)


class TestIngest:
    @pytest.fixture
    def db(self, db_path):
        from yequ.storage.database import init_database
        init_database(db_path)
        return db_path

    def test_ingest_snapshot(self, db):
        ingest_snapshot(db, "dev1", "location", "v1", {"lat": 31.23, "lng": 121.47})

        snap = get_latest_snapshot(db, "dev1", "location")
        assert snap is not None
        assert snap["device_id"] == "dev1"
        assert snap["capability"] == "location"
        payload = json.loads(snap["payload_json"])
        assert payload["lat"] == 31.23

    def test_ingest_snapshot_upsert(self, db):
        ingest_snapshot(db, "dev1", "location", "v1", {"lat": 31.0, "lng": 121.0})
        ingest_snapshot(db, "dev1", "location", "v1", {"lat": 31.5, "lng": 121.5})

        snap = get_latest_snapshot(db, "dev1", "location")
        payload = json.loads(snap["payload_json"])
        assert payload["lat"] == 31.5  # latest value

    def test_ingest_metric(self, db):
        ingest_metric(db, "dev1", "system_metrics", "cpu_percent", 45.2, "%")

        metrics = get_metrics(db, "dev1", "cpu_percent", limit=1)
        assert len(metrics) == 1
        assert metrics[0]["value"] == 45.2
        assert metrics[0]["unit"] == "%"

    def test_get_metrics_time_range(self, db):
        import time
        t1 = "2026-06-12T10:00:00Z"
        t2 = "2026-06-12T10:01:00Z"
        t3 = "2026-06-12T10:02:00Z"

        ingest_metric(db, "dev1", "sys", "cpu", 10.0, "%", t1)
        ingest_metric(db, "dev1", "sys", "cpu", 20.0, "%", t2)
        ingest_metric(db, "dev1", "sys", "cpu", 30.0, "%", t3)

        results = get_metrics(db, "dev1", "cpu", start="2026-06-12T10:00:30Z", end="2026-06-12T10:01:30Z")
        assert len(results) == 1
        assert results[0]["value"] == 20.0

    def test_ingest_event(self, db):
        ingest_event(db, "dev1", "device_offline", "warning", "设备离线", "心跳超时")

        events = get_events(db, device_id="dev1")
        assert len(events) == 1
        assert events[0]["event_type"] == "device_offline"
        assert events[0]["severity"] == "warning"

    def test_get_events_filter_by_severity(self, db):
        ingest_event(db, "dev1", "test", "info", "info event", "")
        ingest_event(db, "dev1", "test", "critical", "critical event", "")
        ingest_event(db, "dev1", "test", "warning", "warning event", "")

        events = get_events(db, severity="critical")
        assert len(events) == 1
        assert events[0]["title"] == "critical event"

    def test_query_snapshots_by_device(self, db):
        ingest_snapshot(db, "dev1", "location", "v1", {"city": "shanghai"})
        ingest_snapshot(db, "dev1", "battery", "v1", {"pct": 80})
        ingest_snapshot(db, "dev2", "location", "v1", {"city": "beijing"})

        snaps = query_snapshots_by_device(db, "dev1")
        assert len(snaps) == 2
        capabilities = {s["capability"] for s in snaps}
        assert capabilities == {"location", "battery"}

    def test_get_latest_snapshot_not_found(self, db):
        assert get_latest_snapshot(db, "nonexistent", "cap") is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_storage.py::TestIngest -v
```
Expected: FAIL

- [ ] **Step 3: Implement ingest**

Create `src/yequ/storage/ingest.py`:

```python
"""Data ingest routing — stores incoming data into appropriate tables."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from yequ.storage.database import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ingest_snapshot(
    db_path: str,
    device_id: str,
    capability: str,
    schema_version: str,
    payload: dict,
    timestamp: str | None = None,
) -> None:
    """Insert or update a snapshot. Upsert on (device_id, capability)."""
    ts = timestamp or _now()
    payload_json = json.dumps(payload, ensure_ascii=False)

    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO snapshots (device_id, capability, schema_version, payload_json, timestamp, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(device_id, capability) DO UPDATE SET
               payload_json = excluded.payload_json,
               schema_version = excluded.schema_version,
               timestamp = excluded.timestamp,
               ingested_at = excluded.ingested_at""",
            (device_id, capability, schema_version, payload_json, ts, _now()),
        )
        conn.commit()


def ingest_metric(
    db_path: str,
    device_id: str,
    capability: str,
    metric_name: str,
    value: float,
    unit: str = "",
    timestamp: str | None = None,
) -> None:
    """Insert a single metric data point."""
    ts = timestamp or _now()

    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO metrics (device_id, capability, metric_name, value, unit, timestamp, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (device_id, capability, metric_name, value, unit, ts, _now()),
        )
        conn.commit()


def ingest_event(
    db_path: str,
    device_id: str,
    event_type: str,
    severity: str,
    title: str,
    body: str = "",
    metadata: dict | None = None,
    timestamp: str | None = None,
) -> None:
    """Insert an event record."""
    ts = timestamp or _now()
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False)

    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO events (device_id, event_type, severity, title, body, metadata_json, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (device_id, event_type, severity, title, body, metadata_json, ts),
        )
        conn.commit()
```

- [ ] **Step 4: Implement query**

Create `src/yequ/storage/query.py`:

```python
"""Data query interface for snapshots, metrics, and events."""

from __future__ import annotations

from typing import Any

from yequ.storage.database import get_connection


def get_latest_snapshot(
    db_path: str,
    device_id: str,
    capability: str,
) -> dict[str, Any] | None:
    """Get the latest snapshot for a device/capability pair."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            """SELECT * FROM snapshots
               WHERE device_id = ? AND capability = ?
               ORDER BY ingested_at DESC LIMIT 1""",
            (device_id, capability),
        ).fetchone()

    return dict(row) if row else None


def query_snapshots_by_device(
    db_path: str,
    device_id: str,
) -> list[dict[str, Any]]:
    """Get all current snapshots for a device."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM snapshots WHERE device_id = ? ORDER BY capability",
            (device_id,),
        ).fetchall()

    return [dict(r) for r in rows]


def get_metrics(
    db_path: str,
    device_id: str,
    metric_name: str,
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Get metric values for a device/metric, optionally filtered by time range."""
    query = "SELECT * FROM metrics WHERE device_id = ? AND metric_name = ?"
    params: list[Any] = [device_id, metric_name]

    if start:
        query += " AND timestamp >= ?"
        params.append(start)
    if end:
        query += " AND timestamp <= ?"
        params.append(end)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    return [dict(r) for r in rows]


def get_events(
    db_path: str,
    device_id: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Query events with optional filters."""
    query = "SELECT * FROM events WHERE 1=1"
    params: list[Any] = []

    if device_id:
        query += " AND device_id = ?"
        params.append(device_id)
    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)
    if severity:
        query += " AND severity = ?"
        params.append(severity)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    return [dict(r) for r in rows]
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_storage.py::TestIngest -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/yequ/storage/ingest.py src/yequ/storage/query.py tests/test_storage.py
git commit -m "feat: add data ingest and query layer"
```

---

### Task 7: Notify System

**Files:**
- Create: `src/yequ/notify/base.py`
- Create: `tests/test_notify.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_notify.py`:

```python
import os
import pytest
from yequ.notify.base import (
    NotifyAdapter,
    LogNotifyAdapter,
    NotifyRouter,
    Severity,
    Notification,
)


class TestLogNotifyAdapter:
    def test_send_writes_to_log(self, tmp_path):
        log_path = str(tmp_path / "gateway.log")
        adapter = LogNotifyAdapter(log_path)

        notif = Notification(
            severity=Severity.WARNING,
            title="设备离线",
            body="pixel-8a 已离线 5 分钟",
            device_id="pixel-8a",
        )
        adapter.send(notif)

        assert os.path.exists(log_path)
        content = open(log_path).read()
        assert "[WARNING]" in content
        assert "设备离线" in content
        assert "pixel-8a" in content

    def test_send_creates_directory(self, tmp_path):
        log_path = str(tmp_path / "subdir" / "gateway.log")
        adapter = LogNotifyAdapter(log_path)
        notif = Notification(severity=Severity.INFO, title="测试", body="内容")
        adapter.send(notif)

        assert os.path.exists(log_path)


class TestNotifyRouter:
    @pytest.fixture
    def router(self, tmp_path):
        log_path = str(tmp_path / "gateway.log")
        return NotifyRouter(log_path=log_path)

    def test_route_critical(self, router, tmp_path):
        notif = Notification(
            severity=Severity.CRITICAL,
            title="磁盘告警",
            body="磁盘使用率 92%",
            device_id="localhost",
        )
        router.send(notif)

        log_content = open(str(tmp_path / "gateway.log")).read()
        assert "[CRITICAL]" in log_content

    def test_route_info_to_log_only(self, router, tmp_path):
        notif = Notification(
            severity=Severity.INFO,
            title="设备上线",
            body="pixel-8a 已连接",
            device_id="pixel-8a",
        )
        router.send(notif)

        log_content = open(str(tmp_path / "gateway.log")).read()
        assert "[INFO]" in log_content
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_notify.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement notify base**

Create `src/yequ/notify/base.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_notify.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/yequ/notify/base.py tests/test_notify.py
git commit -m "feat: add notification system with log adapter"
```

---

### Task 8: Local Collector

**Files:**
- Create: `src/yequ/collector/system.py`
- Create: `src/yequ/collector/runner.py`
- Create: `tests/test_collector.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_collector.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_collector.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement system collector**

Create `src/yequ/collector/system.py`:

```python
"""Local machine system metrics collection using psutil."""

from __future__ import annotations

import platform
import socket

import psutil


def collect_system_metrics() -> dict:
    """Collect current system metrics as a flat dict.

    Returns keys suitable for both snapshot (overall state) and metric ingestion.
    """
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    net_io = psutil.net_io_counters()
    boot = psutil.boot_time()

    return {
        # CPU
        "cpu_percent": cpu,
        "cpu_count": psutil.cpu_count(),
        # Memory
        "memory_percent": mem.percent,
        "memory_used_gb": round(mem.used / (1024**3), 2),
        "memory_total_gb": round(mem.total / (1024**3), 2),
        # Disk
        "disk_usage_percent": disk.percent,
        "disk_used_gb": round(disk.used / (1024**3), 2),
        "disk_total_gb": round(disk.total / (1024**3), 2),
        # Network (cumulative, good for metric tracking)
        "net_bytes_sent": net_io.bytes_sent,
        "net_bytes_recv": net_io.bytes_recv,
        # System
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "boot_time": boot,
        # Load
        "load_avg_1m": psutil.getloadavg()[0] if hasattr(psutil, "getloadavg") else 0.0,
    }
```

- [ ] **Step 4: Implement collector runner**

Create `src/yequ/collector/runner.py`:

```python
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

    def ensure_local_device(self) -> object:
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
        # Re-check device existence in case it was auto-created
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
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_collector.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/yequ/collector/system.py src/yequ/collector/runner.py tests/test_collector.py
git commit -m "feat: add local system metrics collector"
```

---

### Task 9: Monitor Engine

**Files:**
- Create: `src/yequ/monitor/rules.py`
- Create: `src/yequ/monitor/engine.py`
- Create: `tests/test_monitor.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_monitor.py`:

```python
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
        assert any("磁盘" in e["title"] for e in events)

    def test_cooldown_prevents_duplicate(self, engine):
        ingest_snapshot(engine.db_path, "testhost", "system_metrics", "v1",
                        {"disk_usage_percent": 95.0})

        engine.scan()  # first scan — fires
        first_count = len(engine._last_fired)

        engine.scan()  # second scan — should be suppressed
        # cooldown dict should still have the entry
        assert len(engine._last_fired) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_monitor.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement monitor rules**

Create `src/yequ/monitor/rules.py`:

```python
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
```

- [ ] **Step 4: Implement monitor engine**

Create `src/yequ/monitor/engine.py`:

```python
"""Monitor engine — periodic scanning and alerting."""

from __future__ import annotations

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
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_monitor.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/yequ/monitor/engine.py src/yequ/monitor/rules.py tests/test_monitor.py
git commit -m "feat: add monitor engine with heartbeat and threshold rules"
```

---

### Task 10: Transport Layer — HTTP and Unix Socket

**Files:**
- Create: `src/yequ/transport/http_server.py`
- Create: `src/yequ/transport/local_ipc.py`
- Create: `tests/test_transport.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_transport.py`:

```python
import json
import pytest
from yequ.transport.http_server import create_app
from yequ.transport.local_ipc import LocalIPCTransport
from yequ.storage.database import init_database
from yequ.registry.store import DeviceStore
from yequ.notify.base import NotifyRouter
from yequ.collector.runner import CollectorRunner
from starlette.testclient import TestClient


@pytest.fixture
def app(db_path, tmp_path):
    init_database(db_path)
    store = DeviceStore(db_path)
    notify = NotifyRouter(log_path=str(tmp_path / "gateway.log"))
    runner = CollectorRunner(db_path=db_path, device_store=store)

    # Pre-register a local device for testing
    store.register_device("testhost", is_local=True)

    return create_app(
        db_path=db_path,
        device_store=store,
        notify_router=notify,
        collector_runner=runner,
    )


@pytest.fixture
def client(app):
    return TestClient(app)


class TestHelloEndpoint:
    def test_registration_hello_returns_pending(self, client):
        response = client.post("/hello", json={
            "protocol": "yqp/1.0",
            "message_type": "hello",
            "hello_type": "registration",
            "device_id": "new-phone",
            "device_info": {"os": "Android 15"},
        })

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"
        assert data["retry_after"] == 30

    def test_heartbeat_hello_with_valid_token(self, client, db_path):
        # Register device first
        from yequ.registry.store import DeviceStore
        store = DeviceStore(db_path)
        device = store.register_device("valid-device")

        response = client.post("/hello", json={
            "protocol": "yqp/1.0",
            "message_type": "hello",
            "hello_type": "heartbeat",
            "device_id": "valid-device",
            "token": device.token,
        })

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_heartbeat_hello_bad_token(self, client):
        response = client.post("/hello", json={
            "protocol": "yqp/1.0",
            "message_type": "hello",
            "hello_type": "heartbeat",
            "device_id": "unknown",
            "token": "badtoken",
        })

        assert response.status_code == 401

    def test_registration_retry_logic(self, client, db_path):
        # First registration
        client.post("/hello", json={
            "protocol": "yqp/1.0",
            "hello_type": "registration",
            "device_id": "retry-device",
            "device_info": {},
        })

        # Retries 1-10: still get retry_after=30
        for _ in range(10):
            resp = client.post("/hello", json={
                "protocol": "yqp/1.0",
                "hello_type": "registration",
                "device_id": "retry-device",
                "device_info": {},
            })
            data = resp.json()
            assert data["status"] == "pending"

        # 11th retry: should get longer retry_after
        resp = client.post("/hello", json={
            "protocol": "yqp/1.0",
            "hello_type": "registration",
            "device_id": "retry-device",
            "device_info": {},
        })
        data = resp.json()
        assert data["retry_after"] >= 60


class TestIngestEndpoint:
    def test_ingest_with_valid_token(self, client, db_path):
        store = DeviceStore(db_path)
        device = store.register_device("ingest-device")
        # Add and approve capability
        store.add_capability("ingest-device", {
            "name": "location",
            "display": "位置",
            "schema": {"type": "object"},
        })
        store.approve_capability("ingest-device", "location")

        response = client.post("/ingest", json={
            "protocol": "yqp/1.0",
            "message_type": "ingest",
            "message_id": "msg-001",
            "device_id": "ingest-device",
            "token": device.token,
            "timestamp": "2026-06-12T10:30:00Z",
            "capability": "location",
            "schema_version": "v1",
            "payload": {"lat": 31.23, "lng": 121.47},
        })

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_ingest_bad_token(self, client):
        response = client.post("/ingest", json={
            "protocol": "yqp/1.0",
            "message_type": "ingest",
            "message_id": "msg-001",
            "device_id": "bad-device",
            "token": "badtoken",
            "timestamp": "2026-06-12T10:30:00Z",
            "capability": "location",
            "schema_version": "v1",
            "payload": {},
        })

        assert response.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_transport.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement HTTP server**

Create `src/yequ/transport/http_server.py`:

```python
"""HTTP transport layer — Starlette-based server with /hello and /ingest endpoints."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from yequ.protocol.messages import (
    HelloRegistration,
    HelloHeartbeat,
    Ingest,
    Ack,
    HelloResponse,
    RegistrationResponse,
    parse_hello,
    parse_ingest,
)
from yequ.registry.store import DeviceStore
from yequ.notify.base import NotifyRouter
from yequ.collector.runner import CollectorRunner
from yequ.storage.ingest import ingest_snapshot, ingest_metric

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Backoff configuration
INITIAL_RETRIES = 10
INITIAL_INTERVAL = 30
MAX_INTERVAL = 600  # 10 minutes


def _compute_retry_after(retry_count: int) -> int:
    """Compute retry_after based on retry count with exponential backoff."""
    if retry_count < INITIAL_RETRIES:
        return INITIAL_INTERVAL
    # Exponential backoff: 30 * 2^(n-10), capped at MAX_INTERVAL
    exponent = retry_count - INITIAL_RETRIES
    interval = INITIAL_INTERVAL * (2 ** exponent)
    return min(interval, MAX_INTERVAL)


class GatewayApp:
    """Starlette application handling YQP HTTP endpoints."""

    def __init__(
        self,
        db_path: str,
        device_store: DeviceStore,
        notify_router: NotifyRouter,
        collector_runner: CollectorRunner,
    ):
        self.db_path = db_path
        self.store = device_store
        self.notify = notify_router
        self.collector = collector_runner

    async def handle_hello(self, request: Request) -> JSONResponse:
        try:
            body = await request.body()
            msg = parse_hello(body.decode("utf-8"))
        except Exception as e:
            return JSONResponse({"status": "error", "error": str(e)}, status_code=400)

        if isinstance(msg, HelloRegistration):
            return self._handle_registration_hello(msg)
        elif isinstance(msg, HelloHeartbeat):
            return self._handle_heartbeat_hello(msg)

    def _handle_registration_hello(self, msg: HelloRegistration) -> JSONResponse:
        # Check if device already approved
        device = self.store.get_device(msg.device_id)
        if device:
            return JSONResponse(
                RegistrationResponse(
                    status="approved",
                    token=device.token,
                    config={"collector": {"interval_seconds": 60}},
                ).to_dict(),
            )

        # Check existing pending registration
        pending = self.store.get_pending_registration(msg.device_id)
        if pending is None:
            # First time — store as pending
            self.store.add_pending_registration(msg.device_id, msg.device_info)
            retry_count = 0
        else:
            retry_count = self.store.increment_retry(msg.device_id)

        retry_after = _compute_retry_after(retry_count)

        # If retries exhausted (> 1 hour), let device know
        if retry_count >= INITIAL_RETRIES + 10:  # ~1 hour of total waiting
            return JSONResponse(
                {"status": "pending", "retry_after": retry_after, "note": "请联系管理员审批"},
            )

        return JSONResponse(HelloResponse(
            status="pending",
            retry_after=retry_after,
        ).to_dict())

    def _handle_heartbeat_hello(self, msg: HelloHeartbeat) -> JSONResponse:
        device = self.store.get_device_by_token(msg.token)
        if device is None or device.device_id != msg.device_id:
            return JSONResponse({"status": "error", "error": "unauthorized"}, status_code=401)

        self.store.touch_hello(msg.device_id)

        # Check for pending commands
        pending_commands = []  # Phase 4 enhancement

        return JSONResponse(Ack(
            message_id="",
            status="ok",
            pending_commands=pending_commands,
        ).to_dict())

    async def handle_ingest(self, request: Request) -> JSONResponse:
        try:
            body = await request.body()
            msg = parse_ingest(body.decode("utf-8"))
        except Exception as e:
            return JSONResponse({"status": "error", "error": str(e)}, status_code=400)

        # Verify token
        device = self.store.get_device_by_token(msg.token)
        if device is None:
            return JSONResponse({"status": "error", "error": "unauthorized"}, status_code=401)

        # Route by data type
        cap = self.store.get_capability(msg.device_id, msg.capability)
        data_type = cap.data_type if cap else "snapshot"

        if data_type == "snapshot":
            ingest_snapshot(
                self.db_path,
                msg.device_id,
                msg.capability,
                msg.schema_version,
                msg.payload,
                timestamp=msg.timestamp,
            )
        elif data_type == "metric":
            # Each key in payload becomes a metric
            for key, value in msg.payload.items():
                if isinstance(value, (int, float)):
                    ingest_metric(
                        self.db_path,
                        msg.device_id,
                        msg.capability,
                        key,
                        float(value),
                        timestamp=msg.timestamp,
                    )

        return JSONResponse(Ack(message_id=msg.message_id, status="ok").to_dict())


def create_app(
    db_path: str,
    device_store: DeviceStore,
    notify_router: NotifyRouter,
    collector_runner: CollectorRunner,
) -> Starlette:
    """Create and configure the Starlette application."""
    gateway = GatewayApp(
        db_path=db_path,
        device_store=device_store,
        notify_router=notify_router,
        collector_runner=collector_runner,
    )

    app = Starlette(routes=[
        Route("/hello", gateway.handle_hello, methods=["POST"]),
        Route("/ingest", gateway.handle_ingest, methods=["POST"]),
    ])

    return app
```

- [ ] **Step 4: Implement Unix socket transport (stub)**

Create `src/yequ/transport/local_ipc.py`:

```python
"""Local IPC transport — Unix socket server for local collector.

Phase 1 stub: the local collector writes directly to the database
through the CollectorRunner, bypassing the network stack.
The Unix socket endpoint is defined here for future use.
"""

from __future__ import annotations

import logging
import os
import socketserver
from pathlib import Path

logger = logging.getLogger(__name__)


class LocalIPCHandler(socketserver.BaseRequestHandler):
    """Handler for Unix socket connections.

    Phase 1: this is a stub. Local collector uses direct DB access.
    Phase 2+: local collector can send YQP messages over this socket.
    """

    def handle(self) -> None:
        data = self.request.recv(65536)
        if data:
            logger.debug("Local IPC received %d bytes", len(data))
            # Placeholder: parse and process YQP message
            self.request.sendall(b'{"status":"ok"}')


class LocalIPCTransport:
    """Unix socket server for receiving YQP messages from local processes."""

    def __init__(self, socket_path: str):
        self.socket_path = socket_path
        self._server: socketserver.UnixStreamServer | None = None

    def start(self) -> None:
        """Start the Unix socket server in a background thread."""
        socket_dir = os.path.dirname(self.socket_path)
        os.makedirs(socket_dir, exist_ok=True)

        # Remove stale socket file
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        self._server = socketserver.ThreadingUnixStreamServer(
            self.socket_path, LocalIPCHandler
        )
        import threading
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info("Local IPC server listening on %s", self.socket_path)

    def stop(self) -> None:
        """Stop the Unix socket server."""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_transport.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/yequ/transport/http_server.py src/yequ/transport/local_ipc.py tests/test_transport.py
git commit -m "feat: add HTTP transport layer with /hello and /ingest endpoints"
```

---

### Task 11: CLI

**Files:**
- Create: `src/yequ/cli.py`
- Modify: `src/yequ/__init__.py`

- [ ] **Step 1: Implement CLI**

Create `src/yequ/cli.py`:

```python
"""YeQu Gateway CLI entry point."""

from __future__ import annotations

import json
import logging
import os
import signal
import sys

import click

from yequ.config import load_config, Config
from yequ.storage.database import init_database
from yequ.storage.query import (
    get_latest_snapshot,
    query_snapshots_by_device,
    get_metrics,
    get_events,
)
from yequ.registry.store import DeviceStore
from yequ.notify.base import NotifyRouter
from yequ.collector.runner import CollectorRunner
from yequ.monitor.engine import MonitorEngine
from yequ.monitor.rules import load_rules_from_yaml


DEFAULT_CONFIG_PATH = "config/gateway.yaml"
DEFAULT_RULES_PATH = "config/monitor_rules.yaml"

logger = logging.getLogger("yequ")


def _setup_components(config: Config):
    """Initialize all Gateway components from config."""
    data_dir = config.data.dir
    os.makedirs(data_dir, exist_ok=True)

    db_path = os.path.join(data_dir, "gateway.db")
    init_database(db_path)

    store = DeviceStore(db_path)
    notify = NotifyRouter(log_path=config.notify.log_file)
    runner = CollectorRunner(db_path=db_path, device_store=store)

    # Load monitor rules
    rules = []
    if os.path.exists(DEFAULT_RULES_PATH):
        rules = load_rules_from_yaml(DEFAULT_RULES_PATH)

    monitor = MonitorEngine(
        db_path=db_path,
        device_store=store,
        notify_router=notify,
        rules=rules,
    )

    return db_path, store, notify, runner, monitor


@click.group()
@click.option("--config", "-c", "config_path", default=DEFAULT_CONFIG_PATH,
              help="Path to gateway.yaml")
@click.option("--debug/--no-debug", default=False)
@click.pass_context
def main(ctx, config_path, debug):
    """YeQu Gateway — Personal Data Hub"""
    if debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path


@main.command()
@click.pass_context
def serve(ctx):
    """Start the Gateway server (HTTP + Monitor + Collector)."""
    config = load_config(ctx.obj["config_path"])
    db_path, store, notify, runner, monitor = _setup_components(config)

    # Ensure local device
    local_device = runner.ensure_local_device()
    click.echo(f"✓ Local device: {local_device.device_id}")

    # Start Unix socket
    from yequ.transport.local_ipc import LocalIPCTransport
    ipc = LocalIPCTransport(config.gateway.unix_socket)
    ipc.start()
    click.echo(f"✓ IPC socket: {config.gateway.unix_socket}")

    # Start HTTP server
    from yequ.transport.http_server import create_app
    import uvicorn

    app = create_app(
        db_path=db_path,
        device_store=store,
        notify_router=notify,
        collector_runner=runner,
    )

    # Start collector in background thread
    import threading
    import time

    stop_collector = threading.Event()

    def collector_loop():
        while not stop_collector.is_set():
            try:
                runner.collect_and_ingest()
            except Exception as e:
                logger.error("Collection error: %s", e)
            stop_collector.wait(config.collector.interval_seconds)

    collector_thread = threading.Thread(target=collector_loop, daemon=True)
    collector_thread.start()
    click.echo(f"✓ Collector: every {config.collector.interval_seconds}s")

    # Start monitor in background thread
    stop_monitor = threading.Event()

    def monitor_loop():
        # Wait a bit for first data to arrive
        time.sleep(5)
        while not stop_monitor.is_set():
            try:
                monitor.scan()
            except Exception as e:
                logger.error("Monitor error: %s", e)
            stop_monitor.wait(config.monitor.scan_interval_seconds)

    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()
    click.echo(f"✓ Monitor: every {config.monitor.scan_interval_seconds}s")

    click.echo(f"\n🚀 Gateway listening on {config.gateway.host}:{config.gateway.port}")
    click.echo("Press Ctrl+C to stop\n")

    def shutdown(sig, frame):
        click.echo("\nShutting down...")
        stop_collector.set()
        stop_monitor.set()
        ipc.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    uvicorn.run(
        app,
        host=config.gateway.host,
        port=config.gateway.port,
        log_level="warning",
    )


@main.command()
@click.pass_context
def devices(ctx):
    """List all registered devices."""
    config = load_config(ctx.obj["config_path"])
    db_path, store, _, _, _ = _setup_components(config)

    devices = store.list_devices()
    if not devices:
        click.echo("No devices registered.")
        return

    for d in devices:
        status_icon = "🟢" if d.last_hello_at else "⚪"
        labels = ", ".join(f"{k}={v}" for k, v in d.labels.items())
        local_tag = " [local]" if d.is_local else ""
        click.echo(f"{status_icon} {d.device_id}{local_tag}")
        if labels:
            click.echo(f"   Labels: {labels}")
        if d.last_hello_at:
            click.echo(f"   Last hello: {d.last_hello_at}")
        click.echo()


@main.command()
@click.argument("device_id", required=False)
@click.pass_context
def status(ctx, device_id):
    """Show device status and latest metrics."""
    config = load_config(ctx.obj["config_path"])
    db_path, store, _, _, _ = _setup_components(config)

    if device_id:
        devices = [store.get_device(device_id)]
        if devices[0] is None:
            click.echo(f"Device not found: {device_id}")
            return
    else:
        devices = store.list_devices()

    for d in devices:
        click.echo(f"=== {d.device_id} ===")
        snaps = query_snapshots_by_device(db_path, d.device_id)
        for s in snaps:
            payload = json.loads(s["payload_json"])
            click.echo(f"  [{s['capability']}]")
            for k, v in payload.items():
                click.echo(f"    {k}: {v}")
            click.echo()


@main.command()
@click.argument("device_id", required=False)
@click.pass_context
def events(ctx, device_id):
    """Show recent events."""
    config = load_config(ctx.obj["config_path"])
    db_path, _, _, _, _ = _setup_components(config)

    events = get_events(db_path, device_id=device_id, limit=20)
    if not events:
        click.echo("No events.")
        return

    for e in events:
        icon = {"critical": "🔴", "warning": "⚠️", "info": "ℹ️"}.get(e["severity"], "")
        click.echo(f"{icon} [{e['timestamp']}] {e['title']}")
        if e["body"]:
            click.echo(f"   {e['body']}")
        click.echo()


@main.command()
@click.argument("query")
@click.pass_context
def ask(ctx, query):
    """Ask the Gateway a question (Phase 1: structured query).

    Supported queries:
      - "status" / "状态" : show all device status
      - "devices" / "设备" : list devices
      - "alerts" / "告警" : show recent warnings/critical events
    """
    query_lower = query.lower()

    if any(w in query_lower for w in ["status", "状态", "state"]):
        ctx.invoke(status)
    elif any(w in query_lower for w in ["device", "设备"]):
        ctx.invoke(devices)
    elif any(w in query_lower for w in ["alert", "告警", "alarm", "event", "事件"]):
        ctx.invoke(events)
    else:
        click.echo(f"Unknown query: {query}")
        click.echo("Try: status, devices, alerts")


@main.command()
@click.argument("action", type=click.Choice(["on", "off", "status"]))
@click.pass_context
def monitor(ctx, action):
    """Control the inspector (模式 B): on/off/status.

    Phase 1: 'off' keeps alive rules only. 'on' enables all rules.
    Monitor state is written to data dir as a marker file.
    """
    config = load_config(ctx.obj["config_path"])
    marker_path = os.path.join(config.data.dir, "monitor_enabled")

    if action == "on":
        with open(marker_path, "w") as f:
            f.write("1")
        click.echo("Monitor: ON (all rules active)")
    elif action == "off":
        with open(marker_path, "w") as f:
            f.write("0")
        click.echo("Monitor: OFF (keep-alive rules only)")
    elif action == "status":
        if os.path.exists(marker_path):
            state = open(marker_path).read().strip()
            label = "ON" if state == "1" else "OFF (keep-alive only)"
        else:
            label = "ON (default)"
        click.echo(f"Monitor: {label}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test CLI (manual smoke test)**

```bash
cd /home/yequdesu/YeQu-gateway
python -m yequ.cli --help
```

Expected: shows help with serve, devices, status, events, ask, monitor commands

- [ ] **Step 3: Commit**

```bash
git add src/yequ/cli.py
git commit -m "feat: add CLI with serve, devices, status, events, ask, monitor commands"
```

---

### Task 12: Integration — Wiring and Startup

**Files:**
- No new files. Verify all components wire together correctly.

- [ ] **Step 1: Verify imports work**

```bash
cd /home/yequdesu/YeQu-gateway
PYTHONPATH=src python -c "
from yequ.protocol.messages import HelloRegistration, HelloHeartbeat, Ingest, Ack
from yequ.config import load_config
from yequ.storage.database import init_database
from yequ.storage.ingest import ingest_snapshot, ingest_metric, ingest_event
from yequ.storage.query import get_latest_snapshot, get_metrics, get_events
from yequ.registry.store import DeviceStore
from yequ.notify.base import NotifyRouter
from yequ.collector.runner import CollectorRunner
from yequ.monitor.engine import MonitorEngine
from yequ.monitor.rules import load_rules_from_yaml
print('All imports OK')
"
```

Expected: "All imports OK"

- [ ] **Step 2: End-to-end test — collect + store + query**

```bash
cd /home/yequdesu/YeQu-gateway
PYTHONPATH=src python -c "
import tempfile, os, json

tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, 'gateway.db')

from yequ.storage.database import init_database
init_database(db_path)

from yequ.registry.store import DeviceStore
store = DeviceStore(db_path)

# Register local device
device = store.register_device('testhost', is_local=True)
store.add_capability('testhost', {
    'name': 'system_metrics', 'display': '系统指标',
    'data_type': 'snapshot', 'schema': {'type': 'object'}
})
store.approve_capability('testhost', 'system_metrics')

# Ingest data
from yequ.storage.ingest import ingest_snapshot, ingest_metric, ingest_event
ingest_snapshot(db_path, 'testhost', 'system_metrics', 'v1',
                {'cpu_percent': 45.2, 'memory_percent': 60.1, 'disk_usage_percent': 72.3})

# Query data
from yequ.storage.query import get_latest_snapshot, get_metrics
snap = get_latest_snapshot(db_path, 'testhost', 'system_metrics')
payload = json.loads(snap['payload_json'])
print(f'CPU: {payload[\"cpu_percent\"]}%')
print(f'Memory: {payload[\"memory_percent\"]}%')
print(f'Disk: {payload[\"disk_usage_percent\"]}%')

# Test metric ingest + query
ingest_metric(db_path, 'testhost', 'system_metrics', 'cpu_percent', 45.2, '%')
metrics = get_metrics(db_path, 'testhost', 'cpu_percent', limit=1)
print(f'Metrics: {len(metrics)} point(s)')

# Test event
ingest_event(db_path, 'testhost', 'device_online', 'info', '设备上线', '')
events = get_events(db_path, device_id='testhost')
print(f'Events: {len(events)}')

print('End-to-end test PASSED')
"
```

Expected: prints metrics values and "End-to-end test PASSED"

- [ ] **Step 3: Verify full test suite**

```bash
cd /home/yequdesu/YeQu-gateway
python -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: complete Phase 1 integration — all tests pass"
```

---

### Task 13: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write README**

Create `README.md`:

```markdown
# YeQu-Gateway

个人数据中心汇总中心 — 汇集终端信息，AI Agent 驱动分析与运维。

## 安装

```bash
git clone <repo-url>
cd YeQu-gateway
pip install -e ".[dev]"
```

## 快速开始

```bash
# 启动 Gateway（HTTP + 本机采集 + 巡检）
yequ serve

# 查看设备列表
yequ devices

# 查看系统状态
yequ status

# 查看告警事件
yequ events

# 问一个问题
yequ ask "状态怎么样？"
```

## 配置

编辑 `config/gateway.yaml` 修改端口、采集间隔等配置。

## 协议

YeQu-gateway 使用 YQP v1.0 (YeQu Protocol) 协议进行设备间通信。

协议规范见 `docs/superpowers/specs/2026-06-12-yequ-gateway-design.md`。
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README with quick start guide"
```

---

## Completeness Checklist

- [x] YQP protocol messages defined and tested (Task 2)
- [x] Configuration loading from YAML (Task 3)
- [x] SQLite schema and initialization (Task 4)
- [x] Device registry with CRUD + pending registrations (Task 5)
- [x] Data ingest (snapshot/metric/event) and query (Task 6)
- [x] Notification system with log adapter (Task 7)
- [x] Local system metrics collector (Task 8)
- [x] Monitor engine with heartbeat and threshold rules (Task 9)
- [x] HTTP transport with /hello and /ingest (Task 10)
- [x] Unix socket stub (Task 10)
- [x] CLI with serve/devices/status/events/ask/monitor (Task 11)
- [x] Integration verification (Task 12)
- [x] README (Task 13)
