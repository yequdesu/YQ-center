# yq-croc 插件运行时计划

状态：确定执行方案
日期：2026-07-01

## 1. 结论

YeQu 的大文件跨 Node 传输主方案确定为 `yq-croc` 插件运行时。

`yq-croc` 不是把 croc CLI 更深地嵌入 Center，也不是把 croc 协议重写进 Windows Node 或 Linux Node。`yq-croc` 是一个独立发布、可选安装、由 Node 插件适配调用的传输运行时。它基于 croc Go 源码，保留 croc 的 PAKE、relay、跨 NAT、断点恢复和跨平台能力，同时新增 YeQu 需要的结构化事件、确定性同步点、稳定错误码和可诊断状态。

Center 不 import `yq-croc` 源码，不直接执行 `yq-croc`。Center 只依赖 Node 注册的 capability 合同：

- `<platform>.transfer.croc.status`
- `<platform>.transfer.croc.send`
- `<platform>.transfer.croc.receive`
- `<platform>.transfer.croc.reconcile`
- `<platform>.transfer.local.stat`

Node 主进程不实现 croc 协议。Node 只实现本地插件适配层：配置读取、路径策略、子进程管理、NDJSON 事件读取、YQP `job.event` 上报、ledger 持久化和取消处理。

## 2. 使用场景与部署场景

YeQu 的目标场景是个人基础设施控制中心：

- 多个 Node 分布在不同物理位置；
- Node 所在网络各不相同，通常处于 NAT、防火墙或家庭/移动网络之后；
- Node 与 Center 的稳定通道是出站 HTTPS/YQP；
- Center 不能反连 Node；
- Node 之间不能假设有公网可达地址；
- Agent 不能直接控制 Node 或长期轮询底层传输细节；
- Center 负责调度、审计、进度聚合、取消和恢复；
- 大文件数据面不应走 YQP JSON、Agent prompt 或 Center API artifact 小文件路径。

因此默认数据面必须是 relay-capable。`rclone serve sftp`、临时 SFTP、HTTP receiver、`advertise_host` 等直连方案只适合 LAN、公网 IP 或 overlay 网络，不符合 YeQu 默认部署模型。

`yq-croc` 数据面要求：

- sender 和 receiver 都能主动连接到同一个 croc relay；
- relay 可以使用 croc 公共默认 relay；
- relay 也可以由用户自建，或由 YeQu 配置 relay pool；
- relay 只转发 croc 加密后的数据，Center 不接触文件内容；
- 如果 relay 部署在 Center API 同一台公网机器上，该机器会承载数据面带宽；
- 如果要避免 Center 公网入口服务器带宽压力，relay 必须部署到独立公网机器或独立 relay pool。

## 3. Center 唯一调度原则

YeQu 的事务调度者有且只有 Center。`yq-croc` 必须完全集中调度适配，不能引入 Node 自主事务。

固定原则：

- Node 不能主动创建 TransferSession；
- Node 不能主动创建新的 transfer attempt；
- Node 不能在 Job 失败后自行重启 send/receive；
- Node 不能自行切换 relay、生成新 code、改变 source/target path；
- Node 不能绕过 Center 直接通知另一端 Node；
- Node 不能因为本地 ledger 显示 `resumable=true` 就自行恢复传输；
- Node 只能执行 Center 通过 YQP 下发的 Job，并在该 Job 生命周期内上报事件、保存本地事实和响应取消；
- `yq-croc` 只能作为 Node 当前 Job 的本地子进程运行，不拥有 YeQu 调度权。

因此 `reconnect` 和 `resume` 必须分层：

| 机制 | 触发者 | 是否允许 Node 自主执行 | 边界 |
|---|---|---|---|
| 进程内 reconnect | `yq-croc` 当前进程 | 允许 | 只能发生在同一个 Center Job 内，不改变 code、relay、source、target、attempt。 |
| 跨 Job resume | Center | 不允许 Node 自主执行 | 必须由 Center 创建新的 attempt，并重新向两端下发 send/receive Job。 |
| relay 切换 | Center | 不允许 Node 自主执行 | 只能在新的 attempt 中由 Center 选择并下发。 |
| code 轮换 | Center | 不允许 Node 自主执行 | 只能在新的 attempt 中由 Center 生成并下发。 |

Node ledger 是事实存储，不是调度队列。`reconcile` 只能把本地事实报告给 Center，不能触发本地继续传输。

### 3.1 架构适配性决策

