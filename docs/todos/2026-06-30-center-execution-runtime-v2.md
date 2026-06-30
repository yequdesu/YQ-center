# Center Execution Runtime v2 待办

状态：active todo  
日期：2026-06-30  
取代范围：`2026-06-28-center-capability-runtime-v1.md` 的后续主线  
适用阶段：WinNode + LinuxNode 已稳定接入，croc 跨 Node 传输已跑通之后

## 0. 2026-06-30 实施记录

本轮已经完成到阶段 4 的第一版闭环：

- 新增 `ExecutionAdmissionService`，第一版能从结构化函数名判定 `inline`、`sync_wait`、`workflow_operation`，其中 `transfer.create` 固定进入 `workflow_operation`。
- 新增 `Operation` / `OperationEvent` 数据模型、Alembic migration、`OperationService`、`operation.status` / `operation.cancel` Center meta tool 和 `/admin/operations/{operation_id}` API。
- `transfer.create` 现在创建 `TransferSession` 后同步创建 `Operation(kind=transfer)`，返回 `wait_handle`，状态为 `waiting_operation`。
- Agent stream 对 `waiting_operation` 发出 `agent.operation.created`、`agent.operation.waiting`、`agent.run.waiting`、`agent.tool_call.waiting_operation`，并正常关闭本轮流，不再让 LLM 轮询 `transfer.status`。
- Console 新增独立 `OperationCard`，支持状态轮询、取消、终态后 Continue。
- 新增 `/agent/resume-operation/stream`，将 `operation.status` 的事实 observation 注入下一轮 Agent，由 LLM 生成自然语言总结；Center 不生成伪 assistant fallback。
- 已更新 `docs/agent-sse-contract.md` 中 Operation 相关事件合同。

本轮验证：

```text
.\.venv\Scripts\python.exe -m pytest tests/test_execution_admission.py tests/test_agent_runtime_state.py tests/test_transfer_session.py -q
npm run build
.\.venv\Scripts\python.exe -m ruff check <本轮后端修改文件与相关测试>
```

验收结果：20 个后端窄测试通过；前端 typecheck + production build 通过；ruff 通过。

## 1. 结论

项目当前需要把主架构从 **Center Capability Runtime v1** 升级为 **Center Execution Runtime v2**。

原因不是 capability registry 不够，而是项目已经进入以下阶段：

- 多 Node 已接入；
- Artifact 已成为真实数据层；
- croc 跨 Node 文件传输已跑通；
- TransferSession 已出现复合任务编排；
- Agent 不能再通过 ReAct loop 等待长任务；
- 未来还会出现 maintenance resume、SubAgent、Tool RAG、MCP adapter、更多大输出/长任务能力。

因此，Center 的核心不应再被理解为“能力注册与调用”，而应被理解为：

```text
把 Agent / Console / CLI / future MCP 的意图
转成
可审计、可调度、可等待、可取消、可恢复、可观察的执行过程。
```

## 2. 当前实现形态

当前实现已经有比较强的控制面，但执行运行时语义仍分散在多个模块中。

```text
Agent / Console / CLI
  -> API route
  -> application service
  -> 各自进入不同执行路径
```

当前主要路径：

```text
Agent
  -> agent_stream.py / agent_service.py
  -> tool_stream.py
  -> ToolInvocationApplicationService
  -> Capability resolver / Policy
  -> Invocation / Job
  -> YQP Node

Transfer
  -> TransferApplicationService
  -> TransferSession
  -> receiver Job + sender Job
  -> 聚合两端状态

Maintenance
  -> maintenance_executor.py
  -> MaintenanceRun / MaintenanceStep
  -> 自己创建 Invocation / Job
  -> 自己处理 approval / resume / rollback

Approval
  -> approval_service.py
  -> pending / approved / consumed / expired

Artifact
  -> artifact_service.py
  -> upload / list / get / present
```

问题不是没有模块，而是：

```text
每个模块都在自己实现一部分调度语义。
```

典型表现：

- `tool_stream.py` 负责普通 Job polling；
- `transfer.py` 负责 TransferSession 状态聚合和一端失败时取消另一端；
- `maintenance_executor.py` 负责维护计划步骤、等待、审批恢复和回滚；
- `approval_service.py` 负责审批等待；
- Agent 当前缺少统一长任务挂起与恢复语义；
- 前端需要为 tool call、artifact、transfer、approval 分别恢复 UI 状态。

