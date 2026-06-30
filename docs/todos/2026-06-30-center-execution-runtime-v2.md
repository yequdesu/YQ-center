# Center Execution Runtime v2 待办

状态：阶段 4.6、4.7、5A、5B、5C、5D、5E、5F、5G 已落地；阶段 6 前新增 5H、5I、5J、5K 优化门槛
日期：2026-06-30  
取代范围：`docs/archive/todos/2026-06-28-center-capability-runtime-v1.md` 的后续主线
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

阶段 4.5 体验与事实层修补：

- Console 将 OperationCard 从聊天流中抽出到右侧 Activity 面板，Prompt Context / System Prompt 调试信息也移动到该面板中，避免长对话时运行态信息被聊天记录冲走。
- 输入框上方新增可继续 Operation 提示条；断点恢复走显式 Continue / `resume-operation`，不把用户普通输入的“继续”硬编码解释为断点恢复。
- OperationCard 的状态文案改为按真实状态展示，终态不再继续显示 running 文案。
- `transfer.status` 新增 `summary.source` / `summary.target` / `summary.verification`，把源/目标路径、Job、size、sha256 和比对结果集中给 Agent，减少 resume 后乱猜底层工具。
- croc 常见连接失败归一为稳定错误码，例如 `croc_secure_channel_failed`、`croc_secure_channel_not_ready`、`croc_peer_disconnected`、`croc_relay_unreachable`。
- `/agent/resume-operation/stream` 的 INFO prompt 明确要求从 checkpoint 继续，不重建原 Operation，不重复调用 `transfer.create`，终态时只基于事实总结。

仍需后续单独设计：普通 ReAct Loop 因 provider 错误、网络中断或用户手动取消而中止时，不能靠用户输入“继续”恢复断点。普通输入仍应视为新的用户请求；真正的通用断点恢复需要基于 `AgentRun` / `AgentRunStep` checkpoint 建立 `resume-last-run` 或 `resume-run` 语义，不能用自然语言关键词硬编码。

阶段 4.5 验证：

```text
.\.venv\Scripts\python.exe -m pytest tests\test_transfer_session.py tests\test_execution_admission.py -q
.\.venv\Scripts\python.exe -m ruff check src\yequ\application\transfer.py src\yequ\api\routes\agent.py src\yequ\services\operation_service.py
cd console-frontend && npm run build
```

验收结果：9 个后端窄测试通过；ruff 通过；前端 typecheck + production build 通过。

阶段 4.6 / 4.7 / 5A-5G 推进记录：

- 新增 `CenterExecutionRuntime` 和 `RuntimeCommand`，Agent、Admin、approval
  approve-and-run、transfer 子 Job 创建均已切到 runtime。
- 删除 `src/yequ/application/tool_invocation.py` 和 package export，生产与测试不再引用
  `ToolInvocationApplicationService`。
- `/agent/invoke` 非流式旧入口改为 410；CLI `agent invoke` 改为消费
  `/agent/invoke/stream`。
- `_default_functions()` 从生产 Agent route 删除；静态 `system.*` 测试函数迁移到
  `tests/fakes/agent_functions.py`。
- `Capability Context Builder` 删除 legacy unknown node 和 flat function fallback；
  无能力时输出结构化空事实。
- `fallback_runtime_kind` 删除，改为 `allowed_runtime_kinds`。
- `admin.py` 空 compatibility router 删除，`app.py` 只 include 具体 admin routes。
- `MessageDedup` 内存兼容 helper 删除，保留 DB-backed YQP dedup 和后台 cleanup scanner。
- `OperationService` 改为通过 `OperationHandlerRegistry` 投影 operation；
  已有 `transfer`、`job`、`approval_wait` handler。
- 长 Node Job 会由 runtime 按结构化事实包装为 `Operation(kind=job)`，
  transfer workflow 内部 send/receive 子 Job 使用 `suppress_operation=True` 避免双 operation。
- approval required 结果会创建 `Operation(kind=approval_wait)`，Agent SSE 同步发出
  `agent.operation.created` / `agent.operation.waiting`。
- `maintenance_executor.py` 不再直接 `create_invocation()` / `create_job()`，
  step Job 创建与短等待已改走 `CenterExecutionRuntime`。
- `MaintenanceRun` 已接入 `Operation(kind=maintenance, ref_type=maintenance_run)`；
  `OperationHandlerRegistry` 新增 maintenance handler，状态投影覆盖
  `running`、`waiting_approval`、`rollback_recommended`、`succeeded`、`failed`、
  `cancelled`。
- `/admin/maintenance/plans/{plan_id}/run` 返回 `operation` 和 `wait_handle`；
  `/admin/maintenance/runs/{run_id}`、resume、reject 会同步维护 Operation 投影。
- 新增 runtime 层 `AgentRunService`，流式 Agent 主循环会写入 `AgentRun` / `AgentRunStep`
  checkpoint：provider 输出、tool observation、waiting_operation、waiting_approval、
  final 和 failure 均有结构化记录。
- 新增 `/agent/resume-run/stream` 和 `/agent/resume-last-run/stream`；断点恢复走显式
  AgentRun checkpoint，不把用户普通输入的“继续”硬编码为恢复语义。
- 新增 `AgentRunGraph`，provider 输出、provider failure、tool observation、
  waiting_operation、waiting_approval、missing final 等状态转移统一经 graph facade；
  SSE generator 只保留 provider/tool IO 和事件输出。
- 新增 `OperationConsistencyScanner`，生产启动时周期性同步非终态 Operation 投影，并释放
  owner Job 已终态但仍 held 的 resource lock。
- artifact-producing capability 不新增独立 ArtifactTask 表；其执行事实仍归属于 Job，
  `JobOperationHandler` 会把与 `job_id` 关联的 Center artifacts 投影到
  `operation.status` 结果和 `operation.output_data.artifacts` 中。
- Console `OperationCard` 会展示 `operation.status` 返回的 artifacts；输入区新增显式
  `Resume` 操作，调用 `/agent/resume-last-run/stream`，不把普通用户消息伪装为断点恢复。
- Admin 手动 invocation 与 approval approve-and-run 不再设置
  `allow_unregistered_function=True`，生产手动执行必须经过 capability registry。
- 删除 `agent_service.agent_invoke()` 非流式旧 ReAct loop；`agent_service.py` 只保留
  session、planning、history、timeline helper。生产执行路径只剩
  `/agent/invoke/stream` / AgentRun checkpoint 主线，CLI 非流式展示也消费 stream。
- `AgentRunService` 从 `src/yequ/services` 移入 `src/yequ/runtime`；
  AgentRun 状态投影抽到 `src/yequ/runtime/agent_status.py`，避免 Center service
  反向依赖 `yequ.agent`。
- Capability Context Builder 从 `src/yequ/agent/context_engine.py` 移入
  `src/yequ/runtime/capability_context.py`；runtime 通过结构化 Protocol 接收
  function facts，不反向依赖 Agent provider 类型。

阶段 4.6-5D 已验证：

```text
.\.venv\Scripts\python.exe -m ruff check src\yequ\runtime src\yequ\services\operation_service.py src\yequ\agent\tool_stream.py src\yequ\api\routes\agent.py src\yequ\application\transfer.py src\yequ\application\tool_preflight.py
.\.venv\Scripts\python.exe -m pytest tests\test_transfer_session.py -q
.\.venv\Scripts\python.exe -m pytest tests\application\test_tool_invocation_application.py tests\test_artifact_api.py::test_agent_artifact_meta_tools_list_and_present tests\test_capability_runtime_registry.py::test_center_meta_tool_executes_without_node_job tests\test_capability_runtime_registry.py::test_capability_invoke_by_source_id_creates_real_node_job tests\test_capability_runtime_registry.py::test_capability_invoke_requires_disambiguation_for_multiple_sources -q
```

验收结果：ruff 通过；transfer 6 项通过；runtime/meta/capability/artifact 7 项通过。

阶段 5D-5G 本轮补充验证：