`yq-croc` Center schedule-based 大文件传输方案判定为符合 YeQu Center/Nodes 的实际部署模型，确定继续推进。

该结论成立的硬性条件如下：

- Center 继续作为唯一事务调度者，TransferSession、attempt、relay 选择、code 生成、取消和跨 Job resume 全部由 Center 创建和审计。
- Node 只执行 Center 下发的当前 Job，不因为本地 ledger、进程退出码或 relay 错误自行创建新事务。
- `yq-croc` 保持独立插件运行时，不链接 Center，不链接 Node 主进程，不读取 YeQu token，不调用 YQP。
- Node 与 `yq-croc` 的唯一运行时接口是 request JSON、NDJSON event stream、退出码和本地 ledger。
- Center 与 Node 的唯一协作接口是 capability 合同、YQP Job、YQP `job.event`、YQP `job.finished` 和 `reconcile`。
- 公共 relay 模式不承诺单次 attempt 必达；它只承诺可预检、可观测、可失败归因、可由 Center 发起新 attempt 恢复。
- 要承诺高成功率，部署合同必须允许配置至少一个受控 relay 或 relay pool；该 relay 可以独立于 Center API 服务器部署，以避免占用 Center 公网入口带宽。

适配性判断：

| 维度 | 决策 | 原因 |
|---|---|---|
| 部署模型 | 适配 | 两端 Node 只需要出站连接 Center 和同一 relay，不要求公网 IP、LAN、端口映射或 Center 反连。 |
| Center 唯一调度 | 适配 | `yq-croc` 不拥有事务权；attempt、resume、relay 切换和 code 轮换全部在 Center 建模。 |
| YQP 协议 | 适配 | 现有 poll-based Job、`job.event`、`job.finished`、cancel、reconcile 足够承载传输控制面。 |
| Operation Runtime v2 | 适配 | `transfer.create` 已是 workflow Operation，适合承载 sender/receiver fan-out、进度聚合和 waitable 结果。 |
| 稳定性 | 有条件适配 | 不能继续使用 croc CLI stderr 文本解析；必须使用源码级 hook、结构化事件、Center-owned resume 和本地 ledger。 |
| 跨平台 | 适配 | croc Go 运行时覆盖 Windows/Linux；YeQu 只需约束二进制分发、路径策略、编码和子进程事件读取。 |
| 解耦 | 适配 | Center、Node、`yq-croc` 通过版本化合同协作；`yq-croc` 可选安装，不污染核心调度模型。 |
| 协作复杂度 | 可控 | 以 capability contract、event schema、request schema、ledger schema 和合同测试作为边界，不允许隐式行为。 |

该方案覆盖的 YeQu 文件调度场景：

- Windows Node 到 Linux Node；
- Linux Node 到 Windows Node；
- 任意两个跨 NAT Node 之间的大文件传输；
- Center 通过专用本地 Node 或 transfer-worker 参与发送/接收的 Center-adjacent 传输；
- 公共 relay、私有 relay、relay pool 三种 relay 策略；
- 单次传输失败后的 Center 调度式断点恢复。

该方案不承担以下职责：

- 不把大文件内容通过 Center API、YQP JSON、Agent prompt 或普通 artifact 通道中转；
- 不把 Node 变成可自主发起业务事务的 actor；
- 不把 relay 生命周期纳入 Center 核心进程；
- 不保证公共 relay 的可用性，只保证公共 relay 失败可被诊断和恢复调度。

## 4. 为什么必须基于 croc 源码

现有 CLI 包装路径的失败点已经明确：

- `Code is:` 发生在 `sendCollectFiles()` 之后、sender 连接 relay 之前，不是 room ready；
- 靠 stderr 文本解析无法稳定区分 room ready、PAKE、文件开始、字节进度、断线恢复和完成；
- progressbar 终端输出不是稳定机器接口；
- Windows / Linux shell、stdin、编码、终端能力、剪贴板、副作用参数都存在跨平台差异；
- Node 只能通过进程输出猜测内部状态，Center 只能用 sleep 或脆弱事件同步。

croc 源码已经提供可复用基础：

- Go module：`github.com/schollz/croc/v10`；
- 核心包：`src/croc`；
- relay 包：`src/tcp`；
- `croc.NewCtx()` 支持 context cancellation；
- `croc.Options` 覆盖 relay、relay password、overwrite、compression、hash、local discovery、multiplexing 等控制项；
- MIT license 允许 vendor、修改和二次发布。

