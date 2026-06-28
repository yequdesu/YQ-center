# YeQu Center: Readonly Admin APIs + E2E Script

> **For agentic workers:** Use superpowers:subagent-driven-development

**Goal:** Add 7 GET admin query endpoints + E2E verification script + tests.

**Architecture:** All endpoints added to `admin.py`. Query params support `node_id`, `status`, `limit`, `event_type` etc. E2E script calls Center API to verify winClient chain.

---

### Task 1: GET Admin Query Endpoints

**Files:** Modify `src/yequ/api/routes/admin.py`

7 endpoints:

| Method | Route | Query Params |
|---|---|---|
| GET | /admin/nodes | — |
| GET | /admin/nodes/{node_id} | — |
| GET | /admin/capabilities | node_id |
| GET | /admin/jobs | node_id, status, invocation_id, limit |
| GET | /admin/jobs/{job_id} | — |
| GET | /admin/invocations/{invocation_id} | — |
| GET | /admin/timeline | node_id, job_id, invocation_id, session_id, event_type, limit, cursor, created_after, created_before |

### Task 2: E2E Script

**Files:** Create `scripts/e2e_win_node.py`

Python script that:
1. GET /healthz
2. Confirm winClient exists and is online
3. POST /admin/invocations → system.metrics.snapshot on winClient
4. Poll GET /admin/jobs/{job_id} until succeeded
5. Output invocation_id, job_id, result, timeline events
6. POST /agent/sessions → POST /agent/invoke
7. Verify success=true

### Task 3: Tests + Final Verification