```text
.\.venv\Scripts\python.exe -m ruff check src\yequ\runtime\agent_run_service.py src\yequ\agent\agent_stream.py src\yequ\api\routes\agent.py src\yequ\api\routes\admin_invocations.py src\yequ\api\routes\admin_approvals.py src\yequ\api\routes\maintenance.py src\yequ\runtime\operations src\yequ\services\operation_service.py src\yequ\services\operation_scanner.py src\yequ\services\resource_lock_service.py src\yequ\api\app.py
.\.venv\Scripts\python.exe -m pytest tests\test_agent_run_resume.py tests\test_transfer_session.py tests\test_l2c.py::test_waiting_approval_run_can_resume_after_approval tests\test_l2c.py::test_waiting_approval_run_reject_cancels_run -q
.\.venv\Scripts\python.exe -m pytest tests\test_artifact_api.py::test_job_operation_status_projects_linked_artifacts tests\test_artifact_api.py::test_agent_artifact_meta_tools_list_and_present -q
.\.venv\Scripts\python.exe -m pytest tests\application\test_tool_invocation_application.py tests\test_artifact_api.py::test_agent_artifact_meta_tools_list_and_present tests\test_capability_runtime_registry.py::test_center_meta_tool_executes_without_node_job tests\test_capability_runtime_registry.py::test_capability_invoke_by_source_id_creates_real_node_job tests\test_capability_runtime_registry.py::test_capability_invoke_requires_disambiguation_for_multiple_sources -q
cd console-frontend && npm run build
rg "ToolInvocationApplicationService|from yequ\.application\.tool_invocation|_default_functions\(|fallback_runtime_kind|MessageDedup|get_dedup\(" src tests
rg "allow_unregistered_function=True" src\yequ tests
rg "consume_approval\(" src\yequ\services\maintenance_executor.py src\yequ\services\maintenance_service.py src\yequ\api\routes\maintenance.py
.\.venv\Scripts\python.exe -m pytest tests\test_agent.py tests\test_agent_run_resume.py tests\test_agent_runtime_state.py tests\test_import_boundaries.py -q
```

验收结果：ruff 通过；AgentRun resume / transfer / maintenance approval resume 9 项通过；
artifact-producing Job Operation projection 2 项通过；runtime/meta/capability/artifact 7 项通过；
Console typecheck + production build 通过；
旧执行服务、旧非流式 Agent loop、旧 fallback、生产未注册放行和 maintenance 预消费 approval 均无命中；
Agent/session/runtime 边界测试 33 项通过。

阶段 5F/边界收口补充验证：

```text
.\.venv\Scripts\ruff.exe check src\yequ\runtime src\yequ\agent src\yequ\api\routes\agent.py src\yequ\services\agent_turn_service.py src\yequ\services\operation_service.py src\yequ\services\operation_scanner.py tests\test_agent.py tests\test_agent_run_resume.py tests\test_agent_runtime_state.py tests\test_agent_capability_context.py tests\test_import_boundaries.py tests\test_transfer_session.py tests\test_artifact_api.py
.\.venv\Scripts\python.exe -m pytest tests\test_agent.py tests\test_agent_run_resume.py tests\test_agent_runtime_state.py tests\test_agent_capability_context.py tests\test_import_boundaries.py tests\test_transfer_session.py tests\test_artifact_api.py::test_job_operation_status_projects_linked_artifacts tests\test_artifact_api.py::test_agent_artifact_meta_tools_list_and_present -q
.\.venv\Scripts\python.exe -m pytest tests\test_operation_event_dispatcher.py -q
cd console-frontend && npm run build
```

验收结果：ruff 通过；Agent/session/runtime graph/capability context/import boundary/transfer/artifact
与 OperationEvent dispatcher/scanner 共 46 项通过；Console typecheck + production build 通过。

阶段 6 前后续项：

- 阶段 5E 第一版已完成：artifact-producing capability 不再新增独立
  `ArtifactTask` 实体，统一归入 Job Operation 投影；后续如果出现非 Job 型离线 artifact
  处理，再按同一 Operation handler 规则扩展。
- 阶段 5F 已完成 graph facade 第一版：状态决策已从 SSE generator 收敛到
  `AgentRunGraph`；已成功 tool call 的“不重放”仍依赖 checkpoint facts 和 resume
  prompt 约束，后续如要做自动重放跳过，需要把 provider/tool IO 也进一步拆成可持久化
  graph node。
- 阶段 5G 仍是 PostgreSQL scanner 第一版：已有 terminal lock cleanup 和 active operation
  projection sync、OperationEventDispatcher、cancelling 超时专用策略和 startup 恢复报告；
  外部 MQ backend 仍不是本阶段目标。
- 阶段 5H、5I、5J、5K 已提升为阶段 6 前置门槛，详见
  `docs/todos/2026-06-30-pre-phase6-agent-operation-polish.md`。本轮优化覆盖
  Operation 进度透明、`ExecutionGuard` / `ExecutionGate`、Agent 工具选择治理、transfer preflight、
  capability projection 查询、Node capability 描述合同、prompt diagnostics 和 Linux Node
  能力扩展。它们不再归类为“非阻塞后续项”。
- 部分历史文档仍描述 v1/v1.5 旧路径；不影响当前生产路径，但后续文档整理时应归档
  或改为历史记录。

## 1. 结论

项目当前需要把主架构从 **Center Capability Runtime v1** 升级为 **Center Execution Runtime v2**。

原因不是 capability registry 不够，而是项目已经进入以下阶段：

- 多 Node 已接入；
- Artifact 已成为真实数据层；
- croc 跨 Node 文件传输已跑通；
- TransferSession 已出现复合任务编排；
- Agent 不能再通过 ReAct loop 等待长任务；
- 未来还会出现 maintenance resume、SubAgent、Tool RAG、MCP adapter、更多大输出/长任务能力。

其中，当前阶段的 `capability.search` / `capability.describe` 是确定性的 capability
index / structured discovery，不是完整 Tool RAG。未来 Tool RAG 只能在 Runtime 入口前提供候选
capability、示例和参数模式，不能取代 capability registry、preflight facts、`ExecutionGuard`、
`PolicyEngine`、`ExecutionAdmissionService` 或 Operation handler。

因此，Center 的核心不应再被理解为“能力注册与调用”，而应被理解为：

```text
把 Agent / Console / CLI / future MCP 的意图
转成
可审计、可调度、可等待、可取消、可恢复、可观察的执行过程。
```

## 2. 当前实现形态

当前实现已经从 v1 大入口切到 Center Execution Runtime 主路径。

```text
Agent / Console / CLI
  -> API route
  -> CenterExecutionRuntime
  -> Operation / Job / Approval / Transfer handlers
  -> YQP Node 或 Center 本地 meta tool
```

当前主要路径：

```text
Agent
  -> agent_stream.py
  -> tool_stream.py
  -> CenterExecutionRuntime
  -> admission / policy / capability resolver
  -> inline meta tool 或 Invocation / Job / Operation
  -> YQP Node

Transfer
  -> CenterExecutionRuntime
  -> TransferApplicationService
  -> TransferSession
  -> receiver Job + sender Job via runtime suppress_operation
  -> Operation(kind=transfer)

Maintenance
  -> maintenance_executor.py
  -> MaintenanceRun / MaintenanceStep
  -> step Job via CenterExecutionRuntime
  -> 自己保留 step/rollback/artifact 领域逻辑

Approval
  -> approval_service.py
  -> pending / approved / consumed / expired
  -> Operation(kind=approval_wait)

Artifact
  -> artifact_service.py
  -> upload / list / get / present
```

当前剩余问题不再是 v1 大入口，而是：