源码级集成的目标不是改 croc 的安全协议，而是增加 YeQu 所需的稳定运行时接口。

## 5. 运行时边界

### `yq-croc` 进程

`yq-croc` 是独立二进制，提供以下子命令：

```text
yq-croc version
yq-croc probe --json
yq-croc relay-probe --relay <addr> --pass-env <env>
yq-croc send --request <request.json>
yq-croc receive --request <request.json>
```

`send` 和 `receive` 必须只通过 stdout 输出 NDJSON 事件。stderr 只用于本地调试日志，且不得输出 code、relay password、token 或路径策略敏感信息。

`yq-croc` 不知道 Center token，不调用 YQP，不读 YeQu 数据库，不写 Center Timeline。它只负责执行传输和输出事件。

### Node 插件适配层

Node 插件适配层负责：

- 读取 Node 配置；
- 检查 `yq-croc` 二进制存在、可执行、版本兼容；
- 执行 relay probe；
- 执行本地路径 preflight；
- 启动 `yq-croc send/receive`；
- 读取 NDJSON 事件并映射为 YQP `job.event`；
- 将终态写入本地 transfer ledger；
- 处理 Center cancellation；
- 脱敏错误输出；
- 根据 capability manifest 暴露可用性。

Node 插件适配层明确不负责：

- 自动创建下一次 attempt；
- 自动重启失败的 `yq-croc` send/receive；
- 自动切换 relay；
- 自动生成或复用未由 Center 下发的 code；
- 自动调用对端 Node；
- 在 Center Job 已终止后继续后台传输。

### Center

Center 负责：

- `transfer.preflight` 调用两端 status/stat；
- 生成 `transfer_id` 和 code；
- 只保存 `code_hash`；
- 先启动 sender job；
- 等待 sender 上报 `phase=sender_ready`；
- 再启动 receiver job；
- 聚合两端 Job 事件到 `TransferSession` 和 Operation；
- 执行取消、超时、失败分类、reconcile；
- 不接触文件内容。

## 6. yq-croc 事件协议

`yq-croc` 每行输出一个 JSON 对象。

公共字段：

```json
{
  "schema": "yq_croc_event_v1",
  "transfer_id": "trf_x",
  "role": "sender",
  "event": "sender_ready",
  "phase": "sender_ready",
  "status": "running",
  "time": "2026-07-01T12:00:00Z"
}
```

事件集合固定为：

| event | phase | 含义 |
|---|---|---|
| `runtime_ready` | `preparing` | yq-croc 已启动并读取 request。 |
| `source_scanned` | `scanning` | sender 已完成源文件扫描，包含 `total_bytes`。 |
| `sender_ready` | `sender_ready` | sender 已成功连接 relay 并完成 room confirmation，receiver 可以启动。 |
| `receiver_connected` | `connecting` | receiver 已连接 relay control room。 |
| `channel_secured` | `securing` | PAKE 通道已建立。 |
| `file_info` | `metadata` | 文件清单已交换。 |
| `resume_plan` | `resuming` | receiver 已根据目标已有文件计算缺失 chunks。 |
| `file_started` | `transferring` | 当前文件开始传输。 |
| `bytes_progress` | `transferring` | 字节级进度。 |
| `reconnect_attempt` | `reconnecting` | croc 内部正在尝试重新连接 relay/data channel。 |
| `file_done` | `transferring` | 当前文件完成。 |
| `integrity_verified` | `verifying` | size/hash 校验通过。 |
| `transfer_done` | `succeeded` | 本端传输成功。 |
| `transfer_cancelled` | `cancelled` | 本端收到取消并退出。 |
| `transfer_error` | `failed` | 本端失败。 |

`bytes_progress` 必须包含：

```json
{
  "bytes_transferred": 7700000,
  "total_bytes": 8388608,
  "progress_pct": 91.79,
  "rate_bytes_per_sec": 524000,
  "eta_sec": 1
}
```

如果无法计算百分比，`progress_pct` 必须为 `null` 或省略，不能伪造。

## 7. croc 源码 hook 点

`yq-croc` 基于固定 upstream croc source 构建。第一版基线为 `schollz/croc` `v10.4.6`，作为 `yq-croc` source vendor。YeQu 对 vendor 源码的修改只允许集中在事件 hook、日志脱敏、命令入口和测试上，不修改 PAKE、加密、relay 房间协议和文件块协议。

必须加入的 hook：