## 3. 目标形态

目标形态：

```text
Agent / Console / CLI / future MCP
  -> API route
  -> application use case
  -> Center Execution Runtime
  -> Execution Admission
  -> Inline Execution 或 Operation Bus
```

执行分流：

```text
短任务：
  -> Invocation
  -> Job
  -> 等待有限时间内终态
  -> Observation
  -> 返回调用方

长任务 / 复合任务：
  -> Operation
  -> OperationEvent
  -> 关联 TransferSession / MaintenanceRun / Job / AgentRun
  -> 返回 wait_handle
  -> 调用方不空转等待
```

Agent 场景：

```text
Agent tool call
  -> Execution Admission
  -> Operation created
  -> AgentRunStep 记录 wait_handle
  -> AgentRun 进入 waiting_operation
  -> stream close
  -> Operation 完成
  -> 用户手动继续或后续自动 resume
  -> Agent 收到结构化 observation
  -> LLM 生成最终回复
```

## 4. 新主构件

### 4.1 ExecutionAdmissionService

`ExecutionAdmissionService` 是本轮设计的关键。它不能被简单的硬编码 `execution_class` 替代。

它负责在每次执行前动态判定本次调用应如何进入运行时。

输入：

```text
actor
tool/function intent
capability metadata
input payload
target node
execution mode
runtime state
policy / approval result
resource lock pressure
historical execution stats
```

输出：

```text
ExecutionPlan
```

示例：

```json
{
  "decision": "workflow_operation",
  "reason": "transfer.create requires concurrent receiver and sender jobs",
  "sync_wait_budget_sec": 5,
  "requires_operation": true,
  "resume_policy": "manual",
  "cancel_supported": true
}
```

可选 decision：

| decision | 含义 |
|---|---|
| `inline` | 立即执行并返回，不创建 Operation。 |
| `sync_wait` | 创建 Job 后在有限预算内等待终态。 |
| `waitable_operation` | 创建 Operation，调用方拿到 wait handle。 |
| `workflow_operation` | 创建 Operation，并由 workflow handler 编排多个 Job 或领域对象。 |
| `detached_operation` | 后台运行，不默认唤醒 Agent。 |
| `approval_required` | 需要审批，返回审批等待信息。 |
| `denied` | 策略拒绝。 |

判定依据必须是运行时事实，而不是 prompt 文本。

### 4.2 Operation

`Operation` 是 Center 管理的运行时过程，不是业务领域模型。

它不替代：

- `Invocation`
- `Job`
- `TransferSession`
- `MaintenanceRun`
- `ApprovalRequest`
- `AgentRun`

它只表达：

```text
一个可等待、可取消、可恢复、可订阅、可审计的 Center 执行过程。
```

建议字段：

```text
operation_id
kind
status
owner_type
owner_id
session_id
ref_type
ref_id
admission_decision
progress_pct
progress_message
resume_policy
cancel_policy
error_code
error_message
started_at
completed_at
metadata_json
```

状态：

```text
created
running
waiting
succeeded
failed
cancelled
timeout
```

终态不可变：

```text
succeeded
failed
cancelled
timeout
```

### 4.3 OperationEvent

`OperationEvent` 是运行时事件流，不是审计日志的替代品。

用途：

- 前端 OperationCard projection；
- AgentRun resume；
- CLI / Admin 查询；
- 未来 MQ/outbox 投递；
- 多 worker 恢复。

字段建议：

```text
event_id
operation_id
seq
event_type
payload
created_at
published_at
```

Timeline 仍负责审计。OperationEvent 负责运行时投影和唤醒。

### 4.4 OperationWaiter

`OperationWaiter` 表达谁在等这个 Operation。

第一版可以先放在 `AgentRunStep.metadata_json` 中，但长期建议独立模型：

```text
waiter_id
operation_id
waiter_type        # agent_run / console / cli / system
waiter_id_ref
resume_policy
status
created_at
resumed_at
```

这样 Operation 不会变成 Agent 私有机制。

### 4.5 Workflow Handler

