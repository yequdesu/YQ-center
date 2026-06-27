# YQP Production Reliability Report

Date: 2026-06-25

This report records the stage 9 YQP reliability hardening work.

## Completed

1. Replaced the YQP replay-protection path with a database-backed dedup store.
   - New model: `YqpMessage`.
   - New table: `yqp_messages`.
   - New migration: `e9f0a1b2c3d4_add_yqp_message_dedup_store.py`.
   - Duplicate `message_id` is now detected by a database unique constraint, so it survives Center process restarts and works across multiple workers sharing the same database.
2. Kept the old in-memory `MessageDedup` class only as a compatibility helper for older imports and lightweight unit tests.
3. Made empty job polling explicit:
   - `job.poll` with available jobs returns `message_type=job.available`.
   - `job.poll` with no available slots/jobs returns `message_type=job.empty`.
   - The payload remains compatible: `{"jobs": []}`.
4. Added `node_id` to YQP response envelopes for protocol context and easier troubleshooting.
5. Tightened reconcile terminal arbitration:
   - Once Center has a terminal Job state, Center remains authoritative.
   - Late daemon terminal results are returned as `discard_result`.
   - Late daemon running state is returned as `cancel`.
   - Center terminal Job output/status are not overwritten.

## Tests Added

1. Persistent message dedup record is written after a successful YQP request.
2. Empty `job.poll` returns `job.empty`, preserves `jobs: []`, and includes `node_id`.
3. Reconcile with Center terminal and daemon late terminal result discards the daemon result and preserves Center state.

## Verification

Commands run:

```bash
pytest -q tests/test_yqp_protocol.py tests/test_job_poll_capacity.py
pytest -q tests/test_integration.py tests/test_agent_tool_execution.py tests/test_l2a.py tests/test_l2b.py tests/test_l2c.py
ruff check .
```

Observed results:

- YQP focused tests: 25 passed.
- Integration/Agent/L2 focused tests: 46 passed.
- `ruff check .`: passed.

## Remaining YQP Work

1. Add a cleanup strategy for old `yqp_messages` rows beyond lazy expiry cleanup on each incoming YQP request, if message volume becomes high.
2. Consider storing a response fingerprint for strictly idempotent replay responses. Current behavior rejects duplicates with HTTP 409.
3. WebSocket or push delivery remains future work; current production contract is still poll-based.