| 位置 | 事件 |
|---|---|
| `Send()` 读取 request 后 | `runtime_ready` |
| `sendCollectFiles()` 成功后 | `source_scanned` |
| sender 调用 `tcp.ConnectToTCPServer(...)` 成功返回后 | `sender_ready` |
| receiver 调用 `tcp.ConnectToTCPServer(...)` 成功返回后 | `receiver_connected` |
| `Step1ChannelSecured` 置位处 | `channel_secured` |
| 文件清单接收/发送处 | `file_info` |
| receiver 计算 `MissingChunks(...)` 后 | `resume_plan` |
| `setBar()` 或等价当前文件初始化处 | `file_started` |
| `sendData()` / `receiveData()` 写入或读取 chunk 后 | `bytes_progress` |
| croc reconnect attempt 开始和结束处 | `reconnect_attempt` |
| 当前文件关闭且 hash/size 满足处 | `file_done` |
| 最终校验通过处 | `integrity_verified` |
| `SuccessfulTransfer` 成立处 | `transfer_done` |
| context cancellation 触发处 | `transfer_cancelled` |
| 返回错误处 | `transfer_error` |

`sender_ready` 的定义固定为：sender 已成功连接 relay control room，并收到 relay room confirmation。它不是 `Code is:`，也不是 PAKE 完成。这个定义使 Center 可以在不启动 receiver 的情况下等待一个确定的 room-ready 同步点。

## 8. request schema

### send request

```json
{
  "schema": "yq_croc_send_request_v1",
  "transfer_id": "trf_x",
  "attempt": 1,
  "role": "sender",
  "code": "secret-code",
  "source_path": "G:\\Minecraft\\284.zip",
  "relay_url": null,
  "relay_password": null,
  "resume_mode": "resume",
  "expected_size_bytes": 1073741824,
  "expected_sha256": null,
  "timeout_sec": 3600,
  "cleanup_on_failure": false
}
```

### receive request

```json
{
  "schema": "yq_croc_receive_request_v1",
  "transfer_id": "trf_x",
  "attempt": 1,
  "role": "receiver",
  "code": "secret-code",
  "output_dir": "/home/yequdesu",
  "target_path": "/home/yequdesu/284.zip",
  "relay_url": null,
  "relay_password": null,
  "resume_mode": "resume",
  "timeout_sec": 3600,
  "expected_size_bytes": 1073741824,
  "expected_sha256": null,
  "cleanup_on_failure": false
}
```

Secrets can be passed by request file only if the file is created in a Node-owned temp directory with owner-only permissions. On Windows the adapter must create the file under the Node data directory and delete it after process exit. On Linux the adapter must create it with `0600`.

## 9. relay policy

`yq-croc` supports three relay modes:

| mode | 行为 |
|---|---|
| `public_default` | 使用 croc upstream 默认公共 relay。开发和个人临时传输允许。 |
| `configured_single` | 使用 Node/Center 配置的单个 relay。 |
| `configured_pool` | Center 选择 relay pool 中的一个 relay 写入 transfer input。 |

Node status 必须返回：

```json
{
  "transport": "yq-croc",
  "runtime": "yq-croc",
  "installed": true,
  "executable": true,
  "version": "yq-croc 0.1.0+croc-v10.4.6",
  "relay_mode": "public_default",
  "relay_url": null,
  "relay_reachable": true,
  "allow_send": true,
  "allow_receive": true
}
```

Center preflight 的判断规则：

- `installed=true`；
- `executable=true`；
- send 端 `allow_send=true`；
- receive 端 `allow_receive=true`；
- 两端 `relay_mode` 一致，或 Center 明确向两端下发同一个 `relay_url`；
- 两端 `relay_reachable=true`；
- relay password 不出现在 Timeline、Agent observation、普通日志中。

## 10. 公共 relay 不稳定与断点续传

公共 relay 模式必须被视为可用但不稳定的传输环境。`yq-croc` 方案必须同时支持进程内 reconnect 和跨 Job resume，不能只做“失败后整文件重传”。

### 进程内 reconnect

`yq-croc` 必须启用 upstream croc 的 reconnect 逻辑，并将 reconnect 状态输出为结构化事件：

```json
{
  "schema": "yq_croc_event_v1",
  "event": "reconnect_attempt",
  "phase": "reconnecting",
  "attempt": 2,
  "max_attempts": 10,
  "reason": "relay_data_channel_disconnected"
}
```

进程内 reconnect 期间：

