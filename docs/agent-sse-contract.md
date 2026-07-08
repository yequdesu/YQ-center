# Agent SSE 事件合同

状态：当前前后端流式事件合同
更新时间：2026-07-08

Agent 流式事件通过 `POST /agent/invoke/stream` 和
`POST /agent/plan/stream` 返回，响应类型为 `text/event-stream`。

## 1. 事件信封

所有事件使用相同信封：

```json
{
  "event_id": "evt_<16 hex>",
  "event_type": "<见下表>",
  "session_id": "sess_<16 hex>",
  "trace_id": "tr_<16 hex>",
  "timestamp": "ISO-8601 UTC",
  "data": { "type_specific": "payload" }
}
```

## 2. Invoke Stream 事件类型

| 事件类型 | 出现时机 | `data.*` | 说明 |
|---|---|---|---|
| `stream.open` | 永远第一条 | 无 | 打开 SSE 流。 |
| `agent.prompt_context` | `stream.open` 后 | `provider_name`, `system_prompt`, `target_node_id`, `execution_mode`, `available_functions` | 调试元数据。前端应放在诊断面板，不应渲染成 assistant 正文。 |
| `agent.session.resolved` | `stream.open` 后 | `session_status`, `execution_mode` | 确认 session 有效。 |
| `agent.prompt.received` | `session.resolved` 后 | `prompt`（最多 500 字符）, `step` | 服务端确认收到用户 prompt。 |
| `agent.loop.started` | `prompt.received` 后 | `max_steps`, `max_duration_sec` | ReAct loop 开始。 |
| `agent.loop.iteration` | 每轮循环 | `iteration`, `max_steps` | 新一轮迭代开始。 |
| `agent.provider.started` | 每次 provider 调用 | `provider_name` | LLM 调用开始。 |
| `agent.ycr.context` | 每次 provider 调用前后 | `phase`, `step`, `packet_id`, `context_estimate`, `tokens`, `state`, `projections`, `refs` | YCR 对 provider 输入/输出的估算和上下文包信息。`state` 包含 session state counts、capability candidate count、capability context snapshot 和 history compaction 状态；前端用于 token 观测和侧边栏 trace。 |
| `agent.ycr.projection` | provider 调用前，每个 projected tool observation | `call_id`, `name`, `projection_policy`, `raw_estimated_tokens`, `projected_estimated_tokens`, `raw_size_bytes`, `projected_size_bytes`, `refs` | YCR 已把历史 tool shell 转换为 provider-visible observation。 |
| `agent.ycr.error` | YCR 调用或投影失败 | `phase`, `error_code`, `message`, `step` | YCR fail-closed 错误。不能静默降级为 raw tool result。 |
| `agent.output.delta` | 每轮 0 次或多次 | `content` | LLM 文本流片段。同一轮里，如果有工具调用，文本片段应先于工具调用事件出现。 |
| `agent.tool_call.created` | 每个 tool call | `call_id`, `name`, `sanitized_name`, `input` | 工具调用创建。对可并发工具，同组所有 created 事件应先于第一个执行事件出现。 |
| `agent.tool_call.arguments` | `created` 后 | `call_id`, `name`, `input` | 工具入参。 |
| `agent.invocation.created` | 每个 tool call | `call_id`, `name`, `invocation_id`, `target_node_id` | 已为该工具创建 Invocation。 |
| `agent.job.queued` | 每个 tool call | `call_id`, `name`, `invocation_id`, `job_id` | Job 已排队到目标 Node。 |
| `agent.job.running` | 每个 tool call | `call_id`, `name`, `job_id`, `status` | Job 正在执行。 |
| `agent.job.finished` | 每个 tool call | `call_id`, `name`, `job_id`, `status` | Job 进入终态。 |
| `agent.tool_call.completed` | 成功工具 | `call_id`, `name`, `result` | 工具成功并返回结果。 |
| `agent.tool_observation.stored` | 成功工具结果写入 YCR 后 | `call_id`, `name`, `raw_ref`, `shell`, `raw_estimated_tokens`, `raw_size_bytes`, `shell_size_bytes` | raw result 已保存为 YCR ContextRef，Agent history 只保存 shell。 |
| `agent.tool_call.failed` | 失败工具 | `call_id`, `name`, `error_code`, `message` | 工具失败。 |
| `agent.tool_call.waiting_approval` | 需要审批的工具 | `call_id`, `name`, `approval_id`, `status`, `message` | 写操作需要审批。前端必须渲染交互式 ApprovalCard。 |
| `agent.tool_call.waiting_operation` | 工具已创建可等待 Operation | `call_id`, `name`, `operation_id`, `wait_handle`, `result` | 调试轨迹事件。前端可在 tool-call 块内保留，但用户主要视图必须是独立 OperationCard。 |
| `agent.approval.required` | 每个审批 | `call_id`, `name`, `approval_id`, `target_node_id` | 审批请求已创建。 |
| `agent.operation.created` | Center 创建可等待 Operation | `operation_id`, `kind`, `status`, `ref_type`, `ref_id`, `title` | Execution Runtime v2 事件。用于长任务、复合任务和 future workflow 的可恢复投影。 |
| `agent.operation.waiting` | AgentRun 暂停等待 Operation | `operation_id`, `kind`, `status`, `wait_handle`, `resume_policy`, `message` | 前端必须渲染独立 OperationCard，不得放进 tool-call 结果块作为唯一展示。 |
| `agent.operation.completed` | Operation 进入终态 | `operation_id`, `kind`, `status`, `summary`, `error_code`, `message` | 前端更新 OperationCard；是否恢复 Agent 由 resume 策略或用户动作决定。 |
| `agent.run.waiting` | AgentRun 已挂起 | `reason`, `operation_id`, `wait_handle` | 表示本轮 ReAct loop 正常暂停，不是失败。 |
| `agent.observing` | 本轮所有工具结束后 | `tool_count` | 工具完成，Agent 正在观察结果。 |
| `agent.synthesizing` | 最后一轮后 | `source` | Agent 已收到 provider 最终文本。 |
| `agent.completed` | 成功结束 | `status`, `message` | 流正常完成。前端必须渲染为 `system_event`，不能渲染成大气泡。 |
| `agent.failed` | 出错 | `error_code`, `message`, 可选 details | 流失败。协议错误通过该事件显式暴露，不能生成伪造 assistant fallback 文本。 |
| `agent.provider.failed` | provider 出错 | `error_code`, `message` | LLM provider 失败。 |
| `stream.close` | 永远最后一条 | 无 | 关闭 SSE 流。 |

