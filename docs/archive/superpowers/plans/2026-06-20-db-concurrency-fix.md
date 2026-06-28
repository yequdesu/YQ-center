# DB Concurrency Fix Plan

> Execute sequentially. Each fix is independently testable.

### Fix 1: Agent Long Transaction — `wait_invocation_terminal` uses short-lived sessions

Agent invoke must NOT hold a DB session across Provider call + wait loop. Use new sessions for each poll.

### Fix 2: Async Timeline Writer

Add `asyncio.Queue`-backed Timeline writer. Request path enqueues events. Background worker flushes in batches.

### Fix 3: PostgreSQL as Default

Add `asyncpg`, set default DB URL to PostgreSQL, create .env with connection string. SQLite fallback for tests.

### Fix 4: YQP Write Convergence

heartbeat: only update Node, no Timeline. signal.report: batch upsert. job.poll: no-op commit when empty.
