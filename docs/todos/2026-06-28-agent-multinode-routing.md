# Agent Runtime Rebuild And Multi-Node UX TODO

Status: implemented, pending manual Win+Linux demo
Scope: current engineering phase
Owner: Center / Agent / Console

## Implementation Checkpoint

Updated: 2026-06-28

Completed in the current engineering pass:

- Agent prompt context is now built from structured Center state before prompt
  rendering.
- Capability context groups tools by Node and carries routing mode, target Node,
  runtime/platform metadata, per-Node tool counts, and same-name capability
  diagnostics.
- `/agent/*` debug metadata and stream `agent.prompt_context` expose the
  rendered system prompt plus the structured capability context for frontend
  inspection.
- Console session detail now returns durable turn events, allowing the frontend
  to reconstruct a turn from persisted projection events instead of guessing
  from final messages.
- Console chat projection now uses a reducer for live SSE and persisted history,
  preserving assistant text/tool-call order and removing empty assistant
  placeholder bubbles.
- Session switching detaches the local stream view instead of cancelling the
  backend run, so returning to a session can rebuild from persisted turn events.
- `src/yequ/agent/runtime_state.py` now contains the first explicit
  `AgentRuntimeController`: runtime limits, step increments, provider-output
  decisions, waiting-approval pause state, and terminal failure mapping are no
  longer ad hoc local branches inside the stream function.
- Tool observation aggregation moved into `AgentToolObservationCollector`,
  preserving provider tool-call order while normalizing succeeded, failed, and
  waiting-approval results for the next model step.
- Tool validation/preflight/concurrency/job-polling stream execution moved from
  `agent_stream.py` into `src/yequ/agent/tool_stream.py`.
- Non-streaming `/agent/invoke` now reuses `AgentRuntimeController` for initial
  limits, step increments, and provider-output terminal decisions instead of
  maintaining a separate guard implementation.
- `agent_plan_stream` session lookup no longer closes a valid stream
  immediately after resolving the session.

Deferred follow-up boundaries:

- Tool validation, preflight, execution, and job polling are now out of
  `agent_stream.py`; synthesis/final persistence can move next if retry/resume
  grows beyond current event projection needs.
- Non-streaming `agent_service.py` still owns its response assembly and
  historical `/agent/invoke` contract, but no longer owns independent runtime
  guard semantics.
- Checkpointing currently reuses `AgentTurn` + `AgentTurnEvent`; a separate
  `AgentRun` table is still optional unless resume/retry semantics need more
  than event projection.
- Agent approval resume is deferred to a separate Agent resume
  phase. Current Agent behavior is explicit pause + admin approve/approve-and-run;
  it does not pretend the LLM continued after a human approval click.

## Stage Objective

Rebuild the Agent side into a recoverable, node-aware, inspectable runtime.

This phase is not a small multi-node patch. It is the first real Agent runtime
architecture phase for YeQu Center.

The end result should be visibly better in daily use:

- Agent chat opens in Auto routing mode, not pinned to one Node by default.
- The Agent can explain online Nodes and available capabilities from Center
  state.
- Tool calls show where they execute, e.g. `linux.system.info @ linux-node-01`.
- Assistant text and tool-call blocks keep the same order while streaming,
  after stream close, after session switch, and after refresh.
- A one-line assistant thought before a tool call renders as a compact bubble,
  without an unexplained blank row.
- Switching away from a running ReAct turn and back restores the visible
  intermediate state.
- Prompt context and structured capability context are visible in frontend
  diagnostics.
- Errors are explicit. No silent fallback, no hidden alias substitution, no
  hardcoded Agent reply pretending to be model output.

This is the stage success bar. Internal cleanup is not enough unless the above
is true.

## Architectural Decision

Keep the existing Center/Node control plane.

Do not replace:

- YQP;
- Node lifecycle;
- Invocation -> Job execution;
- policy / approval / resource lock;
- timeline / audit;
- short-lived DB session discipline.

The weak area is the Agent runtime and Console transcript model. Therefore this
phase should rebuild the Agent side around three backend primitives and one
frontend primitive:

```text
Backend:
  AgentRun checkpoint model
  AgentRuntime state machine
  AgentContextEngine / CapabilityContextBuilder

Frontend:
  Event-sourced transcript projection
```

This borrows the useful parts of mature Agent projects:

- durable run state and checkpointing;
- explicit step/state-machine execution instead of opaque while loops;
- structured context before prompt rendering;
- traceable tool execution;
- event-sourced UI reconstruction.

It deliberately does not adopt LangGraph, AutoGen, OpenAI Agents SDK, or MCP as
the core runtime. Their patterns are useful; replacing YeQu's control plane is
not.

## Current Problems To Remove

### Backend

1. The Agent receives a flat `available_functions` list.
2. Provider prompt generation owns too much context shaping.
3. ReAct execution is too close to a hand-written streaming loop.
4. There is no clear AgentRun checkpoint lifecycle.
5. SSE is treated as live output, but not enough state exists to restore an
   in-progress turn cleanly.
6. Capability names carry too much routing meaning:
   - Windows mostly exposes `system.*`.
   - Linux exposes `linux.*`.
   - Routing truth should be node/runtime metadata, not prefixes.

### Frontend

1. Live streaming and persisted history use different reconstruction paths.
2. Current live path mutates `ChatBlock[]` directly from SSE events.
3. Current persisted path rebuilds via `blocksFromPersisted()`.
4. These paths can reorder content:
   - live: assistant text -> tool call;
   - persisted: tool call -> assistant text.
5. Empty assistant streaming placeholders are created and later removed,
   causing flicker and blank-space bugs.
6. Markdown paragraph margins can make a single assistant sentence look like it
   contains an extra blank line.
7. Switching sessions currently treats live stream state as disposable.

## Target Runtime Shape

```text
User prompt
  -> AgentRun created
  -> AgentContextEngine builds structured context
  -> AgentRuntime state machine executes steps
  -> each step writes checkpoint + trace/projection event
  -> tool execution goes through Center Invocation -> Job
  -> Console consumes transcript projection
  -> SSE is only a live transport for the same projection
```

The core rule:

```text
State first, stream second.
```

SSE should reflect AgentRun state. It should not be the only place where the
run exists.

## Backend Workstream A: AgentRun Checkpoints

### Goal

Persist enough Agent run state to restore and inspect a running or completed
turn.

### Required Model

Use existing `AgentTurn`, session timeline, and messages if they are enough.
If they are not enough, add a small explicit checkpoint/projection model.

Required concepts:

```text
run_id
session_id
turn_id
status
current_step
step_seq
event_seq
routing_mode
target_node_id
provider_name
execution_mode
created_at
updated_at
terminal_error
```

Status values:

```text
created
building_context
model_running
validating_tools
preflighting
waiting_approval
executing_tools
observing
synthesizing
succeeded
failed
cancelled
```

Checkpoint data must be able to represent:

- prompt context;
- assistant text segments before tool calls;
- tool calls;
- tool call input;
- target node resolution;
- invocation/job IDs;
- tool result or failure;
- approval waiting state;
- final assistant text;
- terminal failure.

### Rules

- Checkpoints must be written with short-lived DB sessions.
- Do not hold a DB session across LLM calls.
- Do not hold a DB session across job polling.
- Do not hold a DB session across approval waits.
- Checkpoint failure should fail visibly; do not silently continue with a
  non-restorable run.
- Every transcript-relevant event must have a stable ordering key.

### Deliverables

- Agent run/checkpoint/projection service or equivalent.
- API or existing session endpoint extension that lets Console rebuild a run.
- Tests proving an in-progress run can reconstruct prompt context and tool
  state.

## Backend Workstream B: AgentRuntime State Machine

### Goal

Replace the implicit ReAct while-loop shape with explicit runtime states and
transitions.

This does not require a full graph framework. It does require code structure
that makes every Agent step named, testable, and checkpointed.

### Required State Flow

```text
created
  -> build_context
  -> model_step
  -> validate_model_output
  -> preflight_tool_calls
  -> execute_tool_calls
  -> observe_tool_results
  -> next_model_step | synthesize_final
  -> succeeded
```