```text
Operation 泛化、AgentRun checkpoint、outbox/scanner 还没有完全收束。
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

### 阶段 4.6：Runtime v1 遗留审计与入口倒换

阶段 4.6 是进入阶段 5 前必须完成的框架倒换阶段。阶段 4.6 的产物不是
“分析报告”，而是一组代码重构任务。完成后，阶段 5 的新领域接入只能走
Center Execution Runtime，不再向 v1 入口追加业务分支。

#### 4.6.1 审计结论

当前代码中 v1/v1.5 遗留运行时语义分布如下，处理动作固定，不再二次讨论：

| 构件 | 文件 | 当前职责 | 判定 | 阶段 4.6 处理动作 |
|---|---|---|---|---|
| `ToolInvocationApplicationService` | `src/yequ/application/tool_invocation.py` | 同时处理 meta tool、capability.invoke、policy、approval、Invocation、Job、resource lock、transfer、operation、同步等待 | v1 大总管 | 阶段 4.6 内缩减为迁移 shim。阶段 4.7 删除生产引用和 shim 本体。新增 `CenterExecutionRuntime` 后，业务逻辑只能存在于 runtime handler。 |
| `ExecutionAdmissionService` | `src/yequ/runtime/admission/service.py` | 返回 `execution_plan`，但不真正控制执行路由 | v2 骨架未接管入口 | 升级为 runtime 的第一道硬闸门。所有执行必须先产生 `ExecutionPlan`，再由 handler registry 路由。 |
| `OperationService` | `src/yequ/services/operation_service.py` | 主要处理 transfer 的 create/status/cancel projection | v2 transfer 专用外壳 | 保留表模型与公共序列化，迁移 transfer 逻辑到 `TransferOperationHandler`，本类改为 runtime facade。 |
| `transfer.create` 分支 | `src/yequ/application/tool_invocation.py` | 在 `_execute_center_meta_tool()` 内创建 `TransferSession` 和 `Operation` | v2 功能挂在 v1 meta tool 分支上 | 必须迁出到 `TransferWorkflowHandler`。`_execute_center_meta_tool()` 不再包含 workflow 创建逻辑。 |
| 非流式 Agent loop | `src/yequ/agent/agent_service.py` | 旧 ReAct loop、同步 tool 等待、旧 `waiting_approval` 汇总、plan 逻辑 | legacy 路径 | `/agent/invoke` 标记 legacy；阶段 4.6 不再扩展该路径。流式路径是主路径。后续删除前，测试迁移到 stream/runtime。 |
| 流式 Agent loop | `src/yequ/agent/agent_stream.py` | 当前主 ReAct stream，已支持 `waiting_operation` | v2 主路径但仍直接调 tool stream | 保留。tool 执行入口改为 `CenterExecutionRuntime`，waiting 事件由 runtime result 投影。 |
| tool stream | `src/yequ/agent/tool_stream.py` | 调用 `ToolInvocationApplicationService` 并翻译 SSE | v1 service 依赖点 | 改为调用 `CenterExecutionRuntime`。SSE 翻译保留在 Agent 层，不承载业务调度。 |
| Maintenance runtime | `src/yequ/services/maintenance_executor.py`、`src/yequ/api/routes/maintenance.py` | 自有 step workflow、approval waiting、polling、resume、artifact、rollback | 领域逻辑和运行时语义混合 | 阶段 4.6 只标界：step/rollback/artifact 为领域逻辑；waiting/poll/resume/cancel 迁移目标为阶段 5D。 |
| Approval waiting | `src/yequ/services/approval_service.py`、`src/yequ/api/routes/admin_approvals.py`、`console-frontend/src/pages/AgentChatPage.tsx` | ApprovalRequest、approveAndRun、前端 pending queue、auto continue | 独立等待机制 | 阶段 4.6 只保留现状兼容；阶段 5C 迁移为 `Operation(kind=approval_wait)`。 |
| capability resolver | `src/yequ/services/capability_resolver.py` | Node/runtime 选择 | 当前可保留 | 保留为 `JobExecutionHandler` 依赖。不得放入 Agent 层或 workflow handler 内重复实现。 |
| capability registry | `src/yequ/services/capability_registry.py` | capability definition/source/snapshot、meta 查询 | 当前可保留 | 保留为 registry/read model。不得承担 execution routing。 |
| test-only default functions | `src/yequ/api/routes/agent.py` 的 `_default_functions()` | 为旧测试提供 system.* 工具 | test-only legacy | 保留但改注释为 test-only legacy。生产 `_available_functions()` 不注入该列表。 |
| Console OperationCard | `console-frontend/src/pages/AgentChatPage.tsx` | Activity 面板展示 Operation | v2 主 UI | 保留。阶段 5C 后 approval queue 不再作为主等待 UI。 |

#### 4.6.2 新目录和新模块

阶段 4.6 必须建立以下目录结构：

```text
src/yequ/runtime/
  __init__.py
  execution_runtime.py
  command.py
  result.py
  handlers/
    __init__.py
    base.py
    inline_meta.py
    capability_invoke.py
    job_execution.py
    approval_gate.py
    transfer_workflow.py
    operation_control.py
  operations/
    __init__.py
    registry.py
    base.py
    transfer.py
