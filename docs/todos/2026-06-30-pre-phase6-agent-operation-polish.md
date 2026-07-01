# 阶段 6 前 Agent 与 Operation 体验优化待办

状态：已完成；5H/5I/5J/5K 已形成阶段性闭环；Center->Node artifact 下发与目标路径 preflight 第一版已落地，croc 长任务进度和 Operation Append 体验已完成阶段验收准备  
日期：2026-06-30  
归属主线：`docs/todos/2026-06-30-center-execution-runtime-v2.md`  
适用范围：WinNode + LinuxNode 已稳定在线，croc 跨 Node 传输已跑通，Operation Runtime v2 已成为长任务主路径之后

## 1. 结论

进入 SubAgent 阶段之前，必须先完成一轮 Agent 与 Operation 的体验和语义治理。

本阶段不是新增大功能，而是让现有 v2 能力在真实使用中变得清晰、可观察、可恢复、可验证。否则阶段 6 的 SubAgent 会把当前问题放大：

- 长任务已经不会继续消耗 LLM token，但前端还不能清楚展示运行进度；
- Agent 会在用户请求不明确时猜测传输落点，例如把 Linux 目标目录猜成 `/home/user/`；
- Agent 有时会把“工具可用性”“节点权限”“路径权限”“传输链路状态”混为一谈；
- Node capability 描述已经成为 Agent 的工具选择输入，但描述质量、schema 约束和前置探测合同还没有系统治理；
- Agent prompt 透明度已经具备，但缺少可用于排查工具选择错误的结构化诊断视图。

因此阶段 6 前新增四个前置阶段：

| 阶段 | 名称 | 目标 |
|---|---|---|
| 5H | Operation 进度透明 | 让长任务在 Activity 面板中展示阶段、进度、速度、错误和可继续状态。 |
| 5I | 意图槽位、先决条件与 Agent 工具选择治理 | 让 Agent 先确认缺失参数、先探测权限与路径、再执行有副作用或长耗时任务；但不把工具选择硬编码成固定流程。 |
| 5J | Capability 合同与 Node 描述治理 | 让 Node manifest / capability schema 成为可靠的工具选择事实来源，而不是依赖自然语言猜测。 |
| 5K | Linux Node 能力扩展 | 补齐 Linux Node 的基础文件、进程、服务、网络、包管理、artifact、传输辅助能力，减少 Agent 绕路。 |

阶段 5H-5K 全部完成并验收后，才能进入阶段 6 SubAgent。

## 2. 当前代码事实

当前实现已经具备以下基础：

- `Operation` 模型有 `progress_pct` / `progress_message` 字段；
- `/admin/operations/{operation_id}` 和 `operation.status` 已返回 Operation 详情；
- Console `OperationCard` 已能轮询状态、取消、终态后 Continue；
- `TransferSession` 已保存 `size_bytes` / `sha256` / `source_job_id` / `target_job_id`；
- Linux Node 的 croc send/receive 已支持 `job.event` 进度事件和 lease renew；
- Agent prompt context 已可在前端诊断面板中展示；
- Agent 默认只暴露 Center meta tools，不再把所有 Node 原始能力注入 prompt。
- Linux Node 源码位于本仓库 `nodes/linux/yequnode`，当前部署实现以该目录为准。
- Windows Node 源码当前可见，路径为 `E:\yequdesu_project\YeQu-Gateway-Win\Node-winClient`。

当前边界：

- 文件流转能力不是全闭环：
  - Node -> Node：已通过 `transfer.create` + croc 跑通；
  - Node -> Center：已通过 Node artifact upload 跑通，适合截图、日志、小中型文件；
  - Center -> Node：Center 已有 Node-auth artifact download，Linux/Windows Node 已有
    `<platform>.artifact.download_file` 原语，Center `artifact.deploy` 第一版会创建目标 Node
    Job Operation；`artifact.deploy.preflight` 已提供目标路径事实检查；artifact deploy
    断点续传属于后续 artifact 扩展，不阻塞本阶段；
  - Node -> Center -> Node：可以通过上传 artifact + `artifact.deploy` 下发到 Linux/Windows 目标；
    大文件仍优先 croc，不把 Center 中转作为默认路径；
- `OperationCard` 已渲染 `progress_pct` / `progress_message`；
- transfer Operation 已从 `TransferSession` 的 source/target job 聚合 `progress_pct`；
- Linux Node `transfer_progress` 已补齐第一版 receiver output-size 进度字段；
- Windows Node transfer progress 合同已与 Linux Node 对齐到第一版 receiver output-size
  progress；sender 端仍只上报 process keepalive 和 total size，不伪造发送百分比；
- `transfer.create` 对目标落点的 schema 约束不够强，Agent 可以在用户未指定时自行猜测；
- `transfer.create` 没有强制执行源路径可读、目标目录可写、剩余空间足够等 preflight；
- Agent 系统提示词没有把“缺少传输落点时必须反问”“不要猜路径”“先探测权限再执行”等行为写成硬性工具使用规则；
- capability 描述里缺少统一的 `requires_confirmation_when`、`preflight_required`、`progress_contract` 等机器可读提示。
- Linux Node 能力仍偏少，部分常见任务缺少直接 capability，导致 Agent 需要通过 find/stat/read_text
  等粗粒度能力绕路。
- Agent `max_steps` 默认已从 20 调整到 40，API 上限仍为 100。复杂多 Node 诊断、
  文件定位、preflight、Operation resume 串联时，40 是当前阶段默认预算；如果任务仍
  频繁打满 40，应优先排查工具选择、preflight 聚合和 capability 查询质量，而不是继续
  无脑调大。

## 3. 设计原则

1. Center 不生成伪 Agent 回复。
   Center 可以发 INFO、状态、错误和 observation；自然语言结论仍由 LLM 生成。

2. Prompt 不是唯一控制面。
   工具选择原则可以写进 prompt，但执行前的必需参数、权限探测和硬约束门禁必须由
   `ExecutionGuard` 结构化校验。`ExecutionGuard` 是独立运行时构件，不把复杂业务流程
   继续塞进 prompt、route、admission 或 workflow handler。

3. Operation 是长任务透明层。
   长任务的阶段、进度、取消、终态和 resume observation 都进入 Operation，不分散到 transfer、maintenance、job、approval 各自 UI。

4. Node capability 描述是合同，不是营销文案。
   Node manifest 中的 description、schema、risk、effect、runtime requirement、output shape 会直接影响 Agent 行为，必须可检查、可测试、可演进。

5. 不通过硬编码用户自然语言实现业务语义。
   例如“继续”不能被硬编码成 resume；“传到 Linux”不能被硬编码成 `/tmp`；“找个文件”不能无限搜索所有盘。

6. 不硬编码工具选择，建模任务先决条件。
   正确模型不是“看到 transfer 就固定调用某几个工具”，而是：

   ```text
   user intent
     -> intent slots
     -> capability preconditions
     -> ExecutionGuard
     -> preflight facts
     -> policy / admission
     -> operation / inline execution
   ```

   LLM 可以负责理解自然语言和提出候选计划；`ExecutionGuard` 必须负责判断计划是否具备执行条件。
   如果条件不足，Center 返回结构化 `needs_input` / `preflight_required` / `preflight_failed`，
   由 LLM 用自然语言向用户解释或追问。

