# 当前项目全貌

状态：当前概览  
更新时间：2026-07-08
当前阶段：Agent Runtime / Plan / Operation / YCR State 架构收敛

## 1. 一句话结论

YeQu Center 是个人基础设施控制中心。Center 负责认证、策略、能力注册、调度、审计、等待、恢复和前端投影；Node 负责在具体设备上执行本地 capability；Agent、Console、CLI 都必须通过 Center 标准路径执行，不能直连 Node。

当前项目的 **Center Execution Runtime v2** 主线已经基本落地。系统不再继续扩张旧的 capability runtime，而是在 capability registry 之上建立：

- `ExecutionGuard` / `ExecutionGate`
- `ExecutionAdmissionService`
- `Operation` / `OperationEvent`
- workflow handlers
- AgentRun checkpoint / resume
- Console Activity / OperationCard
- Node capability 精细合同

下一阶段不是立刻扩展 SubAgent、Provider 系统或继续堆业务能力，而是在质量门禁约束下，
先完成 Agent Runtime 主状态机、通用 Plan、Operation Event Queue、YCR Session State、
Registry/YCR Snapshot Cache 和 Tool RAG Candidate Loader 的架构收敛，确保复杂任务不再依赖
临时 prompt、前端推断、重复 internal turn 或每轮重建上下文。

## 2. 当前已具备的能力

| 能力 | 当前状态 |
|---|---|
| WinNode + LinuxNode 接入 | 已跑通，两个 Node 可同时在线。 |
| YQP 协议 | hello、heartbeat、capability 注册、job poll/accept/running/finish、lease renew、cancel/reconcile、artifact.upload、Node-auth artifact download 已具备。 |
| Capability Registry | Center 维护 capability definition/source；Center meta tools 与 Node capabilities 已统一进入 registry。Provider 默认只直接看到 `capability.groups` / `capability.group.open` / `capability.invoke`；`capability.search` / `capability.describe` 位于 `capabilities` 分组，需要显式打开后再通过 `capability.invoke` 使用。 |
| Agent Runtime | 生产主路径为 `/agent/invoke/stream`；非流式旧 ReAct 路径已退出主线。 |
| Operation Runtime | transfer、job、approval_wait、maintenance 已接入 Operation 投影；transfer Operation 已能从 source/target job 和 Node job.event read model 聚合基础进度并投影到 Console。 |
| AgentRun checkpoint | provider 输出、tool observation、waiting_operation、waiting_approval、final/failure 均有结构化记录；下一阶段要把它提升为所有 Agent 执行的主状态机。 |
| Artifact | Node 可上传 artifact 到 Center；Console 可浏览、下载、预览；Agent 可用 `artifact.present` 展示媒体 artifact；Center 已提供 `artifact.read_text` 按行/通配符/首尾/range 读取文本 artifact，避免把大文本塞进上下文；`artifact.deploy.preflight` / `artifact.deploy` 通过目标 Node 的 `<platform>.artifact.download_file` 下发 artifact。 |
| yq-croc 传输 | Node -> Node 大文件/跨 Node 传输已跑通；Center 通过 `transfer.preflight/create/resume/status/cancel` 做控制面，数据面不占 Center 主带宽。 |
| Linux Node | 源码位于 `nodes/linux/yequnode`，后续直接在本仓库推进。 |
| Windows Node | 源码位于 `nodes/windows/winnode`，与 Linux Node 一样作为本仓库子目录管理。 |

## 3. 当前关键边界

### 3.1 文件流转

| 方向 | 当前支持情况 | 策略 |
|---|---|---|
| Node -> Node | 已支持 | `transfer.preflight` 校验两端路径、runtime、relay 和 `resume_mode`；`transfer.create` 创建 `TransferSession` 和 waitable Operation，编排两端 `<platform>.transfer.croc.send/receive`；默认 `route_policy=auto`，也可显式指定 `relay_only`、`relay_pool`、`local_first`、`local_only` 或 `direct_ip`。这是大文件和跨 Node 默认路径。 |
| Node -> Center | 已支持 | `artifact.upload`。适合截图、日志、小中型文件。 |
| Center -> Node | 已支持 | Center 提供 Node-auth artifact download；`artifact.deploy.preflight` 校验 artifact 可用性和目标路径事实；`artifact.deploy` 通过目标 Node 的 `<platform>.artifact.download_file` 创建写入 Job，并按写操作进入审批和 Operation 投影。该路径不提供跨 Job 断点续传，不作为跨 Node 大文件默认路径。 |
| Node -> Center -> Node | 已支持但限定用途 | Node 先上传为 artifact，Center 再用 `artifact.deploy` 下发到目标 Node。该路径用于截图、日志、构建产物、配置文件等 Center 托管 artifact；Node 间大文件搬运默认走 `transfer.create` / yq-croc，避免占用 Center 主带宽。 |

