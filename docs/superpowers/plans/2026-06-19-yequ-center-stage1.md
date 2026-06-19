# YeQu Center Stage 1: Basic Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the minimal runnable FastAPI skeleton for YeQu Center — config, logging, protocol enums/base models, SQLAlchemy models with Alembic migrations, health check endpoint, and passing tests.

**Architecture:** Monolithic package under `src/yequ/`. Protocol enums and error codes live in `src/yequ/protocol/` as the single source of truth. SQLAlchemy models in `src/yequ/models/`. FastAPI app in `src/yequ/api/`. All DB changes via Alembic. SQLite for dev, configurable to PostgreSQL.

**Tech Stack:** Python 3.11+ (3.12 compatible), FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, structlog, pytest, ruff, mypy

---

## File Map

| File | Responsibility |
|---|---|
| `pyproject.toml` | Project metadata, dependencies, tool config |
| `src/yequ/__init__.py` | Package init, version |
| `src/yequ/config.py` | Pydantic Settings — reads env/file, single Settings class |
| `src/yequ/logging.py` | structlog configuration — structured JSON logging |
| `src/yequ/db.py` | SQLAlchemy engine, AsyncSession factory, get_db dependency |
| `src/yequ/protocol/__init__.py` | Re-exports all protocol types |
| `src/yequ/protocol/enums.py` | All shared enums: NodeStatus, JobStatus, RiskLevel, Effect, Idempotency, etc. |
| `src/yequ/protocol/errors.py` | Standard error codes and YqpError model |
| `src/yequ/protocol/envelope.py` | YQP Envelope model (Pydantic) |
| `src/yequ/models/__init__.py` | Re-exports all models |
| `src/yequ/models/base.py` | SQLAlchemy DeclarativeBase, common mixins (TimestampsMixin) |
| `src/yequ/models/node.py` | Node table model |
| `src/yequ/models/capability.py` | Capability (Function/Signal manifest) table model |
| `src/yequ/models/invocation.py` | Invocation table model |
| `src/yequ/models/job.py` | Job table model |
| `src/yequ/models/timeline.py` | TimelineEvent table model |
| `src/yequ/models/session.py` | Session table model |
| `src/yequ/api/__init__.py` | API package init |
| `src/yequ/api/app.py` | FastAPI application factory |
| `src/yequ/api/deps.py` | Dependency injection (get_db, get_settings) |
| `src/yequ/api/routes/__init__.py` | Routes package init |
| `src/yequ/api/routes/health.py` | GET /healthz endpoint |
| `src/yequ/main.py` | CLI entry point: `uvicorn` launcher |
| `alembic.ini` | Alembic configuration |
| `alembic/env.py` | Alembic env — imports Base, configures target_metadata |
| `alembic/versions/001_initial.py` | Initial migration: all 6 tables |
| `tests/__init__.py` | Tests package init |
| `tests/conftest.py` | pytest fixtures: test DB, test client, settings override |
| `tests/test_health.py` | Health check endpoint tests |
| `tests/test_models.py` | Model instantiation and basic persistence tests |

---

### Task 1: Project Setup — pyproject.toml and Virtual Environment

**Files:**
- Create: `pyproject.toml`

- [ ] **Step 1: Write pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "yequ"
version = "0.1.0"
description = "YeQu Center — personal infrastructure control center"
requires-python = ">=3.11"
dependencies = [
    "fastapi[standard]>=0.115.0",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "sqlalchemy[asyncio]>=2.0",
    "alembic>=1.13",
    "structlog>=24.0",
    "aiosqlite>=0.20",
    "uvicorn[standard]>=0.30",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "httpx>=0.27",
    "ruff>=0.5",
    "mypy>=1.11",
]

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP", "B", "SIM"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"

