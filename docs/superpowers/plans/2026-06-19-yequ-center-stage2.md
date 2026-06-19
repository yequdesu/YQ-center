# YeQu Center Stage 2: Node Access Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all 9 YQP Node protocol endpoints on the Center side — node.hello, register_capabilities, heartbeat, signal.report, job.poll, job.accepted, job.finished, job.lease_renew, node.reconcile_jobs — with auth, dedup, timestamp validation, and contract tests.

**Architecture:** Single `POST /yqp/` endpoint dispatches on `message_type` in the YqpEnvelope. FastAPI dependencies handle Bearer token auth (token hash → Node lookup, node_id binding check), message_id dedup (in-memory TTL cache), and timestamp skew validation. Business logic lives in `src/yequ/services/node_service.py`.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy 2 (async), structlog, pytest + httpx

---

## File Map

| File | Responsibility |
|---|---|
| `src/yequ/services/__init__.py` | Services package init |
| `src/yequ/services/message_dedup.py` | In-memory TTL cache for message_id dedup |
| `src/yequ/services/node_auth.py` | Token hashing, Bearer verification, Node lookup, get_current_node dependency |
| `src/yequ/services/node_service.py` | Business logic: hello, register, heartbeat, signal, job operations, reconcile |
| `src/yequ/api/routes/yqp.py` | `POST /yqp/` — envelope parse, validate, dispatch to node_service |
| `src/yequ/api/routes/admin.py` | `POST /admin/nodes` — pre-provision a Node for testing |
| `src/yequ/api/deps.py` | **Modify:** add `get_current_node` dependency |
| `src/yequ/api/app.py` | **Modify:** include yqp router, admin router |
| `tests/test_yqp_protocol.py` | Contract tests for all 9 protocol messages |

---

### Task 1: Message Dedup Service

**Files:**
- Create: `src/yequ/services/__init__.py`
- Create: `src/yequ/services/message_dedup.py`

- [ ] **Step 1: Create services package init**

`src/yequ/services/__init__.py`:
```python
"""YeQu Center business services."""
```

- [ ] **Step 2: Write MessageDedup**

`src/yequ/services/message_dedup.py`:
```python
"""Message ID deduplication with TTL-based in-memory cache.

Prevents duplicate processing of messages within the dedup window.
"""

import time
from collections.abc import MutableMapping


class MessageDedup:
    """In-memory TTL cache for message_id deduplication.

    On cache hit (duplicate), returns False. On cache miss (new), records
    the message_id and returns True. Expired entries are lazily cleaned up.

    Thread-safe: no. Designed for single-worker async use.
    """

    def __init__(self, ttl_sec: int = 300) -> None:
        self._ttl_sec = ttl_sec
        self._cache: dict[str, float] = {}

    def check_and_record(self, message_id: str) -> bool:
        """Check if message_id is new and record it.

        Returns True if the message_id is new (should process).
        Returns False if the message_id was already seen (should skip).
        """
        now = time.monotonic()
        self._evict_expired(now)

        if message_id in self._cache:
            return False

        self._cache[message_id] = now
        return True

    def _evict_expired(self, now: float) -> None:
        """Remove expired entries from cache."""
        cutoff = now - self._ttl_sec
        expired = [mid for mid, ts in self._cache.items() if ts < cutoff]
        for mid in expired:
            del self._cache[mid]

    def clear(self) -> None:
        """Clear all cached entries (for testing)."""
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)


# Module-level singleton
_dedup: MessageDedup | None = None


def get_dedup() -> MessageDedup:
    """Return the singleton MessageDedup instance."""
    global _dedup
    if _dedup is None:
        from yequ.config import get_settings

        _dedup = MessageDedup(ttl_sec=get_settings().message_dedup_ttl_sec)
    return _dedup
```

- [ ] **Step 3: Smoke test**

```bash
cd /home/yequdesu/YeQu-gateway && source .venv/bin/activate
python -c "
from yequ.services.message_dedup import MessageDedup
d = MessageDedup(ttl_sec=1)
assert d.check_and_record('msg1') == True   # new
assert d.check_and_record('msg1') == False  # dup
assert d.check_and_record('msg2') == True   # new
print('Dedup tests passed')
"
```

- [ ] **Step 4: Commit**

```bash
git add src/yequ/services/
git commit -m "feat: add message_id dedup service with in-memory TTL cache"
```

---

### Task 2: Node Auth Service

**Files:**
- Create: `src/yequ/services/node_auth.py`
- Modify: `src/yequ/api/deps.py` — add get_current_node
- Create: `src/yequ/api/routes/admin.py` — pre-provision node
- Modify: `src/yequ/api/app.py` — include admin router

- [ ] **Step 1: Write Node Auth Service**