```

模块职责固定如下：

| 模块 | 职责 | 禁止事项 |
|---|---|---|
| `execution_runtime.py` | 唯一新执行入口。调用 Admission，分派 handler，返回统一 runtime result。 | 禁止直接写具体 transfer/maintenance/approval 业务分支。 |
| `command.py` | 定义 runtime command，承接现有 `ExecuteToolCommand` 字段。 | 禁止绑定 FastAPI 或 Agent SSE。 |
| `result.py` | 定义 runtime result：inline result、job result、approval wait、operation wait、denied、failed。 | 禁止生成自然语言 assistant 文本。 |
| `handlers/base.py` | handler interface。 | 禁止依赖具体 Node 平台。 |
| `handlers/inline_meta.py` | 执行 `node.*`、`capability.search/describe`、`artifact.*`、`operation.status/cancel`、`transfer.status/cancel`。 | 禁止创建新 workflow。 |
| `handlers/capability_invoke.py` | 解析 `capability.invoke` 为 concrete capability command。 | 禁止创建 Job；解析后交回 runtime。 |
| `handlers/job_execution.py` | 创建 Invocation/Job、resource lock、短同步等待。 | 禁止处理 approval 创建；禁止处理 workflow fan-out。 |
| `handlers/approval_gate.py` | policy ask/deny、ApprovalRequest 创建/验证/消费。 | 禁止创建 Node Job。 |
| `handlers/transfer_workflow.py` | 处理 `transfer.create` workflow。 | 禁止混入 artifact 展示和 Agent resume。 |
| `handlers/operation_control.py` | 处理 `operation.status`、`operation.cancel`。 | 禁止直接读取 Agent session。 |
| `operations/registry.py` | operation handler registry。 | 禁止使用 if/elif 扩张 kind。 |
| `operations/transfer.py` | transfer operation projection/cancel/summary。 | 禁止保存 transfer 领域事实到 Operation 之外。 |

#### 4.6.3 执行步骤

阶段 4.6 按以下顺序执行，不跳步。

1. 建立 runtime command/result 类型

   - 新增 `src/yequ/runtime/command.py`；
   - 新增 `src/yequ/runtime/result.py`；
   - `ExecuteToolCommand` 保留在 `src/yequ/application/schemas.py`，但 runtime 内部使用
     `RuntimeCommand`；
   - 添加 `RuntimeResult.status` 枚举值：
     `succeeded`、`failed`、`denied`、`unavailable`、`created`、`running`、
     `waiting_approval`、`waiting_operation`；
   - 提供 `RuntimeResult.to_execute_tool_result()`，保证旧调用方可继续收到
     `ExecuteToolResult`。

2. 建立 `CenterExecutionRuntime`

   - 新增 `src/yequ/runtime/execution_runtime.py`；
   - 实现 `CenterExecutionRuntime.execute(command: RuntimeCommand)`；
   - `execute()` 的固定流程：

     ```text
     normalize command
       -> admission.plan(command, facts)
       -> handler_registry.resolve(plan)
       -> handler.execute(command, plan)
       -> return RuntimeResult
     ```

   - `execute()` 内不得出现 `transfer.create`、`maintenance`、`approval` 等具体业务
     `if/elif` 分支；具体分支只能存在于 handler registry。

3. 建立 handler registry

   - 新增 `src/yequ/runtime/handlers/base.py`；
   - 新增 handler registry；
   - 路由表第一版固定为：

     | Admission decision / function | Handler |
     |---|---|
     | inline meta tool | `InlineMetaToolHandler` |
     | `capability.invoke` | `CapabilityInvokeHandler` |
     | sync node job | `JobExecutionHandler` |
     | waitable node job | `JobExecutionHandler` + operation wrapping，阶段 5B 完成 |
     | `transfer.create` workflow | `TransferWorkflowHandler` |
     | approval ask/deny | `ApprovalGateHandler` |
     | `operation.status/cancel` | `OperationControlHandler` |

4. 迁移 inline meta tools

   - 从 `ToolInvocationApplicationService._execute_center_meta_tool()` 迁出以下工具到
     `InlineMetaToolHandler`：
     - `node.list`
     - `node.status`
     - `capability.search`
     - `capability.describe`
     - `artifact.list`
     - `artifact.get`
     - `artifact.present`
     - `transfer.status`
     - `transfer.cancel`
   - `operation.status`、`operation.cancel` 放入 `OperationControlHandler`；
   - 迁移后 `_execute_center_meta_tool()` 删除；
   - 阶段 4.6 内不得留下 `_execute_center_meta_tool()` 到 runtime 的转发函数。

5. 迁移 `capability.invoke`

   - 从 `ToolInvocationApplicationService._execute_capability_invoke()` 迁出到
     `CapabilityInvokeHandler`；
   - handler 只负责解析 `capability_ref/source_id/node_id/input`；
   - 解析结果必须重新进入 `CenterExecutionRuntime.execute()`；
   - 不允许 handler 直接创建 Invocation/Job。

6. 迁移 Job 执行

   - 从 `ToolInvocationApplicationService.execute()` 迁出以下逻辑到 `JobExecutionHandler`：
     - `resolve_function()`
     - `create_invocation()`
     - `start_invocation()`
     - `create_job()`
     - resource lock conflict 处理
     - 短同步 `wait_for_invocation()`
     - `_collect_terminal_result()`
   - `JobExecutionHandler` 只处理已经通过 approval gate 的命令；
   - `JobExecutionHandler` 不创建 ApprovalRequest。

7. 迁移 approval gate

   - 从 `ToolInvocationApplicationService.execute()` 迁出 policy 与 approval 逻辑到
     `ApprovalGateHandler`；
   - `ApprovalGateHandler` 负责：
     - declared risk/effect policy check；
     - resolved capability policy check；
     - `create_approval()`；
     - `verify_approval()`；
     - `consume_approval()`；
   - `ApprovalGateHandler` 对需要审批的命令返回 `waiting_approval`；
   - 已携带有效 `approval_id` 的命令继续进入 `JobExecutionHandler`。

8. 迁移 transfer workflow

   - 新增 `TransferWorkflowHandler`；
   - 将 `transfer.create` 从 meta tool 分支迁出；
   - handler 调用 `TransferApplicationService.create()` 创建 `TransferSession`；
   - handler 调用 operation runtime 创建 `Operation(kind=transfer, ref_type=transfer_session)`；
   - handler 返回 `RuntimeResult(status=waiting_operation)`；
   - `TransferWorkflowHandler` 是阶段 4.6 唯一允许创建 workflow operation 的 handler。

9. 建立 operation handler registry

   - 新增 `src/yequ/runtime/operations/registry.py`；
   - 新增 `src/yequ/runtime/operations/base.py`；
   - 新增 `src/yequ/runtime/operations/transfer.py`；
   - 将 `OperationService._sync_from_transfer()`、`_operation_status_from_transfer()`、
     `_transfer_title()` 迁入 `TransferOperationHandler`；
   - `OperationService.status()` 改为：

     ```text
     operation = load operation
       -> handler = registry.resolve(operation.kind, operation.ref_type)
       -> projection = handler.project(operation)
       -> persist operation shell changes
       -> return projection
     ```

10. 倒换生产调用入口

    - `ToolInvocationApplicationService.execute()` 改为：

      ```text
      runtime = CenterExecutionRuntime(db)
      result = await runtime.execute(RuntimeCommand.from_execute_tool_command(command))
      return result.to_execute_tool_result()
      ```

    - `agent_stream.py` 和 `tool_stream.py` 继续可通过 `ToolInvocationApplicationService`
      调用，但实际执行已经进入 runtime；
    - 新代码禁止直接调用 `ToolInvocationApplicationService` 添加业务能力。

11. 标记 legacy Agent 非流式路径

    - `/agent/invoke` 非流式 endpoint 保留，但文档和代码注释标记为 legacy；
    - `agent_service.agent_invoke()` 不再新增 v2 功能；
    - 与生产 Console 相关的新能力只走 `/agent/invoke/stream`；
    - 后续删除条件写入文档：所有 `tests/test_agent.py` 非流式覆盖迁移到 stream 或 runtime 后删除。

12. 标定 maintenance 与 approval 迁移边界

    - 在文档中固定边界：
      - `MaintenanceRun` / `MaintenanceStep` / rollback / maintenance artifact 是领域逻辑；
      - waiting、polling、resume、cancel 是 runtime 逻辑，阶段 5D 迁移；
      - `ApprovalRequest` 是审批事实；
      - approval waiting、approve 后继续、deny 后终态是 runtime 逻辑，阶段 5C 迁移；
    - 阶段 4.6 不改 maintenance/approval 行为，只建立迁移入口，避免扩大变更面。

13. 文档状态修正

    - 修正 `docs/archive/todos/2026-06-28-center-capability-runtime-v1.md` 中与顶部状态冲突的文字；
    - `docs/documentation-index.md` 保持 v2 为当前主线；
    - `docs/todos/README.md` 保持 v1 为已验收基线；
    - 新增或更新一节“Runtime v1 遗留处理结果”，列明已迁移和未迁移对象。

#### 4.6.4 删除与迁移规则

阶段 4.6 中每个旧构件按以下规则处理：

| 类型 | 处理 |
|---|---|
| 生产路径仍调用，且行为正确 | 阶段 4.6 内迁移到 runtime；阶段 4.7 删除旧生产引用。 |
| 生产路径仍调用，但职责属于 runtime | 阶段 4.6 迁移到 runtime handler；阶段 4.7 删除原位置转发和旧入口。 |
| 只被测试调用 | 阶段 4.6 标记 test-only；阶段 4.7 迁移测试并删除 test-only 旧入口。 |
| 无引用代码 | 同阶段删除，并补最小回归测试或删除过期测试。 |
| 文档中仍描述为主线的 v1 内容 | 改为历史基线或归档，不保留双主线描述。 |
| 旧命名但仍对应当前领域事实 | 保留命名，不为“看起来 v1”而重命名。 |
| 旧命名且表达错误主线 | 重命名或迁移到 legacy 模块。 |

#### 4.6.5 禁止事项

阶段 4.6 期间禁止以下做法：

- 禁止继续向 `ToolInvocationApplicationService._execute_center_meta_tool()` 添加新业务分支；
- 禁止让 Agent 根据自然语言关键词决定 resume；
- 禁止让 LLM 决定 sync/async/admission；
- 禁止在 `OperationService` 中继续用 `if operation.kind == ...` 扩张新领域；
- 禁止把 `MaintenanceRun`、`ApprovalRequest`、`TransferSession` 的领域字段复制进
  `Operation` 作为第二事实源；
- 禁止为修测试保留生产不可达的 silent fallback；
- 禁止让前端直接根据 tool name 拼业务状态，状态必须来自 runtime/operation facts。

#### 4.6.6 验收

阶段 4.6 只有满足以下条件才算完成：

- `CenterExecutionRuntime` 存在并成为 `ToolInvocationApplicationService.execute()` 的实际执行入口；
- `transfer.create` 已从 meta tool 分支迁移到 `TransferWorkflowHandler`；
- `OperationService` 使用 operation handler registry，transfer projection 位于
  `TransferOperationHandler`；
- `ToolInvocationApplicationService` 文件行数和职责明显下降，只保留阶段 4.7 要删除的迁移 shim；
- `agent_stream.py` / `tool_stream.py` 的工具执行最终进入 runtime；
- 非流式 `/agent/invoke` 被明确标记 legacy，且没有新增 v2 功能依赖它；
- maintenance/approval 的迁移边界已写入本文档，阶段 5C/5D 不再重新定义边界；
- v1 文档状态冲突已修正；
- 窄测试覆盖：
  - execution admission；
  - transfer.create waiting_operation；
  - operation.status/cancel；
  - approval waiting；
  - one normal short Job；
  - Agent stream waiting_operation；
- `ruff check` 覆盖新增 runtime 模块和被迁移模块；
- 前端不需要修改即可继续通过现有 OperationCard 验收。

### 阶段 4.7：删除迁移 shim 与统一入口

阶段 4.7 是阶段 5 开始前的强制关口。阶段 4.6 允许短期迁移 shim 是为了控制
重构半径；阶段 4.7 负责删除这些 shim，消除双入口、双实现、双等待机制和
test-only 旧能力对架构的污染。阶段 5 不允许在迁移 shim 存在的状态下开始。

#### 4.7.1 逻辑链路审计结论

阶段 4.7 的审计对象不是关键词，而是生产请求从入口到终态的逻辑链。只删除文件名或
类名不能证明架构收敛；必须证明同一类业务事实不再由两套入口、两套等待机制、两套状态
投影共同维护。

当前已确认的生产链路如下，处理动作固定：

| 链路 | 当前实际路径 | 架构问题 | 阶段 4.7 固定处理 |
|---|---|---|---|
| Agent 工具执行链 | `agent_stream` / `tool_stream` -> `ToolInvocationApplicationService.execute()` -> meta tool / node job / transfer / operation 分支 | Agent 主路径仍穿过 v1 大总管。新增 runtime 后如果只在 service 内转发，就会形成“v2 包 v1”的结构。 | Agent 工具执行必须直接进入 `CenterExecutionRuntime`。SSE 层只翻译事件，不持有调度、审批、Job 创建和等待逻辑。 |
| Admin 手动执行链 | `/admin/invocations` -> `ToolInvocationApplicationService.execute(allow_unregistered_function=True)` | Admin 路径可绕过 capability registry，和 Agent 路径使用不同约束。 | Admin 手动执行进入 `CenterExecutionRuntime`，未注册能力不得在生产路径执行。测试专用能力必须放在 test helper。 |
| Approval approve-and-run 链 | `/admin/approvals/{id}/approve-and-run` -> approval 更新 -> `ToolInvocationApplicationService.execute()` | 审批后的继续执行独立于 Operation wait/resume，形成第二套恢复机制。 | approve 只改变 approval fact；后续恢复由 runtime / operation wakeup 接管。旧 approve-and-run 入口删除或改为 Operation resume 的领域动作。 |
| Transfer 编排链 | `transfer.create` -> `TransferApplicationService` -> `_invoke_capability()` -> `ToolInvocationApplicationService.execute()` -> sender/receiver jobs | Transfer 作为“领域服务”反向调用旧工具执行入口创建 Job，导致 transfer、tool invocation、operation 三层互相调用。 | `TransferWorkflowHandler` 直接通过 runtime 的 Job handler 创建 send/receive Job；`TransferSession` 只保存传输领域事实。 |
| Operation 投影链 | `OperationService.status()` -> `TransferApplicationService.status()` -> 根据 sender/receiver Job 刷新 transfer -> 回写 Operation | Operation 状态依赖 transfer 查询时临时同步，状态投影不是统一事件源。 | Operation 状态由 Operation handler / scanner / event 投影维护；status API 只读 Operation projection，不触发领域补偿式同步。 |
| 普通 Job 创建链 | `ToolInvocationApplicationService`、`maintenance_executor`、transfer 间接路径分别创建 Invocation/Job | 同一种 Node Job 有多套 fan-out 入口，resource lock、approval、timeline 和等待语义容易分叉。 | 普通 Node Job 创建收敛到 `JobExecutionHandler`；workflow/maintenance/transfer 只能调用该 handler，不直接调用 `create_job()`。 |
| Approval gate 链 | `ToolInvocationApplicationService` 内部校验、`maintenance_executor` 内部审批、前端 approval queue 自动继续 | approval 是横切 concern，但现在分散在多个业务执行器里。 | `ApprovalGateHandler` 成为唯一 approval gate；领域服务只声明 risk/effect/resource，不能自行完成审批后执行。 |
| 等待与轮询链 | `wait_for_invocation()`、`_wait_invocation_terminal_for_plan()`、`TransferApplicationService.status()`、前端 job polling | 短等待、长等待、审批等待、传输等待各自实现，Agent 中断后不能稳定从等待点恢复。 | 等待统一为 Operation / wait handle / OperationEvent。同步短等待只是 runtime policy 的一种结果，不是独立轮询器。 |
| Agent 非流式链 | `/agent/invoke` -> `agent_service.agent_invoke()` -> 旧 ReAct loop -> `ToolInvocationApplicationService` | 与 stream/checkpoint 主线并存，继续保留会让“继续”语义和中断恢复语义分叉。 | 生产只保留 stream/checkpoint 主线；非流式 CLI 如需存在，只消费 stream 结果并汇聚展示。 |
| Planning 链 | `/agent/plan` -> `agent_plan()` 中旧 planning/invoke 绑定逻辑 | planning 是意图建模，不应复用旧 invoke loop 的执行副作用。 | Planning 逻辑迁到独立 planning service；执行仍进入 runtime。 |
| Capability context 链 | `_available_functions()` -> `_default_functions()` + center meta tools + capability context；context 为空时 legacy flat tools fallback | 多 Node 后仍可能把 test-only `system.*` 和 flat tools 当成真实能力，导致路由错觉。 | 生产上下文只来自 Center meta tools 和 capability registry；无能力时输出结构化空事实，不渲染 legacy flat tools。 |
| API router 链 | `app.py` include `admin.py` compatibility router，同时 include 具体 admin routes | 旧聚合 router 仍在生产 app 中，不能只按文件名判断死代码。 | `app.py` 只 include 具体 router；compatibility router 删除。 |

逻辑链路审计后的判定标准：

- “旧构件无关键词”只是最低条件，不是完成条件；
- 完成条件是：任意一次工具执行、审批继续、传输、maintenance step、operation status
  查询，都能画出唯一的 runtime 主路径；
- 领域模型可以保留多个，例如 `Job`、`TransferSession`、`MaintenanceRun`、`Operation`，
  但每个模型只拥有自己的事实，不拥有别人的调度职责；
- 业务入口可以保留多个，例如 Agent、Admin、CLI、未来移动端，但入口后的 admission、
  approval、job dispatch、wait、cancel、resume 必须汇入同一个 runtime；
- 阶段 4.7 执行后，如果一个 bug 需要同时修改 Agent 执行、Transfer 执行和
  Maintenance 执行三处等待逻辑，说明本阶段验收失败。

#### 4.7.2 构件级自检结论

当前已识别的适配层、兼容层、旧入口、fallback 式结构如下，处理动作固定：

| 对象 | 文件 | 当前问题 | 阶段 4.7 处理动作 |
|---|---|---|---|
| `ToolInvocationApplicationService` | `src/yequ/application/tool_invocation.py` | 旧统一执行入口。阶段 4.6 后会成为迁移 shim。 | 删除生产引用；删除类和文件；测试改测 `CenterExecutionRuntime`。 |
| `ToolInvocationApplicationService` package export | `src/yequ/application/__init__.py` | 旧入口通过 package export 继续扩散。 | 删除 export；所有 import 改为 runtime 模块。 |
| `agent_service.agent_invoke()` | `src/yequ/agent/agent_service.py` | 非流式旧 ReAct loop，与 stream 主路径并存。 | `/agent/invoke` 生产 endpoint 删除或改为 410；CLI 若需要非流式，改为调用 stream 汇聚器，不调用旧 loop。 |
| `agent_service.agent_plan()` 中旧 plan 执行路径 | `src/yequ/agent/agent_service.py` | 与 runtime/checkpoint 主线分离。 | 保留 planning 领域逻辑时迁入独立 planning service；删除与旧 invoke 绑定的执行逻辑。 |
| `_default_functions()` | `src/yequ/api/routes/agent.py` | test-only system.* raw tools 容易污染多 Node/Tool RAG 主线。 | 从 agent route 删除；测试需要的 fake tools 移到 `tests/fakes/agent_functions.py`。 |
| `allow_unregistered_function` | `src/yequ/application/schemas.py`、admin routes | 为 admin 旧手动调用放宽 capability registry。 | 删除生产执行路径中的放宽开关；需要手动调用时必须先进入 registry 或显式 test helper。 |
| `MessageDedup` 内存兼容 helper | `src/yequ/services/message_dedup.py` | 文档声明为旧 import / 轻量单测兼容 helper。 | 无生产引用时删除；测试改用 DB-backed dedup 或专用 fake。 |
| `admin.py` compatibility router | `src/yequ/api/routes/admin.py` | compatibility import 层。 | app 不引用后删除文件；app 若引用则改为直接 include 具体 admin routes。 |
| Capability Context legacy/fallback context | `src/yequ/runtime/capability_context.py` | `_legacy_function_context_node()` 与 `_render_flat_function_fallback()` 保留旧 flat tools 上下文。 | 删除生产 fallback；无 capability context 时返回显式空上下文事实，不渲染旧 flat 工具上下文。 |
| `agent.fallback_synthesis` SSE 类型 | `console-frontend/src/api/types.ts`、测试 | 旧 fallback synthesis 事件已不应出现。 | 删除前端类型和测试引用；协议只保留 `agent.failed` / `agent_protocol_error`。 |
| Approval auto continue 主机制 | `console-frontend/src/pages/AgentChatPage.tsx`、`admin_approvals.py` | 独立等待机制与 Operation waiting 并存。 | 阶段 4.7 标记为阶段 5C 必删旧主机制；阶段 5C 完成后删除 auto continue 主路径。 |
| Maintenance `/runs/{run_id}/resume` | `src/yequ/api/routes/maintenance.py` | 独立 resume 入口与 Operation resume 并存。 | 阶段 4.7 标记为阶段 5D 必删旧主机制；阶段 5D 完成后删除该入口或改为 Operation resume 的领域动作。 |
| `fallback_runtime_kind` | `capability_resolver.py`、`node_service.py`、`capability_registry.py` | 名称表达 fallback，容易被误解为静默降级。当前用于 hybrid runtime requirements。 | 重命名为 `allowed_runtime_kinds`；运行时选择必须在 diagnostics 中显示命中的 runtime kind。 |
| Provider-specific compatible provider adapter | `deepseek_provider.py`、Provider 计划文档 | OpenAI-compatible 是协议适配，不是旧版本兼容。 | 保留为 provider adapter；不得作为 runtime 兼容层，不参与 4.7 删除。 |
| SPA fallback | `api/app.py` | 前端路由 fallback。 | 保留。它是 Web 路由机制，不是业务兼容层。 |

#### 4.7.3 执行步骤

阶段 4.7 按以下顺序执行。

1. 删除 `ToolInvocationApplicationService` 生产引用

   - 修改 `src/yequ/agent/tool_stream.py`，直接调用 `CenterExecutionRuntime`；
   - 修改 `src/yequ/api/routes/admin_approvals.py`，`approveAndRun` 直接调用 runtime；
   - 修改 `src/yequ/api/routes/admin_invocations.py`，手动执行直接调用 runtime；
   - 修改 `src/yequ/application/transfer.py`，删除 `_invoke_capability()` 反向调用旧
     service 的链路，receiver/send job 创建改由 `TransferWorkflowHandler` 调用
     runtime Job handler；
   - 修改所有生产 import，禁止从 `yequ.application` 导入 `ToolInvocationApplicationService`。

2. 删除 `ToolInvocationApplicationService`

   - 删除 `src/yequ/application/tool_invocation.py`；
   - 保留 `ExecuteToolCommand` / `ExecuteToolResult` schema，直到调用方完全迁移到
     `RuntimeCommand` / `RuntimeResult`；
   - 删除 `src/yequ/application/__init__.py` 中的 service export；
   - 运行 `rg "ToolInvocationApplicationService" src`，结果必须为空。

3. 迁移测试

   - `tests/application/test_tool_invocation_application.py` 改名为
     `tests/runtime/test_center_execution_runtime.py`；
   - `tests/test_transfer_session.py` 改为直接调用 runtime；
   - `tests/test_capability_runtime_registry.py` 中执行类断言改为 runtime；
   - `tests/test_artifact_api.py` 中 artifact meta tool 执行改为 runtime；
   - 运行 `rg "ToolInvocationApplicationService" tests`，结果必须为空。

4. 删除非流式旧 Agent 主路径

   - `/agent/invoke` endpoint 删除或返回 410；
   - CLI 的非流式 agent invoke 改为消费 `/agent/invoke/stream` 并汇聚最终事件；
   - `agent_service.agent_invoke()` 删除；
   - `tests/test_agent.py` 中依赖旧 `agent_invoke()` 的测试迁移到 stream 或 runtime；
   - 运行 `rg "agent_invoke\\(" src tests`，只允许 CLI wrapper 或无结果。

5. 移出 test-only raw tools

   - `_default_functions()` 从 `src/yequ/api/routes/agent.py` 删除；
   - 测试需要的 system.* fake tools 移到 `tests/fakes/agent_functions.py`；
   - 生产 `_available_functions()` 只由 Center meta tools 和 capability context 构成；
   - 运行 `rg "_default_functions" src`，结果必须为空。

6. 删除 context legacy fallback

   - 删除 `_legacy_function_context_node()`；
   - 删除 `_render_flat_function_fallback()`；
   - 无 node/capability context 时返回结构化空状态：

     ```json
     {
       "routing_mode": "auto",
       "nodes": [],
       "tool_count_by_node": {},
       "capability_sources": []
     }
     ```

   - provider prompt 不再渲染 flat legacy tool list；
   - 测试断言改为结构化空上下文或 meta-tool discovery。

7. 删除 fallback synthesis 残留

   - 删除前端 `SseEventType` 中 `agent.fallback_synthesis`；
   - 删除测试中对 fallback synthesis 的旧空断言；
   - 文档中只保留“不得生成 fallback synthesis”的规则，不保留事件类型。

8. 清理 compatibility router/helper

   - 删除 `api/routes/admin.py` compatibility router；
   - 删除 `MessageDedup` 内存兼容 helper，保留 DB-backed dedup；
   - 运行 `rg "compatibility|legacy|fallback|shim|kept for" src`，剩余结果必须逐项列入保留表。

9. 重命名 runtime requirement fallback 字段

   - 将 `fallback_runtime_kind` 迁移为显式 `allowed_runtime_kinds`；
   - capability registry 生成 execution requirements 时写入 `allowed_runtime_kinds`；
   - resolver 按 `allowed_runtime_kinds` 匹配；
   - diagnostics 返回实际命中的 `runtime_id` 与 `runtime_kind`；
   - 文档同步 Linux Node 合同和 capability runtime 文档。

10. 文档收口

    - 本文阶段 4.7 标记完成项；
    - `docs/documentation-index.md` 增加“唯一执行入口：CenterExecutionRuntime”；
    - `docs/archive/todos/2026-06-28-center-capability-runtime-v1.md` 只保留历史基线；
    - 删除或归档仍要求旧 service 的测试文档。

#### 4.7.4 验收

阶段 4.7 必须满足以下硬条件：

- `rg "ToolInvocationApplicationService" src tests` 无结果；
- `rg "agent_invoke\\(" src tests` 不再指向旧 ReAct loop；
- `rg "_default_functions" src` 无结果；
- `rg "agent.fallback_synthesis" src console-frontend/src tests docs/agent-sse-contract.md`
  无结果；
- `rg "fallback_runtime_kind" src docs tests` 无结果；
- `src/yequ/application/tool_invocation.py` 删除；
- `src/yequ/api/routes/admin.py` compatibility router 删除；
- `src/yequ/services/message_dedup.py` 中旧内存兼容 helper 删除；
- 所有生产工具执行入口直接依赖 `CenterExecutionRuntime`；
- `rg "create_job\\(" src/yequ` 只允许出现在 `src/yequ/services/job_service.py`
  的定义和 runtime Job handler；不得出现在 transfer 或 maintenance 业务执行器中；
- `rg "wait_for_invocation|_wait_invocation_terminal_for_plan" src/yequ` 无生产结果；
- `OperationService.status()` 不调用 `TransferApplicationService.status()` 做隐式同步；
- `/admin/approvals` 不再存在 approve 后直接创建 Job 的旧继续链；
- 所有 runtime handler 测试直接测试 runtime 或具体 handler；
- 前端 Activity 面板、OperationCard、approval 现有行为通过窄测试或手动验收；
- 阶段 5A 开始前，代码中不存在以“迁移兼容”为理由保留的业务执行层。

### 阶段 5：Operation Runtime 泛化

阶段 5 不再是一个单块任务，而是分为 5A-5K。目标是让 Operation Runtime
成为 Center 长任务、等待、取消、恢复和前端投影的统一层，而不是 transfer 的补丁。

#### 阶段 5A：OperationService 泛化

目标：

- `OperationService` 不再只认识 `transfer_session`；
- 建立 operation kind/ref resolver/projection 机制；
- 领域事实仍由领域模型保存，Operation 只保存运行时外壳。

目标结构：

```text
OperationRuntime
  -> OperationHandlerRegistry
  -> OperationHandler(kind/ref_type)
      - project_status()
      - project_summary()
      - cancel()
      - terminal_observation()
