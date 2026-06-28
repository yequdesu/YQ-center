# Agent SSE Contract

Agent streaming events are delivered via `POST /agent/invoke/stream` and `POST /agent/plan/stream` as `text/event-stream`.

## Event Envelope

Every event has the same envelope:

```json
{
  "event_id": "evt_<16 hex>",
  "event_type": "<see table below>",
  "session_id": "sess_<16 hex>",
  "trace_id": "tr_<16 hex>",
  "timestamp": "ISO-8601 UTC",
  "data": { /* type-specific payload */ }
}
```

## Invoke Stream Event Types

| Event Type | Direction | data.* | Notes |
|---|---|---|---|
| `stream.open` | always first | — | Opens the SSE stream |
| `agent.prompt_context` | after `stream.open` | `provider_name`, `system_prompt`, `target_node_id`, `execution_mode`, `available_functions` | Debug metadata. Frontend SHOULD expose this in diagnostics, not as assistant text |
| `agent.session.resolved` | after `stream.open` | `session_status`, `execution_mode` | Confirms session is valid |
| `agent.prompt.received` | after `session.resolved` | `prompt` (≤500 chars), `step` | User prompt acknowledged |
| `agent.loop.started` | after `prompt.received` | `max_steps`, `max_duration_sec` | ReAct loop begins |
| `agent.loop.iteration` | per loop iteration | `iteration`, `max_steps` | New iteration started |
| `agent.provider.started` | per provider call | `provider_name` | LLM invocation begins |
| `agent.output.delta` | 0+ per iteration | `content` (text chunk) | Streaming LLM text. Emitted BEFORE any tool calls in the same iteration |
| `agent.tool_call.created` | per tool call | `call_id`, `name`, `sanitized_name`, `input` | Tool call started. For concurrent-safe tools, all created events appear before the first execution event |
| `agent.tool_call.arguments` | per tool call, after `created` | `call_id`, `name`, `input` | Tool input arguments |
| `agent.invocation.created` | per tool call | `call_id`, `name`, `invocation_id`, `target_node_id` | Invocation created for this tool |
| `agent.job.queued` | per tool call | `call_id`, `name`, `invocation_id`, `job_id` | Job queued on target node |
| `agent.job.running` | per tool call | `call_id`, `name`, `job_id`, `status` | Job is executing |
| `agent.job.finished` | per tool call | `call_id`, `name`, `job_id`, `status` | Job reached terminal state |
| `agent.tool_call.completed` | per successful tool | `call_id`, `name`, `result` | Tool succeeded with result |
| `agent.tool_call.failed` | per failed tool | `call_id`, `name`, `error_code`, `message` | Tool failed |
| `agent.tool_call.waiting_approval` | per tool needing approval | `call_id`, `name`, `approval_id`, `status`, `message` | Write operation requires approval. Frontend MUST render interactive ApprovalCard |
| `agent.approval.required` | per approval | `call_id`, `name`, `approval_id`, `target_node_id` | Approval has been created |
| `agent.observing` | after all tools in iteration | `tool_count` | Tools completed, agent observing results |
| `agent.synthesizing` | after final iteration | `source` | Agent has received final provider text |
| `agent.completed` | on success | `status`, `message` | Stream finished normally. Frontend MUST render as `system_event`, NOT a big bubble |
| `agent.failed` | on error | `error_code`, `message`, optional details | Stream failed. Protocol errors are surfaced here instead of generating fallback assistant text |
| `agent.provider.failed` | on provider error | `error_code`, `message` | LLM provider error |
| `stream.close` | always last | — | Closes the SSE stream |

## Standard Invoke Event Order

```
stream.open
  agent.prompt_context
  agent.session.resolved
  agent.prompt.received
  agent.loop.started
  ┌─ iteration N ─┐
  │ agent.loop.iteration
  │ agent.provider.started
  │ agent.output.delta              # LLM text (optional, multiples allowed)
  │ ├─ tool block ─┤                # one block per tool call
  │ │ agent.tool_call.created       # (all created first for concurrent tools)
  │ │ agent.tool_call.arguments
  │ │ agent.invocation.created
  │ │ agent.job.queued
  │ │ agent.job.running
  │ │ agent.job.finished
  │ │ agent.tool_call.completed / .failed / .waiting_approval
  │ └──────────────┘
  │ agent.observing                 # after all tools complete
  └────────────────┘
  agent.synthesizing
  agent.completed
stream.close
```

