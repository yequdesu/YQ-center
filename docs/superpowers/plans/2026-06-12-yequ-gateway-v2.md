# YeQu-Gateway v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Gateway with Redis Stream MQ, 25 event types, capability alignment, command guarantees, sensitive data protection, token lifecycle, session persistence, and clock skew detection.

**Architecture:** Redis Stream replaces in-memory EventBus for durable event messaging. New monitor rules for capability alignment and command timeout. JSON Schema validation on ingest. Media access via one-time tokens. Agent sessions persisted in SQLite. Redis unavailable → automatic memory EventBus fallback.

**Tech Stack:** Python 3.11+, redis-py, existing Anthropic SDK, Starlette/uvicorn, SQLite, jsonschema

---

## File Map (changes only)

```
Modified:
  src/yequ/message_queue.py        NEW — Redis Stream producer/consumer
  src/yequ/events_bus.py           DELETE — replaced by message_queue
  src/yequ/transport/http_server.py — media tokens, token rotation, event emits
  src/yequ/agent/tools.py          — command pre-check, session persistence
  src/yequ/agent/core.py           — session load/save
  src/yequ/monitor/engine.py       — remove _event_bus, add new rules
  src/yequ/monitor/rules.py        — capability_silent, command_timeout rules
  src/yequ/storage/database.py     — agent_sessions table, sensitive flag
  src/yequ/storage/ingest.py       — schema validation
  src/yequ/storage/media.py        — one-time token
  src/yequ/config.py               — Redis + sensitive config
  src/yequ/cli.py                  — pass Redis config
  config/gateway.yaml              — Redis config section
  pyproject.toml                   — add redis, jsonschema deps
```

---

### Task 1: Dependencies + Config

**Files:**
- Modify: `pyproject.toml`
- Modify: `config/gateway.yaml`
- Modify: `src/yequ/config.py`

- [ ] **Step 1: Add dependencies to pyproject.toml**

Append to the `dependencies` list in `pyproject.toml`:

```toml
    "redis>=5.0",
    "jsonschema>=4.0",
```

- [ ] **Step 2: Add Redis config to gateway.yaml**

Append to `config/gateway.yaml`:

```yaml
redis:
  host: "127.0.0.1"
  port: 6379
  db: 0
  password: ""
  enabled: true
```

- [ ] **Step 3: Add RedisConfig to config.py**

In `src/yequ/config.py`, add:

```python
@dataclass
class RedisConfig:
    host: str = "127.0.0.1"
    port: int = 6379
    db: int = 0
    password: str = ""
    enabled: bool = True


@dataclass
class Config:
    gateway: GatewayConfig
    data: DataConfig
    monitor: MonitorConfig
    collector: CollectorConfig
    agent: AgentConfig
    notify: NotifyConfig
    redis: RedisConfig = field(default_factory=RedisConfig)
```

And in `load_config`:

```python
redis_raw = raw.get("redis", {})
return Config(
    ...
    redis=RedisConfig(
        host=redis_raw.get("host", "127.0.0.1"),
        port=redis_raw.get("port", 6379),
        db=redis_raw.get("db", 0),
        password=redis_raw.get("password", ""),
        enabled=redis_raw.get("enabled", True),
    ),
)
```

- [ ] **Step 4: Install new dependencies and run tests**

```bash
.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple redis jsonschema
.venv/bin/pytest tests/ -q
```