### 3.2 Guard / Policy / Admission

| 构件 | 职责 |
|---|---|
| `ExecutionGuard` | 事实先决条件和硬约束，例如写前必须读、源路径可读、目标目录可写、空间足够。 |
| `PolicyEngine` | execution mode、risk/effect、审批策略、allow/ask/deny。 |
| `ExecutionAdmissionService` | 选择 inline、sync wait、waitable Operation、workflow Operation。 |
| `ExecutionGate` | 轻薄门面，按 Guard -> Policy -> Admission 组合结果，不拥有具体规则。 |

`PolicyEngine` 不合并进 `ExecutionGuard`。Guard 管事实，Policy 管授权，Admission 管执行形态。

### 3.3 Capability Discovery 与 YCR Tool RAG

当前阶段 provider 的默认工具面是渐进式工具目录：

```text
capability.groups
capability.group.open
capability.invoke
```

Center meta tools 先按分组展开。当前分组为 `nodes`、`capabilities`、`context`、`artifacts`、`operations`、`transfers`。`capability.search` / `capability.describe` 不再默认常驻 provider 工具面；它们位于 `capabilities` 分组，只在当前工作集和已打开分组不足以解决任务时使用。

`capability.search` / `capability.describe` 由结构化能力发现层和 YCR Tool RAG 共同服务。

```text
Capability Registry
  -> YCR capability index jobs
  -> BGE-M3 dense/sparse retrieval
  -> reranker
  -> structured validation / projection
  -> capability.invoke / Center workflow / job dispatch / artifact-transfer path
```

无 query 时，`capability.search` 只做 registry-backed structured filter，并且必须带至少一个过滤条件。带 query 时，YCR 只读取 ready capability index，不在用户请求路径临时构建索引；embedding 或 reranker 不可用时返回明确错误，不做字符串 fallback。

Tool RAG 只负责候选加载和候选召回增强：

```text
自然语言任务
  -> session working set / snapshot cache / semantic retrieval + rerank 得到候选 capability
  -> registry/source/runtime 事实二次校验
  -> ExecutionGuard / PolicyEngine / Admission
  -> Runtime execution
```

Tool RAG 不能取代 registry、schema、preflight、Guard、Policy 或 Operation Runtime，也不能把语义相似度
当成执行授权或事实满足证明。候选不足时，Agent 仍可打开 `capabilities` 分组并调用
`capability.search` 扩大检索。

`capability.invoke` 是执行具体 capability 的入口。Center meta tools 与 Node
capability 已统一进入 capability registry；provider 默认工具面收敛为
`capability.groups` / `capability.group.open` / `capability.invoke`。YCR 通过
capability gateway 产出的 typed entities 维护 session working set，避免 build-turn
解析具体 tool result shape。`capability.search` 仍是 Node/Product capability 的语义检索入口，
但不再作为每轮默认工具。

### 3.4 Node 与 capability 插拔边界

这里的“平台无关”指 Center 可以接入任意平台的 Node，不是要求 Center 运行平台无关，也不是要求每个 Node 自身平台无关。

目标形态：

```text
新平台 Node
  -> 按 YQP provision / hello / heartbeat / runtime snapshot 接入
  -> 按 capability manifest 注册 functions / signals
  -> Center registry 产生 definition/source
  -> Agent 通过分组目录、session working set、capability.search / describe / invoke 发现和调用
```

新增 Node 不应要求修改 Center 调度逻辑。新增 capability 不应要求修改 Center meta tool 列表、Agent prompt 或 Agent 分支代码。只有当协议、manifest 合同或通用 runtime 规则本身不足时，才允许修改 Center。

### 3.5 长任务交互

长任务不应让 LLM loop 持续轮询。正确路径是：

```text
tool call
  -> Operation
  -> AgentRun waiting_operation
  -> Console Activity 面板展示状态/进度
  -> Operation terminal event 进入 Agent Runtime event queue
  -> Center/Agent Runtime 服务端消费者自动汇报终态
  -> 用户仍可 Append operation context chip 手动引用结果
```

旧的直接 Continue 已退出 Console 主交互。Append operation context 保留为用户手动引用结果的交互；自动汇报由后端 `AgentOperationReporter` 消费 `agent_operation_notifications` 队列，按 operation_id 幂等、session 内单消费者，并有 reported/failed 终态。前端不 claim notification、不自行唤醒 Agent，只展示 Operation、Plan、YCR 和最终消息。

### 3.6 Agent Plan 与 YCR Session State

当前已有维护计划路径，但它不是通用 Agent Runtime Plan。通用 Agent Runtime Plan 已作为每个 AgentRun 的任务状态骨架落地：