## 3. Invoke 标准事件顺序

```text
stream.open
  agent.prompt_context
  agent.session.resolved
  agent.prompt.received
  agent.loop.started
  ┌─ 第 N 轮 ─┐
  │ agent.loop.iteration
  │ agent.provider.started
  │ agent.ycr.context              # phase=provider_input
  │ agent.ycr.projection           # 可选，每个历史 shell 一条
  │ agent.output.delta              # LLM 文本，可选，可出现多次
  │ ├─ 工具块 ─┤                    # 每个 tool call 一个块
  │ │ agent.tool_call.created       # 并发工具时，同组 created 先全部出现
  │ │ agent.tool_call.arguments
  │ │ agent.invocation.created
  │ │ agent.job.queued
  │ │ agent.job.running
  │ │ agent.job.finished
  │ │ agent.tool_call.completed / .failed / .waiting_approval
  │ │ agent.tool_observation.stored # 成功结果写入 YCR 后出现
  │ └──────────┘
  │ agent.observing                 # 本轮全部工具结束后
  │ agent.ycr.context               # phase=provider_output
  └────────────┘
  agent.synthesizing
  agent.completed
stream.close
```

## 4. Plan Stream 事件类型

当前 `POST /agent/plan/stream` 仍是维护计划路径的流式事件合同，不等同于
`/agent/invoke/stream` 主链路中已经落地的通用 Agent Runtime `AgentPlan` /
`AgentPlanStep`。通用 AgentPlan 通过 `/agent/sessions/{session_id}/plan`
读取，并由右侧 Plan 面板展示；MaintenancePlan 与 AgentPlan 语义不得混用。

| 事件类型 | `data.*` |
|---|---|
| `stream.open` | 无 |
| `agent.prompt_context` | `provider_name`, `system_prompt`, `target_node_id`, `execution_mode`, `available_functions` |
| `agent.session.resolved` | `session_status` |
| `agent.prompt.received` | `prompt` |
| `agent.provider.started` | `provider_name` |
| `agent.planning.summary` | `message` |
| `agent.plan.step.created` | `seq`, `kind`, `function_name`, `requires_approval` |
| `agent.plan.created` | `plan_id`, `goal`, `status`, `step_count` |
| `agent.approval.required` | `plan_id`, `message` |
| `agent.completed` | 无 |
| `agent.failed` | `error_code`, `message` |
| `stream.close` | 无 |

## 5. 错误码

| `error_code` | 含义 | 前端展示建议 |
|---|---|---|
| `max_duration_exceeded` | 总耗时超过限制。 | `Duration exceeded: {elapsed}s` |
| `max_steps_exceeded` | step 数超过限制。 | `Max steps exceeded` |
| `call_depth_exceeded` | 递归调用深度超过限制。 | `Call depth exceeded` |
| `session_not_found` | session id 无效。 | `Session not found` |
| `provider_timeout` | LLM 调用超时。 | `Provider timed out` |
| `agent_protocol_error` | Provider 既没有返回 assistant 文本，也没有返回 tool call；或返回了非法 planning intent。 | 显示明确协议错误，不编造 assistant 文本。 |
| `internal_error` | 非预期服务端错误。 | `Internal error: {message}` |
| `function_not_available` | 没有在线 Node 拥有该 function。 | `No online node has '{name}'` |
| `circular_dependency` | function 已经在 call path 中。 | `Circular: {name}` |
| `policy_denied` | execution mode 不允许该动作。 | 展示策略拒绝原因。 |
| `tool_failed` | Job 以非成功状态结束。 | `Tool {name} ended with {status}` |