`src/yequ/services/node_auth.py`:
```python
"""Node authentication — token verification and node identity binding."""

import hashlib
import hmac

from fastapi import Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.node import Node


def hash_token(token: str) -> str:
    """Hash a token using SHA-256.

    Tokens are stored as hashes, never in plaintext.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str, token_hash: str) -> bool:
    """Constant-time comparison of token hash.

    Uses hmac.compare_digest to prevent timing attacks.
    """
    computed = hash_token(token)
    return hmac.compare_digest(computed, token_hash)


async def get_current_node(
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: AsyncSession = None,  # injected by FastAPI
) -> Node:
    """FastAPI dependency: extract Bearer token, verify, return Node.

    Raises 401 if token missing, invalid, or node not found.
    Raises 403 if token is valid but node_id doesn't match.
    The node_id binding check is done in the route handler after
    parsing the envelope, since we don't have the envelope here.

    Returns the authenticated Node ORM object.
    """
    import logging

    log = logging.getLogger(__name__)

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    # Parse "Bearer <token>"
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Expected: Bearer <token>",
        )

    token = parts[1]
    # Validate token format: should be a non-empty hex string
    if not token or len(token) < 8:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token format",
        )

    computed_hash = hash_token(token)

    result = await db.execute(
        select(Node).where(Node.token_hash == computed_hash)
    )
    node = result.scalar_one_or_none()

    if node is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    return node


async def verify_node_id_binding(node: Node, envelope_node_id: str) -> None:
    """Verify that the envelope node_id matches the authenticated Node."""
    from fastapi import HTTPException, status

    if node.node_id != envelope_node_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"node_id {envelope_node_id!r} does not match token binding",
        )
```