```text
AgentRun
  -> Plan
  -> PlanStep
  -> ToolCall / Approval / Operation / Artifact
```

Plan 不等于 Workflow Capability。Center 不新增 `artifact.place_on_node`、`screen.capture_and_present` 这类高阶业务能力；Plan 只记录目标、步骤、等待项、已完成事实和禁止重复动作。

YCR 也不再只做 provider 前置投影器。当前已经新增基础 `YcrSessionState`，由 tool observation 和 Operation event 维护当前 session 的 typed working set：

- capability working set；
- artifact working set；
- artifact focus（`last_artifact` / `current_artifact`，用于解析“这张图片/上一张截图”）；
- operation working set；
- node facts；

AgentRunEvent 统一事实源、run-level TaskState、Observation Reducer、Replanner/任务完成判定、Operation/Approval 后端状态回填、YCR TaskState 输入、PlanStep 精确事件绑定、TaskState working set 与 `tool_strategy` 已经完成基础实现。Console 已新增后端只读 `runtime-state` 投影，右侧 Runtime/Plan 面板读取最新非 internal AgentRun、TaskState、events 和 AgentPlan。Tool Candidate Loader 已从“提示优先候选”收敛为实际工具面控制：当 YCR 返回 `reuse_working_set` 且存在候选时，本轮 provider 只看到 `capability.invoke`；空 working set 或候选不足时才暴露 `capability.groups` / `capability.group.open` 渐进发现入口。仍需继续收敛的是旧 ChatTimeline 局部 patch 状态、复杂真实会话验收、completion criteria 强化、PlanStep 多分支归属、Result RAG deterministic read/tail 路径验收和前端业务残留清理。

## 4. 当前主要待办

当前执行顺序以 `docs/todos/README.md` 为准。质量门禁继续作为全局约束存在，但不替代
专题待办的实现顺序。

1. 按 `docs/todos/2026-07-08-post-basic-runtime-closure.md` 做基础实现后的收口：系统提示词与当前实现一致性、`exec.run` profile contract、Replanner 完成边界、TaskState/Reducer 事实抽取、PlanStep 归属、Console 业务状态残留、YCR/RAG 收益与边界验收、真实任务验收、耗时归因、meta tool 输出审计和代码清理门禁。
2. `docs/todos/2026-07-07-agent-runtime-plan-operation-ycr-state.md` 继续作为 Agent Runtime / Plan / Operation / YCR State 的架构来源，不再作为“从零实现”清单。
3. 按 `docs/todos/2026-07-06-ycr-agent-routing-and-transfer-corrections.md` 做行为验收和剩余缺陷：Windows 截图和 Windows -> Linux 传输端到端复验已通过；后续继续处理 meta tool 默认输出边界、复杂任务过度探索和错误展示。
4. Runtime/YCR 状态主线稳定后，再继续推进 Provider 系统：provider registry、模型发现、probe、前端 provider/model 选择和显式 provider 错误展示。
5. YCR core data path 与 unified capability registry 已进入维护核对状态；后续只在行为验收暴露回归时更新对应事实文档或行为待办。

SubAgent 和更多 Node 能力应在上述收敛完成后再进入主线。

## 5. 当前权威文档

| 文档 | 用途 |
|---|---|
| `YQP-Node-Protocol.md` | Node/Center 协议合同。 |
| `docs/current-project-overview.md` | 当前项目全貌和下一阶段主线。 |
| `docs/node-capability-contract.md` | 面向多平台 Node 接入的 capability 合同。 |
| `docs/linux-node-development-contract.md` | 当前 Linux Node 实现合同。 |
| `docs/agent-sse-contract.md` | Agent SSE 前后端事件合同。 |
| `docs/todos/2026-07-03-documentation-and-architecture-quality-gate.md` | 当前质量门禁。 |
| `docs/todos/2026-07-06-ycr-agent-routing-and-transfer-corrections.md` | YCR/Agent 行为缺陷和 transfer 状态修正待办。 |
| `docs/todos/2026-07-07-agent-runtime-plan-operation-ycr-state.md` | Agent Runtime / Plan / Operation / YCR State 架构收敛待办。 |
| `docs/todos/2026-07-08-exec-profile-controlled-exec-design.md` | Exec Profile、`exec.run` 和 Node primitive 能力收敛待办。 |
| `docs/todos/2026-06-29-agent-provider-system.md` | Provider 系统待办。 |
| `docs/documentation-index.md` | 当前文档入口和归档说明。 |
| `docs/documentation-policy.md` | 文档维护规则。 |

旧路线图和已实现提案已归档。归档文档只解释历史演化，不能作为当前开发约束。