- Job 保持 `running`；
- Node 继续发送 keepalive/progress event；
- Center 不启动新的 receive/send job；
- Node 不改变 relay、code、source、target 或 attempt；
- 超过 `max_attempts`、总 timeout 或 context cancellation 后，`yq-croc` 输出 `transfer_error`。

### 跨 Job resume

如果公共 relay 故障导致本次 sender/receiver Job 失败，Center 在同一个 `TransferSession` 上创建新的 attempt。新的 attempt 允许使用新的 croc code 和新的 relay，但必须保持：

- 同一个 `transfer_id`；
- 同一个 source node；
- 同一个 source path；
- 同一个 target node；
- 同一个 target path 或 output dir；
- 相同的 expected size/hash，或重新 preflight 后确认 source 未变化；
- receiver 端保留部分目标文件；
- receiver 端 `resume_mode="resume"`。

Node 不允许在上一次 Job terminal 后自行进入下一次 attempt。即使 Node ledger 显示 `resumable=true`，它也只能在 `reconcile` 或 status 输出中报告该事实，等待 Center 显式下发下一次 send/receive Job。

croc 的跨进程 resume 依赖 receiver 本地部分文件。receiver 会根据已有文件大小/hash 计算缺失 chunk，向 sender 发送 `RemoteFileRequest.CurrentFileChunkRanges`。因此 Node 适配层不得在失败后默认删除部分文件；只有 input 明确 `cleanup_on_failure=true` 或用户明确要求时才允许删除。

### resume ledger

Node ledger 必须为每个 `transfer_id`、`role`、`attempt` 保存：

```json
{
  "transfer_id": "trf_x",
  "attempt": 1,
  "role": "receiver",
  "runtime": "yq-croc",
  "source_node_id": "winClient",
  "target_node_id": "linux-node-01",
  "source_path_hash": "sha256:...",
  "target_path": "/home/yequdesu/284.zip",
  "expected_size_bytes": 1073741824,
  "expected_sha256": null,
  "resume_mode": "resume",
  "relay_mode": "public_default",
  "relay_url_hash": null,
  "last_event": "bytes_progress",
  "bytes_transferred_observed": 713031680,
  "partial_file_path": "/home/yequdesu/284.zip",
  "partial_file_size": 1073741824,
  "missing_chunks_count": 742,
  "status": "interrupted"
}
```

`source_path_hash` 不是文件 hash。它是 path 字符串、size、mtime、可选 sha256 的组合摘要，用于判断 retry 是否仍指向同一个源内容。若 retry 前 source size/mtime/hash 与 ledger 不匹配，Center/Node 必须失败为 `source_changed`，不得继续 resume。

### resume_mode 语义

`resume_mode` 固定为三种：

| resume_mode | 行为 |
|---|---|
| `resume` | 允许使用目标已有部分文件计算缺失 chunks；默认用于公共 relay 大文件传输。 |
| `overwrite` | 接收端先删除或覆盖目标文件，不复用部分文件。 |
| `fail_if_exists` | 目标文件已存在时直接失败，不启动 `yq-croc`。 |

`resume` 不是“无条件接受已有文件”。当目标文件存在但 ledger 不匹配、source facts 不匹配、expected size/hash 不匹配，或者目标文件不是上次同一 transfer 的残留时，Node 必须失败为 `resume_state_mismatch`，不得把无关文件当作 partial file。

### Center retry policy

第一版 Center retry policy 固定为：

- `transfer.create` 只创建第一个 attempt，不自动无限重试；
- `transfer.status` 显示 `interrupted`、`resumable=true/false`、`last_error_code`；
- 新增 `transfer.resume` 后，用户或 Agent 通过 Center 在同一个 `TransferSession` 上发起下一次 attempt；
- 若错误码是 `external_service_failed`、`relay_disconnected`、`operation_timeout` 且两端 ledger 显示 `resumable=true`，允许 resume；
- 若错误码是 `permission_denied`、`source_not_found`、`integrity_mismatch`、`resume_state_mismatch`，不允许自动 resume。

第一版不实现自动重试。后续版本增加 Center-owned bounded auto-retry 时，必须有最大次数、退避、relay 切换策略和 Timeline 记录。auto-retry 仍然由 Center 创建新 attempt，Node 不能本地自启。

## 11. capability 合同变更

Capability 名称保持 `*.transfer.croc.*`，因为它描述的是 croc 协议族传输语义，不暴露实现二进制名称。

输出中的 runtime 字段改为 `yq-croc`：