7. 能力不是系统状态的纯函数。
   很多能力依赖运行时权限、路径存在性、磁盘空间、外部网络、用户确认、长任务状态。
   这些依赖必须被建模为 `preconditions` 和 `preflight`，而不是假设 capability 只要在线就可执行。

8. Node 合同是阶段交付物。
   本阶段不仅修改 Center。所有影响 Node manifest、preflight、progress、cancel、artifact、
   transfer、错误码的设计，都必须同步更新：
   - `YQP-Node-Protocol.md`
   - `docs/node-capability-contract.md`
   - `docs/linux-node-development-contract.md`
   - Windows Node README / capability manifest 相关文档

   合同精细度必须达到“其他 Agent 只按合同执行也不会意图偏离”的程度。不得只写“实现上传文件”
   这类模糊要求，必须写明 input/output、错误码、权限、preflight、progress、cancel 和验收。

## 4. 当前文件流转能力边界

| 方向 | 当前支持情况 | 当前路径 | 结论 |
|---|---|---|---|
| Node -> Node | 已支持，已验收 100MB 文件 | `transfer.create` -> sender/receiver croc jobs | 可作为大文件/跨 Node 主路径。 |
| Node -> Center | 已支持 | Node artifact upload -> Center Artifact | 适合截图、日志、轻量/中型文件；受 `YEQU_ARTIFACT_MAX_UPLOAD_BYTES` 限制。 |
| Center -> Node | 部分支持 | Center Node-auth artifact download + `<platform>.artifact.download_file` + `artifact.deploy.preflight` + `artifact.deploy` | Linux/Windows 目标已有第一版；仍需进度和断点续传策略。 |
| Node -> Center -> Node | 部分支持 | 第一段可上传 artifact，Linux/Windows 目标可通过 `artifact.deploy` 下载 artifact | 大文件仍优先 croc；Center 中转策略需继续细化。 |
| Center 本地文件 -> Node | 部分支持 | 可先手动上传为 artifact，再通过 `artifact.deploy` 下发 Node | 仍需 Center artifact source 的更顺手入口。 |

阶段 5H-5K 期间先完成能力原语和合同；Center -> Node 下发第一版采用 `artifact.deploy` meta tool，不新增领域表：

```text
artifact.deploy
  input: artifact_id, target_node_id, target_path, mode
  -> artifact.deploy.preflight target parent writable / disk enough
  -> Node capability: <platform>.artifact.download_file
  -> Operation(kind=job)
```

当前已完成的原语：

- Center `GET /yqp/artifacts/{artifact_id}/download`：使用 Node Bearer token 下载 Center artifact，不要求 Node 持有 admin token；
- Linux Node `linux.artifact.download_file` 和 Windows Node `windows.artifact.download_file`：输入 `artifact_id`、`output_path`、`mode`，流式下载并校验 SHA-256；
- Center `artifact.deploy.preflight`：输入 `artifact_id`、`target_node_id`、`output_path`、`mode`，检查 artifact 是否可用、目标父目录是否可写、目标是否已存在、目标空间是否足够，并返回短 TTL 的 `preflight_id`；
- Center `artifact.deploy`：输入 `artifact_id`、`target_node_id`、`output_path`、`mode`、`preflight_id`，解析目标 Node 的 `artifact.download_file` capability 并创建 waitable Job Operation；
- 该第一版不负责猜测目标路径，不提供 artifact 断点续传，不创建新的 `ArtifactDeploySession`。

这条路径不替代 croc。建议策略：

- 小文件 / 已在 Center 中的文件：`artifact.deploy`；
- 大文件 / Node 间直接传输：`transfer.create` + croc；
- Node 文件需要长期留存到 Center：artifact upload；
- Node 文件只需移动到另一个 Node：优先 croc，不占 Center 带宽。

## 5. 阶段 5H：Operation 进度透明

### 5.1 目标

让用户在 Agent Activity 面板中清楚看到：

- Operation 当前阶段；
- 传输是否仍在运行；
- 如果有总大小和已传输大小，显示确定进度条；
- 如果只有运行事实，没有字节进度，显示不确定进度条；
- 当前速度和预计剩余时间；
- 失败时显示稳定错误码和人类可读错误；
- 终态后可继续，让 Agent 基于事实总结。

### 5.2 数据合同

`Operation` 增加或明确以下投影字段：

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `progress_pct` | `int | null` | Operation handler | 0-100。未知时为 null。 |
| `progress_message` | `str | null` | Operation handler | 简短状态，例如 `Starting receiver`、`Transferring`、`Verifying`。 |
| `progress_detail` | `object | null` | Operation output/projection | 长任务专用详情，不要求所有 Operation 都有。 |

transfer 的 `progress_detail` 第一版结构：

```json
{
  "kind": "transfer",
  "phase": "starting_receiver | starting_sender | transferring | verifying | succeeded | failed",
  "source": {
    "node_id": "winClient",
    "path": "E:\\file.zip",
    "job_id": "job_x",
    "status": "running",
    "size_bytes": 120945608
  },
  "target": {
    "node_id": "linux-node-01",
    "path": "/tmp/yequ-transfer/file.zip",
    "job_id": "job_y",
    "status": "running",
    "received_bytes": 60000000
  },
  "rate_bytes_per_sec": 5242880,
  "eta_sec": 12,
  "last_progress_at": "2026-06-30T12:34:56Z"
}
```

说明：

- `progress_detail` 是前端展示事实，不是 Agent prompt 的全文注入材料；
- Agent resume observation 可以包含摘要版，不应把长 stdout/stderr 塞进 prompt；
- 没有可靠字节进度时，`progress_pct=null`，前端显示不确定进度条。

### 5.3 Center 实现任务

1. `TransferOperationHandler.project()` 聚合进度。

   - [x] 从 `TransferSession`、source job、target job 中读取进度事实；
   - [x] 计算 `progress_pct`；
   - [x] 更新 `operation.progress_pct` / `operation.progress_message` / `operation.output_data.progress_detail`；
   - [x] 失败时保留 `error_code` / `error_message`；
   - [x] 从 job event/read model 中读取字节级进度、速度和 ETA；
   - [x] 计算更细的 `phase`。

2. `job.event` 进度读取能力。

   - [x] Center 从 `job.event` 中识别 `job.progress` / `transfer_progress` 等进度事件；
   - [x] TimelineEvent 仍作为完整事件日志；
   - [x] 最新进度摘要写入 `Job.progress_pct` / `Job.progress_message`；
   - [x] 字节级速度、ETA、last_progress_at 进入 read model；
   - 不引入外部 MQ。

3. `transfer.status` 输出同步。

   - [x] `transfer.status.summary` 增加 `progress`；
   - [x] `operation.status` 和 `transfer.status` 对同一 transfer 的基础状态判断一致；
   - [x] 字节级 `progress_detail` 与 Node 进度事件对齐。

4. Scanner 同步。

   - [x] `OperationConsistencyScanner` 通过 `OperationService.status()` 同步非终态 Operation 时刷新进度；
   - [x] scanner 使用短 session，不持有长事务。

