# Agent Runtime 重建与多 Node UX 待办

状态：已验收
范围：已完成的 Agent runtime / 多 Node UX 基线
负责人：Center / Agent / Console

2026-06-30 更新：

本文件记录的是已验收的多 Node Agent UX 与基础 Runtime 重建。后续长任务、Operation Bus、Execution Admission、AgentRun `waiting_operation` 和 resume 由 `2026-06-30-center-execution-runtime-v2.md` 跟踪。不要继续在本文件中扩展新的调度总线设计。

## 实现检查点

更新时间：2026-06-28

手动验收：

- Win Node and Linux Node were connected together and ran stably.
- Multi-node Agent routing, prompt diagnostics, tool node identity, transcript
  ordering, and session restoration were accepted as the current baseline.
- Follow-up architecture should now start from the Tool RAG / Center meta-tool
  roadmap instead of continuing broad runtime refactors in this phase.

本轮工程已完成：

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

延后处理的边界：

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

## 阶段目标

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

## 架构决策

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

## 需要移除的当前问题

### 后端

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

### 前端

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

## 目标 runtime 形态

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

## 后端工作流 A：AgentRun Checkpoint

### 目标

Persist enough Agent run state to restore and inspect a running or completed
turn.

### 必要模型

Use existing `AgentTurn`, session timeline, and messages if they are enough.
If they are not enough, add a small explicit checkpoint/projection model.

必要概念：

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

状态值：

```text
created
building_context
model_running
validating_tools
preflighting
waiting_approval
waiting_operation
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
- operation waiting state;
- final assistant text;
- terminal failure.

### 规则

- Checkpoints must be written with short-lived DB sessions.
- Do not hold a DB session across LLM calls.
- Do not hold a DB session across job polling.
- Do not hold a DB session across approval waits.
- Checkpoint failure should fail visibly; do not silently continue with a
  non-restorable run.
- Every transcript-relevant event must have a stable ordering key.

### 交付物

- Agent run/checkpoint/projection service or equivalent.
- API or existing session endpoint extension that lets Console rebuild a run.
- Tests proving an in-progress run can reconstruct prompt context and tool
  state.

## 后端工作流 B：AgentRuntime 状态机

### 目标

Replace the implicit ReAct while-loop shape with explicit runtime states and
transitions.

This does not require a full graph framework. It does require code structure
that makes every Agent step named, testable, and checkpointed.

### 必要状态流

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
execute_tool_calls -> waiting_operation
waiting_approval -> execute_tool_calls
waiting_operation -> resume_with_observation
execute_tool_calls -> failed | observe_tool_results
```

### 必要 step 合同

Each step should have explicit input/output data:

- `BuildContextInput` / `BuildContextResult`
- `ModelStepInput` / `ModelStepResult`
- `ToolValidationResult`
- `ToolPreflightResult`
- `ToolExecutionResult`
- `ObservationResult`
- `SynthesisResult`

Names can differ, but the boundaries must exist.

### 规则

- The runtime may still execute inside the existing stream route initially.
- The route should orchestrate the runtime, not contain runtime logic.
- Tool execution must continue using Center application services.
- Policy, approval, resolver, invocation, job, and timeline behavior must not
  be bypassed.
- If the model returns invalid output, fail explicitly with an Agent error
  event.
- If a tool is unknown or unavailable, fail explicitly.
- Do not create hardcoded natural-language fallback answers.

### 交付物

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
- [~] Agent operation resume is deferred to Center Execution Runtime v2. Long
  operations must return a wait handle instead of forcing the LLM to poll status
  tools inside the ReAct loop.

## 后端工作流 C：AgentContextEngine 与 Capability Context

### 目标

Build Agent context as structured data first, then render prompt text from that
data.

### 必要模块

```text
src/yequ/agent/capability_context.py
```

or a broader:

```text
src/yequ/agent/context_engine.py
```

The broader name is preferred if implementation also includes runtime state,
node state, diagnostics, or future artifact context.

### 必要上下文形态

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

### 路由规则