```

第一批 handler：

| kind | ref_type | 领域模型 |
|---|---|---|
| `transfer` | `transfer_session` | `TransferSession` |
| `job` | `job` | `Job` / `Invocation` |
| `approval_wait` | `approval_request` | `ApprovalRequest` |
| `maintenance` | `maintenance_run` | `MaintenanceRun` |
| `artifact_task` | `job` 或后续 `artifact_task` | `Job` + `Artifact` |

验收：

- `operation.status` 对不同 `kind/ref_type` 走 handler registry；
- Operation status、summary、error、cancel 支持统一投影；
- 未支持的 kind/ref_type 明确报错，不静默 fallback；
- `transfer` handler 从旧 `OperationService` 内联逻辑迁出。

#### 阶段 5B：长 Job 接入 Operation

目标：

- 解决单个 Node capability 执行时间较长时 Agent/SSE 不应持续等待或消耗 token 的问题；
- 不把所有 tool call 强制异步，而是由 Admission 根据结构化事实决定。

Admission 输入事实：

- capability `timeout_sec`；
- capability `effect` / `risk`；
- `conflict_policy` / `resource_keys`；
- capability 是否声明 progress/cancel/resume；
- 是否可能产出大 artifact；
- 用户 execution mode；
- 调用方是否要求 `wait_for_result`。

目标路径：

```text
capability.invoke / concrete capability
  -> ExecutionAdmissionService
  -> sync_wait 或 waitable_operation(kind=job)
  -> Invocation + Job
  -> Operation(ref_type=job)
  -> OperationCard / wait_handle