复合任务由 workflow handler 处理。

第一批 handler：

| Handler | 领域对象 | 职责 |
|---|---|---|
| `TransferWorkflow` | `TransferSession` | 创建 receiver/send Job，聚合两端状态。 |
| `MaintenanceWorkflow` | `MaintenanceRun` | 维护计划步骤、审批、回滚、恢复。 |
| `LongJobWorkflow` | `Job` | 单个长 Job 的后台等待。 |
| `SubAgentWorkflow` | child `AgentRun` | 未来 SubAgent parent/child run。 |

`TransferApplicationService` 在长期形态中应成为 `TransferWorkflow` 的门面或内部实现，而不是独立运行时。

## 5. 与现有模型的关系

| 现有对象 | 保留职责 | v2 中的变化 |
|---|---|---|
| `CapabilityDefinition` / `CapabilitySource` | 能力语义身份与具体来源 | 增加 execution hints，但不硬编码最终执行方式。 |
| `RuntimeInstance` | Node 运行时事实 | 作为 Admission 判定输入。 |
| `Invocation` | 语义调用意图 | 仍是 Job 创建前的调用事实。 |
| `Job` | Node 上实际执行任务 | 不承担 Agent 唤醒语义。 |
| `TransferSession` | 传输领域事实 | 不负责 Agent 等待；由 Operation 管等待和事件。 |
| `MaintenanceRun` | 维护领域事实 | 不再自成一套运行时；接入 Operation。 |
| `ApprovalRequest` | 审批事实 | 可成为 waitable 的 ref。 |
| `AgentRun` / `AgentRunStep` | LLM 运行与 checkpoint | 记录 wait_handle，接收 Operation observation。 |
| `TimelineEvent` | 审计事实 | 不作为运行时事件总线。 |
| `Artifact` | 二进制/媒体资产 | 作为 observation 和 Operation output 的引用。 |

## 6. Agent 解耦要求

Agent 不能拥有调度总线。

正确关系：

```text
Agent 是 Center Runtime 的客户端。
Console 是 Center Runtime 的客户端。
CLI 是 Center Runtime 的客户端。
Future MCP Adapter 也是 Center Runtime 的客户端。
```

Agent 只看到：

```json
{
  "type": "operation_observation",
  "operation_id": "op_xxx",
  "status": "succeeded",
  "summary": {}
}
```

Agent 不知道：

- croc 如何并发；
- Node 如何 poll；
- Job 如何续租；
- TransferSession 如何聚合；
- OperationEvent 如何投递；
- 是否使用外部 MQ。

自然语言回复由 LLM 生成。Center 只提供结构化事实和 INFO/system event，不伪装成 Agent 回复。

## 7. MQ 边界

第一阶段不引入外部 MQ。

当前项目已有 DB-backed Job 队列：

```text
jobs 表 + node poll + job status
```

如果立刻引入 RabbitMQ/NATS/Redis Streams，会形成两套队列事实源，增加一致性成本。

v2 的正确路线：

```text
Phase 1: PostgreSQL-backed Operation Bus
Phase 2: operation_events / outbox
Phase 3: background dispatcher / scanner
Phase 4: 如确实需要，再替换 dispatcher backend 为 NATS / Redis Streams / RabbitMQ
```

外部 MQ 只能作为 `OperationEventDispatcher` 的后端，不改变领域模型。

## 8. 目录目标

建议逐步建立：

```text
src/yequ/runtime/
  admission/
    service.py
    schemas.py
    estimates.py

  execution/
    core.py
    invocation.py
    job.py
    locks.py

  operations/
    service.py
    events.py
    waiters.py
    dispatcher.py
    projections.py

  workflows/
    transfer.py
    maintenance.py
    long_job.py
    subagent.py

  agent_bridge/
    wait.py
    observation.py
    resume.py
```

迁移原则：

- API route 不直接实现运行时逻辑；
- Agent stream 不直接等待长任务；
- Transfer/Maintenance 不各自发明等待/恢复；
- 领域 service 保存领域事实；
- Runtime service 负责调度事实。

## 9. 分阶段执行

### 阶段 0：文档和边界收口

目标：