- Auto routing means no single node is pinned.
- Auto exposes capabilities from all online schedulable nodes.
- Pinned routing exposes only the pinned node's executable capabilities.
- Offline/unschedulable nodes may appear as diagnostic state, but not as
  executable tools.
- Same-name capabilities must show all source nodes.
- Platform must come from node/runtime metadata, not tool-name prefix.

### Prompt 渲染

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

### 交付物

- Context builder.
- Prompt renderer using grouped context.
- Prompt context event/API metadata exposing both raw prompt and structured
  context.
- Tests for Auto, pinned Linux, pinned Win, same-name capability, and wrong
  tool failure.

## 前端工作流：Transcript Projection

### 目标

Replace ad hoc chat block mutation with a canonical transcript projection.

The frontend should not have separate logic for:

```text
live SSE rendering
persisted history rendering
session restore rendering
```

All inputs should pass through one reducer.

### 必要流程

```text
SSE events
Persisted session messages
AgentRun projection
        -> transcript reducer
        -> TranscriptSegment[]
        -> React components
```

### Segment 类型

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

### 排序规则

- 不能按 role 重新排序 segment。
- 保持 AgentRun event sequence 生成的顺序。
- tool call 前的 assistant text 在刷新后仍必须位于 tool call 前。
- tool result / observation 必须挂在正确的 tool call 上。
- observation 之后的 assistant text 必须位于对应 tool call 之后。
- 空 assistant placeholder 不能成为可渲染 transcript segment。
- Thinking / provider-running 状态是 run status indicator，不是空 assistant 气泡。

### 渲染规则

- 单句纯文本必须紧凑显示。
- Markdown 间距只用于真实 markdown block，不能让单行消息出现空行。
- assistant thought 与后续 tool-call block 之间只能使用标准 transcript segment 间距。
- 工具卡片显示 `tool_name @ node_id`。
- 后续 runtime 元数据可以扩展为 `tool_name @ node_id / runtime_id`。
- Prompt diagnostics 可检查，但不是 assistant message。

### 建议模块拆分

```text
console-frontend/src/agent-transcript/types.ts
console-frontend/src/agent-transcript/reducer.ts
console-frontend/src/agent-transcript/fromSse.ts
console-frontend/src/agent-transcript/fromPersisted.ts
console-frontend/src/agent-transcript/rendering.ts
```

`useAgentChat` 应退化为编排胶水：

- 启停 stream；
- 将事件送入 reducer；
- 加载 history / projection；
- 暴露 `TranscriptSegment[]`；
- 暴露 run status；
- 暴露 diagnostics；
- 不再拥有排序规则。

### Session 切换

普通 session 切换不应等价于“取消并丢失运行中的 trace”。

必要行为：

- 从运行中的 session 切走时，应保留 run 或与 run 分离，而不是取消。
- 切回时重新加载 projection / history 并恢复 transcript。
- 如果 stream 仍活跃或可重连，新事件应从恢复后的状态继续。
- 如果 run 已结束，应显示终态。
- 取消必须是显式动作，不能作为导航的默认含义。

## Capability 命名决策

当前状态是混合的：

- Windows uses mostly `system.*`.
- Linux uses `linux.*`.

除非必要，本阶段不做完整重命名；但必须停止把前缀当作路由事实。

近期规则：

- 保持 registered name 原样；
- 按 node / runtime metadata 路由；
- 暴露分组上下文，让混合命名可理解；
- 不增加静默 alias；
- 在 diagnostics 中记录不一致。

更长期的 canonical capability id 和 aliases 属于独立 capability model 任务或 Tool RAG 路线图。

## 实现顺序

1. 后端 context engine 与 grouped prompt。
2. AgentRun checkpoint / projection 基线。
3. AgentRuntime state-machine 边界。
4. 前端 transcript reducer 与渲染重构。
5. Session 切换恢复。
6. Prompt diagnostics UI。
7. 聚焦测试和用户级演示。

实现过程中可以调整顺序，但最终结果必须覆盖所有工作流。只完成 context grouping 或只完成前端渲染，不足以认为本阶段完成。