[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
warn_unused_ignores = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create virtual environment and install**

```bash
cd /home/yequdesu/YeQu-gateway
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

- [ ] **Step 3: Verify environment**

```bash
python -c "import fastapi; import pydantic; import sqlalchemy; import structlog; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add pyproject.toml with project dependencies"
```

---

### Task 2: Protocol Layer — Enums, Errors, Envelope

**Files:**
- Create: `src/yequ/__init__.py`
- Create: `src/yequ/protocol/__init__.py`
- Create: `src/yequ/protocol/enums.py`
- Create: `src/yequ/protocol/errors.py`
- Create: `src/yequ/protocol/envelope.py`

- [ ] **Step 1: Create package init with version**

`src/yequ/__init__.py`:
```python
"""YeQu Center — personal infrastructure control center."""

__version__ = "0.1.0"
```

- [ ] **Step 2: Define all shared enums**

`src/yequ/protocol/enums.py`:
```python
"""Shared enums — single source of truth for Center, Daemon, and Agent."""

from enum import StrEnum


class YqpVersion(StrEnum):
    V0_1 = "0.1"


class MessageType(StrEnum):
    """All YQP message types."""

    # Node lifecycle
    NODE_HELLO = "node.hello"
    NODE_ACCEPTED = "node.accepted"
    NODE_REGISTER_CAPABILITIES = "node.register_capabilities"
    REGISTRY_ACCEPTED = "registry.accepted"
    NODE_HEARTBEAT = "node.heartbeat"
    NODE_RECONCILE_JOBS = "node.reconcile_jobs"
    JOB_RECONCILIATION = "job.reconciliation"

    # Signal
    SIGNAL_REPORT = "signal.report"

    # Job delivery
    JOB_POLL = "job.poll"
    JOB_AVAILABLE = "job.available"
    JOB_EMPTY = "job.empty"
    JOB_DISPATCH = "job.dispatch"
    JOB_ACCEPTED = "job.accepted"
    JOB_EVENT = "job.event"
    JOB_FINISHED = "job.finished"
    JOB_LEASE_RENEW = "job.lease_renew"
    JOB_LEASE_ACCEPTED = "job.lease_accepted"
    JOB_LEASE_DENIED = "job.lease_denied"
    JOB_CANCEL = "job.cancel"

    # Error
    ERROR = "error"


class NodeStatus(StrEnum):
    PROVISIONED = "provisioned"
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    REJOINING = "rejoining"


class JobStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class JobDeliveryMode(StrEnum):
    POLL = "poll"
    WEBSOCKET_PUSH = "websocket_push"


class RiskLevel(StrEnum):
    SAFE = "safe"
    MAINTENANCE = "maintenance"
    DESTRUCTIVE = "destructive"
    CATASTROPHIC = "catastrophic"


class Effect(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    EXTERNAL = "external"


class Idempotency(StrEnum):
    IDEMPOTENT = "idempotent"
    NON_IDEMPOTENT = "non_idempotent"
    TRANSACTIONAL = "transactional"


class ExecutionMode(StrEnum):
    AUTO = "auto"
    ASSIST = "assist"
    READONLY = "readonly"
    MANUAL = "manual"


class SignalScope(StrEnum):
    NODE = "node"
    PLUGIN = "plugin"
    RESOURCE = "resource"


class ConflictPolicy(StrEnum):
    ALLOW_PARALLEL = "allow_parallel"
    SERIALIZE = "serialize"
    REJECT_IF_RUNNING = "reject_if_running"


class ReconciliationAction(StrEnum):
    CONTINUE = "continue"
    CANCEL = "cancel"
    ACCEPT_RESULT = "accept_result"
    DISCARD_RESULT = "discard_result"
    FORGET = "forget"


class InvocationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    PARTIAL = "partial"


class PluginStatus(StrEnum):
    LOADED = "loaded"
    ERROR = "error"


class ActorType(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    INTERRUPTED = "interrupted"
```

- [ ] **Step 3: Define error codes and YqpError**

`src/yequ/protocol/errors.py`:
```python
"""Standard error codes and error structures for YQP."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCode(StrEnum):
    AUTH_FAILED = "auth_failed"
    SCHEMA_INVALID = "schema_invalid"
    FUNCTION_NOT_AVAILABLE = "function_not_available"
    JOB_NOT_FOUND = "job_not_found"
    LEASE_EXPIRED = "lease_expired"
    CANCEL_REQUESTED = "cancel_requested"
    PLUGIN_LOAD_FAILED = "plugin_load_failed"
    VERSION_INCOMPATIBLE = "version_incompatible"
    INTERNAL_ERROR = "internal_error"
    DUPLICATE_MESSAGE = "duplicate_message"
    TIMESTAMP_OUT_OF_RANGE = "timestamp_out_of_range"
    NODE_NOT_FOUND = "node_not_found"
    NODE_OFFLINE = "node_offline"
    INVALID_STATE_TRANSITION = "invalid_state_transition"
    CALL_DEPTH_EXCEEDED = "call_depth_exceeded"
    MAX_STEPS_EXCEEDED = "max_steps_exceeded"
    MAX_DURATION_EXCEEDED = "max_duration_exceeded"
    CIRCULAR_DEPENDENCY = "circular_dependency"
    POLICY_DENIED = "policy_denied"


class YqpError(BaseModel):
    """Standard YQP error structure."""

    code: ErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 4: Define YQP Envelope**

`src/yequ/protocol/envelope.py`:
```python
"""YQP message envelope — every message is wrapped in this."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from yequ.protocol.enums import MessageType, YqpVersion


class YqpEnvelope(BaseModel):
    """Unified YQP message envelope.

    All messages between Center and Node use this structure.
    Center and Agent communication may also use this in the future.
    """

    yqp_version: YqpVersion = YqpVersion.V0_1
    message_id: str = Field(..., description="Unique message ID for dedup and audit")
    message_type: MessageType = Field(..., description="Message type")
    trace_id: str = Field(..., description="Trace ID for distributed tracing")
    node_id: str | None = Field(default=None, description="Node ID, required for Node messages")
    session_id: str | None = Field(default=None, description="Session ID for user/agent context")
    timestamp: datetime = Field(..., description="Message generation time")
    payload: dict[str, Any] = Field(default_factory=dict, description="Business payload")
```

- [ ] **Step 5: Create protocol __init__ that re-exports everything**

`src/yequ/protocol/__init__.py`:
```python
"""YQP Protocol — single source of truth for message types, enums, and error codes."""

from yequ.protocol.enums import (
    ActorType,
    ConflictPolicy,
    Effect,
    ExecutionMode,
    Idempotency,
    InvocationStatus,
    JobDeliveryMode,
    JobStatus,
    MessageType,
    NodeStatus,
    PluginStatus,
    ReconciliationAction,
    RiskLevel,
    SessionStatus,
    SignalScope,
    YqpVersion,
)
from yequ.protocol.envelope import YqpEnvelope
from yequ.protocol.errors import ErrorCode, YqpError

__all__ = [
    "ActorType",
    "ConflictPolicy",
    "Effect",
    "ErrorCode",
    "ExecutionMode",
    "Idempotency",
    "InvocationStatus",
    "JobDeliveryMode",
    "JobStatus",
    "MessageType",
    "NodeStatus",
    "PluginStatus",
    "ReconciliationAction",
    "RiskLevel",
    "SessionStatus",
    "SignalScope",
    "YqpEnvelope",
    "YqpError",
    "YqpVersion",
]
```

- [ ] **Step 6: Run ruff check and commit**

```bash
cd /home/yequdesu/YeQu-gateway && source .venv/bin/activate
ruff check src/yequ/
git add src/yequ/__init__.py src/yequ/protocol/
git commit -m "feat: add protocol layer — enums, error codes, YQP envelope"
```

---

### Task 3: Configuration and Logging

**Files:**
- Create: `src/yequ/config.py`
- Create: `src/yequ/logging.py`

- [ ] **Step 1: Write config module**

`src/yequ/config.py`:
```python
"""Application configuration via Pydantic Settings.

Reads from environment variables, .env file, or defaults.
Prefix: YEQU_
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """YeQu Center configuration."""

    model_config = SettingsConfigDict(
        env_prefix="YEQU_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_name: str = "YeQu Center"
    debug: bool = False

    # Database
    database_url: str = "sqlite+aiosqlite:///yequ.db"

    # Node protocol
    allowed_timestamp_skew_sec: int = 30
    message_dedup_ttl_sec: int = 300  # 5 minutes
    default_heartbeat_interval_sec: int = 10
    default_heartbeat_timeout_multiplier: int = 3
    default_signal_report_interval_sec: int = 5
    default_signal_stale_multiplier: int = 3
    default_job_poll_interval_sec: int = 3
    default_lease_sec: int = 30

    # Recovery
    recovery_window_sec: int = 300  # 5 minutes for nodes to rejoin after restart

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"  # json or console

    @property
    def heartbeat_timeout_sec(self) -> int:
        return self.default_heartbeat_interval_sec * self.default_heartbeat_timeout_multiplier

    @property
    def signal_stale_sec(self) -> int:
        return self.default_signal_report_interval_sec * self.default_signal_stale_multiplier


def get_settings() -> Settings:
    """Return a cached Settings instance (loads once)."""
    return Settings()


# Project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
```

- [ ] **Step 2: Write logging module**

`src/yequ/logging.py`:
```python
"""Structured logging via structlog."""

import logging
import sys

import structlog

from yequ.config import get_settings


def setup_logging() -> None:
    """Configure structlog for the application.

    Call once at startup. In JSON mode, logs are machine-readable.
    In console mode, logs are human-readable with colors.
    """
    settings = get_settings()

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if settings.log_format == "json":
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """Get a structured logger instance."""
    return structlog.get_logger(name or __name__)
```

- [ ] **Step 3: Quick smoke test**

```python
from yequ.config import get_settings
from yequ.logging import setup_logging, get_logger

settings = get_settings()
print(f"Database URL: {settings.database_url}")
print(f"Heartbeat timeout: {settings.heartbeat_timeout_sec}s")

setup_logging()
log = get_logger("test")
log.info("logging works", app_name=settings.app_name)
```

- [ ] **Step 4: Commit**

```bash
git add src/yequ/config.py src/yequ/logging.py
git commit -m "feat: add Pydantic config and structlog logging"
```

---

### Task 4: Database Setup — Engine, Session, Base

**Files:**
- Create: `src/yequ/db.py`
- Create: `src/yequ/models/__init__.py`
- Create: `src/yequ/models/base.py`

- [ ] **Step 1: Write database module**

`src/yequ/db.py`:
```python
"""Database engine and session factory.

Uses SQLAlchemy 2.0 async API. SQLite for dev, configurable to PostgreSQL.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yequ.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    echo=_settings.debug,
    connect_args={"check_same_thread": False} if "sqlite" in _settings.database_url else {},
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields an AsyncSession."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
```

- [ ] **Step 2: Write model base with common mixins**

`src/yequ/models/base.py`:
```python
"""SQLAlchemy declarative base and common mixins."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def generate_uuid() -> str:
    """Generate a UUID string with prefix."""
    return f"id_{uuid.uuid4().hex[:16]}"


class Base(DeclarativeBase):
    """Declarative base for all models."""

    pass


class TimestampMixin:
    """Mixin for created_at / updated_at columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
```

`src/yequ/models/__init__.py`:
```python
"""YeQu Center database models."""

from yequ.models.base import Base, TimestampMixin

__all__ = [
    "Base",
    "TimestampMixin",
]
```

- [ ] **Step 3: Smoke test — engine connects**

```python
import asyncio
from yequ.db import engine

async def test_connect():
    async with engine.connect() as conn:
        result = await conn.execute("SELECT 1")
        print(f"DB connected: {result.scalar()}")

asyncio.run(test_connect())
```
Expected: `DB connected: 1`

- [ ] **Step 4: Commit**

```bash
git add src/yequ/db.py src/yequ/models/
git commit -m "feat: add SQLAlchemy engine, session, and declarative base"
```

---

### Task 5: Core Table Models

**Files:**
- Create: `src/yequ/models/node.py`
- Create: `src/yequ/models/capability.py`
- Create: `src/yequ/models/invocation.py`
- Create: `src/yequ/models/job.py`
- Create: `src/yequ/models/timeline.py`
- Create: `src/yequ/models/session.py`
- Modify: `src/yequ/models/__init__.py` (add imports)

- [ ] **Step 1: Write Node model**

`src/yequ/models/node.py`:
```python
"""Node model — represents a connected device/execution environment."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from yequ.models.base import Base, TimestampMixin, generate_uuid
from yequ.protocol import NodeStatus


class Node(Base, TimestampMixin):
    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    node_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    node_name: Mapped[str] = mapped_column(String(256), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="compute")
    locality: Mapped[str] = mapped_column(String(16), nullable=False, default="local")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=NodeStatus.PROVISIONED
    )
    daemon_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    platform_os: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    platform_arch: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_interval_sec: Mapped[Optional[int]] = mapped_column(nullable=True)
    job_delivery_mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    capabilities: Mapped[list["Capability"]] = relationship(
        "Capability", back_populates="node", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Node(node_id={self.node_id!r}, status={self.status!r})>"
```

- [ ] **Step 2: Write Capability model**

`src/yequ/models/capability.py`:
```python
"""Capability model — stores Function and Signal manifests for each Node."""

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from yequ.models.base import Base, TimestampMixin, generate_uuid


class Capability(Base, TimestampMixin):
    __tablename__ = "capabilities"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    node_record_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plugin_id: Mapped[str] = mapped_column(String(256), nullable=False)
    plugin_version: Mapped[str] = mapped_column(String(32), nullable=False)
    capability_type: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # "function" or "signal"
    name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="loaded")
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Function-specific fields
    input_schema: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    output_schema: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    risk: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    effect: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    timeout_sec: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    idempotency: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    resource_keys: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    conflict_policy: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # Signal-specific fields
    scope: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    ttl_sec: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    value_schema: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # Snapshot tracking
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    node: Mapped["Node"] = relationship("Node", back_populates="capabilities")

    def __repr__(self) -> str:
        return f"<Capability(name={self.name!r}, type={self.capability_type!r}, node={self.node_record_id!r})>"
```

- [ ] **Step 3: Write Invocation model**

`src/yequ/models/invocation.py`:
```python
"""Invocation model — represents a semantic call intent by an Actor."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from yequ.models.base import Base, TimestampMixin, generate_uuid


class Invocation(Base, TimestampMixin):
    __tablename__ = "invocations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    invocation_id: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)  # user, agent, system
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    function_name: Mapped[str] = mapped_column(String(256), nullable=False)
    input_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    target_node_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    call_path: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    max_depth: Mapped[Optional[int]] = mapped_column(nullable=True)
    max_steps: Mapped[Optional[int]] = mapped_column(nullable=True)
    max_total_duration_sec: Mapped[Optional[int]] = mapped_column(nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<Invocation(invocation_id={self.invocation_id!r}, status={self.status!r})>"
```

- [ ] **Step 4: Write Job model**

`src/yequ/models/job.py`:
```python
"""Job model — represents an actual execution task dispatched to a Node."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from yequ.models.base import Base, TimestampMixin, generate_uuid


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    job_id: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    invocation_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    function_name: Mapped[str] = mapped_column(String(256), nullable=False)
    input_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="created")
    timeout_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    lease_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    output: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cancel_reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    progress_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    progress_message: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    def __repr__(self) -> str:
        return f"<Job(job_id={self.job_id!r}, status={self.status!r}, node={self.node_id!r})>"
```

- [ ] **Step 5: Write TimelineEvent model**

`src/yequ/models/timeline.py`:
```python
"""TimelineEvent model — immutable audit/event log."""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from yequ.models.base import Base, generate_uuid


class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    global_seq: Mapped[int] = mapped_column(
        BigInteger, autoincrement=True, nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    actor_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    invocation_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    job_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    node_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    message_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    trace_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)

    __mapper_args__ = {"eager_defaults": True}

    def __repr__(self) -> str:
        return f"<TimelineEvent(event_type={self.event_type!r}, global_seq={self.global_seq})>"
```

- [ ] **Step 6: Write Session model**

`src/yequ/models/session.py`:
```python
"""Session model — interactive context for a user or agent."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from yequ.models.base import Base, TimestampMixin, generate_uuid


class Session(Base, TimestampMixin):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_uuid)
    session_id: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)  # user or agent
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    close_reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)

    def __repr__(self) -> str:
        return f"<Session(session_id={self.session_id!r}, status={self.status!r})>"
```

- [ ] **Step 7: Update models __init__.py**

`src/yequ/models/__init__.py`:
```python
"""YeQu Center database models."""

from yequ.models.base import Base, TimestampMixin, generate_uuid
from yequ.models.capability import Capability
from yequ.models.invocation import Invocation
from yequ.models.job import Job
from yequ.models.node import Node
from yequ.models.session import Session
from yequ.models.timeline import TimelineEvent

__all__ = [
    "Base",
    "Capability",
    "Invocation",
    "Job",
    "Node",
    "Session",
    "TimelineEvent",
    "TimestampMixin",
    "generate_uuid",
]
```

- [ ] **Step 8: Verify imports work**

```bash
python -c "from yequ.models import Base, Node, Capability, Invocation, Job, TimelineEvent, Session; print('All models import OK')"
```

- [ ] **Step 9: Commit**

```bash
git add src/yequ/models/
git commit -m "feat: add core table models — Node, Capability, Invocation, Job, TimelineEvent, Session"
```

---

### Task 6: Alembic Setup and Initial Migration

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/001_initial_schema.py`

- [ ] **Step 1: Initialize Alembic and configure**

First run alembic init:
```bash
cd /home/yequdesu/YeQu-gateway && source .venv/bin/activate
alembic init alembic
```

Then write the env.py:

`alembic/env.py`:
```python
"""Alembic environment configuration for YeQu Center."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from yequ.config import get_settings
from yequ.models.base import Base

# Import all models so Base.metadata is populated
import yequ.models.node  # noqa: F401
import yequ.models.capability  # noqa: F401
import yequ.models.invocation  # noqa: F401
import yequ.models.job  # noqa: F401
import yequ.models.timeline  # noqa: F401
import yequ.models.session  # noqa: F401

config = context.config
settings = get_settings()

# Override sqlalchemy.url from settings
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

Update `alembic.ini` sqlalchemy.url line:
```
sqlalchemy.url = sqlite+aiosqlite:///yequ.db
```

- [ ] **Step 2: Generate the initial migration**

```bash
cd /home/yequdesu/YeQu-gateway && source .venv/bin/activate
alembic revision --autogenerate -m "initial_schema"
```

- [ ] **Step 3: Apply the migration and verify**

```bash
alembic upgrade head
```
Expected: migration runs successfully, creates tables

Verify with:
```bash
python -c "
import asyncio
from yequ.db import engine
from sqlalchemy import text

async def check():
    async with engine.connect() as conn:
        result = await conn.execute(text(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\"))
        tables = [row[0] for row in result.fetchall()]
        print('Tables:', tables)
        assert 'nodes' in tables
        assert 'capabilities' in tables
        assert 'invocations' in tables
        assert 'jobs' in tables
        assert 'timeline_events' in tables
        assert 'sessions' in tables
        print('All tables created successfully')

asyncio.run(check())
"
```

- [ ] **Step 4: Commit**

```bash
git add alembic.ini alembic/env.py alembic/script.py.mako alembic/versions/
git commit -m "feat: add Alembic setup and initial schema migration"
```

---

### Task 7: FastAPI App and Health Check

**Files:**
- Create: `src/yequ/api/__init__.py`
- Create: `src/yequ/api/app.py`
- Create: `src/yequ/api/deps.py`
- Create: `src/yequ/api/routes/__init__.py`
- Create: `src/yequ/api/routes/health.py`
- Create: `src/yequ/main.py`

- [ ] **Step 1: Write FastAPI application factory**

`src/yequ/api/app.py`:
```python
"""FastAPI application factory."""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from yequ.logging import setup_logging, get_logger
from yequ.api.routes.health import router as health_router

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — setup and teardown."""
    setup_logging()
    log.info("yeau center starting", version="0.1.0")
    yield
    log.info("yeau center shutting down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="YeQu Center",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health_router)
    return app
```

- [ ] **Step 2: Write deps (get_db, get_settings)**

`src/yequ/api/deps.py`:
```python
"""FastAPI dependency injection."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from yequ.config import Settings, get_settings as _get_settings
from yequ.db import get_db as _get_db


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency: yields an async database session."""
    async for session in _get_db():
        yield session


def get_settings() -> Settings:
    """Dependency: provides application settings."""
    return _get_settings()
```

- [ ] **Step 3: Write health check endpoint**

`src/yequ/api/routes/__init__.py`:
```python
"""API route modules."""
```

`src/yequ/api/routes/health.py`:
```python
"""Health check endpoint."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.api.deps import get_db

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(db: AsyncSession = Depends(get_db)) -> dict:
    """Health check — verifies app is running and DB is reachable."""
    try:
        result = await db.execute(text("SELECT 1"))
        result.scalar()
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "version": "0.1.0",
        "database": "connected" if db_ok else "disconnected",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 4: Write entry point**

`src/yequ/main.py`:
```python
"""YeQu Center entry point."""

import uvicorn


def main() -> None:
    """Start the YeQu Center server."""
    uvicorn.run(
        "yequ.api.app:create_app",
        host="127.0.0.1",
        port=8000,
        factory=True,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Start the app and test health check**

```bash
cd /home/yequdesu/YeQu-gateway && source .venv/bin/activate
python src/yequ/main.py &
sleep 3
curl -s http://127.0.0.1:8000/healthz | python -m json.tool
# Expected: {"status":"ok","version":"0.1.0","database":"connected","timestamp":"..."}
kill %1
```

- [ ] **Step 6: Commit**

```bash
git add src/yequ/api/ src/yequ/main.py
git commit -m "feat: add FastAPI app with /healthz endpoint"
```

---

### Task 8: Tests — conftest, Health Check, Models

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_health.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write conftest with fixtures**

`tests/conftest.py`:
```python
"""pytest fixtures for YeQu Center tests."""

import asyncio
import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yequ.api.app import create_app
from yequ.config import Settings, get_settings
from yequ.models.base import Base

# Import all models so Base.metadata is populated
import yequ.models.node  # noqa: F401
import yequ.models.capability  # noqa: F401
import yequ.models.invocation  # noqa: F401
import yequ.models.job  # noqa: F401
import yequ.models.timeline  # noqa: F401
import yequ.models.session  # noqa: F401

TEST_DB_PATH = "test_yequ.db"


@pytest.fixture(autouse=True)
def override_settings(monkeypatch):
    """Override settings for test environment."""
    test_settings = Settings(
        database_url=f"sqlite+aiosqlite:///{TEST_DB_PATH}",
        debug=True,
        log_level="WARNING",
    )
    monkeypatch.setattr("yequ.config._get_settings", lambda: test_settings)
    monkeypatch.setattr("yequ.api.deps._get_settings", lambda: test_settings)
    monkeypatch.setattr("yequ.db._settings", test_settings)
    return test_settings


@pytest_asyncio.fixture
async def db_engine():
    """Create a test database engine with fresh tables."""
    db_url = f"sqlite+aiosqlite:///{TEST_DB_PATH}"
    engine = create_async_engine(db_url, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

    # Clean up test DB file
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(db_engine) -> AsyncGenerator[AsyncClient, None]:
    """Create a test HTTP client."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
```

`tests/__init__.py`:
```python
"""YeQu Center test suite."""
```

- [ ] **Step 2: Write health check tests**

`tests/test_health.py`:
```python
"""Tests for the /healthz endpoint."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_healthz_returns_ok(client: AsyncClient):
    """Health check should return status ok when DB is connected."""
    response = await client.get("/healthz")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"
    assert data["database"] == "connected"
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_healthz_response_is_json(client: AsyncClient):
    """Health check should return JSON content type."""
    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
```

- [ ] **Step 3: Write model persistence tests**

`tests/test_models.py`:
```python
"""Tests for database model instantiation and persistence."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models import Node, Capability, Invocation, Job, TimelineEvent, Session
from yequ.protocol import JobStatus, NodeStatus, RiskLevel, Effect, Idempotency


@pytest.mark.asyncio
async def test_create_and_query_node(db_session: AsyncSession):
    """Node model should persist and be queryable."""
    node = Node(
        node_id="test-node-1",
        node_name="Test Node",
        token_hash="hash_abc123",
        role="compute",
        locality="local",
        status=NodeStatus.PROVISIONED,
    )
    db_session.add(node)
    await db_session.commit()

    result = await db_session.execute(
        select(Node).where(Node.node_id == "test-node-1")
    )
    fetched = result.scalar_one()
    assert fetched.node_name == "Test Node"
    assert fetched.status == NodeStatus.PROVISIONED
    assert fetched.created_at is not None


@pytest.mark.asyncio
async def test_create_job_with_status(db_session: AsyncSession):
    """Job model should store status and transition history."""
    job = Job(
        job_id="job_test_001",
        invocation_id="inv_001",
        node_id="test-node-1",
        function_name="system.metrics.snapshot",
        status=JobStatus.CREATED,
        timeout_sec=30,
        lease_sec=10,
    )
    db_session.add(job)
    await db_session.commit()

    result = await db_session.execute(
        select(Job).where(Job.job_id == "job_test_001")
    )
    fetched = result.scalar_one()
    assert fetched.status == JobStatus.CREATED
    assert fetched.timeout_sec == 30
    assert fetched.attempt == 1


@pytest.mark.asyncio
async def test_create_capability_function(db_session: AsyncSession):
    """Capability model should store function manifests."""
    cap = Capability(
        plugin_id="system.metrics",
        plugin_version="0.1.0",
        capability_type="function",
        name="system.metrics.snapshot",
        risk=RiskLevel.SAFE,
        effect=Effect.READ,
        timeout_sec=5,
        idempotency=Idempotency.IDEMPOTENT,
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"cpu": {"type": "number"}}},
    )
    db_session.add(cap)
    await db_session.commit()

    result = await db_session.execute(
        select(Capability).where(Capability.name == "system.metrics.snapshot")
    )
    fetched = result.scalar_one()
    assert fetched.capability_type == "function"
    assert fetched.risk == RiskLevel.SAFE
    assert fetched.input_schema == {"type": "object", "properties": {}}


@pytest.mark.asyncio
async def test_create_capability_signal(db_session: AsyncSession):
    """Capability model should store signal manifests."""
    cap = Capability(
        plugin_id="system.metrics",
        plugin_version="0.1.0",
        capability_type="signal",
        name="system.cpu.usage",
        scope="node",
        ttl_sec=15,
        value_schema={"type": "number", "minimum": 0, "maximum": 100},
    )
    db_session.add(cap)
    await db_session.commit()

    result = await db_session.execute(
        select(Capability).where(Capability.name == "system.cpu.usage")
    )
    fetched = result.scalar_one()
    assert fetched.capability_type == "signal"
    assert fetched.scope == "node"
    assert fetched.ttl_sec == 15


@pytest.mark.asyncio
async def test_create_invocation(db_session: AsyncSession):
    """Invocation model should persist call intent."""
    inv = Invocation(
        invocation_id="inv_test_001",
        actor_type="user",
        actor_id="user_test",
        session_id="sess_001",
        function_name="system.metrics.snapshot",
        call_path=["system.metrics.snapshot"],
        max_depth=3,
        max_steps=10,
        max_total_duration_sec=300,
    )
    db_session.add(inv)
    await db_session.commit()

    result = await db_session.execute(
        select(Invocation).where(Invocation.invocation_id == "inv_test_001")
    )
    fetched = result.scalar_one()
    assert fetched.actor_type == "user"
    assert fetched.call_path == ["system.metrics.snapshot"]
    assert fetched.max_depth == 3


@pytest.mark.asyncio
async def test_create_timeline_event(db_session: AsyncSession):
    """TimelineEvent should be insertable."""
    event = TimelineEvent(
        event_type="job.started",
        actor_type="system",
        actor_id="node_test",
        node_id="test-node-1",
        job_id="job_001",
        invocation_id="inv_001",
        data={"message": "job started"},
    )
    db_session.add(event)
    await db_session.commit()

    result = await db_session.execute(
        select(TimelineEvent).where(TimelineEvent.job_id == "job_001")
    )
    fetched = result.scalar_one()
    assert fetched.event_type == "job.started"
    assert fetched.global_seq is not None


@pytest.mark.asyncio
async def test_create_session(db_session: AsyncSession):
    """Session model should persist interactive context."""
    sess = Session(
        session_id="sess_test_001",
        actor_type="user",
        actor_id="user_1",
        execution_mode="auto",
        status="active",
    )
    db_session.add(sess)
    await db_session.commit()

    result = await db_session.execute(
        select(Session).where(Session.session_id == "sess_test_001")
    )
    fetched = result.scalar_one()
    assert fetched.actor_type == "user"
    assert fetched.execution_mode == "auto"
    assert fetched.status == "active"


@pytest.mark.asyncio
async def test_node_capability_relationship(db_session: AsyncSession):
    """Node and Capability should have a working relationship."""
    node = Node(
        node_id="test-node-rel",
        node_name="Relationship Test",
        token_hash="hash_xyz",
        status=NodeStatus.ONLINE,
    )
    cap1 = Capability(
        plugin_id="sys.metrics",
        plugin_version="0.1.0",
        capability_type="function",
        name="sys.metrics.cpu",
    )
    cap2 = Capability(
        plugin_id="sys.metrics",
        plugin_version="0.1.0",
        capability_type="signal",
        name="sys.cpu.usage",
    )
    node.capabilities.extend([cap1, cap2])
    db_session.add(node)
    await db_session.commit()

    result = await db_session.execute(
        select(Node).where(Node.node_id == "test-node-rel")
    )
    fetched = result.scalar_one()
    assert len(fetched.capabilities) == 2
    assert {c.name for c in fetched.capabilities} == {"sys.metrics.cpu", "sys.cpu.usage"}
```

- [ ] **Step 4: Run all tests**

```bash
cd /home/yequdesu/YeQu-gateway && source .venv/bin/activate
pytest tests/ -v
```
Expected: all tests pass (8 tests)

- [ ] **Step 5: Run ruff and mypy**

```bash
ruff check src/yequ/ tests/
ruff format --check src/yequ/ tests/
mypy src/yequ/
```
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add tests/
git commit -m "test: add health check and model persistence tests"
```

---

### Task 9: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
cd /home/yequdesu/YeQu-gateway && source .venv/bin/activate
pytest tests/ -v --tb=short
```

- [ ] **Step 2: Verify Alembic migrations are up to date**

```bash
alembic upgrade head
alembic check
```

- [ ] **Step 3: Verify app starts and health check works**

```bash
python src/yequ/main.py &
sleep 3
curl -s http://127.0.0.1:8000/healthz
kill %1
```

- [ ] **Step 4: Verify ruff + mypy clean**

```bash
ruff check src/yequ/ tests/
mypy src/yequ/
```

- [ ] **Step 5: Final commit for Stage 1**

```bash
git add -A
git commit -m "chore: final verification — all tests pass, ruff+mypy clean"
```

---

## Stage 1 Exit Criteria

- [x] `pyproject.toml` with all dependencies, venv created
- [x] Protocol layer: all enums, ErrorCode, YqpError, YqpEnvelope in `src/yequ/protocol/`
- [x] Config via Pydantic Settings with env prefix `YEQU_`
- [x] Structlog logging with JSON and console modes
- [x] SQLAlchemy async engine + session factory
- [x] Alembic with initial migration for 6 tables
- [x] All 6 models: Node, Capability, Invocation, Job, TimelineEvent, Session
- [x] FastAPI app with `/healthz` endpoint
- [x] pytest with conftest (test DB, test client)
- [x] Health check tests pass
- [x] Model persistence tests pass (8 tests)
- [x] ruff + mypy clean
- [x] App starts and responds to health check