```json
{
  "transport": "croc",
  "runtime": "yq-croc",
  "runtime_version": "0.1.0",
  "upstream_croc_version": "v10.4.6"
}
```

`*.transfer.croc.status` 必须继续在 runtime 不可用时可调用，不能因为 `yq-croc` 不存在就不注册 status。

`*.transfer.croc.send` / `receive` 才要求 transfer runtime label：

```json
{
  "runtime_kind": "privileged",
  "labels": ["linux", "transfer", "yq-croc"]
}
```

Windows 对应：

```json
{
  "runtime_kind": "user",
  "labels": ["windows", "transfer", "yq-croc"]
}
```

## 12. Center 修改计划

Center 保持 `TransferSession`、Operation、YQP、Job lifecycle 的所有权。

必须修改：

1. 保留 `transport="croc"` 作为传输协议族名称。
2. `transfer.preflight` 要求可执行运行时为 `runtime="yq-croc"`；旧 croc CLI runtime 不作为可执行路线接受。
3. `_job_has_sender_ready()` 接受 `progress_source="yq_croc_event"` 且 `event="sender_ready"`。
4. `transfer.status` 聚合 `bytes_progress`，优先选择 sender/receiver 中更可靠的字节进度。
5. Timeline 只记录 `code_hash`、`relay_mode`、masked relay，不记录 code 明文和 relay password。
6. Agent 工具描述从“croc CLI”改为“Center-managed croc-compatible transfer runtime”。
7. Capability diagnostics 增加 yq-croc runtime 字段检查。
8. 取消逻辑继续调用 `cancel_job()`，不直接杀进程。
9. `transfer.status` 增加 `resumable`、`attempt`、`last_resumable_error`、`resume_hint`。
10. 新增 `TransferAttempt` 持久化模型，字段包含 `session_id`、`attempt`、`source_job_id`、`target_job_id`、`code_hash`、`relay_mode`、`relay_url_masked`、`status`、`resumable`、`error_code`、`started_at`、`completed_at`。
11. 新增 `transfer.resume`，只接受已有 `TransferSession`，创建下一条 `TransferAttempt` 并重新下发 sender/receiver Jobs；`transfer.create` 只创建新 session 和 attempt 1。
12. 测试覆盖 sender_ready 事件新格式、公共 relay status、configured relay status、relay unreachable preflight failure、interrupted/resumable 状态和 resume attempt。
13. 所有 retry、resume、relay switch、code rotation 必须产生 Timeline 事件。

Center 不做：

- 不 import Go package；
- 不执行 yq-croc；
- 不做数据面 relay；
- 不读取文件字节；
- 不管理 Node 本地 yq-croc 进程 PID。

## 13. Windows Node 修改计划

Windows Node 修改已收敛为 `windows.transfer.croc.*` + `yq-croc.exe` adapter。旧直连传输 adapter、manifest、dispatch 和随附工具包已删除。

已实现：

1. `windows.transfer.croc.status/send/receive/reconcile`。
2. `TransferYqCrocConfig`：

```yaml
transfer:
  yq_croc:
    enabled: true
    binary_path: "./tools/yq-croc/current/yq-croc.exe"
    relay_mode: "public_default"
    relay_url: null
    relay_password_env: null
    temp_dir: "./data/transfers"
    allow_send: true
    allow_receive: true
    max_concurrent_transfers: 1
```

3. Windows plugin adapter 启动 `yq-croc.exe send/receive --request <json>`。
4. 逐行读取 stdout NDJSON，映射为 `JobEventType.PROGRESS`。
5. `transfer_error` 映射为 `NodeExecutionError`，保留脱敏 details。
6. 取消时 terminate 子进程；超时后 kill。
7. request 文件写入 Node data temp，退出后删除。
8. ledger 存储每个 `transfer_id` 的 role、attempt、terminal result 和 partial file facts。
9. 禁止在 `transfer_error` 或进程退出后自动重启 `yq-croc`；只能返回终态并等待 Center 下一次 Job。
10. `windows.transfer.croc.status` 报告 `firewall_allows_outbound`。yq-croc 默认关闭 local relay/discovery，不要求入站防火墙规则；如果 Windows Firewall 或上级策略默认阻断程序出站，必须为 `yq-croc.exe` 到 relay TCP 端口建立 outbound allow rule，默认端口 `9009`。

管理员 PowerShell 配置示例：

