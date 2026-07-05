# 当前项目全貌

状态：当前概览  
更新时间：2026-07-03
当前阶段：`docs/todos/2026-07-03-documentation-and-architecture-quality-gate.md`

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

下一阶段不是立刻扩展 SubAgent 或继续堆业务能力，而是先完成文档系统收口、代码质量审查、架构边界复核，以及 Node/capability 插拔性确认。

## 2. 当前已具备的能力

| 能力 | 当前状态 |
|---|---|
| WinNode + LinuxNode 接入 | 已跑通，两个 Node 可同时在线。 |
| YQP 协议 | hello、heartbeat、capability 注册、job poll/accept/running/finish、lease renew、cancel/reconcile、artifact.upload、Node-auth artifact download 已具备。 |
| Capability Registry | Center 维护 capability definition/source，Agent 默认通过 meta tools 搜索、描述和调用。 |
| Agent Runtime | 生产主路径为 `/agent/invoke/stream`；非流式旧 ReAct 路径已退出主线。 |
| Operation Runtime | transfer、job、approval_wait、maintenance 已接入 Operation 投影；transfer Operation 已能从 source/target job 和 Node job.event read model 聚合基础进度并投影到 Console。 |
| AgentRun checkpoint | provider 输出、tool observation、waiting_operation、waiting_approval、final/failure 均有结构化记录。 |
| Artifact | Node 可上传 artifact 到 Center；Console 可浏览、下载、预览；Agent 可用 `artifact.present` 展示；Center 已提供 `artifact.deploy.preflight` / `artifact.deploy`，通过目标 Node 的 `<platform>.artifact.download_file` 下发 artifact。 |
| yq-croc 传输 | Node -> Node 大文件/跨 Node 传输已跑通；Center 通过 `transfer.preflight/create/resume/status/cancel` 做控制面，数据面不占 Center 主带宽。 |
| Linux Node | 源码位于 `nodes/linux/yequnode`，后续直接在本仓库推进。 |
| Windows Node | 源码位于 `E:\yequdesu_project\YeQu-Gateway-Win\Node-winClient`，后续由本项目同步更新。 |

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

当前阶段的 `capability.search` / `capability.describe` 由结构化能力发现层和 YCR Tool RAG 共同服务。

```text
Capability Registry
  -> YCR capability index jobs
  -> BGE-M3 dense/sparse retrieval
  -> reranker
  -> structured validation / projection
  -> capability.invoke
```

无 query 时，`capability.search` 只做 registry-backed structured filter，并且必须带至少一个过滤条件。带 query 时，YCR 只读取 ready capability index，不在用户请求路径临时构建索引；embedding 或 reranker 不可用时返回明确错误，不做字符串 fallback。

Tool RAG 只负责候选召回增强：

```text
自然语言任务
  -> semantic retrieval + rerank 得到候选 capability
  -> registry/source/runtime 事实二次校验
  -> ExecutionGuard / PolicyEngine / Admission
  -> Runtime execution
```

Tool RAG 不能取代 registry、schema、preflight、Guard、Policy 或 Operation Runtime，也不能把语义相似度
当成执行授权或事实满足证明。

### 3.4 Node 与 capability 插拔边界

这里的“平台无关”指 Center 可以接入任意平台的 Node，不是要求 Center 运行平台无关，也不是要求每个 Node 自身平台无关。

目标形态：

```text
新平台 Node
  -> 按 YQP provision / hello / heartbeat / runtime snapshot 接入
  -> 按 capability manifest 注册 functions / signals
  -> Center registry 产生 definition/source
  -> Agent 通过 capability.search / describe / invoke 发现和调用
```

新增 Node 不应要求修改 Center 调度逻辑。新增 capability 不应要求修改 Center meta tool 列表、Agent prompt 或 Agent 分支代码。只有当协议、manifest 合同或通用 runtime 规则本身不足时，才允许修改 Center。

### 3.5 长任务交互

长任务不应让 LLM loop 持续轮询。正确路径是：

```text
tool call
  -> Operation
  -> AgentRun waiting_operation
  -> Console Activity 面板展示状态/进度
  -> 用户 Append operation context chip 到输入框
  -> 新一轮 Agent 携带 operation observation 和用户补充文本
```

旧的直接 Continue 已退出 Console 主交互。当前主路径是 Append operation context 到输入区后通过 `/agent/invoke/stream` 的 `context_refs` 提交；请求中的 `prompt` 是 LLM 看到的用户补充指令，`user_visible_prompt` 是带 operation chip 摘要的聊天记录展示文本。Console composer 已使用内联 operation chip，已提交 context 可通过 AgentTurnEvent 恢复为调试事件，未提交 chip 按 session 本地恢复。

## 4. 当前主要待办

当前进入下一轮功能扩展前，必须先完成质量门禁：

1. 文档系统收口：active 文档职责清晰，旧计划、旧提案、复盘和展示材料全部归档。
2. Node 接入插拔性：新增任意平台 Node 后，只要完成 provisioning、YQP hello、runtime 上报和 capability 注册，Center 不应新增平台分支。
3. Capability 插拔性：新增 capability 按 manifest 注册后，应自动进入 Center registry、structured discovery 和 Agent meta tool 调用路径。
4. 错误传播：Provider、Runtime、Tool、YQP、Operation 统一稳定错误码和 problem projection，前端不得收到伪成功或空失败。
5. 无静默 fallback：生产路径不得保留旧 croc CLI、rclone、直连传输、未注册 capability 执行或 provider 自动切换。
6. 模块职责拆分：继续拆小 `CenterExecutionRuntime`、`TransferApplicationService` 和 Agent route，避免形成新的大总管。
7. Agent 上下文预算：工具 schema、tool result、history、context_refs、resume prompt 都必须有预算、投影和摘要策略。
8. 边界测试扩展：import boundary、fallback residue、meta tool output size、Node onboarding 和 capability onboarding 都应进入测试。

SubAgent 和更多 Node 能力应在质量门禁通过后再进入主线。

## 5. 当前权威文档

| 文档 | 用途 |
|---|---|
| `YQP-Node-Protocol.md` | Node/Center 协议合同。 |
| `docs/current-project-overview.md` | 当前项目全貌和下一阶段主线。 |
| `docs/node-capability-contract.md` | 面向多平台 Node 接入的 capability 合同。 |
| `docs/linux-node-development-contract.md` | 当前 Linux Node 实现合同。 |
| `docs/agent-sse-contract.md` | Agent SSE 前后端事件合同。 |
| `docs/todos/2026-07-03-documentation-and-architecture-quality-gate.md` | 当前质量门禁。 |
| `docs/todos/2026-06-29-agent-provider-system.md` | 仍未完成的 Provider 系统路线图。 |
| `docs/documentation-index.md` | 当前文档入口和归档说明。 |
| `docs/documentation-policy.md` | 文档维护规则。 |

旧路线图和已实现提案已归档。归档文档只解释历史演化，不能作为当前开发约束。