### 5.4 Node 合同任务

Windows Node 和 Linux Node 的 `*.transfer.croc.send/receive` 进度合同已由 `docs/todos/2026-07-01-yq-croc-plugin-runtime-plan.md` 接管。

当前进度：

- [x] Center 能把 Node `job.event` 进度投影到 Job read model；
- [x] Windows Node transfer manifest 已声明 `supports_progress`、`supports_cancel`、`supports_resume` 和 `progress_contract=transfer_progress_v1`；
- [x] Linux Node send/receive 实现已发送 `transfer_started`、`transfer_progress`、`transfer_cancelled` 等 `job.event`，并执行 lease renew/cancel；
- [x] Linux Node Rust `CapabilityManifest` 顶层字段已结构化声明 `supports_progress`、`supports_cancel`、`supports_resume`、`progress_contract`、`preconditions`、`required_intent_slots`；
- [x] Windows Node 和 Linux Node 的旧 croc CLI/stderr progress 路径已废弃；后续实现只接受 `yq-croc` stdout NDJSON。
- [x] receiver output-size 方案经实测存在假进度风险，已降级为 observation，不再生成 `progress_pct`。
- [x] sender ready 同步点已收敛为 `progress_source="yq_croc_event"` 且 `event="sender_ready"`；Center 不再接受旧 CLI 输出作为 ready。
- [x] sender_ready 是由 yq-croc 结构化事件派生的粘性事实，后续 `bytes_progress` 可以携带 `sender_ready=true`。
- [x] transfer 子 job 不再使用固定 30 秒 lease，sender-ready 等待窗口也不再固定 30 秒。croc sender 在暴露 room/code 前可能需要收集和哈希大文件，Center 必须给传输类 job 足够的初始 lease 和 ready 等待窗口。
- [x] `Code is:`、stderr progressbar、CLI flag 拼装和 Node 本地 receive retry 均不再是 active contract；Center 是唯一调度者。
- [x] `transfer.create` 在 Operation 创建前如被客户端取消，必须取消已创建的 sender/receiver job 并标记 TransferSession cancelled，避免资源锁残留阻塞下一次传输。

```json
{
  "event_type": "job.progress",
  "transfer_id": "trf_x",
  "role": "sender | receiver",
  "status": "running",
  "process_pid": 1234,
  "progress_source": "process_keepalive | yq_croc_event | receiver_output_size_observation",
  "bytes_transferred": 60000000,
  "total_bytes": 120945608,
  "rate_bytes_per_sec": 5242880,
  "eta_sec": 12
}
```

如果 croc 当前版本无法稳定输出字节级进度：

- Node 仍必须上报 keepalive 型 `transfer_progress`；
- Center 只能显示不确定进度条；
- 不得伪造百分比；
- Node 可在 receive 侧探测输出目录变化作为 observation，但必须标记 `progress_source="receiver_output_size_observation"`，且不得携带 `bytes_transferred`、`progress_pct`、`eta_sec` 这类会被 Center 解释为真实进度的字段。

### 5.5 Console 实现任务

1. `OperationCard` 渲染进度。

   - [x] `progress_pct !== null`：显示确定进度条和百分比；
   - [x] `progress_pct === null && !terminal`：显示不确定进度条；
   - [x] 显示 `progress_message`；
   - [x] transfer Operation 显示源节点、目标节点、源文件名、目标目录、size。

2. Activity 面板分组。

   - running / waiting Operation 固定显示在 Activity 面板上方；
   - terminal Operation 保留折叠展示；
   - 聊天流中只保留轻量提示，不把完整进度卡片塞回对话流。

3. 刷新策略。

   - running Operation 每 2 秒轮询；
   - 有 `last_progress_at` 时展示最后更新时间；
   - terminal 后停止轮询。

### 5.6 Operation Context Append 语义

旧的 Continue 直接触发一轮 resume，能保证结构化事实进入 Agent，但用户无法在唤醒时追加自己的新指令。
这不适合作为长期交互。

目标交互改为 **Append Operation Context**：

当前第一版进度：

- [x] `/agent/resume-operation/stream` 接受 `user_message`；
- [x] 后端把 Center 构造的 operation observation 与用户追加文本合并进同一轮 LLM 输入；
- [x] Console 的 Operation 卡片按钮从直接 Continue 改为 Append；
- [x] Append 后输入区出现 operation context chip，用户可追加文本后提交；
- [x] 用户提交后，前端显示短的可读消息，不显示内部完整 operation observation prompt；
- [x] `/agent/invoke/stream` 原生接受 `context_refs`；
- [x] Console 主路径通过 `context_refs` 唤醒 operation context；
- [x] `/agent/invoke/stream` 区分 `prompt` 与 `user_visible_prompt`，前者进入 LLM，后者进入聊天记录和前端乐观气泡；
- [x] AgentRun metadata 记录 `context_refs` 和已加载 context block 摘要；
- [x] 已提交的 context block 通过 AgentTurnEvent 重放恢复为 `agent.context_block.loaded` 系统事件；
- [x] 未提交的 operation context chip 按 session 保存在 `sessionStorage`，切换 session 或刷新后可恢复；
- [x] chip 已进入输入框内部，Console 使用 contenteditable composer 渲染一行高 operation chip；
  chip 可通过删除/Backspace 移除，提交时仍作为结构化 `context_refs` 而非用户文本发送；

```text
OperationCard
  -> Append to input
  -> composer 中插入一个一行高 context chip
  -> 用户可以继续输入、删除 chip、移动光标、追加要求
  -> Submit
  -> Center 根据 chip 的 operation_id 拉取最新 operation.status
  -> 将 operation_observation 作为结构化 context block 发送给 Agent
```

这个 context chip 类似 Claude CLI 中粘贴长文本后的压缩块：

- 视觉上是输入框内的一枚短块，例如 `Operation trf_x: succeeded`；
- 行为上参与输入编辑，可以删除；
- 提交时不把 chip 展开成用户手写文本，而是作为结构化 `context_refs` 发送；
- Center 在提交瞬间读取最新事实，避免 chip 显示 stale facts；
- 用户可以在 chip 后追加文本，例如“如果成功，顺便把 sha256 和目标路径列出来”。

API 目标：

```json
{
  "prompt": "如果成功，顺便把 sha256 和目标路径列出来",
  "user_visible_prompt": "[Operation op_x]\n如果成功，顺便把 sha256 和目标路径列出来",
  "context_refs": [
    {
      "type": "operation",
      "operation_id": "op_x",
      "mode": "observation"
    }
  ]
}
```

后端行为：

- [x] `/agent/invoke/stream` 接受 `context_refs`；
- [x] `/agent/invoke/stream` 接受 `user_visible_prompt`，避免把 operation chip 展示文案当作用户自然语言指令传给 LLM；
- [x] 对 `type=operation` 的 ref，调用 `OperationService.status()`；
- [x] 将 observation 以 INFO/context block 注入 provider messages；
- [x] 不重复调用原工具，不重新创建原 Operation；
- [x] 旧 `/agent/resume-operation/stream` 保留为短期兼容入口，Console 主路径已改为 append chip + context_refs；
- [x] 生成 `agent.context_block.loaded` SSE 事件，前端可调试。