Failure and pause edges:

```text
any step -> failed
preflight_tool_calls -> waiting_approval
waiting_approval -> execute_tool_calls
execute_tool_calls -> failed | observe_tool_results
```

### Required Step Contracts

Each step should have explicit input/output data:

- `BuildContextInput` / `BuildContextResult`
- `ModelStepInput` / `ModelStepResult`
- `ToolValidationResult`
- `ToolPreflightResult`
- `ToolExecutionResult`
- `ObservationResult`
- `SynthesisResult`

Names can differ, but the boundaries must exist.

### Rules

- The runtime may still execute inside the existing stream route initially.
- The route should orchestrate the runtime, not contain runtime logic.
- Tool execution must continue using Center application services.
- Policy, approval, resolver, invocation, job, and timeline behavior must not
  be bypassed.
- If the model returns invalid output, fail explicitly with an Agent error
  event.
- If a tool is unknown or unavailable, fail explicitly.
- Do not create hardcoded natural-language fallback answers.

### Deliverables

- [x] First Agent runtime/state-machine module:
  `src/yequ/agent/runtime_state.py`.
- [x] Targeted tests for runtime limits, provider-output decisions,
  waiting-approval state, and stream-event status mapping.
- [x] Tool observation collector for preserving provider call order and approval
  pause detection.
- [x] Stream route reduced to transport/orchestration for tool execution.
- [x] Tool validation/preflight/execution/observation moved behind explicit step
  contracts.
- [x] Targeted tests for successful ReAct step ordering, wrong tool failure,
  non-streaming guard failures, and checkpoint/event ordering.
- [~] Agent approval resume as a full graph transition is deferred to a
  dedicated Agent resume API/task. Maintenance runs already have their own
  resume mechanism.

## Backend Workstream C: AgentContextEngine And Capability Context

### Goal

Build Agent context as structured data first, then render prompt text from that
data.

### Required Module

```text
src/yequ/agent/capability_context.py
```

or a broader:

```text
src/yequ/agent/context_engine.py
```

The broader name is preferred if implementation also includes runtime state,
node state, diagnostics, or future artifact context.

### Required Context Shape

```json
{
  "routing_mode": "auto",
  "target_node_id": null,
  "nodes": [
    {
      "node_id": "winClient",
      "platform_os": "windows",
      "status": "online",
      "runtime_ids": [],
      "capabilities": [
        {
          "name": "system.info",
          "capability_id": "...",
          "effect": "read",
          "risk": "safe",
          "description": "..."
        }
      ]
    },
    {
      "node_id": "linux-node-01",
      "platform_os": "linux",
      "status": "online",
      "runtime_ids": [],
      "capabilities": [
        {
          "name": "linux.system.info",
          "capability_id": "...",
          "effect": "read",
          "risk": "safe",
          "description": "..."
        }
      ]
    }
  ],
  "tool_count_by_node": {
    "winClient": 12,
    "linux-node-01": 4
  }
}
```

### Routing Rules

- Auto routing means no single node is pinned.
- Auto exposes capabilities from all online schedulable nodes.
- Pinned routing exposes only the pinned node's executable capabilities.
- Offline/unschedulable nodes may appear as diagnostic state, but not as
  executable tools.
- Same-name capabilities must show all source nodes.
- Platform must come from node/runtime metadata, not tool-name prefix.

### Prompt Rendering

Replace flat prompt text:

```text
Tools:
- system.info [nodes: winClient]: ...
- linux.system.info [nodes: linux-node-01]: ...
```

with grouped context:

```text
Routing mode: auto
No single current node is pinned.

Nodes and capabilities:

Node: winClient
Platform: windows
Capabilities:
- system.info: ...

Node: linux-node-01
Platform: linux
Capabilities:
- linux.system.info: ...
```

Provider-visible raw tool definitions may remain for this phase. Long-term raw
tool injection is tracked in the Tool RAG roadmap.

### `agent.prompt_context`

Keep:

- `provider_name`
- `system_prompt`
- `target_node_id`
- `execution_mode`
- `available_functions`

Add:

- `routing_mode`
- `capability_context`
- `nodes`
- `tool_count_by_node`

### Deliverables

- Context builder.
- Prompt renderer using grouped context.
- Prompt context event/API metadata exposing both raw prompt and structured
  context.
- Tests for Auto, pinned Linux, pinned Win, same-name capability, and wrong
  tool failure.

## Frontend Workstream: Transcript Projection

### Goal

Replace ad hoc chat block mutation with a canonical transcript projection.

The frontend should not have separate logic for:

```text
live SSE rendering
persisted history rendering
session restore rendering
```

All inputs should pass through one reducer.

### Required Flow

```text
SSE events
Persisted session messages
AgentRun projection
        -> transcript reducer
        -> TranscriptSegment[]
        -> React components
```

### Segment Types

```text
user_message
assistant_text
tool_group
tool_call
approval
system_event
run_status
diagnostic
```

Every segment must have:

- stable `segment_id`;
- ordering key;
- session/run/turn identity if available;
- status if mutable;
- source event/message ID if available.

### Ordering Rules

- Never reorder segments by role.
- Preserve the order generated by AgentRun event sequence.
- Assistant text before tool calls remains before tool calls after refresh.
- Tool result/observation remains attached to the correct tool call.
- Assistant text after observation remains after the relevant tool call.
- Empty assistant placeholders are not renderable transcript segments.
- Thinking/provider-running state is a run status indicator, not an empty
  assistant bubble.

### Rendering Rules

- A single plain sentence is compact.
- Markdown spacing is used for real markdown blocks, not to create blank rows
  in one-line messages.
- Standard transcript segment gap is the only gap between an assistant thought
  and the following tool-call block.
- Tool cards show `tool_name @ node_id`.
- Later runtime metadata may extend this to `tool_name @ node_id / runtime_id`.
- Prompt diagnostics are inspectable but are not assistant messages.

### Suggested Module Split

```text
console-frontend/src/agent-transcript/types.ts
console-frontend/src/agent-transcript/reducer.ts
console-frontend/src/agent-transcript/fromSse.ts
console-frontend/src/agent-transcript/fromPersisted.ts
console-frontend/src/agent-transcript/rendering.ts
```

`useAgentChat` should become orchestration glue:

- start/stop streams;
- feed events to reducer;
- load history/projection;
- expose `TranscriptSegment[]`;
- expose run status;
- expose diagnostics;
- no longer own ordering rules.

### Session Switching

Normal session switching should not mean "cancel and lose the running trace".

Required behavior:

- switching away from a running session keeps or detaches from the run;
- switching back reloads projection/history and restores the transcript;
- if the stream is still active or reconnectable, new events continue from the
  restored state;
- if the run ended, the terminal state is shown;
- cancellation is an explicit action, not the default meaning of navigation.

## Capability Naming Decision

Current state is mixed:

- Windows uses mostly `system.*`.
- Linux uses `linux.*`.

This phase should not perform a full rename unless it becomes necessary, but
it must stop treating prefixes as routing truth.

Near-term rule:

- keep registered names as-is;
- route by node/runtime metadata;
- expose grouped context so mixed names are understandable;
- do not add silent aliases;
- document inconsistency in diagnostics.

Longer-term canonical capability IDs and aliases belong in a separate
capability model task or the Tool RAG roadmap.

## Implementation Order

1. Backend context engine and grouped prompt.
2. AgentRun checkpoint/projection baseline.
3. AgentRuntime state-machine boundary.
4. Frontend transcript reducer and rendering refactor.
5. Session switching restoration.
6. Prompt diagnostics UI.
7. Focused tests and user-level demo.

The ordering may be adjusted during implementation, but the final result must
include all workstreams. Completing only context grouping or only frontend
rendering is not enough for this phase.

## Acceptance Criteria

### Backend

1. Auto routing with Win + Linux online:
   - `routing_mode == "auto"`;
   - `target_node_id` is null;
   - capability context includes both nodes;
   - prompt groups capabilities by node.