- 本文成为新的主待办；
- v1 文档降级为历史基线；
- transfer、agent、SSE、Tool RAG 文档全部指向 Execution Runtime v2；
- 明确 UTF-8 文档规则。

验收：

- 文档索引一致；
- 没有一份活跃文档继续把 Capability Runtime v1 描述为未来主线；
- 没有文档要求 Agent 用 LLM 轮询长任务。

### 阶段 1：Admission 初版

新增：

- `ExecutionAdmissionService`
- `ExecutionPlan`
- 针对 Center meta tools 的第一批规则

第一版覆盖：

| 调用 | 计划 |
|---|---|
| `node.list` / `node.status` | `inline` |
| `capability.search` / `capability.describe` | `inline` |
| 普通 `capability.invoke` | 保持当前路径，可用 `sync_wait` |
| `transfer.create` | `workflow_operation` |
| `transfer.status` | `inline` |
| `transfer.cancel` | `sync_wait` 或 `waitable_operation` |

验收：

- Admission 输出可被测试；
- 不由 prompt 决定同步/异步；
- 不引入外部 MQ。

### 阶段 2：Operation Bus 基线

新增：

- `Operation` model；
- `OperationEvent` model；
- `OperationService`；
- `operation.status` / `operation.cancel` meta tool。

先接入：

- `TransferSession`

验收：

- 发起 transfer 后有 `operation_id`；
- Operation 可从 `running` 刷新到终态；
- transfer 失败会同步 Operation failed；
- operation cancel 能取消两端非终态 Job；
- Timeline 仍记录审计事件。

### 阶段 3：Agent waiting_operation

新增：

- `waiting_operation` AgentRun 状态；
- `wait_handle`；
- `agent.operation.created`；
- `agent.operation.waiting`；
- `agent.run.waiting`。

行为：

```text
transfer.create
  -> 返回 wait_handle
  -> AgentRunStep 记录等待点
  -> Agent stream 关闭
  -> 不继续消耗 LLM token 轮询 transfer.status
```

验收：

- 大文件传输时 Agent 不轮询；
- 前端能看到 OperationCard；
- 切换 session 后 OperationCard 可恢复；
- 不生成硬编码 fallback assistant 文本。

### 阶段 4：手动 Resume

新增：

- Agent resume API；
- terminal operation observation；
- AgentRunStep 注入 operation observation。

验收：

- transfer 成功后点击继续，Agent 能基于 size/hash/status 总结；
- transfer 失败后点击继续，Agent 明确报告失败原因；
- Center 只提供事实，LLM 生成自然语言。

### 阶段 5：扩展到 Maintenance / Approval / 长 Job

接入：

- `MaintenanceRun`；
- `ApprovalRequest`；
- 单个长 Job；
- Artifact 生产型任务。

验收：

- 不同领域共享 Operation/Waiter/Event；
- 领域详情仍由各自模型保存；
- 前端有统一等待卡片和领域详情入口。

### 阶段 6：SubAgent 预留与实现

目标：

```text
parent AgentRun
  -> Operation(kind=subagent_run)
  -> child AgentRun
  -> child terminal observation
  -> parent resume
```

验收：

- parent/child run 可追踪；
- 子 Agent 失败显式传播；
- 不通过 prompt 魔法伪装 handoff。

## 10. 非目标

本阶段不做：

- 外部 MQ；
- 企业级多租户 ACL；
- 自动 provider fallback；
- 复杂分布式 DAG 引擎；
- 用 Operation 替代 TransferSession / Job / MaintenanceRun；
- 让 Agent 直接订阅 MQ；
- 让 LLM 决定等待策略；
- 对所有工具强制异步。

## 11. 第一轮验收目标

第一轮应至少完成：

1. 文档完成 v2 路线图；
2. Admission 初版存在；
3. Operation / OperationEvent model 存在；
4. `transfer.create` 返回 wait_handle；
5. Agent 不再用 LLM 轮询 `transfer.status`；
6. 前端能显示 transfer OperationCard；
7. 手动查询 `operation.status` 能看到两端 Job 与 TransferSession 状态；
8. 错误、取消、超时均显式传播。

第一轮不要求：

- Maintenance 全接入；
- SubAgent；
- 外部 MQ；
- 自动 resume；
- 历史耗时统计 admission；
- 复杂 workflow DSL。