## 验收标准

### 后端

1. Win + Linux 在线时的 auto routing：
   - `routing_mode == "auto"`;
   - `target_node_id` is null;
   - capability context includes both nodes;
   - prompt groups capabilities by node.

2. Pinned Linux：
   - only Linux executable tools are provider-visible;
   - Win-only tools are absent;
   - prompt says pinned to `linux-node-01`.

3. Pinned Win：
   - only Win executable tools are provider-visible;
   - Linux-only tools are absent;
   - prompt says pinned to `winClient`.

4. AgentRun 恢复：
   - an in-progress run can reconstruct prompt context, assistant text
     segments, tool call state, and terminal/error state if present.

5. 状态机行为：
   - successful ReAct run records ordered steps;
   - wrong tool call fails explicitly;
   - no silent fallback;
   - no hardcoded assistant answer in place of model/tool output.

6. DB 纪律：
   - no DB session is held across LLM calls, job polling waits, or approval
     waits.

### 前端

1. Live SSE 渲染和 persisted 渲染使用同一个 transcript reducer。
2. stream close、refresh、session switch 后，assistant text -> tool call 的顺序保持不变。
3. tool call 前的单行 assistant text 不出现无法解释的空行。
4. 工具卡片渲染 node identity。
5. Prompt diagnostics 展示 raw prompt 和 structured capability context。
6. Agent chat 默认使用 Auto routing。
7. Node pinning 是高级/调试 override，不是主路径。
8. 在运行中的 ReAct turn 期间切换 session，切回后能恢复中间态。
9. 取消是显式动作。

### 用户级演示

宣布本阶段完成前，必须跑以下演示：

1. 启动 Center，并保持 Win Node 与 Linux Node 在线。
2. 打开 Agent chat。
3. 确认未选择 node 时 Auto routing 已启用。
4. 提问：“当前有哪些节点和能力？”
5. 确认回答基于 Center state 描述两个节点。
6. 打开 diagnostics，确认 grouped capability context 可见。
7. 提问：“显示 Linux 系统信息。”
8. 确认工具卡片显示 `linux.system.info @ linux-node-01`。
9. 发送会让 assistant 在 tool call 前输出文本的 prompt，例如：“检查当前项目结构。”
10. 确认 assistant text 气泡位于 tool-call block 前，并且间距干净。
11. refresh 或 reload session。
12. 确认顺序和间距不变。
13. 启动一个长时间运行的 Agent / tool 操作。
14. 在它完成前切换到另一个 session。
15. 再切回来。
16. 确认中间 tool state 和 run status 已恢复。

## 需要新增的测试

### 后端

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

### 前端

- Transcript reducer 保持 `assistant_text -> tool_call -> observation`。
- Transcript reducer 从 live SSE 和 persisted projection/history 生成相同 segments。
- tool call 前的 assistant text 在 refresh 后不会移动。
- 空 streaming assistant placeholder 不被渲染。
- 单行 assistant markdown 紧凑显示。
- 工具卡片显示 `name @ node_id`。
- Prompt diagnostics 渲染 raw prompt 和 structured context。
- Auto mode 不发送 `target_node_id`。
- 显式 pin mode 发送 `target_node_id`。
- Session switching 恢复进行中的 ReAct turn。

## 验收策略

- 迭代期间优先跑聚焦测试。
- 同时修改后端和前端时，使用仓库 fast-check 脚本。
- 全量测试是较大 merge / push 前的 checkpoint，不是每次 inner-loop 都要跑。

## 非目标

- 本阶段不实现完整 Tool RAG。
- 不构建 multi-agent swarm。
- 不用 MCP 替代 YQP。
- 不采用完整外部 Agent framework。
- 不立刻迁移所有 capability name。
- 不增加静默 alias fallback。
- 不隐藏执行错误。

更长期的 capability discovery 和 Tool RAG 工作单独跟踪：

- `docs/todos/2026-06-28-agent-tool-rag-roadmap.md`