这样，“用户发消息”与“结构化唤醒”可以合并在同一次输入里，但不会丢失 Operation 事实。

### 5.7 验收

- 发起 100MB 以上跨 Node 传输时，Agent run 进入 `waiting_operation` 后停止消耗 LLM token；
- Activity 面板立刻显示 transfer Operation；
- 运行中显示进度条；若 Node 未提供字节进度，则显示不确定进度条和 running 状态；
- 传输成功后进度到 100%，显示 size/hash 验证事实；
- 传输失败时显示稳定错误码，例如 `permission_denied`、`croc_secure_channel_failed`、`lease_expired`；
- 切换 session 再切回，OperationCard 仍显示当前状态；
- 点击 OperationCard 的 Append 后，输入框出现 context chip；
- 用户追加文本并提交后，Agent 基于 `operation.status` 最新事实和用户文本一起响应；
- 不重新创建 transfer。

## 6. 阶段 5I：意图槽位、先决条件与 Agent 工具选择治理

### 6.1 目标

让 Agent 在真实操作前做正确判断：

- 请求缺少关键参数时先反问；
- 用户没有指定落点时不猜测路径；
- 对跨 Node 传输先确认源、目标、落点、覆盖策略；
- 对可能失败的路径先做 preflight；
- 对读/写/维护/外部传输的风险边界表达清楚；
- 工具失败后按事实诊断，不把 capability 不存在、节点离线、权限不足、链路失败混成一个原因；
- 避免把工具选择硬编码为固定流程，改为由任务类型声明 `intent slots` 和 `preconditions`。

### 6.2 更好的建模：Intent Slot + Preconditions + ExecutionGuard

当前文档中“transfer 前必须 preflight”容易被误读成硬编码工具选择。正确实现不应是：

```text
if tool == transfer.create:
  always call transfer.local.stat A/B/C in fixed order
```

目标实现应是：

```text
IntentSchema(transfer_file)
  required_slots:
    source_node
    source_path
    target_node
    target_location
    overwrite_policy
  preconditions:
    source.exists
    source.readable
    target.parent_exists_or_creatable
    target.writable
    target.free_space >= source.size
    transfer_runtime.available_on_both_nodes
  guard:
    if missing slots -> needs_input
    if missing facts -> preflight_required
    if failed facts -> preflight_failed
    if hard invariant failed -> blocked
  admission:
    if ok -> workflow_operation
```

这个模型的分工：

| 层 | 职责 | 不做什么 |
|---|---|---|
| LLM | 从用户自然语言抽取 intent 和候选 slots；解释 guard/preflight 结果；向用户追问。 | 不决定绕过 Center guard/admission。 |
| ExecutionGuard | 校验 slots、preconditions、硬约束和事实依赖；返回 allow / needs_input / preflight_required / preflight_failed / blocked。 | 不执行写操作；不生成自然语言回复；不做调度策略。 |
| PolicyEngine | 根据 execution mode、risk、effect、用户审批策略决定 allow / ask / deny。 | 不负责路径权限、文件是否读过、目标空间等事实先决条件。 |
| ExecutionAdmissionService | 决定 inline、sync_wait、waitable_operation、workflow_operation。 | 不负责业务先决条件和反幻觉硬约束。 |
| Capability Registry | 提供每个 capability 的 preconditions、runtime requirements、风险和输出事实。 | 不执行任务。 |
| Node | 提供本机事实探测和执行能力。 | 不理解 Agent prompt。 |
| Operation Runtime | 管理等待、进度、取消、恢复。 | 不保存领域事实以外的重复模型。 |

因此阶段 5I 的任务不是写死工具链，而是新增或强化：

- `IntentSchema` 或等价结构；
- `ExecutionGuard`；
- `Precondition` 描述；
- `PreflightResult`；
- `GuardDecision` 对 missing slots / missing facts / hard invariant failure 的结构化返回；
- Agent prompt 只解释这些结构化状态，不承担唯一控制责任。

### 6.3 ExecutionGuard

`ExecutionGuard` 是阶段 5I 新增的独立构件，专门处理“不是权限审批，但执行前必须满足”的硬条件。

当前进度：

- 已新增 `src/yequ/runtime/guards/`。
- `CenterExecutionRuntime.execute()` 已在 admission 前调用 `ExecutionGuard`。
- `transfer.create` 第一版 guard 已落地：
  缺 `source_node_id`、`target_node_id`、`source_path`、`target_output_dir` / `target_path`
  时返回结构化 `needs_input`，不创建 `TransferSession`、`Operation` 或 `Job`。
- `transfer.create` 默认要求 `preflight_id`。缺少时返回结构化 `preflight_required`，
  不创建 `TransferSession`、`Operation` 或 `Job`。
- `skip_preflight=true` 但没有 `skip_reason` 时返回结构化 `blocked`。

后续增强：

- runtime label / privilege mismatch 可以继续增加更细的自然语言诊断文案；当前阶段已通过
  `transfer.preflight` 和 capability diagnostics 暴露结构化 runtime facts。

已完成：

- runtime 状态事实校验已接入 `transfer.preflight`：source 侧检查 `transfer.croc.status`
  的 `installed` / `allow_send`，target 侧检查 `installed` / `allow_receive`；
- preflight 事实缓存已落地为 `TransferPreflight`，并把 source/target runtime status
  嵌入对应 fact，便于后续诊断。
- fact freshness 策略已落地：`transfer.preflight` 输出 `observed_at`、`ttl_sec`、`expires_at`，
  Center 将 TTL 限制在 30-300 秒，`transfer.create` 只接受未过期且 intent 匹配的
  `preflight_id`。

它解决的问题包括：

- 写文件前必须先知道目标当前状态；
- 修改配置前必须读过当前配置或持有 preflight snapshot；
- 跨 Node 传输前必须知道源可读、目标可写、空间足够；
- 调用高风险能力前必须有明确用户意图槽位；
- 能力需要 runtime label / privilege 时必须能匹配在线 runtime；
- 避免 LLM 因幻觉路径、幻觉文件名、幻觉参数而直接写入。

与 Claude “写前必须读”类似，本项目的通用规则应建模为 guard rule，而不是 prompt 句子：

```json
{
  "rule_id": "file_write_requires_prior_read_or_stat",
  "applies_to": ["*.filesystem.write_text", "*.filesystem.remove", "*.filesystem.move"],
  "requires": [
    {
      "fact": "target.parent_writable",
      "source": "preflight",
      "max_age_sec": 120
    },
    {
      "fact": "target.snapshot_known",
      "source": "read_or_stat",
      "max_age_sec": 300
    }
  ],
  "on_missing": "preflight_required"
}
```

Guard 输入：

- normalized tool call；
- extracted intent slots；
- capability contract；
- runtime/node facts；
- recent AgentRun facts；
- preflight facts；
- user execution mode。

Guard 输出：

| decision | 含义 |
|---|---|
| `allow` | 先决条件满足，可进入 PolicyEngine / Admission。 |
| `needs_input` | 缺用户语义槽位，例如目标目录、覆盖策略、具体文件。 |
| `preflight_required` | 需要读取事实，例如 stat、runtime status、free space。 |
| `preflight_failed` | 事实已知且不满足，例如权限不足、空间不足。 |
| `blocked` | 硬约束不允许，例如未读先写且用户未授权跳过。 |