- [ ] **Step 2: Update deps.py — add get_current_node to exports (it's used via Depends)**

The existing `deps.py` stays as-is. The `get_current_node` function is imported directly in route handlers where needed, using `Depends()` with the db session.

Actually, `get_current_node` needs a `db` parameter. Since FastAPI's `Depends` resolves parameters, we need a wrapper or use the function directly. The cleanest approach: in the route, use `node: Node = Depends(get_current_node)` and FastAPI will inject `db` from `Depends(get_db)`.

But there's a problem: `get_current_node` has `db: AsyncSession = None` which won't work with FastAPI's Depends resolution. Let me fix the design.

Better approach: make `get_current_node` a dependency factory.

Update `src/yequ/api/deps.py` to add:

```python
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from yequ.services.node_auth import get_current_node as _auth_node

# Re-export for convenience
get_current_node = _auth_node
```

Actually, the simplest approach: in routes, use `Depends(get_current_node)` where `get_current_node` takes `db` via `Depends(get_db)`. But the standard FastAPI pattern requires `get_current_node` to be a callable that FastAPI can resolve.

Let me use a different pattern — the auth function gets db and authorization header:

```python
async def get_current_node(
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> Node:
    ...
```

But this creates a circular import if deps.py imports from node_auth.py and node_auth.py imports from deps.py.

Solution: put the dependency version in deps.py that delegates to node_auth, or put everything in node_auth.py and have deps.py re-export.

Simplest: have node_auth.py take `db` as a plain parameter (no Depends), and create the Depends-wired version in deps.py.

Let me restructure:

`node_auth.py` — pure business logic, no FastAPI Depends:
```python
async def authenticate_node(db: AsyncSession, authorization: str | None) -> Node:
    ...

def verify_node_id_binding(node: Node, envelope_node_id: str) -> None:
    ...
```

`deps.py` — adds the Depends-wired version:
```python
async def get_current_node(
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> Node:
    return await authenticate_node(db, authorization)
```

This avoids circular imports and keeps things clean.

Let me update the plan for Task 2 accordingly.

- [ ] **Step 3: Write Admin endpoint for pre-provisioning nodes**

`src/yequ/api/routes/admin.py`:
```python
"""Admin endpoints for Node management."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.api.deps import get_db
from yequ.models.node import Node
from yequ.protocol import NodeStatus
from yequ.services.node_auth import hash_token

router = APIRouter(prefix="/admin", tags=["admin"])


class ProvisionNodeRequest(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=128)
    node_name: str = Field(..., min_length=1, max_length=256)
    token: str = Field(..., min_length=8, description="Plaintext token for this node")
    role: str = Field(default="compute")
    locality: str = Field(default="local")


class ProvisionNodeResponse(BaseModel):
    node_id: str
    node_name: str
    status: str
    message: str


@router.post("/nodes", response_model=ProvisionNodeResponse, status_code=status.HTTP_201_CREATED)
async def provision_node(
    body: ProvisionNodeRequest,
    db: AsyncSession = Depends(get_db),
) -> ProvisionNodeResponse:
    """Pre-provision a Node in the system.

    Creates a Node record with hashed token. The node can then
    connect via node.hello using the provided token.
    """
    # Check if node already exists
    result = await db.execute(
        select(Node).where(Node.node_id == body.node_id)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Node {body.node_id!r} already exists",
        )

    node = Node(
        node_id=body.node_id,
        node_name=body.node_name,
        token_hash=hash_token(body.token),
        role=body.role,
        locality=body.locality,
        status=NodeStatus.PROVISIONED,
    )
    db.add(node)
    await db.commit()

    return ProvisionNodeResponse(
        node_id=body.node_id,
        node_name=body.node_name,
        status=NodeStatus.PROVISIONED,
        message="Node provisioned. Use the token to authenticate via node.hello.",
    )
```

- [ ] **Step 4: Update app.py to include admin router**

Add to `src/yequ/api/app.py`:
```python
from yequ.api.routes.admin import router as admin_router

# In create_app():
app.include_router(admin_router)
```

- [ ] **Step 5: Smoke test auth**

```bash
cd /home/yequdesu/YeQu-gateway && source .venv/bin/activate
python -c "
import asyncio
from yequ.services.node_auth import hash_token
print(f'Token hash: {hash_token(\"test-token-12345678\")}')
print('Auth smoke test OK')
"
```

- [ ] **Step 6: Commit**

```bash
ruff check src/yequ/services/ src/yequ/api/
git add src/yequ/services/node_auth.py src/yequ/api/routes/admin.py src/yequ/api/app.py src/yequ/api/deps.py
git commit -m "feat: add node auth service and admin node provisioning"
```

---

### Task 3: YQP Router Foundation + Node Hello + Heartbeat

**Files:**
- Create: `src/yequ/api/routes/yqp.py` — YQP router with envelope parsing, dispatch
- Create: `src/yequ/services/node_service.py` — hello and heartbeat handlers
- Modify: `src/yequ/api/app.py` — include yqp router

- [ ] **Step 1: Write node_service.py (hello + heartbeat)**

`src/yequ/services/node_service.py` (initial version):
```python
"""Node service — business logic for YQP node protocol messages."""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.node import Node
from yequ.protocol import JobDeliveryMode, NodeStatus


async def handle_hello(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
    """Process node.hello — accept a Node connection.

    Updates Node status to online, records daemon version and platform info.
    Returns node.accepted payload with heartbeat/intervals configuration.
    """
    node.status = NodeStatus.ONLINE
    node.daemon_version = payload.get("daemon_version")
    node.last_seen_at = datetime.now(timezone.utc)

    platform = payload.get("platform", {})
    node.platform_os = platform.get("os")
    node.platform_arch = platform.get("arch")

    node.heartbeat_interval_sec = settings.default_heartbeat_interval_sec
    node.job_delivery_mode = JobDeliveryMode.POLL

    await db.commit()

    return {
        "heartbeat_interval_sec": settings.default_heartbeat_interval_sec,
        "heartbeat_timeout_multiplier": settings.default_heartbeat_timeout_multiplier,
        "signal_report_interval_sec": settings.default_signal_report_interval_sec,
        "signal_stale_multiplier": settings.default_signal_stale_multiplier,
        "job_delivery_mode": JobDeliveryMode.POLL,
        "job_poll_interval_sec": settings.default_job_poll_interval_sec,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


async def handle_heartbeat(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
    """Process node.heartbeat — update last_seen and heartbeat times."""
    now = datetime.now(timezone.utc)
    node.last_seen_at = now
    node.last_heartbeat_at = now

    # If node was OFFLINE or REJOINING, bring back to ONLINE
    if node.status in (NodeStatus.OFFLINE, NodeStatus.REJOINING):
        node.status = NodeStatus.ONLINE

    await db.commit()

    return {}  # No response payload for heartbeat
```

- [ ] **Step 2: Write YQP Router**

`src/yequ/api/routes/yqp.py`:
```python
"""YQP Node Protocol — single POST /yqp/ endpoint dispatching on message_type."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import ValidationError

from yequ.api.deps import get_db, get_settings
from yequ.config import Settings
from yequ.models.node import Node
from yequ.protocol import MessageType
from yequ.protocol.envelope import YqpEnvelope
from yequ.protocol.errors import ErrorCode, YqpError
from yequ.services.message_dedup import get_dedup
from yequ.services.node_auth import authenticate_node, verify_node_id_binding
from yequ.services.node_service import handle_hello, handle_heartbeat

router = APIRouter(prefix="/yqp", tags=["yqp"])


# Handler registry: message_type -> async handler function
HandlerFunc = callable  # async (db, node, payload, settings) -> response_payload_dict

_HANDLERS: dict[MessageType, HandlerFunc] = {}


def register_handler(msg_type: MessageType):
    """Decorator to register a handler for a message type."""
    def decorator(func):
        _HANDLERS[msg_type] = func
        return func
    return decorator


@register_handler(MessageType.NODE_HELLO)
async def _handle_hello(db, node, payload, settings):
    return await handle_hello(db, node, payload, settings)

@register_handler(MessageType.NODE_HEARTBEAT)
async def _handle_heartbeat(db, node, payload, settings):
    return await handle_heartbeat(db, node, payload, settings)


@router.post("/")
async def yqp_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Main YQP protocol endpoint.

    Accepts a YqpEnvelope, validates auth/dedup/timestamp,
    dispatches to the appropriate handler based on message_type.
    """
    # 1. Parse envelope
    try:
        body = await request.json()
        envelope = YqpEnvelope.model_validate(body)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=YqpError(
                code=ErrorCode.SCHEMA_INVALID,
                message=f"Invalid YQP envelope: {e}",
            ).model_dump(),
        )

    # 2. Authenticate node from Authorization header
    auth_header = request.headers.get("Authorization")
    try:
        node = await authenticate_node(db, auth_header)
    except HTTPException:
        raise  # Re-raise 401

    # 3. Verify node_id binding
    if envelope.node_id:
        await verify_node_id_binding(node, envelope.node_id)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=YqpError(
                code=ErrorCode.SCHEMA_INVALID,
                message="node_id is required in envelope for Node messages",
            ).model_dump(),
        )

    # 4. Message dedup
    dedup = get_dedup()
    if not dedup.check_and_record(envelope.message_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=YqpError(
                code=ErrorCode.DUPLICATE_MESSAGE,
                message=f"Duplicate message_id: {envelope.message_id}",
            ).model_dump(),
        )

    # 5. Timestamp validation
    now = datetime.now(timezone.utc)
    skew = abs((envelope.timestamp.replace(tzinfo=timezone.utc) - now).total_seconds())
    if skew > settings.allowed_timestamp_skew_sec:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=YqpError(
                code=ErrorCode.TIMESTAMP_OUT_OF_RANGE,
                message=f"Timestamp skew {skew:.1f}s exceeds allowed {settings.allowed_timestamp_skew_sec}s",
            ).model_dump(),
        )

    # 6. Dispatch to handler
    handler = _HANDLERS.get(envelope.message_type)
    if handler is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=YqpError(
                code=ErrorCode.SCHEMA_INVALID,
                message=f"Unsupported message_type: {envelope.message_type}",
            ).model_dump(),
        )

    try:
        response_payload = await handler(db, node, envelope.payload, settings)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=YqpError(
                code=ErrorCode.INTERNAL_ERROR,
                message=str(e),
            ).model_dump(),
        )

    # 7. Return response envelope
    return {
        "yqp_version": "0.1",
        "message_id": envelope.message_id,
        "message_type": _response_type(envelope.message_type),
        "trace_id": envelope.trace_id,
        "timestamp": now.isoformat(),
        "payload": response_payload,
    }


def _response_type(request_type: MessageType) -> str:
    """Map request message_type to response message_type."""
    _RESPONSE_MAP: dict[MessageType, str] = {
        MessageType.NODE_HELLO: MessageType.NODE_ACCEPTED,
        MessageType.NODE_REGISTER_CAPABILITIES: MessageType.REGISTRY_ACCEPTED,
        MessageType.NODE_HEARTBEAT: MessageType.NODE_HEARTBEAT,
        MessageType.SIGNAL_REPORT: MessageType.SIGNAL_REPORT,
        MessageType.JOB_POLL: MessageType.JOB_AVAILABLE,
        MessageType.JOB_ACCEPTED: MessageType.JOB_ACCEPTED,
        MessageType.JOB_FINISHED: MessageType.JOB_FINISHED,
        MessageType.JOB_LEASE_RENEW: MessageType.JOB_LEASE_ACCEPTED,
        MessageType.NODE_RECONCILE_JOBS: MessageType.JOB_RECONCILIATION,
    }
    return _RESPONSE_MAP.get(request_type, MessageType.ERROR)
```

- [ ] **Step 3: Update app.py to include yqp router**

Add to `create_app()`:
```python
from yequ.api.routes.yqp import router as yqp_router
app.include_router(yqp_router)
```

- [ ] **Step 4: Contract test — node.hello**

Set up a test that:
1. Provisions a test node via admin endpoint
2. Sends a node.hello message with Authorization header
3. Verifies node.accepted response with correct intervals

Write test in `tests/test_yqp_protocol.py` (initial version with hello + heartbeat tests).

- [ ] **Step 5: Verify all imports, run tests, commit**

```bash
ruff check src/yequ/
pytest tests/ -v
git add src/yequ/services/node_service.py src/yequ/api/routes/yqp.py src/yequ/api/app.py tests/test_yqp_protocol.py
git commit -m "feat: add YQP router, node.hello and node.heartbeat handlers"
```

---

### Task 4: Capability Registration

**Files:**
- Modify: `src/yequ/services/node_service.py` — add handle_register_capabilities
- Modify: `src/yequ/api/routes/yqp.py` — register handler

- [ ] **Step 1: Add handle_register_capabilities to node_service.py**

```python
async def handle_register_capabilities(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
    """Process node.register_capabilities — full snapshot semantics.

    For each plugin in the payload, deactivate all existing capabilities
    for that (node, plugin_id) and insert the new set as active.
    """
    from sqlalchemy import update as sql_update
    from yequ.models.capability import Capability

    plugins = payload.get("plugins", [])
    registered_count = 0
    failed_count = 0
    now = datetime.now(timezone.utc)

    for plugin in plugins:
        plugin_id = plugin["plugin_id"]
        plugin_version = plugin.get("plugin_version", "0.0.0")
        plugin_status = plugin.get("status", "loaded")

        # Deactivate existing capabilities for this (node, plugin_id)
        await db.execute(
            sql_update(Capability)
            .where(
                Capability.node_record_id == node.id,
                Capability.plugin_id == plugin_id,
            )
            .values(is_active=False)
        )

        if plugin_status == "error":
            # Still record the failed plugin
            cap = Capability(
                node_record_id=node.id,
                plugin_id=plugin_id,
                plugin_version=plugin_version,
                capability_type="function",
                name=f"{plugin_id}.error",
                status="error",
                error_code=plugin.get("error", {}).get("code"),
                error_message=plugin.get("error", {}).get("message"),
                is_active=True,
                registered_at=now,
            )
            db.add(cap)
            failed_count += 1
            continue

        # Register functions
        for func in plugin.get("functions", []):
            cap = Capability(
                node_record_id=node.id,
                plugin_id=plugin_id,
                plugin_version=plugin_version,
                capability_type="function",
                name=func["name"],
                status=plugin_status,
                input_schema=func.get("input_schema"),
                output_schema=func.get("output_schema"),
                risk=func.get("risk"),
                effect=func.get("effect"),
                timeout_sec=func.get("timeout_sec"),
                idempotency=func.get("idempotency"),
                resource_keys=func.get("resource_keys"),
                conflict_policy=func.get("conflict_policy"),
                is_active=True,
                registered_at=now,
            )
            db.add(cap)
            registered_count += 1

        # Register signals
        for sig in plugin.get("signals", []):
            # Validate ttl_sec constraint
            ttl = sig.get("ttl_sec", 0)
            report_interval = settings.default_signal_report_interval_sec
            if ttl < report_interval * 3:
                # Allow registration but log warning
                pass

            cap = Capability(
                node_record_id=node.id,
                plugin_id=plugin_id,
                plugin_version=plugin_version,
                capability_type="signal",
                name=sig["name"],
                status=plugin_status,
                scope=sig.get("scope"),
                ttl_sec=ttl,
                value_schema=sig.get("value_schema"),
                is_active=True,
                registered_at=now,
            )
            db.add(cap)
            registered_count += 1

    await db.commit()

    return {
        "registered_count": registered_count,
        "failed_count": failed_count,
        "accepted_at": now.isoformat(),
    }
```

- [ ] **Step 2: Register handler in yqp.py**

Add to the handler registrations:
```python
@register_handler(MessageType.NODE_REGISTER_CAPABILITIES)
async def _handle_register(db, node, payload, settings):
    return await handle_register_capabilities(db, node, payload, settings)
```

Also add the import:
```python
from yequ.services.node_service import (
    handle_hello,
    handle_heartbeat,
    handle_register_capabilities,
)
```

- [ ] **Step 3: Contract test for capability registration**

Test full snapshot semantics, plugin error handling, function + signal manifest storage.

- [ ] **Step 4: Commit**

```bash
git add src/yequ/services/node_service.py src/yequ/api/routes/yqp.py tests/test_yqp_protocol.py
git commit -m "feat: add capability registration with full snapshot semantics"
```

---

### Task 5: Signal Report

**Files:**
- Modify: `src/yequ/services/node_service.py` — add handle_signal_report
- Modify: `src/yequ/api/routes/yqp.py` — register handler
- Add dependency: `jsonschema` to pyproject.toml

- [ ] **Step 1: Add jsonschema dependency**

Add to `pyproject.toml` dependencies:
```
"jsonschema>=4.20",
```

Reinstall:
```bash
pip install -e ".[dev]"
```

- [ ] **Step 2: Add handle_signal_report**

```python
async def handle_signal_report(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
    """Process signal.report — validate and store signal values.

    Each signal value is validated against its registered value_schema.
    Invalid values are rejected but don't block valid signals in the batch.
    """
    import jsonschema
    from sqlalchemy import select
    from yequ.models.capability import Capability
    from yequ.models.timeline import TimelineEvent

    signals = payload.get("signals", [])
    accepted = 0
    rejected = 0
    now = datetime.now(timezone.utc)

    # Load registered signal schemas for validation
    result = await db.execute(
        select(Capability).where(
            Capability.node_record_id == node.id,
            Capability.capability_type == "signal",
            Capability.is_active == True,
        )
    )
    registered_signals: dict[str, dict] = {
        cap.name: cap.value_schema for cap in result.scalars().all()
    }

    for sig in signals:
        name = sig["name"]
        value = sig.get("value")
        value_schema = registered_signals.get(name)

        # Validate against value_schema if registered
        if value_schema:
            try:
                jsonschema.validate(value, value_schema)
            except jsonschema.ValidationError as e:
                # Write audit event for schema validation failure
                event = TimelineEvent(
                    event_type="signal.schema_invalid",
                    actor_type="system",
                    actor_id=node.node_id,
                    node_id=node.node_id,
                    data={
                        "signal_name": name,
                        "value": value,
                        "error": str(e),
                    },
                    timestamp=now,
                )
                db.add(event)
                rejected += 1
                continue

        # Write accepted signal to timeline
        event = TimelineEvent(
            event_type="signal.reported",
            actor_type="system",
            actor_id=node.node_id,
            node_id=node.node_id,
            data={
                "signal_name": name,
                "value": value,
                "scope": sig.get("scope"),
                "collected_at": sig.get("collected_at"),
                "ttl_sec": sig.get("ttl_sec"),
            },
            timestamp=now,
        )
        db.add(event)
        accepted += 1

    await db.commit()

    return {
        "accepted": accepted,
        "rejected": rejected,
    }
```

- [ ] **Step 3: Register handler + contract test**

- [ ] **Step 4: Commit**

---

### Task 6: Job Poll, Accept, Finish, Lease Renew

**Files:**
- Modify: `src/yequ/services/node_service.py` — add job operation handlers
- Modify: `src/yequ/api/routes/yqp.py` — register handlers

This task implements four endpoints:
- `job.poll` — Node requests available jobs
- `job.accepted` — Node confirms it will execute a job
- `job.finished` — Node reports job completion (succeeded/failed/cancelled/timeout)
- `job.lease_renew` — Node requests lease extension

- [ ] **Step 1: Add handle_job_poll**

```python
async def handle_job_poll(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
    """Process job.poll — return available jobs for this node.

    Returns up to `capacity` pending jobs assigned to this node.
    """
    from sqlalchemy import select
    from yequ.models.job import Job
    from yequ.protocol import JobStatus

    capacity = payload.get("capacity", 1)
    running_jobs = payload.get("running_jobs", [])

    # Find queued/claimed jobs for this node
    result = await db.execute(
        select(Job)
        .where(
            Job.node_id == node.node_id,
            Job.status.in_([JobStatus.QUEUED, JobStatus.CLAIMED]),
        )
        .limit(capacity)
    )
    pending_jobs = result.scalars().all()

    if not pending_jobs:
        return {"jobs": []}  # job.empty

    now = datetime.now(timezone.utc)
    jobs = []
    for job in pending_jobs:
        # Claim the job
        job.status = JobStatus.CLAIMED
        job.claimed_at = now
        job.lease_expires_at = datetime.fromtimestamp(
            now.timestamp() + job.lease_sec, tz=timezone.utc
        )
        jobs.append({
            "job_id": job.job_id,
            "invocation_id": job.invocation_id,
            "function": job.function_name,
            "input": job.input_payload or {},
            "timeout_sec": job.timeout_sec,
            "lease_sec": job.lease_sec,
        })

    await db.commit()

    return {"jobs": jobs}
```

- [ ] **Step 2: Add handle_job_accepted**

```python
async def handle_job_accepted(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
    """Process job.accepted — Node confirms job execution will begin."""
    from sqlalchemy import select
    from yequ.models.job import Job
    from yequ.protocol import JobStatus

    job_id = payload["job_id"]
    result = await db.execute(
        select(Job).where(Job.job_id == job_id, Job.node_id == node.node_id)
    )
    job = result.scalar_one_or_none()

    if job is None:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=YqpError(
                code=ErrorCode.JOB_NOT_FOUND,
                message=f"Job {job_id!r} not found or not assigned to this node",
            ).model_dump(),
        )

    # Validate transition: claimed -> running
    if job.status != JobStatus.CLAIMED:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=YqpError(
                code=ErrorCode.INVALID_STATE_TRANSITION,
                message=f"Cannot accept job in status {job.status}",
            ).model_dump(),
        )

    now = datetime.now(timezone.utc)
    job.status = JobStatus.RUNNING
    job.started_at = now
    await db.commit()

    return {"job_id": job_id, "status": "accepted"}
```

- [ ] **Step 3: Add handle_job_finished**

```python
async def handle_job_finished(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
    """Process job.finished — Node reports job completion.

    Accepts terminal states: succeeded, failed, cancelled, timeout.
    Writes result or error to the Job record.
    """
    from sqlalchemy import select
    from yequ.models.job import Job
    from yequ.models.timeline import TimelineEvent
    from yequ.protocol import JobStatus

    job_id = payload["job_id"]
    result = await db.execute(
        select(Job).where(Job.job_id == job_id, Job.node_id == node.node_id)
    )
    job = result.scalar_one_or_none()

    if job is None:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=YqpError(
                code=ErrorCode.JOB_NOT_FOUND,
                message=f"Job {job_id!r} not found",
            ).model_dump(),
        )

    terminal_status = payload["status"]
    valid_terminals = {
        JobStatus.SUCCEEDED, JobStatus.FAILED,
        JobStatus.CANCELLED, JobStatus.TIMEOUT,
    }
    if terminal_status not in valid_terminals:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=YqpError(
                code=ErrorCode.SCHEMA_INVALID,
                message=f"Invalid terminal status: {terminal_status}",
            ).model_dump(),
        )

    # Reject if already in terminal state
    if job.status in valid_terminals:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=YqpError(
                code=ErrorCode.INVALID_STATE_TRANSITION,
                message=f"Job {job_id!r} already in terminal state {job.status}",
            ).model_dump(),
        )

    now = datetime.now(timezone.utc)
    job.status = terminal_status
    job.finished_at = now
    job.output = payload.get("output")
    job.error_code = payload.get("error_code")
    job.error_message = payload.get("error_message")

    # Write timeline event
    event = TimelineEvent(
        event_type=f"job.{terminal_status}",
        actor_type="system",
        actor_id=node.node_id,
        node_id=node.node_id,
        job_id=job_id,
        invocation_id=job.invocation_id,
        data={
            "status": terminal_status,
            "output": payload.get("output"),
            "finished_at": now.isoformat(),
        },
        timestamp=now,
    )
    db.add(event)
    await db.commit()

    return {"job_id": job_id, "status": terminal_status}
```

- [ ] **Step 4: Add handle_job_lease_renew**

```python
async def handle_job_lease_renew(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
    """Process job.lease_renew — extend a running job's lease."""
    from sqlalchemy import select
    from yequ.models.job import Job
    from yequ.protocol import JobStatus

    job_id = payload["job_id"]
    result = await db.execute(
        select(Job).where(Job.job_id == job_id, Job.node_id == node.node_id)
    )
    job = result.scalar_one_or_none()

    if job is None:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=YqpError(
                code=ErrorCode.JOB_NOT_FOUND,
                message=f"Job {job_id!r} not found",
            ).model_dump(),
        )

    # Only running jobs can renew lease
    if job.status != JobStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=YqpError(
                code=ErrorCode.LEASE_EXPIRED,
                message=f"Job {job_id!r} is not running (status: {job.status})",
            ).model_dump(),
        )

    # Check if lease is already expired
    now = datetime.now(timezone.utc)
    if job.lease_expires_at and job.lease_expires_at < now:
        return {
            "job_id": job_id,
            "status": "denied",
            "reason": "lease_expired",
        }

    # Extend lease
    extend_sec = payload.get("lease_extend_sec", settings.default_lease_sec)
    job.lease_expires_at = datetime.fromtimestamp(
        now.timestamp() + extend_sec, tz=timezone.utc
    )
    await db.commit()

    return {
        "job_id": job_id,
        "status": "accepted",
        "lease_expires_at": job.lease_expires_at.isoformat(),
    }
```

- [ ] **Step 5: Register all four handlers in yqp.py**

```python
@register_handler(MessageType.JOB_POLL)
async def _handle_poll(db, node, payload, settings):
    return await handle_job_poll(db, node, payload, settings)

@register_handler(MessageType.JOB_ACCEPTED)
async def _handle_accepted(db, node, payload, settings):
    return await handle_job_accepted(db, node, payload, settings)

@register_handler(MessageType.JOB_FINISHED)
async def _handle_finished(db, node, payload, settings):
    return await handle_job_finished(db, node, payload, settings)

@register_handler(MessageType.JOB_LEASE_RENEW)
async def _handle_lease(db, node, payload, settings):
    return await handle_job_lease_renew(db, node, payload, settings)
```

- [ ] **Step 6: Contract tests + Commit**

---

### Task 7: Node Reconcile Jobs

**Files:**
- Modify: `src/yequ/services/node_service.py` — add handle_reconcile_jobs
- Modify: `src/yequ/api/routes/yqp.py` — register handler

- [ ] **Step 1: Add handle_reconcile_jobs**

```python
async def handle_reconcile_jobs(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
    """Process node.reconcile_jobs — reconcile job state after reconnection.

    Compares Daemon's local job states with Center's authoritative state
    and returns reconciliation actions.
    """
    from sqlalchemy import select
    from yequ.models.job import Job
    from yequ.protocol import JobStatus, ReconciliationAction

    known_jobs = payload.get("known_jobs", [])
    actions = []
    now = datetime.now(timezone.utc)

    terminal_statuses = {
        JobStatus.SUCCEEDED, JobStatus.FAILED,
        JobStatus.CANCELLED, JobStatus.TIMEOUT,
    }

    for kj in known_jobs:
        job_id = kj["job_id"]
        local_status = kj["local_status"]

        # Look up job in Center
        result = await db.execute(
            select(Job).where(Job.job_id == job_id, Job.node_id == node.node_id)
        )
        job = result.scalar_one_or_none()

        if job is None:
            # Center doesn't know this job
            actions.append({
                "job_id": job_id,
                "action": ReconciliationAction.FORGET,
            })
            continue

        center_status = job.status

        if center_status in terminal_statuses and local_status == "succeeded":
            # Center already has a terminal state, Daemon reports success
            if "output" in kj:
                actions.append({
                    "job_id": job_id,
                    "action": ReconciliationAction.ACCEPT_RESULT,
                    "reconciled": True,
                })
            else:
                actions.append({
                    "job_id": job_id,
                    "action": ReconciliationAction.DISCARD_RESULT,
                })
        elif center_status in terminal_statuses:
            # Center has already terminated this job
            actions.append({
                "job_id": job_id,
                "action": ReconciliationAction.CANCEL,
                "reason": f"already_{center_status}",
            })
        elif center_status in (JobStatus.RUNNING, JobStatus.CLAIMED):
            # Both agree job is running — continue with new lease
            actions.append({
                "job_id": job_id,
                "action": ReconciliationAction.CONTINUE,
                "lease_sec": settings.default_lease_sec,
            })
        elif local_status in ("succeeded", "failed", "cancelled", "timeout"):
            # Daemon completed but Center didn't know
            actions.append({
                "job_id": job_id,
                "action": ReconciliationAction.ACCEPT_RESULT,
                "reconciled": True,
            })
        else:
            # Default: continue
            actions.append({
                "job_id": job_id,
                "action": ReconciliationAction.CONTINUE,
                "lease_sec": settings.default_lease_sec,
            })

    return {"actions": actions}
```

- [ ] **Step 2: Register handler + contract tests + Commit**

---

### Task 8: Full Contract Tests + Final Verification

**Files:**
- Modify: `tests/test_yqp_protocol.py` — comprehensive tests
- Modify: `tests/conftest.py` — add helpers for YQP test messages

- [ ] **Step 1: Add conftest helpers for YQP tests**

Add to conftest.py:
```python
import uuid
from datetime import datetime, timezone

def make_yqp_envelope(message_type: str, node_id: str, payload: dict | None = None) -> dict:
    """Build a YQP envelope dict for testing."""
    return {
        "yqp_version": "0.1",
        "message_id": f"msg_{uuid.uuid4().hex[:16]}",
        "message_type": message_type,
        "trace_id": f"tr_{uuid.uuid4().hex[:16]}",
        "node_id": node_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload or {},
    }


@pytest_asyncio.fixture
async def provisioned_node(db_session) -> Node:
    """Create a provisioned test node and return (node, token)."""
    from yequ.models.node import Node
    from yequ.services.node_auth import hash_token

    token = "test-token-" + uuid.uuid4().hex[:8]
    node = Node(
        node_id="test-node-yqp",
        node_name="Test YQP Node",
        token_hash=hash_token(token),
        status="provisioned",
    )
    db_session.add(node)
    await db_session.commit()
    return node, token
```

- [ ] **Step 2: Write comprehensive contract tests**

Tests to cover:
1. `test_hello_returns_accepted` — full node.hello flow
2. `test_hello_missing_auth_returns_401` — auth failure
3. `test_hello_wrong_node_id_returns_403` — node_id binding failure  
4. `test_hello_duplicate_message_id_returns_409` — dedup
5. `test_hello_timestamp_skew_returns_400` — timestamp validation
6. `test_register_capabilities_full_snapshot` — capability registration
7. `test_register_capabilities_plugin_error` — failed plugin in registration
8. `test_heartbeat_updates_node` — heartbeat processing
9. `test_signal_report_validates_schema` — signal validation
10. `test_signal_report_rejects_invalid_value` — schema violation
11. `test_job_poll_returns_jobs` — job dispatch
12. `test_job_poll_empty` — no jobs available
13. `test_job_accepted_sets_running` — job state transition
14. `test_job_finished_succeeded` — terminal state
15. `test_job_finished_rejects_double_terminal` — idempotent terminal
16. `test_job_lease_renew_extends_lease` — lease renewal
17. `test_reconcile_returns_actions` — job reconciliation
18. `test_reconcile_forget_unknown_job` — unknown job in reconcile

- [ ] **Step 3: Run full test suite, fix issues**

```bash
pytest tests/ -v
ruff check src/yequ/ tests/
```

Expected: all tests pass, ruff clean.

- [ ] **Step 4: Final verification**

```bash
rm -f yequ.db test_yequ.db
alembic upgrade head
pytest tests/ -v --tb=short
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "test: add comprehensive YQP contract tests, final verification"
```

---

## Stage 2 Exit Criteria

- [ ] All 9 protocol endpoints implemented:
  - [ ] `POST /yqp/` → node.hello
  - [ ] `POST /yqp/` → node.register_capabilities
  - [ ] `POST /yqp/` → node.heartbeat
  - [ ] `POST /yqp/` → signal.report
  - [ ] `POST /yqp/` → job.poll
  - [ ] `POST /yqp/` → job.accepted
  - [ ] `POST /yqp/` → job.finished
  - [ ] `POST /yqp/` → job.lease_renew
  - [ ] `POST /yqp/` → node.reconcile_jobs
- [ ] Bearer token auth on all requests
- [ ] node_id ↔ token binding enforced
- [ ] message_id 5-min dedup
- [ ] timestamp skew validation
- [ ] Capability full snapshot semantics
- [ ] Signal value_schema validation
- [ ] Job reconcile returns correct actions
- [ ] 18+ contract tests passing
- [ ] ruff clean, mypy clean
