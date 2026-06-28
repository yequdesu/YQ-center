# YeQu Center Stage 5: Integration Closed-Loop Tests

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** pytest-only integration tests covering success, failure, timeout, cancel, and reconcile — no external services. Fake Node + FakeAgentProvider, all through ASGITransport.

**Architecture:** Single test file `tests/test_integration.py`. Each scenario provisions a node, sends YQP messages via HTTP, and asserts the chain end-to-end.

**Tech Stack:** pytest + httpx (ASGITransport) + FakeAgentProvider

---

## Scenarios (6 tests)

### 1. `test_full_success_flow`
Node hello → register capabilities → create invocation → poll → accept → finish(succeeded) → verify Job terminal + Timeline events + Invocation aggregation

### 2. `test_job_failure_flow`
Same as above but finish(failed) → verify Job failed + Invocation failed

### 3. `test_job_timeout_flow`
Create job with lease_sec=1 → poll → sleep 2s → scanner scan → verify timeout

### 4. `test_job_cancel_flow`
Create job → poll → accept → job.cancel → verify cancelling → finish(cancelled)

### 5. `test_reconcile_flow`
Create running job in DB → node.reconcile_jobs → verify continue action. Unknown job → forget.

### 6. `test_agent_invoke_with_function_calls`
Session → invoke with FakeAgentProvider returning function_calls → verify Timeline events

### 7. `test_security_boundaries`
401/403/409/400 coverage for auth/dedup/timestamp