推荐目录：

```text
src/yequ/runtime/guards/
  __init__.py
  service.py
  schemas.py
  rules.py
  facts.py
  preflight.py
```

执行顺序：

```text
normalize command
  -> resolve capability / intent
  -> ExecutionGuard.evaluate()
  -> PolicyEngine
  -> ExecutionAdmissionService
  -> runtime handler
```

`ExecutionGuard` 不应成为新大总管。它只回答“现在能不能进入执行路径，以及缺什么事实”，不创建 Job，不创建 Operation，不写自然语言。

#### 6.3.1 ExecutionGate 门面

`ExecutionGuard` 不合并 `PolicyEngine`。但是为了避免调用方散落地调用 Guard、Policy、Admission，
可以新增一个轻薄 `ExecutionGate` 门面：

```text
ExecutionGate.evaluate(command)
  -> GuardDecision
  -> PolicyDecision
  -> ExecutionPlan
```

边界固定：

| 构件 | 保留独立原因 |
|---|---|
| `ExecutionGuard` | 事实先决条件和硬约束会频繁依赖 capability contract、recent facts、preflight，不等同于用户授权策略。 |
| `PolicyEngine` | 风险矩阵、execution mode、approval ask/deny 是授权策略，未来可能接用户角色、时间窗口、设备组策略。 |
| `ExecutionAdmissionService` | 决定同步/异步/Operation/workflow，是调度策略。 |
| `ExecutionGate` | 只组合三者结果，提供统一入口，不拥有具体规则。 |

如果把 PolicyEngine 合并进 ExecutionGuard，会让 Guard 同时承担事实、授权和部分调度，形成新的大总管。
阶段 5I 禁止这种合并。

### 6.4 Agent 系统提示词治理

当前进度：

- [x] DeepSeek provider system prompt 已明确要求使用 `capability.search` /
  `capability.describe` 做结构化候选缩小；
- [x] 已明确传输任务不得猜 `source_node_id`、`target_node_id`、`source_path`
  或落点路径；
- [x] 已明确 `transfer.create` 前应使用 `transfer.preflight`，并尊重
  `preflight_id`、权限失败、runtime 不可用和 stale facts；
- [x] 已明确 `dispatchable=false` / `unavailable_reasons` 是权威运行时事实；
- [x] 已将这些规则从 DeepSeek 私有 prompt 文本抽出到 provider 无关的
  `yequ.agent.prompt_policy.render_agent_system_prompt()`；DeepSeek 只负责调用共享 policy。

后续 provider 系统化改造仍需要 provider registry、模型发现和连通性检测；但 Agent 行为规则不再绑定 DeepSeek 文件。

Agent system prompt 必须明确以下规则：

1. 缺少任务必需参数时必须提问。

   对 `transfer.create`，以下信息不完整时不得执行：

   - `source_node_id`
   - `target_node_id`
   - `source_path`
   - `target_output_dir` 或 `target_path`
   - `resume_mode`

   用户只说“传到 Linux”时，Agent 必须询问目标目录，或者先查询用户常用目录后给出候选，不得直接猜 `/home/user/` 或 `/tmp`。
   用户没有说明目标冲突处理方式时，Agent 必须询问是覆盖、续传/复用部分文件，还是遇到已有文件就失败；
   不得把 `resume_mode=fail_if_exists`、`resume` 或 `overwrite` 当作隐式默认值。

2. 执行前先做事实探测。

   对传输任务，Agent 在调用 `transfer.create` 前必须使用 Center 提供的 transfer preflight 或 local stat 能力确认：

   - 源路径存在；
   - 源路径可读；
   - 目标目录存在或父目录可写；
   - 目标空间足够；
   - 覆盖策略明确；
   - 两端 transfer runtime 可用。

3. 工具选择必须以 capability context 为准。

   - 不得根据名字前缀猜 Node；
   - 不得调用未暴露的工具名；
   - 多个来源存在时，使用 `source_id` 或 `node_id` 消除歧义；
   - Windows 任务优先使用 Windows node capability，Linux 任务优先使用 Linux node capability；
   - 当用户任务本身跨 Node 时，使用 Center workflow meta tool，不直接手写底层 send/receive。

4. 长任务必须接受 Operation 语义。

   - `waiting_operation` 是正常结果，不是失败；
   - Agent 不应轮询 `transfer.status` 消耗 token；
   - 终态总结通过 Append context chip / operation observation 完成。

5. 多媒体和 artifact 必须区分展示与理解。

   - `artifact.present` 可以展示图片/文件；
   - 展示不等于视觉理解；
   - 若没有 image-understanding capability，Agent 不得声称“看懂了图片内容”。

### 6.5 Center 结构化约束

Prompt 规则必须有 Center 约束配合，否则会变成软建议。

`transfer.create` 第一版结构化约束应由 `IntentSchema(transfer_file)` + `ExecutionGuard`
或等价 precondition 模型驱动：

- `target_output_dir` 和 `target_path` 不能同时为空；已完成；
- `target_path` 只表示最终落点事实。croc receive 只能接收 `output_dir`，
  Center 已将 `target_path=/tmp/name` 正规化为 `output_dir=/tmp`；如果
  `target_path` 的文件名与源文件名不同，Center 必须拒绝，因为当前 croc
  workflow 不支持传输时改名；
- `source_node_id`、`target_node_id`、`source_path` 必填；已完成；
- `resume_mode` 不能为空；已完成。工具 schema、`ExecutionGuard` 和 `transfer.preflight` 均不再接受隐式默认值；
- 对跨 Node 传输，默认要求 preflight 已通过；已完成 `preflight_id` 绑定；
- 如果 preflight 未执行或失败，返回 `preflight_required` / `preflight_failed`，不创建 Operation；缺失、过期、intent mismatch、preflight failed 均已阻断；
- 如果用户显式选择跳过 preflight，必须在 input 中有 `skip_preflight=true` 和 `skip_reason`；第一版已完成 skip reason 约束。
- croc workflow 已改为 sender-first：先启动发送端并等待其被 Node 领取，再启动接收端；这是 croc room 建立的执行先决条件，不能由 Agent prompt 自行协调。
- Node 的 transfer local stat 必须用真实探针判断目录可写性；Linux 目录不能通过普通写模式 `open(directory)` 判断 writable。

新增或强化 Center meta tool：

| 工具 | 作用 |
|---|---|
| `transfer.preflight` | 聚合两端 `*.transfer.local.stat`、`*.transfer.croc.status`、目标空间和覆盖策略，返回是否允许创建 transfer；已完成并持久化 `TransferPreflight`。 |
| `transfer.create` | 只在参数明确且 `preflight_id` 匹配、未过期、allowed=true 后创建 TransferSession/Operation；显式 `skip_preflight + skip_reason` 可绕过。 |
| `operation.status` | 查询长任务事实，不直接承担业务诊断。 |

### 6.6 Agent 调试诊断

前端 Prompt Context 面板需要能解释“Agent 为什么这样选工具”。

新增诊断字段：