```

验收：

- 可配置阈值让长 Job 返回 `waiting_operation`；
- 短 Job 仍可走 `sync_wait`；
- Job 成功、失败、timeout、cancelled 会同步 Operation；
- Agent 对长 Job 不再用 LLM 轮询；
- Console 可从 OperationCard 进入 Job 详情。

#### 阶段 5C：Approval 接入 Operation

目标：

- 把 approval waiting 从 Console 特有逻辑收敛到 Operation Runtime；
- ApprovalRequest 仍保存审批事实，Operation 负责等待、投影、resume/cancel 入口。

目标路径：

```text
tool call requires approval
  -> ApprovalRequest
  -> Operation(kind=approval_wait, ref_type=approval_request)
  -> OperationCard
  -> approve/deny
  -> terminal observation / resume
```

需要处理：

- 兼容现有 `agent.tool_call.waiting_approval` 事件；
- 定义 `agent.operation.waiting` 与 approval UI 的关系；
- Console 中 approval queue 与 OperationCard 不能长期并存两套主等待入口；
- `approveAndRunApproval` 后的 Job 也应能继续接入 Operation。

验收：

- 写操作需要审批时，用户能在统一 Activity 面板看到 approval Operation；
- approve/deny 后 Operation 显式终态；
- approve 后如产生长 Job，等待可转入 Job Operation 或同一 Operation 的后续阶段；
- 不再需要前端独立拼接一套 approval auto continue 作为主机制。

#### 阶段 5D：MaintenanceRun 接入 Operation

目标：

- MaintenanceRun 继续保存维护领域事实；
- check / repair / verify / rollback_hint 等步骤不丢失；
- 等待、取消、resume、前端投影交给 Operation Runtime。

目标路径：

```text
maintenance plan run
  -> Operation(kind=maintenance, ref_type=maintenance_run)
  -> MaintenanceRun / MaintenanceStep
  -> OperationEvent
  -> OperationCard