## 6. 前端 ChatBlock 映射

| SSE 事件 | ChatBlock 类型 | 前端动作 |
|---|---|---|
| `agent.output.delta` | `assistant_text` | 追加到当前 assistant 文本块，或创建新文本块。 |
| `agent.prompt_context` | diagnostics/debug panel | 存入诊断面板，不渲染成 assistant 正文。 |
| `agent.tool_call.created` | `tool_group` | 追加到当前 tool group，或创建新 tool group。 |
| `agent.tool_call.completed` | 更新 `tool_group` | 将工具状态更新为 succeeded。 |
| `agent.tool_observation.stored` | 更新 `tool_group` + YCR trace | 在对应 ToolCallCard 上显示 raw ref、raw bytes、shell bytes。 |
| `agent.ycr.projection` | 更新 `tool_group` + YCR trace | 在对应 ToolCallCard 上显示 provider projection、投影 token/bytes 和 refs。 |
| `agent.ycr.context` | YCR side panel + bubble token estimate | 更新 upload/download token estimate、provider call count、context estimate、snapshot 状态、candidate count 和 session state counts。 |
| `agent.ycr.error` | YCR side panel + `system_event` | 显示 YCR fail-closed 错误。 |
| `agent.tool_call.completed` 且 `name == "artifact.present"` | `artifact_presentation` + 更新 `tool_group` | 更新工具状态，并把返回的 artifacts 渲染为独立媒体块，不能只放在 tool-call 卡片里。 |
| `agent.tool_call.failed` | 更新 `tool_group` | 将工具状态更新为 failed。 |
| `agent.tool_call.waiting_approval` | 更新 `tool_group` + `system_event` | 将工具状态更新为 waiting_approval，并渲染 ApprovalCard。 |
| `agent.operation.created` | `operation_card` | 创建或更新独立 OperationCard。 |
| `agent.operation.waiting` | `operation_card` + `system_event` | 显示 AgentRun 正在等待 Operation，tool-call 块只保留调试轨迹。 |
| `agent.operation.completed` | 更新 `operation_card` | 更新终态、summary、错误或完成信息。 |
| `agent.run.waiting` | `system_event` | 显示当前 AgentRun 已正常挂起，不应渲染为失败。 |
| `agent.approval.required` | `system_event` | 渲染低强调提示；ApprovalCard 仍以 waiting_approval 事件为准。 |
| `agent.completed` | `system_event` | 渲染低强调 `Completed (status)` 标签，不能渲染成大气泡。 |
| `agent.failed` / `agent.provider.failed` | `system_event` | 渲染错误标签。 |
| 用户 prompt | `user` | 用户消息气泡。 |

## 7. 约束

1. `tool_group` 不能包裹 `assistant_text`；`assistant_text` 也不能包裹 `tool_group`。它们是时间线中的独立块。
2. `agent.completed` 必须渲染成低强调 `system_event`，不能渲染成大内容气泡。
3. 同一 `call_id` 的所有工具执行事件必须出现在对应 `agent.tool_call.created` 之后。
4. 并发工具同组执行时，所有 `agent.tool_call.created` 必须先于任何 `agent.invocation.created` 或 `agent.job.queued`。
5. 工具观察结果写回 LLM history 时，必须保持 provider 原始 tool call 顺序，不受并发执行完成顺序影响。
6. 用户输入“确认”“批准”“可以执行”等文本不能自动审批。用户必须点击 ApprovalCard 按钮。
7. 当 provider 没有给出最终回答时，stream 不能合成 fallback assistant 文本。必须用 `agent.failed` 和 `agent_protocol_error` 显式暴露。
8. 前端可以在 SSE 发出 `agent.prompt.received` 前乐观渲染用户消息，但必须在服务端事件到达后完成 reconcile，不能出现重复用户气泡。
9. `artifact.present` 是展示型元工具。它返回的 artifacts 应渲染成独立聊天媒体/内容块；tool-call 卡片只保留执行和调试记录，不能成为展示媒体的唯一位置。
10. `agent.operation.*` 是 Center Execution Runtime v2 的等待/恢复事件。OperationCard 必须独立于 `tool_group`，因为 Operation 可能由 transfer、maintenance、approval、long job 或 future subagent 触发。
11. Agent 因 Operation 等待而关闭 stream 时，这是正常暂停，不是失败。前端应保留 wait handle，并允许后续查询、取消或恢复。
12. Tool raw result 不允许直接进入 provider history。前端可以展示 tool result，但 provider 可见内容必须来自 YCR `agent.ycr.projection`。
13. YCR 搜索、投影或 ref 展开失败时，前端必须展示明确错误，不得显示“看似成功但无内容”的伪状态。
