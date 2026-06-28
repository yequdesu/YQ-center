# Center/Agent Refactor Completion Report

Date: 2026-06-25

This report records the final state of the Center/Agent decoupling and code-quality cleanup work.

## Completed

1. Added an application layer under `src/yequ/application/` and moved shared tool execution, tool preflight, and maintenance plan entry points behind stable application services.
2. Updated Agent non-streaming and SSE tool execution paths to use the application layer instead of directly creating Invocations, Jobs, Approvals, Resource Locks, and timeline records.
3. Added import-boundary tests to prevent the Agent runtime from regressing back to direct `yequ.services.*` dependencies.
4. Split the former large Admin route module into focused route modules:
   - `admin_provisioning.py`
   - `admin_invocations.py`
   - `admin_sessions.py`
   - `admin_nodes.py`
   - `admin_activity.py`
   - `admin_approvals.py`
   - `admin_schemas.py`
5. Kept `admin.py` as a compatibility router only.
6. Closed the Maintenance L2 approval loop with pending approval creation, resume, and reject endpoints.
7. Added `SignalState` as the current-state store for reported Signals, including migration and Admin query endpoints.
8. Added `SignalStateScanner` to proactively mark expired Signals stale and write `signal.stale` timeline events.
9. Restored `ruff check .` as a passing repository-wide quality gate.

## Current Architecture Direction

```text
api/routes/*
  -> application/*
       -> services/*
            -> models/*
            -> protocol/*

agent/*
  -> application/*
  -> provider implementations
```

The most important dependency correction is that Agent code now calls Center behavior through application services rather than assembling Center internals directly.

## Verification

Commands used during the final pass:

```bash
ruff check .
pytest -q tests/test_signal_state.py
pytest -q tests/test_admin_api.py tests/test_agent_tool_execution.py tests/test_approval_api.py tests/test_l2c.py
pytest -q
alembic heads
git diff --check
mypy src/
```

Final observed results:

- `ruff check .`: passed.
- `pytest -q`: 239 passed, 17 skipped.
- `alembic heads`: `d8e9f0a1b2c3 (head)`.
- `git diff --check`: passed, with Windows line-ending warnings only.
- `mypy src/`: failed with 249 errors in 28 files.

## Remaining Debt

`mypy src/` is still a separate historical debt area. The latest failures are concentrated in generic `dict` annotations, dynamic Agent/provider payload typing, SQLAlchemy result typing, CLI annotations, route DTO boundaries, nullable datetime handling, and missing `jsonschema` stubs. This should be treated as a dedicated type-hardening phase rather than mixed into the Center/Agent architectural refactor.