```

需要迁移：

- `maintenance_executor.py` 中属于 runtime/wait/poll/resume 的部分；
- `maintenance.py` 中 `/runs/{run_id}/resume` 的语义；
- maintenance waiting approval 与阶段 5C 的 approval wait 关系；
- maintenance artifact 与阶段 5E 的 artifact task 关系。

验收：

- MaintenanceRun 可从 OperationCard 查看状态；
- waiting_approval、running、rollback_recommended、succeeded、failed 能投影为 Operation 状态/summary；
- cancel/retry/resume 入口明确；
- Maintenance 领域详情不被 Operation 吞并。

当前落地：

- `src/yequ/runtime/operations/maintenance.py` 新增 maintenance handler；
- `OperationService.create_for_maintenance()` 创建 `kind=maintenance` 的 Operation；
- maintenance run API 返回 `operation` / `wait_handle`，run 查询、resume、reject 会同步
  operation projection；
- cancel 通过 Operation handler 改写 `MaintenanceRun` / `MaintenancePlan` / running step，
  并写入 `maintenance.run.cancelled` timeline。

剩余缺口：

- `/admin/maintenance/runs/{run_id}/resume` 仍是领域 API；阶段 6 前可保留为领域动作，
  但前端主展示和 Agent 等待必须走 Operation；
- maintenance retry 尚未设计，不能伪装为已支持；
- 维护执行器内部仍是顺序执行器，不是 graph executor。

#### 阶段 5E：Artifact 生产型任务接入 Operation

目标：

- 截图、摄像头、日志包、目录打包、大文件读取、多模态输出等 artifact-producing
  capability 进入统一等待和展示路径；
- Artifact 层只负责资产、元数据、存储和引用，不负责长任务等待。

目标路径：

```text
artifact-producing capability
  -> Job
  -> Operation(kind=job)
  -> Artifact(s)
  -> Operation terminal summary
  -> artifact.present / Console preview
