# 当前项目全貌

状态：当前概览  
更新时间：2026-06-30  
主路线图：`docs/todos/2026-06-30-center-execution-runtime-v2.md`

## 1. 一句话结论

YeQu Center 是个人基础设施控制中心。Center 负责认证、策略、能力注册、调度、审计、等待、恢复和前端投影；Node 负责在具体设备上执行本地 capability；Agent、Console、CLI 都必须通过 Center 标准路径执行，不能直连 Node。

当前项目已经进入 **Center Execution Runtime v2** 阶段。新的主线不是继续扩张旧的 capability runtime，而是在 capability registry 之上建立：

- `ExecutionGuard` / `ExecutionGate`
- `ExecutionAdmissionService`
- `Operation` / `OperationEvent`
- workflow handlers
- AgentRun checkpoint / resume
- Console Activity / OperationCard
- Node capability 精细合同

## 2. 当前已具备的能力

| 能力 | 当前状态 |
|---|---|
| WinNode + LinuxNode 接入 | 已跑通，两个 Node 可同时在线。 |
| YQP 协议 | hello、heartbeat、capability 注册、job poll/accept/running/finish、lease renew、cancel/reconcile、artifact.upload、Node-auth artifact download 已具备。 |
| Capability Registry | Center 维护 capability definition/source，Agent 默认通过 meta tools 搜索、描述和调用。 |
| Agent Runtime | 生产主路径为 `/agent/invoke/stream`；非流式旧 ReAct 路径已退出主线。 |
| Operation Runtime | transfer、job、approval_wait、maintenance 已接入 Operation 投影；transfer Operation 已能从 source/target job 和 Node job.event read model 聚合基础进度并投影到 Console。 |
| AgentRun checkpoint | provider 输出、tool observation、waiting_operation、waiting_approval、final/failure 均有结构化记录。 |
| Artifact | Node 可上传 artifact 到 Center；Console 可浏览、下载、预览；Agent 可用 `artifact.present` 展示；Linux/Windows Node 已有最小 `<platform>.artifact.download_file` 原语。 |
| croc 传输 | Node -> Node 大文件/跨 Node 传输已跑通，Center 做控制面，数据面不占 Center 主带宽。 |
| Linux Node | 源码位于 `nodes/linux/yequnode`，后续直接在本仓库推进。 |
| Windows Node | 源码位于 `E:\yequdesu_project\YeQu-Gateway-Win\Node-winClient`，后续由本项目同步更新。 |

## 3. 当前关键边界

### 3.1 文件流转

| 方向 | 当前支持情况 | 策略 |
|---|---|---|
| Node -> Node | 已支持 | `transfer.create` + croc。适合大文件和跨 Node。 |
| Node -> Center | 已支持 | `artifact.upload`。适合截图、日志、小中型文件。 |
| Center -> Node | 部分支持 | Center 已提供 Node-auth artifact download；Linux/Windows Node 已有 `<platform>.artifact.download_file`；Center `artifact.deploy.preflight` 会检查 artifact 与目标路径事实；`artifact.deploy` 创建目标 Node Job Operation。断点续传未完成。 |
| Node -> Center -> Node | 部分支持 | 第一段已支持；Linux/Windows 目标可通过 `artifact.deploy` 落地；大文件策略仍需完善。 |

### 3.2 Guard / Policy / Admission

| 构件 | 职责 |
|---|---|
| `ExecutionGuard` | 事实先决条件和硬约束，例如写前必须读、源路径可读、目标目录可写、空间足够。 |
| `PolicyEngine` | execution mode、risk/effect、审批策略、allow/ask/deny。 |
| `ExecutionAdmissionService` | 选择 inline、sync wait、waitable Operation、workflow Operation。 |
| `ExecutionGate` | 轻薄门面，按 Guard -> Policy -> Admission 组合结果，不拥有具体规则。 |

