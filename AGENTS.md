# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

YeQu Center is a personal infrastructure control center. It connects devices (Nodes), collects state (Signals), dispatches capabilities (Functions), records an audit timeline, and provides a unified entry point for LLM Agents, CLI, and future Web/mobile clients.

## Build & Development Commands

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run the server (port 9800, reload enabled)
python -m yequ.main
# or via entry point:
yequ  # (CLI tool, not server)

# Run all tests (uses SQLite, no PostgreSQL needed)
pytest

# Run a single test file
pytest tests/test_yqp_protocol.py

# Run a single test function
pytest tests/test_yqp_protocol.py::test_hello_flow

# Run tests with verbose output
pytest -v

# Lint
ruff check .

# Format
ruff format .

# Type check (strict mode)
mypy src/

# Create a new DB migration (requires running PostgreSQL via docker-compose)
docker compose up -d postgres
alembic revision --autogenerate -m "description_of_change"
alembic upgrade head

# Run the CLI
python -m yequ.cli health
python -m yequ.cli nodes list
```

## Architecture

### Core Concepts (layered top to bottom)

| Layer | Responsibility |
|---|---|
| **Center** | Auth, registry, policy, scheduling, state, audit, job lifecycle, waitable execution |
| **Node** | A connectable execution environment (device, VM, etc.) |
| **Daemon** | Persistent process on a Node; executes Jobs, reports Signals |
| **Plugin** | Adapts local system/service capabilities into Functions and Signals |
| **Agent** | LLM-driven caller; goes through Center's standard path, never direct to Node |

### Key Domain Objects

- **Invocation** — semantic call intent by an Actor (user/agent/system). Created in PENDING, transitions to RUNNING when Jobs are fanned out.
- **Job** — the actual execution task dispatched to a specific Node. Follows a strict state machine: `created → queued → claimed → running → (succeeded|failed|cancelled|cancelling→cancelled|timeout)`. Terminal states are immutable.
- **Capability** — a Function or Signal registered by a Node's Plugin. Contains risk level, effect, input/output schemas, resource keys, and conflict policy.
- **Operation** — planned Center Execution Runtime v2 waitable runtime process. It will represent long/workflow execution such as transfer, maintenance, long jobs, approval waits, and future subagent runs without replacing domain models such as Job or TransferSession.
- **TimelineEvent** — every significant action is recorded with a monotonically increasing `global_seq`. Serves as audit log and distributed tracing backbone.

### Center Execution Runtime v2 Direction

The current architecture is moving from "Capability Runtime v1" to "Center Execution Runtime v2". The target runtime adds:

- `ExecutionAdmissionService` to decide per call whether to run inline, wait synchronously, create a waitable Operation, create a workflow Operation, detach, require approval, or deny.
- `Operation` / `OperationEvent` / wait handles for long tasks and workflow fan-out/fan-in.
- Workflow handlers such as TransferWorkflow and MaintenanceWorkflow. `TransferSession` remains the transfer domain model; Operation owns waiting, events, cancellation, and resume projection.
- AgentRun `waiting_operation` semantics. Agents must not burn LLM tokens polling status tools for long operations.
- A PostgreSQL-backed operation event/outbox first; external MQ can be introduced later as a dispatcher backend, not as a second source of truth.

Primary planning document: `docs/todos/2026-06-30-center-execution-runtime-v2.md`.

### YQP Protocol (`src/yequ/protocol/`)

The YeQu Protocol is the communication contract between Center and Node Daemons. All messages use a unified envelope with `message_type` routing:

- **Node lifecycle**: `node.hello` → `node.accepted`, heartbeat, capability registration
- **Job delivery**: poll-based (`job.poll` → `job.available`), claimed→running→finished
- **Signal reporting**: periodic push of signal values with schema validation
- **Reconciliation**: reconnection recovery with conflict resolution rules

Single POST endpoint at `/yqp/` dispatches by `message_type`. Auth is Bearer token in HTTP header (not in payload).

### Policy Engine (`src/yequ/services/policy.py`)

Execution mode × risk level matrix:

| Mode | safe | maintenance | destructive | catastrophic |
|---|---|---|---|---|
| auto | allow | allow | ask | deny |
| assist | allow | conditional | ask | deny |
| readonly | allow | deny | deny | deny |
| manual | allow | ask | ask | deny |

L2 write operations always require approval, regardless of risk level.

### Agent Pipeline (`src/yequ/agent/`)

```
prompt → Provider.invoke() → raw tool_calls
→ validate (known function? loop? policy?) → resolve target node
→ create Invocation + Job → poll for terminal status → collect result → final response
```

- `AgentProvider` is the abstract interface for LLM backends. Two implementations: `FakeAgentProvider` (deterministic testing) and `DeepSeekProvider` (OpenAI-compatible API).
- Agent never calls Nodes directly — everything goes through Center's standard Invocation→Job path.
- Enforcement: depth limit, step limit, total duration limit, circular dependency detection.

### Maintenance Plans (`src/yequ/services/maintenance_executor.py`)

Multi-step maintenance operations with check→repair→verify flow:
- **Conditions**: `always`, `if_previous_unhealthy`, `after_repair`, `if_previous_failed`, `manual`
- **Artifacts**: `before`, `after`, `check_result`, `verify_result`, `error`, `rollback_hint` written at each step boundary
- **Rollback**: failures in repair/write/verify steps trigger `rollback_recommended` with hints
- Test failure injection via `test.maintenance.repair_fail` / `test.maintenance.verify_fail` functions or `__test_fail_stage` in input_data

### Job State Machine (`src/yequ/services/job_state_machine.py`)

ALL job status changes MUST go through `transition()`. Illegal transitions are rejected with audit events. Terminal states (succeeded/failed/cancelled/timeout) are immutable.

### Database

- Production: PostgreSQL via asyncpg. Connection URL in `YEQU_DATABASE_URL`.
- Dev/Test: SQLite via aiosqlite. Test conftest creates a fresh in-file SQLite DB per run.
- Migrations in `alembic/versions/`. Always use alembic for schema changes.
- SQLite is rejected at startup in non-test mode (enforced in `api/app.py` lifespan).

### Route Structure

| Prefix | Tag | Auth Scope | Purpose |
|---|---|---|---|
| `/healthz` | health | none | Health check |
| `/agent/*` | agent | agent token | Agent sessions, invoke, plan |
| `/admin/*` | admin | admin token | Node provisioning, invocations, jobs, timeline, approvals, tokens, maintenance plans |
| `/yqp/` | yqp | node Bearer token | Node protocol endpoint |

## Testing

- Tests use SQLite with WAL mode (configured in `tests/conftest.py` via `override_settings` fixture)
- `test_mode=True` skips background tasks (timeout scanner, timeline writer, recovery scan)
- `require_admin_auth=False` in test mode bypasses token auth
- Key fixtures: `client` (async HTTP client), `db_session` (raw DB access), `provisioned_node`, `node_with_hello` (provisioned + hello'd node)
- `make_yqp_envelope()` helper builds protocol messages for YQP endpoint tests
- Use `pytest-asyncio` with `asyncio_mode = "auto"`

## Configuration

All settings via environment variables with `YEQU_` prefix (see `src/yequ/config.py`):
- `YEQU_DATABASE_URL` — PostgreSQL connection string
- `YEQU_DEEPSEEK_API_KEY` — DeepSeek API key for LLM agent
- `YEQU_DEBUG` — enable debug logging
- `YEQU_DEBUG_TIMELINE` — write diagnostic timeline events
- `YEQU_REQUIRE_ADMIN_AUTH` — enforce admin/agent token scopes
- `YEQU_LOG_FORMAT` — `json` or `console`

## Key Patterns

- **Never commit without a timeline event**: State changes should be accompanied by a `TimelineEvent` with the next `global_seq`.
- **State transitions through `job_state_machine.transition()`**: Never set `job.status` directly.
- **Write operations need approval**: L2 write/destructive effects automatically create an `ApprovalRequest`. The caller must approve it before the Job is created.
- **Timeline writer is async**: For diagnostic/fire-and-forget events, use `get_timeline_writer().enqueue(event)`. For critical audit events, write synchronously in the same transaction.
- **DB sessions are short-lived**: The Agent and Maintenance executor poll for results using fresh sessions from `async_session_factory` — never hold a session open while waiting.
- **Documentation encoding**: All repository documents must be UTF-8. See `docs/documentation-policy.md`.