```json
{
  "tool_selection_context": {
    "routing_mode": "auto",
    "available_meta_tools": ["node.list", "capability.search", "..."],
    "nodes": [
      {
        "node_id": "winClient",
        "platform": "Windows",
        "online": true,
        "capability_count": 50
      }
    ],
    "transfer_policy": {
      "requires_target": true,
      "requires_preflight": true,
      "default_resume_mode": null,
      "requires_explicit_resume_mode": true
    },
    "guard": {
      "last_decision": "allow | needs_input | preflight_required | preflight_failed | blocked",
      "missing_slots": [],
      "missing_facts": [],
      "failed_preconditions": []
    }
  }
}
```

要求：

- system prompt 和 structured context 继续对前端透明；
- 不把 token 密钥、croc code、relay pass 暴露到前端；
- 失败时能看到是 prompt 约束不足、schema 不足、preflight 不足还是 Node capability 输出不足。

### 6.7 Capability Query / Projection 优化

当前进度：

- 已将 capability source 合同字段纳入 Center registry：
  `supports_progress`、`supports_cancel`、`supports_resume`、`progress_contract`、
  `preconditions`、`required_intent_slots`。
- 已增强 `capability.search`：
  支持 `node_id`、`platform_os`、`effect`、`risk`、`runtime_kind`、
  `runtime_labels`、`supports_progress`、`supports_cancel`、`supports_resume`、
  `preflight_supported`、`artifact_input`、`artifact_output`、`projection`、`limit`。
- 已增强 `capability.describe`：
  支持 `sections` 和 `projection`，可以只返回 `schema`、`preconditions`、
  `examples`、`diagnostics`、`sources` 或 `runtime` 相关信息。

这一步已经解决“查出一坨信息”的基础问题。`capability.search` 结果已补齐“为什么匹配”
和“为什么不可调度”的诊断解释，减少 Agent 通过多次试错理解候选能力。

阶段 5I/5J 的目标是把 capability 查询做成结构化、可筛选、可投影的 read model。

`capability.search` 输入应支持：

```json
{
  "query": "transfer file to linux",
  "node_id": "linux-node-01",
  "platform_os": "linux",
  "effect": "read | write | external",
  "risk": "maintenance",
  "capability_type": "function",
  "supports_progress": true,
  "preflight_supported": true,
  "artifact_input": false,
  "artifact_output": true,
  "runtime_kind": "privileged",
  "runtime_labels": ["linux", "transfer"],
  "limit": 5,
  "projection": "summary | invoke_ready | schema | diagnostics"
}
```

`projection` 语义：

| projection | 返回内容 |
|---|---|
| `summary` | id/name/node/platform/短描述/risk/effect，默认。 |
| `invoke_ready` | 加入必填 input 摘要、source_id、node_id、runtime requirements。 |
| `schema` | 返回完整 input/output schema。 |
| `diagnostics` | 返回不可调度原因、runtime mismatch、最近失败、preconditions。 |

要求：

- 默认 limit 小，默认 projection 短；已完成；
- 完整 schema 只能按需取；已完成；
- `capability.describe` 支持 `sections` 参数，例如 `["schema", "preconditions", "examples"]`；已完成；
- 搜索结果必须包含“为什么匹配”和“为什么不可调度”；已完成：
  `match_reasons` 标明 query/filter 命中来源，source 级 `dispatchable` /
  `unavailable_reasons` 标明 node offline、source inactive、source status 异常等不可调度原因；
- `artifact_input` / `artifact_output` 已作为 definition 级过滤条件接入
  `capability.search` 和 Agent 可见工具 schema；
- 不再把全部 capability context 一次性塞给 LLM。

#### 6.7.1 与未来 Tool RAG 的边界

当前阶段的实现不命名为 Tool RAG，也不等同于已经完成 Tool RAG。它的正确边界是：

```text
Capability Registry
  -> Capability Index / Structured Discovery
  -> Capability Context Builder
  -> Agent 可见的少量相关工具上下文
```

本阶段必须完成的是确定性、可审计、可测试的能力发现层：

| 层 | 当前阶段职责 | 禁止承担的职责 |
|---|---|---|
| Capability Registry | 保存 capability definition/source、Node、runtime、risk/effect、schema、preconditions、contract diagnostics。 | 不做自然语言语义召回，不根据相似度决定是否可执行。 |
| Structured Discovery | 按 `node_id`、`platform_os`、`effect`、`risk`、`runtime_kind`、`runtime_labels`、`supports_*`、`artifact_*` 等字段过滤，并按 projection 控制返回量。 | 不把 embedding 相似度结果当成执行授权，不绕过 Guard/Policy/Admission。 |
| Capability Context Builder | 把结构化查询结果压缩为 Agent 当前任务需要的工具摘要。 | 不把全部 Node capability 塞进 prompt，不生成伪 Agent 回复。 |
| ExecutionGuard | 基于 intent slots、preconditions、preflight facts 阻断不完整或不安全的调用。 | 不接受 Tool RAG 的语义匹配结果作为事实满足证明。 |

未来 Tool RAG 只能作为 **候选召回增强层** 接入：

```text
用户任务 / 当前上下文
  -> Tool RAG semantic retrieval
  -> 候选 capability ids / examples / parameter patterns
  -> Structured Discovery 二次过滤
  -> ExecutionGuard / PolicyEngine / Admission
  -> Runtime execution
```

因此 Tool RAG 不会取代以下事实来源：

- capability registry；
- Node runtime/source dispatchability；
- input/output schema；
- preflight facts；
- PolicyEngine；
- ExecutionGuard；
- Operation Runtime。

Tool RAG 可以增强：

- 用户自然语言到候选 capability 的语义召回；
- 历史成功案例、失败案例和参数模板检索；
- 大量工具存在时的排序和解释；
- 多步骤任务的候选能力组合建议。

Tool RAG 不允许直接增强：

- 是否允许执行；
- 是否需要审批；
- 是否可调度到某个 Node/runtime；
- 是否满足写前读、路径权限、空间、runtime 可用等硬约束；
- 是否创建 Operation、Job 或 TransferSession。

第一版继续保持确定性 DB 查询。未来引入 embedding/vector retrieval 时，也必须把结果降级为候选集，
再进入 `capability.search` / `capability.describe` / `ExecutionGuard` 的结构化验证链路。

### 6.8 max_steps 调整

当前 Agent API 的 `max_steps` 默认已从 20 调整到 40，上限保持 100。20 对以下任务偏少：

- 多 Node 文件定位；
- transfer preflight；
- 长任务 resume 后核验；
- 复杂系统诊断；
- 后续 SubAgent 前的 parent/child 交互。

阶段 5I 要做两件事：

1. 默认值调整。

   - [x] 普通会话默认从 20 调整到 40；
   - 上限保持 100；
   - [x] Console 输入区已暴露 per-turn step budget 选择器：40 / 60 / 80 / 100；
   - `waiting_operation` 不计入后续 resume 的旧 loop 消耗。

2. 结构性减步。

   - 通过 `transfer.preflight` 聚合多个 local stat，减少 LLM 自己绕工具；
   - 通过结构化 `capability.search` 返回更高质量候选，减少试错；
   - 通过 Operation progress/resume observation 减少反复查询。