```

设计修正：

- 不新增 `ArtifactTask` 表。当前项目里的 artifact-producing capability 都是 Node Job 的
  结果或副产物；Job 已经是执行事实，Artifact 是资产事实，Operation 是等待/展示事实。
  再增加 ArtifactTask 会形成 Job/ArtifactTask 双执行事实。
- `JobOperationHandler` 是 artifact-producing task 的投影点：它按 `job_id` 查询 Center
  artifacts，并把 artifact refs 放入 `operation.output_data.artifacts` 和
  `operation.status` 返回值。
- 未来如果出现“纯 Center 后台 artifact 处理”，例如离线转码、OCR、缩略图生成，可以新增
  `Operation(kind=artifact_processing)` handler；不能把 Node Job 型 artifact 能力迁到第二套
  执行模型。

验收：

- 任务完成后 Operation summary 返回 artifact refs；
- Console Activity 面板能看到任务状态，聊天流能展示 artifact；
- Agent resume 后基于 artifact refs 总结，而不是读取二进制；
- 不把图片/文件展示硬塞进 tool-call 结果块作为唯一展示。

当前落地：

- `JobOperationHandler.project()` 查询并返回与 `job_id` 关联的 artifacts；
- `Operation.output_data` 包含 `{"job": ..., "artifacts": [...]}`；
- 有 artifact 时 `operation.progress_message` 显示 artifact 可用数量；
- 前端 Activity 面板已经展示 Job Operation 中的 artifacts；
- `tests/test_artifact_api.py::test_job_operation_status_projects_linked_artifacts`
  覆盖该投影。

剩余缺口：

- Agent resume 已能拿到 artifact refs，但完全避免重读二进制仍需依赖 graph executor 的
  tool step 去重。

#### 阶段 5F：通用 AgentRun Checkpoint / Resume

目标：

- 当前只支持 Operation 终态后的手动 Continue；
- 普通 ReAct Loop 因 provider 错误、网络中断、SSE 断开、用户取消而中止时，不能靠用户输入“继续”恢复；
- 必须基于 `AgentRun` / `AgentRunStep` 建立结构化 resume。

新增语义：

```text
resume-run(run_id)
resume-last-run(session_id)
```

需要记录：

- provider request/response 边界；
- assistant delta 是否已经部分输出；
- tool call created/arguments/result；
- waiting approval / waiting operation wait_handle；
- provider error 是否发生在输出前还是输出后；
- resume observation。

验收：

- 用户手动断开 SSE 后，前端能显示可恢复 run；
- provider 失败后，Center 不静默重试已部分输出的 stream；
- resume 不重新执行已经成功的 tool call；
- 普通用户输入仍被视为新请求，不用自然语言关键词硬判断点恢复。

当前落地：

- `src/yequ/runtime/agent_run_service.py` 负责创建 run、追加 step、更新 checkpoint、
  查询指定 run 和查询 session 下最近可恢复 run；
- `src/yequ/runtime/agent_status.py` 负责 AgentRun 状态投影，Center service 不再反向
  导入 `yequ.agent.runtime_state`；
- `agent_invoke_stream()` 会发出 `agent.run.created`，并记录 provider/tool/final/failure
  checkpoint；
- `/agent/resume-run/stream` 按 `run_id` 恢复；
- `/agent/resume-last-run/stream` 按 `session_id` 找最近
  `waiting_operation` / `waiting_approval` / `failed` run 恢复；
- 如果 checkpoint 中存在 `operation_id`，resume prompt 会携带最新 `operation.status`
  observation。

剩余缺口：

- SSE generator 仍承担 provider/tool IO 和事件输出；状态决策已经进入
  `AgentRunGraph`，但 provider/tool IO 还没有进一步拆成可持久化 graph node。
- 已成功 tool call 不重放目前由 checkpoint facts 和 resume prompt 约束；只有当后续需要
  自动跨进程恢复到中间 step 时，才继续把 IO node 持久化。
- 前端已提供显式 `Resume` 操作进入 `resume-last-run/stream`；普通用户消息仍按新请求处理。

#### 阶段 5G：OperationEvent / Outbox / Scanner 硬化

目标：

- 在不引入外部 MQ 的前提下，先把 PostgreSQL-backed Operation Bus 做可靠；
- 为未来 NATS / Redis Streams / RabbitMQ 预留 dispatcher backend，但不让外部 MQ 成为第二事实源。

需要完成：

- OperationEvent 写入规范；
- stuck operation scanner；
- cancelling timeout；
- terminal sync scanner；
- resource lock 自动释放；
- Operation 与 Job / TransferSession / ApprovalRequest / MaintenanceRun 终态一致性检查；
- OperationEventDispatcher 接口；
- Console/Agent 可依赖 Operation 状态，而不是各自轮询多个领域 API。

验收：

- Center 重启后，running/cancelling/stuck Operation 能被恢复或显式终结；
- cancelled/failed/timeout 后 resource lock 不悬挂；
- OperationEvent 可作为 UI projection 和 future MQ outbox；
- 所有 scanner 使用短 session，不重新制造 idle-in-transaction 问题。

当前落地：

- `src/yequ/services/operation_scanner.py` 新增 `OperationConsistencyScanner`；
- `app.py` 在非 test mode 启动/停止 operation consistency scanner；
- scanner 使用短 session 分两步执行：释放 terminal owner job 的 held lock，同步非终态
  Operation projection；
- `resource_lock_service.release_locks_for_terminal_jobs()` 负责统一释放 owner Job 已终态的
  held lock；
- `OperationService.status()` 继续通过 handler registry 追加 operation terminal event。
- `OperationEvent` 增加 `dispatch_status`、`dispatch_attempts`、`dispatched_at`、
  `last_dispatch_error`，成为 PostgreSQL-backed outbox。
- `OperationEventDispatcher` 负责推进 pending/retry event；当前 backend 为
  PostgreSQL-local，不引入外部 MQ。
- `OperationConsistencyScanner.startup_recovery_report()` 在启动时统计非终态 Operation
  和 pending OperationEvent。
- scanner 会将超时 `cancelling` Operation 显式标记为 `cancelled`，写入
  `operation.cancel_timeout`；长时间未更新的 queued/running Operation 写入
  `operation.stuck_detected` 事件暴露问题，不静默终结。

剩余缺口：

- 外部 MQ backend 未实现；按本文第 8 节，只有当 PostgreSQL-local dispatcher
  无法满足实际吞吐或跨进程通知需求时才引入。
- queued/running stuck 当前只报告，不自动失败；长任务是否终结必须由对应 handler
  或显式取消策略决定。

#### 阶段 5H：Operation 进度透明

阶段 5H 是进入阶段 6 前的用户可见优化门槛。完整执行计划见
`docs/todos/2026-06-30-pre-phase6-agent-operation-polish.md`。

目标：

- `OperationCard` 展示 `progress_pct` / `progress_message`；
- transfer Operation 投影当前阶段、源/目标节点、文件名、size、速度、ETA；
- 有可靠字节进度时显示确定进度条；
- 没有可靠字节进度时显示不确定进度条，不伪造百分比；
- long-running Operation 的状态固定展示在 Activity 面板，不被聊天流冲走。

需要完成：

- `TransferOperationHandler.project()` 聚合 `TransferSession`、source job、target job
  和最近 `job.event`；
- Node transfer progress 合同增加 `bytes_transferred`、`total_bytes`、
  `rate_bytes_per_sec`、`eta_sec`；如果只能上报 keepalive，必须显式标记；
- `transfer.status.summary` 与 `operation.status` 使用同一套 progress projection；
- Console `OperationCard` 渲染确定/不确定进度条；
- scanner 同步 running transfer 时刷新 Operation 进度。

验收：

- 100MB 以上跨 Node 传输时，Agent run 进入 `waiting_operation` 后不消耗 LLM token；
- Activity 面板可看到进度；
- 失败时显示稳定错误码；
- 终态后通过 Append context chip 将 `operation.status` 引用放入输入框，用户可追加文本后提交；
  Agent 基于最新 operation observation 总结，不重复创建 transfer。

#### 阶段 5I：意图槽位、ExecutionGuard 与 Agent 工具选择治理

阶段 5I 解决当前 Agent 在工具选择和业务判断上的混乱。它不把工具选择硬编码为固定流程，
而是建立 intent slots、preconditions、`ExecutionGuard`、preflight facts 和 execution admission。
完整执行计划见
`docs/todos/2026-06-30-pre-phase6-agent-operation-polish.md`。

目标：

- 用户请求缺少关键参数时，Agent 必须反问；
- 对 transfer 任务，Agent 不得猜测目标目录；
- 执行 transfer 前必须做源路径、目标目录、权限、空间、runtime 状态 preflight；
- 工具失败后，Agent 必须区分节点离线、capability 未注册、权限不足、链路失败和策略拒绝；
- Prompt Context 面板展示工具选择所依赖的结构化事实。
- `ExecutionGuard` 负责硬约束，例如写前必须读、目标路径必须已探测、缺少先决事实时阻断执行。
- `ExecutionGate` 作为轻薄门面组合 GuardDecision、PolicyDecision 和 ExecutionPlan，但不吞并
  `ExecutionGuard`、`PolicyEngine` 或 `ExecutionAdmissionService`。

需要完成：

- `transfer.create` schema 和 Center 结构化校验要求 `target_output_dir` 或 `target_path`；
- 新增 `src/yequ/runtime/guards/`，实现 `ExecutionGuard`、guard rules、facts 和 preflight decision；
- 新增 `ExecutionGate` 门面，固定执行顺序为 Guard -> Policy -> Admission；
- 新增或强化 `transfer.preflight` Center meta tool；
- Agent system prompt 增加明确规则：缺少传输落点必须询问，不能默认 `/home/user` 或 `/tmp`；
- `capability.search` / `capability.describe` 支持 node/platform/effect/risk/runtime/projection/limit 等结构化筛选；
- prompt diagnostics 增加 `tool_selection_context` 和 transfer policy；
- resume prompt 保持 INFO/observation 语义，不生成硬编码 assistant fallback。

验收：

- “把 Win 上那个 zip 传到 Linux”不会直接启动传输；
- “传到 `/root`”会在 preflight 阶段失败，不启动 croc；
- “传到 `/tmp/yequ-transfer` overwrite”会先 preflight，再创建 Operation；
- `waiting_operation` 后 Agent 停止本轮，不轮询。
- OperationCard 的原 Continue 主交互改为 Append context chip：把 operation observation 引用插入输入框，允许用户追加文本后一起提交。
- LLM 不需要接收完整无关 capability 列表即可完成工具选择。

#### 阶段 5J：Capability 合同与 Node 描述治理

阶段 5J 解决“Node 能力描述也是提示词系统的一部分”的问题。完整执行计划见
`docs/todos/2026-06-30-pre-phase6-agent-operation-polish.md`。

目标：

- capability name、description、input schema、output schema、risk/effect、
  runtime requirement、progress/cancel/resume 声明共同构成工具选择事实；
- WinNode 和 LinuxNode 的 transfer 能力合同对齐；
- 新增 capability manifest lint 或测试，避免新增能力时继续产生模糊描述和缺失 schema；
- `capability.describe` 能让 Agent 清楚知道某能力属于哪个 Node、哪个 runtime、需要什么权限。

需要完成：

- transfer capability 明确声明 `supports_progress`、`supports_cancel`、
  `supports_resume`、`preflight_supported`；
- `*.transfer.local.stat` 输出路径存在性、可读/可写、空间、size、mtime、sha256；
- permission denied、source not found、target not writable、insufficient space 等错误码稳定；
- output 不得把旧 ledger 或其他文件误报为当前任务结果。

验收：

- Agent 不再用函数名前缀作为唯一路由事实；
- Linux/Windows transfer 能力描述一致；
- capability lint 或窄测试能检出缺少 required input、空 description、写操作缺 resource key、
  长任务缺 progress/cancel 声明等问题。

#### 阶段 5K：Linux Node 能力扩展

阶段 5K 直接在仓库内 `nodes/linux/yequnode` 推进。完整执行计划见
`docs/todos/2026-06-30-pre-phase6-agent-operation-polish.md`。

目标：

- 补齐 Linux Node 基础文件、进程、服务、网络、包管理、artifact、传输辅助能力；
- 减少 Agent 为常见 Linux 操作绕路；
- Center -> Node artifact 下发第一版进入 Job Operation 主路径；
- 每个新增能力都声明 runtime、risk、effect、resource_keys、preflight/progress/cancel 支持。
- 同步更新 `YQP-Node-Protocol.md`、`docs/node-capability-contract.md`、
  `docs/linux-node-development-contract.md` 和 Windows Node 相关合同文档。

第一批候选能力：

- `linux.filesystem.write_text`、`copy`、`move`、`remove`、`mkdir`、`chmod`、
  `chown`、`disk_usage`、`hash`；
- `linux.artifact.download_file`、`windows.artifact.download_file`、`linux.artifact.register_local_file`；
- `linux.process.kill`、`linux.process.tree`；
- `linux.service.start`、`stop`、`enable`、`disable`、`logs`；
- `linux.network.ping`、`dns_lookup`、`http_probe`、`port_check`；
- `linux.package.install`、`remove`、`update_cache`；
- 强化 `linux.transfer.local.stat`、`linux.transfer.croc.reconcile` 和 transfer progress。

验收：

- Agent 能直接完成常见 Linux 查询、文件落点准备、权限探测、传输核验；
- Center Artifact 可以通过 `artifact.deploy.preflight` + `artifact.deploy` 第一版下发到 Linux/Windows Node；
- 新能力不会绕过 Center policy 和 Operation Runtime；
- 权限不足在 preflight 阶段暴露；
- Node 合同精细到 input/output/error/preflight/progress/cancel/risk/effect/runtime，其他 Agent
  按合同执行不会出现“能用就行”的偏离实现。

阶段 5 总体验收：

- 不同领域共享 Operation / WaitHandle / OperationEvent / Activity 面板；
- 领域详情仍由各自模型保存，Operation 不吞并 TransferSession / Job / MaintenanceRun / ApprovalRequest；
- 新长任务不再各自发明等待、取消、resume、前端投影；
- `ToolInvocationApplicationService` 不再作为 v2 新功能的扩张点；
- SubAgent 所需的 parent/child run、wait_handle、resume observation 有可复用基础。
- 长任务在前端可观察，传输进度以 Operation projection 表达；
- Agent 对参数不完整、权限不明、路径不明的任务先确认或 preflight，不直接执行；
- Node capability 描述和 schema 足以支撑 Agent 做正确工具选择。
- Linux Node 能力足以支撑常见 Linux 文件、服务、网络、artifact 和传输辅助操作。

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