`PolicyEngine` 不合并进 `ExecutionGuard`。Guard 管事实，Policy 管授权，Admission 管执行形态。

### 3.3 Capability Discovery 与未来 Tool RAG

当前阶段的 `capability.search` / `capability.describe` 是 **结构化能力发现层**，不是完整 Tool RAG。

```text
Capability Registry
  -> Capability Index / Structured Discovery
  -> Capability Context Builder
  -> Agent 当前任务所需的少量工具上下文
```

它负责按 Node、平台、risk/effect、runtime、progress/cancel/resume、artifact 输入输出和 projection
精确筛选能力，减少无关工具占用上下文。

未来 Tool RAG 只作为候选召回增强层接入：

```text
自然语言任务
  -> semantic retrieval 得到候选 capability / 示例 / 参数模式
  -> structured discovery 二次过滤
  -> ExecutionGuard / PolicyEngine / Admission
  -> Runtime execution
```

Tool RAG 不能取代 registry、schema、preflight、Guard、Policy 或 Operation Runtime，也不能把语义相似度
当成执行授权或事实满足证明。

### 3.4 长任务交互

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

当前进入阶段 6 前，必须先完成：

1. Operation 进度透明：基础 `progress_pct/progress_message`、字节级 read model、Console 进度条、Node keepalive progress 和 receiver output-size 字节进度已落地；sender 端在 croc 无稳定机器可读输出时不伪造百分比。
2. `ExecutionGuard` / `ExecutionGate`：`transfer.create` 缺槽位硬阻断已落地，路径权限、croc runtime status preflight 和 30-300 秒 fact freshness 策略已接入。
3. transfer preflight：Center meta tool、`TransferPreflight` 持久事实、`preflight_id` 强制绑定和 runtime status 校验已落地。
4. capability search / describe 的结构化筛选和 projection 诊断增强已完成基础闭环：支持按 node/platform/risk/effect/runtime/progress/preflight/artifact 输入输出筛选，按 projection 控制返回量，并返回 `match_reasons`、`dispatchable`、`unavailable_reasons`。
5. Node capability 合同细化和 lint：第一版 `contract_issues` 已接入 registry 诊断，后续还需扩展独立 CLI 或 Node 侧构建期检查。
6. Linux Node 能力扩展：已新增 `linux.filesystem.hash`、`linux.filesystem.disk_usage`、`linux.filesystem.mkdir`、`linux.network.dns_lookup` 和 `linux.network.port_check`，用于传输后校验、落点空间探测、落点目录准备和基础网络连通性排查；后续继续补写入、复制、移动、删除、服务日志、HTTP 探测等能力。
7. Windows Node transfer/artifact/progress/cancel 合同对齐：artifact download 已对齐，transfer/progress/cancel 仍需继续验收。
8. Center -> Node artifact 下发 workflow：Node-auth 下载端点、Linux/Windows Node 下载原语、`artifact.deploy.preflight` 和 `artifact.deploy` 第一版已落地；仍需进度/断点续传策略。

阶段 6 才开始 SubAgent。

## 5. 当前权威文档

| 文档 | 用途 |
|---|---|
| `YQP-Node-Protocol.md` | Node/Center 协议合同。 |
| `docs/node-capability-contract.md` | 跨平台 Node capability 合同。 |
| `docs/linux-node-development-contract.md` | 当前 Linux Node 实现合同。 |
| `docs/agent-sse-contract.md` | Agent SSE 前后端事件合同。 |
| `docs/todos/2026-06-30-center-execution-runtime-v2.md` | 当前主路线图。 |
| `docs/todos/2026-06-30-pre-phase6-agent-operation-polish.md` | 阶段 6 前置优化计划。 |
| `docs/todos/2026-06-29-agent-provider-system.md` | Provider 系统路线图。 |
| `docs/documentation-policy.md` | 文档维护规则。 |

旧路线图和已实现提案已归档。归档文档只解释历史演化，不能作为当前开发约束。