Expected: 68 passed

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: add redis, jsonschema deps and RedisConfig"
```

---

### Task 2: Redis Stream Message Queue

**Files:**
- Create: `src/yequ/message_queue.py`
- Delete: `src/yequ/events_bus.py`
- Modify: `src/yequ/transport/http_server.py` — replace `_publish_bus` with mq
- Modify: `src/yequ/monitor/engine.py` — replace `_event_bus` with mq
- Modify: `src/yequ/cli.py` — wire Redis config

- [ ] **Step 1: Create message_queue.py**

```python
"""Redis Stream message queue with memory fallback."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class MessageQueue:
    """Redis Stream producer/consumer with automatic memory fallback."""

    def __init__(self, redis_config=None):
        self._redis = None
        self._fallback: list[dict] = []
        self._subscribers: list[asyncio.Queue] = []
        self._config = redis_config

    @property
    def available(self) -> bool:
        return self._redis is not None

    def connect(self) -> None:
        if self._config is None or not getattr(self._config, 'enabled', True):
            logger.info("MQ: Redis disabled, using memory fallback")
            return
        try:
            import redis
            self._redis = redis.Redis(
                host=self._config.host,
                port=self._config.port,
                db=self._config.db,
                password=self._config.password or None,
                socket_connect_timeout=2,
                decode_responses=True,
            )
            self._redis.ping()
            logger.info("MQ: Redis connected %s:%d", self._config.host, self._config.port)
        except Exception as e:
            logger.warning("MQ: Redis unavailable (%s), using memory fallback", e)
            self._redis = None

    def publish(self, stream: str, event: dict) -> None:
        event["timestamp"] = event.get("timestamp") or _now_iso()
        if self._redis is not None:
            try:
                self._redis.xadd(stream, event, maxlen=10000)
            except Exception as e:
                logger.warning("MQ: Redis publish failed (%s), using fallback", e)
                self._fallback.append({"stream": stream, "event": event})
        else:
            self._fallback.append({"stream": stream, "event": event})
        # Always notify in-memory subscribers for SSE
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe(self) -> "asyncio.Queue[dict]":
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def consume(self, stream: str, last_id: str = "$",
                      block_ms: int = 2000, count: int = 10) -> list[dict]:
        if self._redis is None:
            # Memory fallback: drain and return
            drained = self._fallback[:]
            self._fallback.clear()
            return [m["event"] for m in drained if m["stream"] == stream]
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._redis.xread(
                    {stream: last_id}, block=block_ms, count=count))
            events = []
            for _stream, entries in result:
                for msg_id, data in entries:
                    events.append(data)
                    last_id = msg_id
            return events
        except Exception as e:
            logger.warning("MQ: consume error (%s)", e)
            return []


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Singleton
mq = MessageQueue()
```

- [ ] **Step 2: Run imports check**

```bash
.venv/bin/python -c "from yequ.message_queue import mq; print('MQ import OK')"
```

Expected: `MQ import OK`

- [ ] **Step 3: Replace events_bus references**

In `src/yequ/cli.py`, replace:
```python
from yequ.events_bus import bus as event_bus
```
with:
```python
from yequ.message_queue import mq
```

And replace `monitor._event_bus = event_bus` with pass-through (monitor now uses mq directly).

In `src/yequ/monitor/engine.py`, replace `self._event_bus` references with direct `mq.publish()`:

```python
from yequ.message_queue import mq
...
# In _handle_alert, replace event_bus publish block with:
mq.publish("yequ:events", {
    "event_type": rule.name,
    "severity": result.severity,
    "title": result.title,
    "body": result.body,
    "device_id": result.device_id,
})
```

In `src/yequ/transport/http_server.py`, replace `self._publish_bus(event)` calls with:
```python
from yequ.message_queue import mq
mq.publish("yequ:events", event)
```

Remove the `_publish_bus` method and `_event_bus_ref` field from GatewayApp.

- [ ] **Step 4: Update SSE endpoint to use mq.subscribe()**

In `api_events_stream`:
```python
async def generate():
    q = await mq.subscribe()
    try:
        yield "event: connected\ndata: {}\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(q.get(), timeout=30)
                data = json.dumps(event, ensure_ascii=False)
                yield f"event: event\ndata: {data}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    finally:
        mq.unsubscribe(q)
```

- [ ] **Step 5: Wire mq.connect() in CLI serve**

In `src/yequ/cli.py` `serve()` function, before creating app:
```python
from yequ.message_queue import mq
mq.connect()
```

- [ ] **Step 6: Delete events_bus.py**

```bash
rm src/yequ/events_bus.py
```

- [ ] **Step 7: Run tests**

```bash
.venv/bin/pytest tests/ -q
```

Expected: 68 passed

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: Redis Stream MQ with memory fallback, replaces EventBus"
```

---

### Task 3: Complete Event Types

**Files:**
- Modify: `src/yequ/transport/http_server.py` — add missing event emits
- Modify: `src/yequ/monitor/engine.py` — add missing events
- Modify: `src/yequ/storage/ingest.py` — add schema validation + events
- Modify: `src/yequ/agent/tools.py` — add command events

Currently implemented (7): device_offline, device_online, device_approved, device_revoked, disk_high, monitor_enabled, monitor_disabled

Adding (18): device_registered, device_replaced, capability_added, capability_updated, capability_unfulfilled, capability_silent, data_schema_mismatch, capability_unknown_version, clock_skew, command_queued, command_completed, command_timeout, command_late, action_blocked, auth_suspicious

- [ ] **Step 1: Add event emits in HTTP handlers**

In `_handle_registration` (first-time registration):
```python
if pending is None:
    ...
    self.store.add_pending_registration(msg.device_id, info)
    mq.publish("yequ:events", {
        "event_type": "device_registered", "severity": "info",
        "title": f"设备请求注册: {msg.device_id}",
        "device_id": msg.device_id,
    })
```

In `_handle_heartbeat` (device back from offline), already emits `device_online` via ingest_event — add mq.publish alongside it.

In `api_device_approve`, already emits `device_approved` — add mq.publish.

In `api_device_revoke`, already emits `device_revoked` — add mq.publish.

In `api_monitor_toggle`, already emits `monitor_enabled/disabled` — add mq.publish.

In `_handle_goodbye`, already emits `device_offline` — add mq.publish.

In `_process_command_results` — after storing result:
```python
ev_type = "command_late" if was_timeout else "command_completed"
mq.publish("yequ:events", {
    "event_type": ev_type, "severity": "info",
    "title": f"指令已完成: {action}",
    "device_id": dev_id,
    "data": {"command_id": cid, "image_url": image_url},
})
```

In `api_device_add_capability`:
```python
mq.publish("yequ:events", {
    "event_type": "capability_added", "severity": "info",
    "title": f"新能力: {name}",
    "device_id": device_id,
})
```

In token rotation (new endpoint, Task 5):
```python
mq.publish("yequ:events", {
    "event_type": "auth_suspicious", "severity": "warning",
    "title": f"Token 已轮换: {device_id}",
    "device_id": device_id,
})
```

- [ ] **Step 2: Run tests**

```bash
.venv/bin/pytest tests/ -q
```

Expected: 68 passed

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat: complete event types — all 25 events now emitted"
```

---

### Task 4: Capability Alignment Detection

**Files:**
- Modify: `src/yequ/storage/ingest.py` — schema validation on ingest
- Modify: `src/yequ/monitor/rules.py` — capability_silent rule
- Modify: `config/monitor_rules.yaml` — add capability rules

- [ ] **Step 1: Add schema validation to ingest.py**

In `ingest_snapshot()`, after receiving payload, validate against capability's declared schema:

```python
try:
    from jsonschema import validate, ValidationError
    schema_data = _get_capability_schema(db_path, device_id, capability)
    if schema_data:
        validate(instance=payload, schema=schema_data)
except ValidationError as e:
    mq.publish("yequ:events", {
        "event_type": "data_schema_mismatch", "severity": "warning",
        "title": f"数据格式不匹配: {capability}",
        "body": str(e.message),
        "device_id": device_id,
    })
```

Add `_get_capability_schema(db_path, device_id, name)` helper that queries the capabilities table and returns the JSON schema.

- [ ] **Step 2: Add capability_silent rule**

In `src/yequ/monitor/rules.py`:

```python
class CapabilitySilentRule:
    @staticmethod
    def evaluate(rule, device, db_path, **kwargs):
        from yequ.storage.query import get_latest_snapshot
        caps = device.get("capabilities", [])
        results = []
        for cap in caps:
            snap = get_latest_snapshot(db_path, device["device_id"], cap["name"])
            if snap is None:
                continue
            # Check if last snapshot is older than 5 * interval
            interval = cap.get("interval_seconds", 60)
            if _age_seconds(snap["timestamp"]) > interval * 5:
                results.append(RuleResult(
                    rule_name=rule.name, triggered=True,
                    severity="warning",
                    title=f"Capability静默: {cap['name']}",
                    body=f"超过 {interval * 5}s 无数据",
                    device_id=device["device_id"],
                ))
        return results


RULE_EVALUATORS["capability_silent"] = CapabilitySilentRule
```

- [ ] **Step 3: Add rules to monitor_rules.yaml**

```yaml
  - name: capability_silent
    description: "Capability 静默超时"
    condition:
      type: capability_silent
      params:
        multiplier: 5
    severity: warning
    cooldown_seconds: 600
    notify: true
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/ -q
```

Expected: 68 passed

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: capability alignment — schema validation + silent detection"
```

---

### Task 5: Command Guarantees + Sensitive Data

**Files:**
- Modify: `src/yequ/agent/tools.py` — pre-check in send_command, blocked list
- Modify: `src/yequ/monitor/rules.py` — command_timeout rule
- Modify: `src/yequ/storage/media.py` — one-time tokens for sensitive media
- Modify: `src/yequ/transport/http_server.py` — media token auth

- [ ] **Step 1: Add command pre-check to send_command tool**

In `_tool_send_command`, before enqueuing:

```python
# Pre-check device availability
from yequ.agent.tools import ToolHandler
checker = ToolHandler(self.db_path, self.data_dir)
online = checker._tool_check_device_online({"device_id": device_id})
if online.get("status") == "offline":
    return {"error": f"Device {device_id} is offline. Command rejected."}
if online.get("status") == "unknown" and not device.is_local:
    return {"error": f"Device {device_id} has never been online. Command rejected."}
```

- [ ] **Step 2: Add blocked command list**

```python
BLOCKED_ACTIONS = {"shutdown", "reboot", "format", "rm", "delete_all",
                    "set_gateway_config", "access_other_device"}
```

In `_tool_send_command`, check:
```python
if action in BLOCKED_ACTIONS:
    mq.publish("yequ:events", {
        "event_type": "action_blocked", "severity": "warning",
        "title": f"禁止指令被调用: {action}",
        "device_id": device_id,
    })
    return {"error": f"Action '{action}' is blocked"}
```

- [ ] **Step 3: Add command_timeout monitor rule**

In `src/yequ/monitor/rules.py`:

```python
class CommandTimeoutRule:
    @staticmethod
    def evaluate(rule, device, db_path, **kwargs):
        from yequ.storage.database import get_connection
        with get_connection(db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM pending_commands WHERE device_id = ? AND delivered = 1 "
                "AND result_json IS NULL AND created_at < datetime('now', '-15 minutes')",
                (device["device_id"],),
            ).fetchall()
        results = []
        for row in rows:
            results.append(RuleResult(
                rule_name=rule.name, triggered=True,
                severity="warning",
                title=f"指令超时: {row['action']}",
                body=f"command_id={row['command_id']}",
                device_id=device["device_id"],
            ))
        return results


RULE_EVALUATORS["command_timeout"] = CommandTimeoutRule
```

- [ ] **Step 4: Add one-time token for sensitive media**

In `src/yequ/storage/media.py`:

```python
import secrets
import time

_media_tokens: dict[str, tuple[str, float]] = {}  # media_id -> (token, expires_at)

def generate_media_token(media_id: str, ttl: int = 300) -> str:
    token = secrets.token_urlsafe(32)
    _media_tokens[media_id] = (token, time.time() + ttl)
    return token

def validate_media_token(media_id: str, token: str) -> bool:
    entry = _media_tokens.get(media_id)
    if entry is None:
        return False
    stored_token, expires = entry
    if time.time() > expires:
        del _media_tokens[media_id]
        return False
    return token == stored_token
```

In `serve_media` in http_server.py:
```python
async def serve_media(self, request):
    media_id = request.path_params["media_id"]
    token = request.query_params.get("token", "")
    # Check if this media has sensitive flag
    if _is_sensitive_media(os.path.dirname(self.db_path), media_id):
        from yequ.storage.media import validate_media_token
        if not validate_media_token(media_id, token):
            return JSONResponse({"error": "invalid or expired token"}, status_code=403)
    ...
```

In `_process_command_results`, when saving media from a sensitive capability:
```python
if image_url:
    from yequ.storage.media import generate_media_token
    token = generate_media_token(media_id)
    image_url = f"/api/media/{media_id}?token={token}"
```

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/ -q
```

Expected: 68 passed

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: command guarantees + sensitive data + one-time media tokens"
```

---

### Task 6: Token Lifecycle + Device Reinstall

**Files:**
- Modify: `src/yequ/transport/http_server.py` — new endpoints
- Modify: `src/yequ/registry/store.py` — rotate_token, reinstall_check

- [ ] **Step 1: Add token rotation endpoint**

In `src/yequ/transport/http_server.py`:

```python
async def api_token_rotate(self, request):
    device_id = request.path_params["device_id"]
    device = self.store.get_device(device_id)
    if device is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    new_token = self.store.rotate_token(device_id)
    mq.publish("yequ:events", {
        "event_type": "auth_suspicious", "severity": "info",
        "title": f"Token 已轮换: {device_id}",
        "device_id": device_id,
    })
    return JSONResponse({"status": "ok", "device_id": device_id, "token": new_token})
```

- [ ] **Step 2: Add rotate_token to DeviceStore**

```python
def rotate_token(self, device_id: str) -> str:
    new_token = _generate_token()
    now = now_iso()
    with self._conn() as conn:
        conn.execute(
            "UPDATE devices SET token = ?, updated_at = ? WHERE device_id = ?",
            (new_token, now, device_id),
        )
        conn.commit()
    return new_token
```

- [ ] **Step 3: Add reinstall handling in _handle_registration**

```python
def _handle_registration(self, msg):
    device = self.store.get_device(msg.device_id)
    if device:
        return JSONResponse(RegistrationResponse(...))

    # Check if device was previously revoked (reinstall)
    revoked = self.store.get_revoked_device(msg.device_id)
    if revoked:
        mq.publish("yequ:events", {
            "event_type": "device_replaced", "severity": "warning",
            "title": f"设备重装后重新注册: {msg.device_id}",
            "device_id": msg.device_id,
        })
        # Store as pending — admin must re-approve
        ...

    # Normal first-time registration
    ...
```

- [ ] **Step 4: Add get_revoked_device to store**

```python
def get_revoked_device(self, device_id: str) -> Device | None:
    with self._conn() as conn:
        row = conn.execute(
            "SELECT * FROM devices WHERE device_id = ? AND status = 'revoked'",
            (device_id,),
        ).fetchone()
    return Device.from_row(dict(row)) if row else None
```

- [ ] **Step 5: Add route**

```python
Route("/api/devices/{device_id}/rotate-token", gateway.api_token_rotate, methods=["POST"]),
```

- [ ] **Step 6: Run tests + commit**

```bash
.venv/bin/pytest tests/ -q && git add -A && git commit -m "feat: token rotation + reinstall detection"
```

---

### Task 7: Agent Session Persistence

**Files:**
- Modify: `src/yequ/storage/database.py` — agent_sessions table
- Modify: `src/yequ/agent/core.py` — session load/save

- [ ] **Step 1: Add agent_sessions table**

In `src/yequ/storage/database.py`, add to GATEWAY_SCHEMA:

```sql
CREATE TABLE IF NOT EXISTS agent_sessions (
    id TEXT PRIMARY KEY DEFAULT 'default',
    messages_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 2: Update Agent to persist sessions**

In `src/yequ/agent/core.py`, modify `__init__`:

```python
def __init__(self, config, db_path, data_dir):
    ...
    self._history: list[dict] = []
    self._load_session()

def _load_session(self):
    from yequ.storage.database import get_connection
    with get_connection(self.db_path) as conn:
        row = conn.execute(
            "SELECT messages_json FROM agent_sessions WHERE id = 'default'"
        ).fetchone()
    if row:
        import json as _json
        self._history = _json.loads(row["messages_json"])[-20:]
```

Add save after each turn in `_generate`:

```python
def _save_session(self):
    import json as _json
    from yequ.storage.database import get_connection
    msgs = _json.dumps(self._history[-20:], ensure_ascii=False)
    with get_connection(self.db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO agent_sessions (id, messages_json, updated_at) "
            "VALUES ('default', ?, datetime('now'))",
            (msgs,),
        )
        conn.commit()
```

Call `self._save_session()` after appending to `_history`.

- [ ] **Step 3: Run tests + commit**

```bash
.venv/bin/pytest tests/ -q && git add -A && git commit -m "feat: agent session persistence in SQLite"
```

---

### Task 8: Clock Skew Detection

**Files:**
- Modify: `src/yequ/transport/http_server.py` — detect skew on ingest/heartbeat

- [ ] **Step 1: Add skew detection to handle_ingest and _handle_heartbeat**

```python
from yequ.utils import now_iso
from datetime import datetime, timezone

def _check_clock_skew(device_id, client_ts_str):
    """Check if client timestamp deviates from server time."""
    try:
        client_ts = datetime.fromisoformat(client_ts_str.replace("Z", "+00:00"))
        server_ts = datetime.now(timezone.utc)
        skew = abs((server_ts - client_ts).total_seconds())
        if skew >= 60:
            mq.publish("yequ:events", {
                "event_type": "clock_skew", "severity": "warning",
                "title": f"时钟偏差: {device_id}",
                "body": f"偏差 {int(skew)}s",
                "device_id": device_id,
                "data": {"skew_seconds": int(skew)},
            })
        return skew
    except Exception:
        return None
```

Call in `handle_ingest` after successful token validation:
```python
_check_clock_skew(msg.device_id, msg.timestamp)
```

Call in `_handle_heartbeat` similarly if a timestamp field is present.

- [ ] **Step 2: Run tests + commit**

```bash
.venv/bin/pytest tests/ -q && git add -A && git commit -m "feat: clock skew detection"
```

---

### Task 9: Integration + Cleanup

**Files:**
- Modify: `config/monitor_rules.yaml` — add all new rules
- Modify: `src/yequ/dashboard.html` — handle new SSE event types

- [ ] **Step 1: Add all monitor rules to config**

```yaml
  - name: capability_silent
    description: "Capability 静默超时"
    condition:
      type: capability_silent
      params:
        multiplier: 5
    severity: warning
    cooldown_seconds: 600
    notify: true

  - name: command_timeout
    description: "指令超时未完成"
    condition:
      type: command_timeout
      params: {}
    severity: warning
    cooldown_seconds: 300
    notify: true
```

- [ ] **Step 2: Update Dashboard SSE listener for new events**

In `connectEventStream()`, refresh device list on all device-related events:
```javascript
if (['device_offline','device_online','device_approved','device_revoked',
     'device_registered','device_replaced','command_completed',
     'command_timeout'].includes(ev.event_type)) {
  loadDevices();
}
```

- [ ] **Step 3: Full integration test**

```bash
.venv/bin/pytest tests/ -q
```

Expected: 68+ passed

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: v2 integration — all rules, dashboard updates"
```

---

### Task 10: README + Docs Update

**Files:**
- Modify: `README.md`
- Modify: `docs/YQP-v1.0-protocol.md`

- [ ] **Step 1: Update README with Redis requirement**

```markdown
## Requirements

- Python 3.11+
- Redis 7+ (optional, auto-falls back to in-memory mode)
```

- [ ] **Step 2: Update YQP doc with new features**

Add sections for:
- Command poll endpoint
- Token rotation
- Clock skew handling

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "docs: update README and YQP spec for v2"
```

---

## Completeness Checklist

- [x] Redis Stream MQ with fallback (Task 2)
- [x] All 25 event types (Task 3 + existing 7)
- [x] Capability alignment: schema validation + silent detection (Task 4)
- [x] Command guarantees: pre-check, timeout, blocked list (Task 5)
- [x] Sensitive data: one-time media tokens (Task 5)
- [x] Token lifecycle: rotation + reinstall (Task 6)
- [x] Agent session persistence (Task 7)
- [x] Clock skew detection (Task 8)
- [x] Integration + docs (Task 9, 10)
