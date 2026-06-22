# Agent Console UX Closed-Loop Optimization — Design Spec

**Date**: 2025-06-22
**Status**: Approved, implementing

## Overview

Five areas of improvement to make the Agent Console meet "real-time, sequential, readable, recoverable" interaction expectations.

## 1. Chat Block Timeline Model

Replace `ChatMessage[]` (flat list of assistant bubbles with embedded toolCalls) with `ChatBlock[]` (timeline blocks).

### Block Types

```typescript
type ChatBlock =
  | { type: "user"; id; content; created_at }
  | { type: "assistant_text"; id; content; streaming; created_at }
  | { type: "tool_group"; id; tool_calls; created_at }
  | { type: "tool_call"; id; call_id; name; status; input; result; error }
  | { type: "system_event"; id; label; created_at };
```

### SSE → Block Rules

- `agent.output.delta` → append to current `assistant_text` block; create new one if previous block is not `assistant_text`
- `agent.tool_call.created` → create/append to `tool_group` block; create new one if previous is not `tool_group`
- `agent.completed` → render as `system_event` (subtle), NOT as a big bubble
- `tool_group` must NOT wrap `assistant_text`, and vice versa

### Files

- `console-frontend/src/hooks/useAgentChat.ts` — rewrite state management
- `console-frontend/src/pages/AgentChatPage.tsx` — rewrite rendering

## 2. Tool Call Concurrency Scheduling

### Rules

- `risk=safe && effect=read` → can run concurrently (max 4)
- `maintenance/write/destructive/approval-required` → serial only
- Same `resource_key` with `conflict_policy=serialize` → serial only
- Emit all `created` events first, then individual running/completed/failed
- Write observations back to LLM history in original provider call order

### Files

- `src/yequ/agent/agent_stream.py` — new `_execute_tool_calls_with_scheduling()`

## 3. Session Sidebar Redesign

### Backend

- Add `updated_at`, `label` columns to Session model
- `GET /admin/sessions` returns: `updated_at`, `last_message_preview`, `message_count`, `running`, `label`
- Update `session.updated_at` on every message write
- `PATCH /admin/sessions/{id}` rename must not overwrite other metadata
- Sort by `updated_at` desc (not `started_at`)

### Frontend

- Current session highlighted
- Each item shows: label, last prompt preview, update time, status icon (idle/running/error)
- Rename, delete, create
- Search/filter input
- Switch confirmation when streaming
- Fetch fresh on new session (no stale cache)

### Files

- `src/yequ/models/session.py` — model changes
- `alembic/versions/` — migration
- `src/yequ/api/routes/admin.py` — endpoint changes
- `src/yequ/agent/agent_stream.py` — update session on message write
- `console-frontend/src/pages/AgentChatPage.tsx` — sidebar rebuild
- `console-frontend/src/api/types.ts` — type updates
- `console-frontend/src/api/admin.ts` — API updates

## 4. Agent Output Quality

Rewrite `_fallback_synthesis_from_stream`:
- All tools failed → list each tool and failure reason
- Partial success → "已确认 / 未确认 / 下一步" sections
- Node offline / no capability → explicit Center/Node status diagnostic
- "再次检查" → compare with previous session history

## 5. Tests

New tests for:
- Block ordering in stream (assistant_text → tool_group → assistant_text → ...)
- Concurrent safe/readonly tool created events appear first
- Write/approval tools NOT concurrent
- Session refresh preserves chat history order
- Session sidebar returns correct last_message_preview / updated_at
- No online node produces explicit diagnostic, not generic failure list

### Key constraints

- Order must come from event stream, not hardcoded keywords
- Tool calls must NOT go back into assistant bubbles
- agent.completed must NOT render as big bubble
- Concurrency must not break approval/resource lock semantics
- Frontend must NOT fake streaming with setTimeout; SSE-driven only
- React Query stale cache must not override current session messages