2. Pinned Linux:
   - only Linux executable tools are provider-visible;
   - Win-only tools are absent;
   - prompt says pinned to `linux-node-01`.

3. Pinned Win:
   - only Win executable tools are provider-visible;
   - Linux-only tools are absent;
   - prompt says pinned to `winClient`.

4. AgentRun restoration:
   - an in-progress run can reconstruct prompt context, assistant text
     segments, tool call state, and terminal/error state if present.

5. State-machine behavior:
   - successful ReAct run records ordered steps;
   - wrong tool call fails explicitly;
   - no silent fallback;
   - no hardcoded assistant answer in place of model/tool output.

6. DB discipline:
   - no DB session is held across LLM calls, job polling waits, or approval
     waits.

### Frontend

1. Live SSE rendering and persisted rendering use the same transcript reducer.
2. Assistant text -> tool call order is preserved after stream close, refresh,
   and session switch.
3. One-line assistant text before a tool call has no unexplained blank row.
4. Tool cards render node identity.
5. Prompt diagnostics show raw prompt and structured capability context.
6. Agent chat defaults to Auto routing.
7. Node pinning is an advanced/debug override, not the main path.
8. Switching sessions during a running ReAct turn restores intermediate state.
9. Cancellation is explicit.

### User-Level Demo

Run this before declaring the phase complete:

1. Start Center with Win Node and Linux Node online.
2. Open Agent chat.
3. Confirm Auto routing is active without selecting a node.
4. Ask: "What nodes and capabilities are currently available?"
5. Confirm the answer describes both nodes from Center state.
6. Open diagnostics and confirm grouped capability context is visible.
7. Ask: "Show Linux system info."
8. Confirm the tool card shows `linux.system.info @ linux-node-01`.
9. Ask a prompt that causes assistant text before a tool call, e.g. "Inspect
   the current project structure."
10. Confirm the assistant text bubble appears before the tool-call block with
    clean spacing.
11. Refresh or reload the session.
12. Confirm the order and spacing are unchanged.
13. Start a long-running Agent/tool operation.
14. Switch to another session before it finishes.
15. Switch back.
16. Confirm intermediate tool state and run status are restored.

## Tests To Add

### Backend

- `test_capability_context_groups_by_node`
- `test_auto_prompt_declares_no_pinned_node`
- `test_pinned_linux_prompt_excludes_win_tools`
- `test_pinned_win_prompt_excludes_linux_tools`
- `test_prompt_context_includes_grouped_capability_context`
- `test_same_name_capability_lists_multiple_source_nodes`
- `test_wrong_platform_tool_call_returns_explicit_error`
- `test_agent_run_checkpoint_records_ordered_steps`
- `test_agent_run_projection_rebuilds_in_progress_tool_state`
- `test_agent_runtime_state_machine_success_path`
- `test_agent_runtime_state_machine_wrong_tool_failure`

### Frontend

- Transcript reducer preserves `assistant_text -> tool_call -> observation`.
- Transcript reducer produces the same segments from live SSE and persisted
  projection/history.
- Assistant text before tool calls does not move after refresh.
- Empty streaming assistant placeholders are not rendered.
- One-line assistant markdown is compact.
- Tool cards show `name @ node_id`.
- Prompt diagnostics render raw prompt and structured context.
- Auto mode sends no `target_node_id`.
- Explicit pin mode sends `target_node_id`.
- Session switching restores an in-flight ReAct turn.

## Validation Policy

- Prefer targeted tests during iteration.
- Use the repository fast-check script when touching both backend and frontend.
- Full suite is a checkpoint before larger merge/push decisions, not every
  inner-loop run.

## Non-Goals

- Do not implement full Tool RAG in this phase.
- Do not build a multi-agent swarm.
- Do not replace YQP with MCP.
- Do not adopt a full external Agent framework.
- Do not migrate every capability name immediately.
- Do not add silent alias fallback.
- Do not hide execution errors.

Longer-range capability discovery and Tool RAG work is tracked separately:

- `docs/todos/2026-06-28-agent-tool-rag-roadmap.md`