```powershell
$exe = "E:\yequdesu_project\YeQu-Gateway-Win\Node-winClient\tools\yq-croc\current\yq-croc.exe"
New-NetFirewallRule `
  -DisplayName "YeQu yq-croc outbound" `
  -Direction Outbound `
  -Program $exe `
  -Action Allow `
  -Protocol TCP `
  -RemotePort 9009
```

## 14. Linux Node 修改计划

必须修改：

1. 保留 `linux.transfer.croc.*` capability 名称。
2. 将 `croc_command.rs` 的 CLI 拼装替换为 `yq-croc` request-file 调用。
3. 将 `croc_progress.rs` 的 stderr parser 替换为 NDJSON event parser。
4. `linux_transfer_croc_status.rs` 改为探测 yq-croc：
   - `yq-croc version`
   - `yq-croc probe --json`
   - `yq-croc relay-probe`
5. `linux_transfer_croc_send.rs` 生成 send request file 并读取事件。
6. `linux_transfer_croc_receive.rs` 生成 receive request file并读取事件。
7. `transfer_ledger.rs` 保留，但字段升级为 yq-croc runtime event snapshot、partial file facts、missing chunk summary、resumable。
8. long-running capability 列表保持 `linux.transfer.croc.send/receive`。
9. runtime snapshot metadata 增加：

```json
{
  "runtime": "yq-croc",
  "yq_croc_binary_path": "/usr/local/bin/yq-croc",
  "upstream_croc_version": "v10.4.6",
  "relay_mode": "public_default"
}
```

10. cargo tests 覆盖 NDJSON parser、status probe、cancel、ledger reconcile、interrupted resume、resume_state_mismatch。
11. 禁止在 `transfer_error` 或进程退出后自动重启 `yq-croc`；只能返回终态并等待 Center 下一次 Job。

## 15. yq-croc 项目交付计划

`yq-croc` 源码作为独立插件项目发布。Center repo 只保存合同、计划和合同测试。第一版发布物：

```text
yq-croc/
  go.mod
  cmd/yq-croc/main.go
  internal/runtime/
  internal/events/
  internal/request/
  internal/crocvendor/
  internal/probe/
  internal/resume/
  testdata/
```

构建产物：

```text
yq-croc_v0.1.0_windows_amd64.zip
yq-croc_v0.1.0_linux_amd64.tar.gz
yq-croc_v0.1.0_checksums.txt
```

Node 安装路径：

```text
Windows: ./tools/yq-croc/current/yq-croc.exe
Linux:   /usr/local/bin/yq-croc
```

## 16. 验收标准

必须通过以下验收后才能启用 yq-croc 大文件传输路线：

1. Windows Node 和 Linux Node 都能注册 `*.transfer.croc.status`，且 status 明确显示 `runtime="yq-croc"`。
2. 未安装 `yq-croc` 时，status 可调用，send/receive 不注册或明确 unavailable。
3. Windows Node 在默认出站阻断策略下，`windows.transfer.croc.status` 返回 `firewall_allows_outbound=false`，Center preflight 返回 `runtime_egress_blocked`；添加 outbound allow rule 后 status 变为 ready。
4. public relay 模式下 Win -> Linux 传输 1 GiB 文件成功。
5. public relay 模式下 Linux -> Win 传输 1 GiB 文件成功。
6. configured relay 模式下双向 1 GiB 文件成功。
7. sender_ready 不依赖 `Code is:` 或 sleep。
8. 进度来自 `bytes_progress` NDJSON，不解析 progressbar stderr。
9. 取消后两端 Job 在 10 秒内进入 terminal 状态。
10. 断网/relay 失败返回稳定错误码 `external_service_failed`。
11. hash 不一致返回 `integrity_mismatch`。
12. Center Timeline 不包含 code 明文、relay password 或 request JSON 全量。
13. Agent 只看到 `transfer.create/status/cancel`，不看到底层 yq-croc 参数。
14. 人为中断 public relay 传输后，`transfer.status` 显示 `interrupted`、`resumable=true`。
15. 使用同一 `TransferSession` 发起 resume attempt 后，不从 0 字节整文件重传，receiver 输出 `resume_plan` 且最终 hash/size 校验通过。
16. 目标已有无关同名文件时，`resume_mode="resume"` 必须失败为 `resume_state_mismatch`。
17. `cleanup_on_failure=false` 时失败不会删除 partial file；`cleanup_on_failure=true` 时 ledger 必须记录清理结果。
18. Node 在 Job terminal 后不会自行创建新进程、新 attempt、新 code 或 relay switch；所有下一次 attempt 都可在 Center Timeline 中追溯。