验收不能只看“max_steps 变大”。如果任务仍然频繁打满 40，说明工具选择和 preflight 聚合仍有问题。

### 6.9 验收

- 用户说“把 Win 上这个文件传到 Linux”但未给目标目录时，Agent 必须反问或列出候选，不得直接执行；
- 用户给出明确目标目录时，Agent 先执行 preflight，再调用 `transfer.create`；
- 目标目录无权限时，Agent 在 preflight 阶段得到 `permission_denied`，不会启动 croc；
- 源文件不存在时，Agent 在 preflight 阶段得到 `source_not_found`，不会启动 croc；
- 如果 transfer 进入 `waiting_operation`，Agent 本轮停止，不再轮询；
- Prompt Context 能显示 transfer policy 和可用工具事实。
- `ExecutionGuard` 能阻断未读先写、缺目标目录、缺 preflight facts 等硬约束；
- `capability.search` 能按 node/platform/effect/risk/projection/limit 筛选返回；
- LLM 不需要接收完整无关 capability 列表即可完成工具选择。

## 7. 阶段 5J：Capability 合同与 Node 描述治理

### 7.1 目标

让 Node capability manifest 成为 Agent 工具选择的可靠事实源。Agent 的“提示词系统”不只是系统 prompt，也包括：

- capability name；
- description；
- input schema；
- output schema；
- risk/effect；
- runtime requirements；
- resource keys；
- conflict policy；
- progress/cancel/resume 支持声明；
- preflight 支持声明。

### 7.2 Capability 描述标准

每个面向 Agent 的 capability 必须满足：

1. 名称平台清晰。

   - Windows 能力使用 `windows.*`；
   - Linux 能力使用 `linux.*`；
   - 跨 Node / Center workflow 使用无平台前缀，例如 `transfer.create`；
   - 不得把 Windows 能力继续注册为泛化 `system.*`。

2. description 必须说明实际边界。

   示例：

   ```text
   Read metadata for a local filesystem path on this Linux node.
   This does not read file content. It reports existence, kind, readability,
   writability, size, mtime, and optional sha256 for files.
   ```

3. input schema 必须表达必填参数。

   不允许把真正必填的 `path`、`output_dir`、`name` 留成 optional 后靠 Agent 猜。

4. output schema 必须稳定。

   JSON 字段可以是 object/list/string，但同一 capability 的语义字段必须稳定。例如 transfer receive 成功必须返回实际 `received_path`、`size_bytes`、`sha256`。

5. progress/cancel/resume 显式声明。

   新增建议字段：

   ```json
   {
     "supports_progress": true,
     "progress_contract": "transfer_progress_v1",
     "supports_cancel": true,
     "supports_resume": true,
     "preflight_supported": true,
     "preconditions": [
       "source_path.exists",
       "source_path.readable",
       "target_parent.writable"
     ],
     "required_intent_slots": [
       "source_node",
       "source_path",
       "target_node",
       "target_location"
     ]
   }
   ```

### 7.3 Node 合同更新

Linux Node 和 WinNode 合同都要更新：

- `*.transfer.local.stat` 是 transfer preflight 的基础能力；
- `*.transfer.croc.status` 必须报告 croc 可执行性、版本、可用 flag、send/receive 开关、临时目录和错误；
- `*.transfer.croc.send/receive` 必须支持 `transfer_id` 幂等；
- `job.cancel` 必须能终止 croc 子进程并上报终态；
- `job.event` progress 事件必须脱敏；
- permission denied 必须是稳定错误码，不只返回系统错误文本；
- output 不得把旧传输记录或错误文件误报成当前传输结果。

### 7.4 Capability Lint

新增 capability manifest lint，用于 Center 或 Node 开发阶段检查：

当前进度：

- [x] Center registry 已提供第一版非破坏性合同 lint；
- [x] `node.status` / `capability.describe` 的 source summary 或 diagnostics 会返回
  `contract_issues`；
- [x] lint 结果包含 `severity`、`code`、`message`，第一版只诊断不拒绝注册；
- [x] 已覆盖 description、agent_description、risk/effect、input/output schema、
  execution requirements、resource keys、conflict policy、long task progress/cancel、
  transfer send/receive preflight/preconditions/intent slots/resume 等检查；
- [x] 已增加 `yequ capabilities lint`，可在 Node 注册后用 Center diagnostics 做开发期合同检查。

检查项：

- [x] 名称前缀是否符合平台；
- [x] risk/effect 是否存在；
- [x] 写操作是否有 resource_keys；
- [x] `conflict_policy=serialize` 时是否有明确 resource key；
- [x] 长任务是否声明 progress/cancel；
- [x] transfer send/receive 是否声明 preflight/preconditions/required intent slots/resume；
- [x] description 是否为空或过短；
- [x] input schema 是否为 object 且明确 `additionalProperties`；
- [x] output schema 是否存在。

第一版已提供 Center registry diagnostics、meta capability diagnostics 和 CLI 检查入口。
Node 侧构建期 lint 可以后续继续补，但不再阻塞阶段 5J。

### 7.5 验收

- `capability.describe` 能让 Agent 区分 `windows.transfer.croc.send` 和 `linux.transfer.croc.receive` 的平台、runtime、风险、输入输出；
- Linux Node 和 WinNode 的 transfer 能力描述一致；
- Agent 不再把 capability prefix 当作唯一事实；
- 新增/修改 Node capability 前，可以用 lint 或测试发现合同缺口；
- 阶段 5I 的 preflight 能依赖这些合同稳定运行。
- `capability.search` / `capability.describe` 的 projection 能减少无关上下文返回。

## 8. 阶段 5K：Linux Node 能力扩展

### 8.1 目标

当前仓库内 `nodes/linux/yequnode` 就是当前部署的 Linux Node 源码。阶段 5K 直接在该目录推进，不再把 Linux Node 当成外部未知项目。

目标是让 Linux Node 具备足够基础能力，避免 Agent 为常见 Linux 运维任务绕路。

本阶段 Node 相关工作由本项目执行，不再写成“由 Linux Node 开发方另行实现”。如果本机 Windows
环境缺 Linux Rust toolchain，可以在 WSL 中安装 Rust/Cargo/build-essential 后完成编译和测试。

Windows Node 相关 transfer/artifact/progress/cancel 合同也由本阶段同步落地，路径：

```text
E:\yequdesu_project\YeQu-Gateway-Win\Node-winClient
```

### 8.2 第一批能力补齐

文件系统：

- `linux.filesystem.write_text`
- `linux.filesystem.read_bytes` 或 artifact upload 替代路径说明；
- `linux.filesystem.copy`
- `linux.filesystem.move`
- `linux.filesystem.remove`
- [x] `linux.filesystem.mkdir`：已新增目录创建能力，用于传输落点准备和 artifact 下发落点准备；
- `linux.filesystem.chmod`
- `linux.filesystem.chown`
- [x] `linux.filesystem.disk_usage`：已新增文件系统容量与可用空间查询能力；
- [x] `linux.filesystem.hash`：已新增 SHA-256 文件哈希能力，用于传输后独立完整性校验；

Artifact / Center 文件下发：