## Plan Stream Event Types

| Event Type | data.* |
|---|---|
| `stream.open` | — |
| `agent.prompt_context` | `provider_name`, `system_prompt`, `target_node_id`, `execution_mode`, `available_functions` |
| `agent.session.resolved` | `session_status` |
| `agent.prompt.received` | `prompt` |
| `agent.provider.started` | `provider_name` |
| `agent.planning.summary` | `message` |
| `agent.plan.step.created` | `seq`, `kind`, `function_name`, `requires_approval` |
| `agent.plan.created` | `plan_id`, `goal`, `status`, `step_count` |
| `agent.approval.required` | `plan_id`, `message` |
| `agent.completed` | — |
| `agent.failed` | `error_code`, `message` |
| `stream.close` | — |

## Error Codes

| error_code | Meaning | Frontend Display |
|---|---|---|
| `max_duration_exceeded` | Total duration exceeded limit | "Duration exceeded: {elapsed}s" |
| `max_steps_exceeded` | Step count exceeded limit | "Max steps exceeded" |
| `call_depth_exceeded` | Recursive call depth exceeded | "Call depth exceeded" |
| `session_not_found` | Session ID invalid | "Session not found" |
| `provider_timeout` | LLM invocation timed out | "Provider timed out" |
| `agent_protocol_error` | Provider returned neither assistant text nor tool calls, or returned an invalid planning intent | Show explicit protocol error; do not invent assistant text |
| `internal_error` | Unexpected server error | "Internal error: {message}" |
| `function_not_available` | No online node has this function | "No online node has '{name}'" |
| `circular_dependency` | Function already in call path | "Circular: {name}" |
| `policy_denied` | Execution mode disallows this action | Policy reason |
| `tool_failed` | Job ended with non-success status | "Tool {name} ended with {status}" |

## Frontend ChatBlock Mapping

| SSE Event | ChatBlock Type | Action |
|---|---|---|
| `agent.output.delta` | `assistant_text` | Append to current or create new |
| `agent.prompt_context` | diagnostics/debug panel | Store for inspection; do not render as assistant text |
| `agent.tool_call.created` | `tool_group` | Append to current tool_group or create new |
| `agent.tool_call.completed` | (update tool_group) | Update tool status → succeeded |
| `agent.tool_call.completed` where `name == "artifact.present"` | `artifact_presentation` + update tool_group | Update tool status and render returned artifacts as a first-class media block outside the tool-call card |
| `agent.tool_call.failed` | (update tool_group) | Update tool status → failed |
| `agent.tool_call.waiting_approval` | (update tool_group) + `system_event` (approval) | Update tool status → waiting_approval. Also render ApprovalCard |
| `agent.approval.required` | `system_event` | Subtle banner. Frontend also shows ApprovalCard from waiting_approval |
| `agent.completed` | `system_event` | Subtle "Completed (status)" label. NOT a big bubble |
| `agent.failed` / `agent.provider.failed` | `system_event` | Error label |
| User prompt | `user` | User message bubble |

## Constraints

1. `tool_group` MUST NOT wrap `assistant_text`. `assistant_text` MUST NOT wrap `tool_group`. They are separate blocks in the timeline.
2. `agent.completed` MUST be rendered as a subtle `system_event`, never as a large content bubble.
3. All tool_call execution events for the same `call_id` MUST appear after its `agent.tool_call.created`.
4. For concurrent tools: all `agent.tool_call.created` events for the concurrent group MUST appear before any `agent.invocation.created` or `agent.job.queued`.
5. Tool call observations MUST be written back to LLM history in the original provider call order, regardless of concurrent execution order.
6. Approval text ("确认", "批准", "可以执行") MUST NOT auto-approve. User MUST click the ApprovalCard buttons.
7. The stream MUST NOT synthesize fallback assistant text when the provider gives no final answer. Surface `agent.failed` with `agent_protocol_error`.
8. Frontend MAY optimistically render the outgoing user prompt before the SSE
   stream emits `agent.prompt.received`, but it MUST reconcile the optimistic
   block with the server event instead of rendering a duplicate user bubble.
9. `artifact.present` is a presentation meta tool. Its artifacts SHOULD render
   as standalone chat media/content blocks. The tool-call card remains an
   execution/debug record and MUST NOT be the only place where presented media
   appears.