## 17. 执行状态

已完成：

1. 独立 `yq-croc` 插件项目已建立，vendor croc v10.4.6，提供 `version`、`probe`、`relay-probe`、`send`、`receive`。
2. Center 已新增 `TransferAttempt`，`transfer.create` 使用 yq-croc request 合同，`transfer.resume` 只在 interrupted/resumable session 上创建下一 attempt。
3. Center sender ready 只接受 `progress_source="yq_croc_event"` 且 `event="sender_ready"`；旧 croc stderr/CLI ready 路径已从 active code 删除。
4. Linux Node 已从旧 croc CLI/stderr parser 切到 `yq-croc` request/NDJSON，并删除 `croc_command.rs`、`croc_progress.rs`。
5. Windows Node 已删除 rclone adapter、rclone 工具包和 `windows.transfer.rclone.*` 能力，改为 `windows.transfer.croc.*` + `yq-croc.exe`。
6. Center 仓库旧 bundled croc release 包已删除。
7. yq-croc 已新增可重复打包脚本，生成 Windows zip、Linux tar.gz 和 SHA-256 checksums。
8. 本机 Windows 已完成 public relay 小文件 smoke test：按 Center 调度顺序先启动 sender，等待 `sender_ready` 后启动 receiver，事件链包含 `sender_ready`、`channel_secured`、`bytes_progress`、`transfer_done`，文件成功落地。
9. Windows Node status 已新增 `firewall_allows_outbound` 事实；Center preflight 已接入 `runtime_egress_blocked`。
10. WSL Ubuntu 已安装 Rust/C 工具链，`cargo check -p yequnode-core` 在真实 Linux target 上通过。
11. Windows yq-croc sender -> WSL/Linux yq-croc receiver 的 public relay 小文件 smoke test 已通过，证明 Windows/Linux 二进制能按 Center 的 sender-ready 调度顺序完成跨平台传输。

仍需端到端验收：

1. 独立 Linux Node 真实部署环境安装 `/usr/local/bin/yq-croc` 后执行 systemd/daemon 级启动验收。
2. Win/Linux 双向 1 GiB、取消、relay failure、hash mismatch、interrupted/resume、reconcile 验收。
3. configured relay pool 第一版策略尚未落地；当前实现覆盖 public default 和 single configured relay。

## 18. 非目标

第一版不做：

- 自研 PAKE；
- 自研 relay 协议；
- 把 yq-croc 嵌进 Center 进程；
- 把 yq-croc 嵌进 Node 主进程；
- 让 Agent 直接调用 yq-croc；
- 通过 YQP 传输大文件内容；
- 默认强制用户自建 relay；
- 默认删除部分接收文件。
- 无上限自动重试。
- Node 自主事务、Node 自主 retry、Node 自主 resume。

## 19. 实现就绪性审计

判定：本计划可以作为实现 goal 的起点。

该判定的含义是：架构方向、运行时边界、Center/Node 分工、公共 relay 策略、断点续传边界、事件协议、request schema、错误归因、验收标准和执行顺序已经收敛，不存在需要重新选择技术路线的开放问题。

项目当前处于快速迭代期，质量优先于旧实现兼容。实现本计划时，不合格路线必须直接删除，不保留 fallback、不保留兼容代码、不保留隐藏配置开关、不保留未使用依赖。删除范围包括旧 croc CLI wrapper、stderr/progressbar parser、rclone/SFTP/advertise_host 直连路线、相关 manifest、测试夹具、随附二进制和文档执行语义。

当前剩余内容全部属于真实环境验收任务，不属于决策缺口：

- Linux Node 的 Rust 编译需要 Linux toolchain；Windows host 和当前 WSL 环境不能完成 cargo check。
- 双向 1 GiB、断网/relay 中断后 resume、configured relay pool 仍需在真实 Win/Linux Node 上执行。

不允许在实现 goal 中重新引入以下路线：

- rclone、SFTP、HTTP receiver、`advertise_host` 或任何要求 Node 入站可达的默认方案；
- 继续扩展 croc CLI stderr/parser 包装；
- 为旧 croc CLI、rclone 或直连传输保留 fallback、兼容层、隐藏配置开关或未使用代码；
- Node 自主 retry、Node 自主 resume、Node 自主 relay switch；
- 通过 Center API/YQP JSON 转发大文件内容。