- [x] `linux.artifact.download_file`：已新增 Center artifact 到 Linux 本地路径的下载原语；
- [x] `windows.artifact.download_file`：已新增 Center artifact 到 Windows 本地路径的下载原语；
- `linux.artifact.register_local_file`（仅 Center 同机或信任 runtime 可启用）
- `linux.artifact.upload_file` 强化大文件限制、权限错误和 content type；
- [x] `artifact.deploy.preflight` Center meta tool 第一版：检查 artifact 可用性和目标路径写入事实，返回短 TTL `preflight_id`；
- [x] `artifact.deploy` Center workflow 第一版：委托到目标 Node 的 `artifact.download_file` 并创建 Job Operation；

进程与服务：

- `linux.process.kill`
- `linux.process.tree`
- `linux.service.restart` 已有，但需补 `start` / `stop` / `enable` / `disable`
- `linux.service.logs`

网络：

- `linux.network.ping`
- [x] `linux.network.dns_lookup`：已新增从 Linux Node 视角解析 hostname 的能力；
- `linux.network.http_probe`
- [x] `linux.network.port_check`：已新增从 Linux Node 视角检查 TCP host:port 可达性的能力；
- `linux.network.speed_probe`（可选）

包和系统：

- `linux.package.install` / `remove` / `update_cache`（默认需要审批）
- `linux.user.current`
- `linux.runtime.status`
- `linux.command.exec` 不作为默认开放能力；如果加入，必须强审批、强 runtime label、强审计。

传输辅助：

- `linux.transfer.local.stat` 强化 sha256、free space、parent writable；
- `linux.transfer.croc.progress` 或通过 job event/read model 投影进度；
- `linux.transfer.croc.reconcile` 返回 ledger 当前事实。

### 8.3 权限模型

每个新增能力必须声明：

- `runtime_kind`
- `labels`
- `risk`
- `effect`
- `resource_keys`
- `conflict_policy`
- 是否需要 sudo/root；
- sudo 失败时稳定错误码；
- 是否支持 preflight；
- 是否支持 cancel/progress/resume。

普通 daemon 用户能完成的能力不能声明 privileged。需要 root 的能力不能在普通 runtime 上注册。

### 8.4 开发方式

- 优先在 Windows 本仓库编辑；
- Rust 编译如果本机 Windows 缺 Linux toolchain，可以在 WSL 中安装 cargo/rustup/build-essential；
- 如果 WSL 环境缺失，可以直接安装，不把“本机环境没有 Rust”作为阻塞；
- 每批能力至少跑：
  - Rust format/check；
  - 能力 manifest lint；
  - 针对新增 capability 的最小单元或集成测试。

### 8.5 验收

- Agent 能直接完成常见 Linux 查询、文件落点准备、权限探测、传输核验；
- 传输前不再需要绕多个粗糙 filesystem 能力；
- 权限不足在 preflight 阶段暴露；
- 新能力不会绕过 Center policy 和 Operation Runtime；
- `capability.describe` 足以说明每个能力何时可用、需要什么权限、会产生什么副作用。

## 8.6 合同文档交付

阶段 5K 完成前必须同步以下文档：

| 文档 | 必须覆盖 |
|---|---|
| `YQP-Node-Protocol.md` | manifest 扩展字段、job.event progress 结构。 |
| `docs/node-capability-contract.md` | 跨平台 capability 精细度、preflight/progress/cancel/resume、错误码、artifact/transfer 合同。 |
| `docs/linux-node-development-contract.md` | Linux Node 当前实现、权限模型、新增能力清单、验收方法。 |
| Windows Node README / capability 文档 | Windows Node 新增能力、artifact download、transfer progress/cancel、manifest 字段。 |

合同验收标准：

- 合同不得只描述“功能名”，必须描述 input/output/error/preflight/progress/cancel/risk/effect/runtime；
- 合同中的每个 MUST 都能对应到代码检查、单元测试、集成测试或手动验收步骤；
- 合同不得要求 Node 理解 Agent prompt；
- 合同不得让 Node 绕过 Center policy、ExecutionGuard、Operation Runtime。

## 9. 阶段 5H-5K 总体验收剧本

### 9.1 明确传输

用户请求：

```text
把 winClient 上 E:\Kazumi_windows_1.9.3.zip 传到 linux-node-01 的 /tmp/yequ-transfer，overwrite。
```

期望：

- Agent 调用 `transfer.preflight`；
- preflight 成功；
- Agent 调用 `transfer.create`；
- 前端 OperationCard 显示进度；
- Agent 本轮进入 `waiting_operation` 后停止；
- 传输完成后点击 OperationCard 的 Append，将 operation context chip 插入输入框；
- 用户可追加文本后提交；
- Agent 总结 size/hash/目标路径。

### 9.2 不明确传输

用户请求：

```text
把 Win 上那个 zip 传到 Linux。
```

期望：

- Agent 先查找候选文件或询问具体源文件；
- Agent 询问目标目录；
- 不得直接猜 `/home/user/` 或 `/tmp` 并启动传输。

### 9.3 权限失败

用户请求：

```text
把文件传到 linux-node-01 的 /root。
```

期望：

- `transfer.preflight` 返回 `permission_denied`；
- 不创建 TransferSession；
- 不启动 croc；
- Agent 基于事实说明目标目录无权限，并询问替代目录。

### 9.4 长任务透明

传输 100MB 以上文件。

期望：

- Activity 面板可观察；
- 不需要用户滚动聊天记录找状态；
- 若 Node 提供字节进度，显示百分比；
- 若 Node 只提供 keepalive，显示不确定进度条；
- Center 重启后 scanner 能恢复 Operation 投影。

### 9.5 Center 下发 Artifact 到 Node

用户请求：

```text
把 Center artifact id_x 下载到 linux-node-01 的 /tmp/yequ-transfer/a.bin。
```

期望：

- Agent 或 Center 确认 artifact 存在；
- `artifact.deploy.preflight` 检查目标路径，并把 `preflight_id` 绑定到 `artifact.deploy`；
- 目标 Node 执行 `<platform>.artifact.download_file`；
- OperationCard 展示进度或至少 running 状态；
- 完成后 size/hash 一致。

## 10. 不做事项

本阶段不做：

- SubAgent；
- Tool RAG embedding；
- 外部 MQ；
- 自动替用户选择传输目标目录；
- 用自然语言关键词硬编码 resume；
- 让 Artifact 层承担长任务等待；
- 把所有 Node stdout/stderr 注入 Agent prompt；
- 伪造 croc 字节级进度。
- 用固定工具调用序列替代 precondition/admission 建模。
- 让 `ExecutionGuard` 创建 Job、Operation 或自然语言回复。

## 11. 与阶段 6 的关系

阶段 6 SubAgent 依赖以下能力：

- parent Agent 能等待 child Operation；
- Activity 面板能展示多个运行中 Operation；
- Agent 能区分等待、失败、权限不足、参数缺失；
- capability 描述足够稳定，不会让子 Agent 继承错误工具选择；
- resume observation 可靠。

如果阶段 5H-5K 不做，SubAgent 会继承当前工具选择混乱、长任务不可观察、preflight 不可靠和 Linux Node 能力不足的问题。阶段 6 必须在本阶段验收后开始。
