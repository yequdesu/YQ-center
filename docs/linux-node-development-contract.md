# Linux Node 开发合同

状态：当前 Linux Node 开发硬约束
依赖协议：`YQP-Node-Protocol.md`
依赖能力合同：`docs/node-capability-contract.md`
目标：在部署 Center 的 Linux 服务器或其他 Linux 主机上实现可长期演进的 Linux Node，用于多 Node 调度、Linux 运维、artifact、跨 Node 传输和后续 Operation Runtime 验证。

## 1. 开发目标

当前 Linux Node 已经不再只是 POC。仓库内 `nodes/linux/yequnode` 是当前部署实现，后续 Linux Node 相关工作直接在该目录推进。

本合同的目标不是“能跑通就行”，而是让其他 Agent 或人类开发者按合同实现时不会偏离意图。新增 capability 必须满足：

- manifest 精确；
- input/output schema 精确；
- preflight、progress、cancel、resume 支持情况明确；
- 错误码稳定；
- 权限模型清楚；
- Center `capability.describe` 能解释该能力何时可用、需要什么权限、会产生什么副作用。

图形和摄像头能力不是 Linux Node 当前核心路径；framebuffer 截图只作为探测通过时才注册的可选 artifact 能力。Linux Node 当前核心能力是文件、服务、网络、包管理、artifact 和 transfer 辅助能力。

## 2. 必须遵守的协议

Linux node 必须以 `YQP-Node-Protocol.md` 为准，实现：

- `node.hello`
- `node.register_capabilities`
- `node.heartbeat`
- `job.poll`
- `job.accepted`
- `job.event`（至少支持可选日志/进度）
- `job.finished`
- `job.lease_renew`（长任务需要）
- `node.reconcile_jobs`（重启恢复需要）

可以暂缓：

- `signal.report`：如果当前实现没有周期 Signal，可以暂缓；但推荐至少上报 load/memory。

不能暂缓：

- `job.cancel` 语义：poll/reconcile 模式下收到 Center `cancelling` 后，必须终止本地子进程并上报终态。长任务不能长期卡在 `cancelling`。
- 长任务 `job.lease_renew`。
- 长任务 `job.event` progress/keepalive。
- Node 重启后的 `node.reconcile_jobs`。

## 3. 配置约定

建议配置文件：

```yaml
node_id: <linux-node-id>
center_base_url: https://gtw.yequdesu.top
yqp_path: /yqp/
node_token: ${YEQU_NODE_TOKEN}
poll_interval_sec: null
heartbeat_interval_sec: null
```

运行时以 `node.accepted.payload` 为准覆盖本地默认值：

- `heartbeat_interval_sec`
- `job_poll_interval_sec`
- `signal_report_interval_sec`
- `default lease` 来自每个 Job 的 `lease_sec`

## 4. Runtime Manifest

Linux node 至少上报一个 runtime：

```json
{
  "runtime_id": "default",
  "kind": "privileged",
  "status": "online",
  "interactive": false,
  "privilege": "user",
  "labels": ["linux", "system"],
  "metadata": {
    "implementation": "linux-node-python"
  }
}
```

如果后续支持容器隔离，可以新增：

```json
{
  "runtime_id": "docker",
  "kind": "docker",
  "status": "online",
  "interactive": false,
  "labels": ["linux", "container"]
}
```

不要把 OS 细节写进 Center 的调度逻辑。OS 细节只能出现在 Node manifest、runtime labels、capability name 或 metadata 中。

## Linux 权限模型

当前 Linux Node 默认以普通用户运行，不要求 root。

Node 启动时必须对 capability 做 permission probe。未通过 probe 的能力不得注册为可用能力。Center 只根据 Node 上报的 runtime 和 capability 做调度，不替 Node 解决本地权限问题。

当前权限策略基线：

| capability | 权限策略 |
|---|---|
| `linux.exec.run` | 普通用户和 sudo/root runtime 均可上报 profile；Center 不解析命令语义，第一版静态按 `maintenance/write` 走通用 approval/audit。 |
| `linux.artifact.*` | 需要 sudo/root 或可选 framebuffer 探测；用于 artifact 上传、下载、诊断包和截图。 |
| `linux.transfer.*` | 由 yq-croc runtime 与 transfer 本地 fact 能力承担；send/receive 只由 Center transfer workflow 调度。 |

普通用户 runtime 示例：

```json
{
  "runtime_id": "user",
  "kind": "privileged",
  "status": "online",
  "interactive": false,
  "privilege": "user",
  "labels": ["linux", "filesystem", "filesystem:limited", "network"],
  "metadata": {
    "uid": 1000,
    "user": "yequ",
    "visible_roots": ["/home/yequ", "/tmp"],
    "namespace": "host"
  }
}
```

需要 sudo/root 能力时，必须使用独立 runtime 和独立 capability，不得混入普通 capability。

sudo/root runtime 示例：

```json
{
  "runtime_id": "sudo-limited",
  "kind": "privileged",
  "status": "online",
  "interactive": false,
  "privilege": "root",
  "labels": ["linux", "filesystem", "artifact", "sudoers:yequnode", "filesystem:host"],
  "metadata": {
    "sudoers_file": "/etc/sudoers.d/yequnode",
    "allowed_commands": ["stat", "journalctl"]
  }
}
```

高权限 runtime 示例：

```json
{
  "runtime_id": "sudo-limited",
  "kind": "privileged",
  "status": "online",
  "interactive": false,
  "privilege": "root",
  "labels": ["linux", "exec", "filesystem", "artifact", "sudoers:yequnode", "filesystem:host"],
  "metadata": {
    "execution_profiles": ["admin.readonly", "admin.write"]
  }
}
```

说明：`filesystem` 和 `artifact` 是 capability routing 用的通用标签；`filesystem:host` 是更细的诊断/权限语义标签。需要文件读写或 artifact 读写的能力不得要求一个 runtime 未声明的标签。

执行时权限不足必须显式失败：

```json
{
  "job_id": "job_...",
  "status": "failed",
  "error": {
    "code": "permission_denied",
    "message": "current runtime cannot access /var/log/auth.log",
    "details": {
      "runtime_id": "user",
      "path": "/var/log/auth.log"
    }
  }
}
```

## Artifact 下发能力合同

Linux Node 必须把 Center artifact 下发和 croc 跨 Node 传输区分开：

- `linux.artifact.upload_file`：从 Linux 本地读取文件并上传到 Center；
- `linux.artifact.download_file`：从 Center artifact 存储下载文件并写入 Linux 本地路径；
- `linux.transfer.croc.*`：Node 到 Node 直接传输，大文件优先使用该路径。

`linux.artifact.download_file` 是 Center -> Linux 的能力原语，不是完整 workflow。它不负责选择 artifact、不负责选择目标 Node、不负责替用户猜测路径，也不负责把多个 Node 串成一个流程。完整流程由 Center 的 `artifact.deploy.preflight` 和 `artifact.deploy` 组合。

### `linux.artifact.download_file`

输入：

```json
{
  "artifact_id": "id_x",
  "output_path": "/tmp/yequ-transfer/a.bin",
  "mode": "fail_if_exists"
}
```

字段要求：

| 字段 | 要求 |
|---|---|
| `artifact_id` | 必填。必须是 Center 已存在的 artifact id。 |
| `output_path` | 必填。必须是绝对路径。Agent 不得在用户未指定或未确认时猜测落点。 |
| `mode` | 可选，`fail_if_exists` 或 `overwrite`，默认 `fail_if_exists`。 |

输出：

```json
{
  "artifact_id": "id_x",
  "output_path": "/tmp/yequ-transfer/a.bin",
  "size_bytes": 123,
  "sha256": "...",
  "expected_sha256": "...",
  "content_type": "application/octet-stream",
  "verified": true
}
```

执行要求：

- 使用 Node Bearer token 访问 Center `GET /yqp/artifacts/{artifact_id}/download`；
- 不得使用 admin token；
- 不得把 artifact bytes 经过 Agent prompt、tool JSON、stdout/stderr 或日志；
- 必须流式写入文件并同步计算 SHA-256；
- Center 返回 `X-YeQu-Artifact-Sha256` 时必须校验，不一致必须失败；
- `mode=fail_if_exists` 且目标已存在时必须失败；
- `output_path` 父目录不存在时可以创建；创建失败或权限不足必须失败；
- 不支持断点续传时必须声明 `supports_resume=false`；
- 如果需要写系统目录，应由 runtime 权限决定；权限不足不得 fallback 到其他目录。

能力 manifest 要求：

```json
{
  "name": "linux.artifact.download_file",
  "risk": "maintenance",
  "effect": "write",
  "execution_requirements": {
    "runtime_kind": "privileged",
    "labels": ["linux", "artifact"]
  },
  "resource_keys": ["node.filesystem", "center.artifact"],
  "conflict_policy": "serialize",
  "supports_progress": false,
  "supports_cancel": false,
  "supports_resume": false,
  "required_intent_slots": ["artifact_id", "output_path"]
}
```

## 文件系统写能力合同

Linux Node 不再暴露 `linux.filesystem.*`、`linux.system.*`、`linux.network.*`、`linux.service.*`、`linux.log.*` 这类低价值 primitive 能力。目录创建、普通文件读写、hash、stat、系统诊断、网络诊断、日志查看等简单命令由 `linux.exec.run` 承载。第一版不实现命令语义门控，`linux.exec.run` 静态按 `maintenance/write` 进入 Center 通用 approval/audit。

结构化业务写能力仍然保留为独立 capability，例如 artifact 下载、transfer 接收和服务维护。能力不得在权限不足时改写到其他目录，也不得静默降级。

`linux.exec.run` 输入必须包含：

```json
{
  "profile": "user.readonly",
  "command": "sha256sum /home/yequdesu/file.zip",
  "reason": "verify downloaded file checksum"
}
```

执行要求：

- Node 只负责按 profile 执行命令并返回 exit code、stdout/stderr preview、截断标记和错误信息；
- Center 不做命令语义判断；第一版只按 `linux.exec.run` manifest 的 `maintenance/write` 进入通用 approval/audit；
- 权限不足必须返回显式失败；
- 不得 fallback 到其他目录、其他用户或其他 runtime。

## 网络探测任务

Linux Node 不再暴露独立网络探测 capability。网络诊断通过 `linux.exec.run` 执行系统命令，例如 `getent hosts example.com`、`nc -vz host port`、`curl -I URL`。连接失败体现为命令 exit code 和 stderr/stdout，由 Agent 解释为网络状态。

错误码要求：

| 场景 | code |
|---|---|
| artifact 不存在或 Center 返回 404 | `source_not_found` 或 `artifact_not_found` |
| 下载 HTTP 失败 | `external_service_failed` |
| 目标已存在且禁止覆盖 | `target_exists` |
| 输出路径不是绝对路径 | `invalid_input` |
| 无法创建父目录或写入文件 | `permission_denied` |
| SHA-256 不一致 | `integrity_mismatch` |

## yq-croc 插件运行时部署合同

Linux Node 的跨 Node 大文件传输以 `yq-croc` 插件运行时为准。`yq-croc` 是独立二进制，基于 croc Go 源码构建，提供 YeQu 所需的 NDJSON 事件、稳定 ready 同步点、取消、relay probe 和错误码。Linux Node 主进程不实现 croc 协议，只负责调用 `yq-croc`、读取事件、执行本地路径策略、上报 YQP job events 和维护本地 ledger。

推荐安装路径：

```bash
sudo install -m 0755 yq-croc /usr/local/bin/yq-croc
yq-croc version
```

如果不能使用 `sudo`，可以安装到 Node daemon 用户自己的 bin 目录：

```bash
mkdir -p "$HOME/.local/bin"
install -m 0755 yq-croc "$HOME/.local/bin/yq-croc"
"$HOME/.local/bin/yq-croc" version
```

Node 配置中必须允许显式指定 `yq-croc` 路径，不得只依赖 `PATH`：

```yaml
transfer:
  yq_croc:
    enabled: true
    binary_path: /usr/local/bin/yq-croc
    relay_mode: public_default
    relay_url: null
    relay_password_env: null
    temp_dir: /tmp/yequ-transfer
    allow_send: true
    allow_receive: true
    max_concurrent_transfers: 1
```

`linux.transfer.croc.status` 必须返回 `runtime="yq-croc"`、`runtime_version`、`upstream_croc_version`、`relay_mode`、`relay_reachable`、`binary_path`、`temp_dir`、`allow_send`、`allow_receive` 和明确错误。status 必须在 `yq-croc` 不存在时仍可调用。status 必须接受可选 `relay_url` 输入；当 Center 为 configured relay transfer/preflight 传入该字段时，Linux Node 必须探测这个 relay control address，而不是只探测本地默认配置。

`linux.transfer.croc.send` 和 `linux.transfer.croc.receive` 必须通过 request file 调用 `yq-croc send/receive`，逐行读取 stdout NDJSON，将 `sender_ready`、`bytes_progress`、`transfer_done`、`transfer_error` 等事件映射为 YQP `job.event`。不得解析 terminal progressbar 或 stderr 作为进度来源。

公共 relay 模式下，Linux Node 必须把 relay 断开、超时、进程异常退出后的本地状态保存为 `interrupted`，并在 ledger 中记录 partial file facts、expected size/hash、source facts digest 和 `resumable`。失败时不得默认删除 partial file。后续 resume attempt 必须使用 `resume_mode="resume"`，且只有 ledger/source/target 匹配时才允许复用部分文件；不匹配时必须失败为 `resume_state_mismatch`。

Linux Node 不能主动发起任何传输事务。`interrupted` 或 `resumable=true` 只允许作为事实上报给 Center；Linux Node 不得自行重启 `yq-croc`、自行创建下一次 attempt、自行切换 relay、自行生成 code 或自行通知对端 Node。进程内 reconnect 只允许发生在当前 Center Job 尚未 terminal 的生命周期内。

历史方案记录见 `docs/archive/todos/2026-07-01-yq-croc-plugin-runtime-plan.md`。当前执行约束以本文和 `docs/node-capability-contract.md` 为准。

## transfer runtime requirement 分层

transfer 相关 capability 必须按职责声明不同的 `execution_requirements`。不要让诊断能力依赖只有传输可用时才存在的 runtime 标签，否则 Center 无法调用 status 来诊断 transfer 为什么不可用。

诊断/事实类能力只要求基础 Linux runtime：

```json
{
  "runtime_kind": "privileged",
  "labels": ["linux"]
}
```

适用能力：

- `linux.transfer.croc.status`
- `linux.transfer.local.stat`
- `linux.transfer.croc.reconcile`

实际传输类能力必须要求 yq-croc transfer runtime：

```json
{
  "runtime_kind": "privileged",
  "labels": ["linux", "transfer", "yq-croc"]
}
```

适用能力：

- `linux.transfer.croc.send`
- `linux.transfer.croc.receive`

能力声明要求：

- `risk` 必须声明为 `maintenance`。
- `effect` 必须声明为 `external`，不要声明为 `write`。
- `resource_keys` 应包含稳定的传输资源键，例如 `node.transfer`。
- `conflict_policy` 应声明为 `serialize`，避免同一 Node 上多个 yq-croc 传输互相抢占临时目录、ledger 或网络资源。

原因：`linux.transfer.croc.receive` 确实会在目标 Node 写入文件，但在 YeQu 建模中，yq-croc send/receive 是“外部传输进程控制能力”，目标路径权限由 Node runtime/path policy 自己执行；Center 的 `transfer.create` 负责一次高层 TransferSession 编排。如果 Node 把 receive 声明为 `effect=write`，当前 Center L2 策略会要求单独审批底层 receive job，导致 `transfer.create` 无法自动编排 sender-ready 和 receiver startup。

后续如果需要对跨 Node 文件落盘做强审批，应在 `transfer.create` 这个高层元工具上建模一次完整审批，而不是让底层 `*.transfer.croc.receive` 单独触发审批；否则会破坏 TransferSession 的并发编排。

如果 Linux Node 给 send/receive 声明了 `labels=["linux", "transfer", "yq-croc"]`，则必须同时上报一个匹配 runtime：

```json
{
  "runtime_id": "linux-transfer",
  "kind": "privileged",
  "status": "online",
  "interactive": false,
  "privilege": "user",
  "labels": ["linux", "transfer", "yq-croc"],
  "metadata": {
    "runtime": "yq-croc",
    "yq_croc_binary_path": "/usr/local/bin/yq-croc",
    "upstream_croc_version": "v10.4.6",
    "relay_mode": "public_default",
    "temp_dir": "/tmp/yequ-transfer"
  }
}
```

如果 yq-croc 未安装或 send/receive 被配置禁用，Node 可以不注册 send/receive，或注册为 unavailable/degraded；但 `linux.transfer.croc.status` 必须仍然可调用，用于返回 `runtime="yq-croc"`、`installed=false`、`allow_send=false`、`allow_receive=false` 和明确错误。

send/receive 能力必须基于 status 探测事实：

- `linux.transfer.croc.send`
- `linux.transfer.croc.receive`

如果 `yq-croc` 不可用、目标路径不允许、权限不足、relay 不可用或校验失败，必须让 Job 失败并传播错误，不得静默降级，不得 fallback 到旧 croc CLI。

### send/receive 执行语义

`linux.transfer.croc.send` 和 `linux.transfer.croc.receive` 是阻塞型长任务：capability 返回时表示该端 yq-croc 子进程已经结束，状态必须是成功、失败、取消或超时之一。

因此手动传输不能依赖 Agent 顺序调用底层 send/receive：

```text
receive 阻塞等待 sender
Agent 等 receive 返回后才会调用 send
=> 会超时或失败
```

当前主路径由 Center `TransferSession` 编排：Agent 只调用 `transfer.preflight` 和 `transfer.create`；Center 先创建 sender job，等待 `progress_source="yq_croc_event"` 且 `event="sender_ready"` 后再创建 receiver job。只有绕过 Center TransferSession 做底层调试时，才允许用两个并发控制流直接启动两端任务。

Node 不得把阻塞型 receive 伪装成已完成。如果实现“启动后台进程后立即返回”的非阻塞模式，必须另起新的 capability 或在输出中明确 `process_status=started`，并提供查询/cancel 机制；当前合同不采用这种模式。

### receive 成功判定

`linux.transfer.croc.receive` 只有在实际收到文件或目录后才能返回 succeeded。以下情况必须返回 failed，并写入稳定错误码：

- yq-croc 子进程 returncode 为 0，但 `output_dir` 中没有新增文件或目标 `target_path` 不存在；
- 只检测到输出目录本身，不得把目录 inode/block size（例如 4096 bytes）当成传输文件大小；
- `received_path` 指向目录但本次传输预期是单文件，且没有可验证的文件级 size/sha256；
- `expected_sha256` 已提供但接收文件 sha256 不一致；
- 目标路径被权限、白名单、磁盘空间或覆盖策略拒绝。

receive 输出中的 `size_bytes` 和 `sha256` 必须来自实际接收的文件；如果接收的是目录，必须明确返回 `received_kind="directory"`，并提供目录清单摘要或 archive 级校验，不能用目录自身 stat 伪装成文件校验。

### yq-croc 命令与进度接口

Node 必须在 `linux.transfer.croc.status` 中探测 `yq-croc` 二进制、版本、上游 croc 版本、relay 配置和 relay reachability。send/receive 不得直接拼接 croc CLI 参数。

`resume_mode` 必须写入 yq-croc request file：

- `resume`：发现同一 transfer 的部分文件时尝试续传，不删除 partial file。
- `overwrite`：明确允许覆盖已有文件。
- `fail_if_exists`：目标已存在或 partial file 已存在时直接失败，不启动传输。

`linux.transfer.croc.send` 和 `linux.transfer.croc.receive` 必须逐行读取 `yq-croc` stdout NDJSON。进度事件必须来自 `bytes_progress`，不得解析 terminal progressbar、stderr 或人类可读日志。

映射后的 YQP `job.event` 示例：

```json
{
  "event_type": "transfer_progress",
  "data": {
    "transfer_id": "trf_x",
    "role": "sender",
    "phase": "transferring",
    "status": "running",
    "progress_source": "yq_croc_event",
    "event": "bytes_progress",
    "progress_pct": 92,
    "bytes_transferred": 7700000,
    "total_bytes": 8388608,
    "rate_bytes_per_sec": 524000,
    "eta_sec": 1
  }
}
```

规则：

- `sender_ready` 必须来自 `yq-croc` 结构化事件，且事件含义固定为 sender 已成功连接 relay control room 并收到 relay room confirmation。
- Center 必须等待 `progress_source="yq_croc_event"` 且 `event="sender_ready"` 后再启动 receive。
- `bytes_progress` 上报频率必须在 Node 侧节流到约 1 秒一次，完成时允许立即上报 100。
- `total_bytes` 优先使用 `transfer.local.stat` 或 Center 传入的 `expected_size_bytes`，不得被人类可读日志中的四舍五入大小覆盖。
- stderr 只允许作为本地调试日志和失败摘要来源，不得作为进度来源。
- 如果 `yq-croc` 没有输出可用进度，Node 只能上报 `process_keepalive`，不得伪造 `progress_pct`。
- 接收目录大小只能作为 `receiver_output_size_observation` 观察值，不能作为真实进度。

### 取消语义

`linux.transfer.croc.send` 和 `linux.transfer.croc.receive` 必须以可取消的 yq-croc 子进程方式执行。收到 Center `job.cancel` 或本地任务取消时：

- 必须终止对应 yq-croc 子进程，必要时先 terminate 再 kill；
- ledger 状态更新为 `cancelled` 或 `interrupted`；
- Job 必须尽快上报 `cancelled` 或 `failed`，不能长期停留在 `running` / `cancelling`；
- 不得默认删除部分文件，除非用户或 capability 输入显式要求清理；
- stdout/stderr 摘要仍需脱敏上报，便于判断取消前状态。

### yq-croc 错误上报合同

yq-croc 子进程失败时，Node 上报的 Job error 必须包含可诊断信息：

```json
{
  "code": "yq_croc_failed",
  "message": "yq-croc receive failed with exit code 1",
  "details": {
    "returncode": 1,
    "stdout": "... last 4000 chars, code redacted ...",
    "stderr": "... last 4000 chars, code redacted ...",
    "binary_path": "/usr/local/bin/yq-croc",
    "relay_url": null,
    "output_dir": "/tmp/yequ-transfer",
    "resume_mode": "overwrite"
  }
}
```

要求：

- `stdout` / `stderr` 至少保留尾部摘要，便于判断 relay、权限、路径、code、网络问题。
- croc code、relay pass、token 必须脱敏。
- 不允许只返回 `exit 1` 或空消息。
- 目录不存在、权限不足、目标已存在、sha256 不匹配必须有稳定 `code`。
- 失败时 ledger 必须从 `running` 更新为 `failed` 或 `interrupted`，不得长期停留在 `running`。

### yq-croc 断点续传与 Node 配合

yq-croc 保留 croc 的 interrupted transfer resume 能力，但这不是 Center 单方面能保证的能力。Linux Node 必须配合保存本地传输事实，并在 Center 下发新 attempt 时重新构造与上次兼容的 request file。

Node 必须持久化一份本地 transfer ledger，至少包含：

- `transfer_id`
- `role`：`sender` 或 `receiver`
- `status`：`created`、`running`、`interrupted`、`succeeded`、`failed`、`cancelled`
- `code_hash`：croc code 的 hash，不保存明文 code 到日志或普通状态接口
- `relay_url` / `relay_pass_configured`
- `source_path` 或 `target_path` / `output_dir`
- `source_size_bytes`
- `source_mtime` 或 `source_sha256`，用于判断源文件是否已变化
- `partial_path`：接收端已存在的部分文件路径
- `resume_mode`：`resume`、`overwrite`、`fail_if_exists`
- `attempt`：Center 下发的 attempt 序号，Node 不得本地自增
- `started_at` / `last_progress_at` / `completed_at`
- `last_error_code` / `last_error_message`

断点续传成立的前提：

- sender 和 receiver 使用同一个 `code`、relay 配置和目标路径语义重新启动；
- 接收端保留未完成的目标文件或 croc 可识别的部分文件；
- 源文件没有发生变化；
- Node 没有使用会强制覆盖部分文件的参数；
- Node 没有在失败清理中删除部分文件；
- 目标目录仍有权限和足够空间。

因此 `linux.transfer.croc.receive` 必须显式支持 `resume_mode`：

```json
{
  "code": "not-logged-secret",
  "output_dir": "/tmp/yequ-transfer/inbox",
  "relay_url": null,
  "timeout_sec": 3600,
  "resume_mode": "resume",
  "expected_sha256": "optional"
}
```

`resume_mode` 语义：

| 值 | 行为 |
|---|---|
| `resume` | 默认策略。发现部分文件时尝试续传。不得主动删除部分文件。 |
| `overwrite` | 明确允许覆盖已有文件。用于用户确认后的重新传输。 |
| `fail_if_exists` | 目标已存在或存在部分文件时直接失败。 |

Node 必须测试并固定本平台的 yq-croc request 语义。不能在 `resume` 模式下使用会强制覆盖已有文件的请求参数。yq-croc 版本和上游 croc 版本必须通过 `linux.transfer.croc.status` 暴露。

必须实现辅助能力：

- `linux.transfer.local.stat`：检查本地路径、大小、mtime、sha256、可读/可写、剩余空间。
- `linux.transfer.croc.reconcile`：Node 重启后扫描本地 transfer ledger，返回 interrupted/running/succeeded/failed 状态，供 Center 修复 TransferSession。

这些辅助能力不是替代 send/receive，而是让 Center 在大文件传输前后能建立事实。没有这些能力时，不得启用 Center schedule-based 大文件传输。

长时间 yq-croc Job 必须：

- 定期发送 `job.event` 进度事件，至少包含状态和已知的 stdout/stderr 摘要；
- 定期 `job.lease_renew`，避免 Center 将长传输误判为 timeout；
- 支持 job cancel，取消时只杀进程，不默认删除部分文件；
- 完成后计算 size/sha256 并上报；
- stdout/stderr 中如包含 code，必须脱敏。

## 5. 当前 Capabilities 分类

Linux Node 当前生产能力清单以 `nodes/linux/yequnode/yequnode-core/src/registry.rs` 的 `production::collect_manifests()` 为准。文档不手写完整 manifest JSON，避免和代码漂移；新增或删除 capability 必须修改 registry、对应 capability manifest、权限探测和测试。

当前能力分为：

| 分类 | 能力前缀或代表能力 | Runtime / 探测来源 |
|---|---|---|
| Primitive exec | `linux.exec.run` | user/sudo/root runtime；第一版静态按 `maintenance/write` 进入 Center 通用 approval/audit。 |
| Artifact | `linux.artifact.upload_file`、`linux.artifact.download_file`、`linux.artifact.upload_log`、`linux.artifact.diagnostics`、`linux.artifact.upload_proc`、`linux.artifact.screenshot_via_fb` | 需要 sudo/root 或可选 framebuffer 探测；必须遵守 artifact 合同。 |
| Transfer | `linux.transfer.local.stat`、`linux.transfer.croc.status`、`linux.transfer.croc.send`、`linux.transfer.croc.receive`、`linux.transfer.croc.reconcile` | yq-croc transfer runtime；由 `transfer.yq_croc` 配置和二进制可执行性决定是否注册 send/receive。 |

Linux Node 启动时必须构建 runtime snapshot，并用权限探测过滤 manifest：

- `user` runtime：`kind="privileged"`、`privilege="user"`，labels 至少包含 `linux`、`filesystem`、`filesystem:limited`、`network`。
- `sudo-limited` runtime：只有 sudo 可用时上报，labels 包含 `linux`、`filesystem`、`artifact`、`sudoers:yequnode`、`filesystem:host`。
- `linux-transfer` runtime：只有 `transfer.yq_croc.enabled=true` 且 `yq-croc` 可执行时上报，labels 为 `linux`、`transfer`、`yq-croc`。

所有文件能力必须遵循 runtime 可见性和 OS 权限。不可访问时必须返回稳定错误码，例如 `permission_denied`、`source_not_found`、`target_not_found` 或 `insufficient_space`，不得静默降级为“文件不存在”。

## 6. Job 执行规则

Node 本地执行必须遵守：

1. `job.poll` 收到 Job 后先持久化本地 Job 记录。
2. 发送 `job.accepted` 成功后再执行本地 function。
3. 执行期间可发送 `job.event`，但不能用 event 改变状态。
4. 成功必须发送 `job.finished(status="succeeded", output=...)`。
5. 失败必须发送 `job.finished(status="failed", error=...)`。
6. 不要重复发送终态；如果网络失败导致不确定，重启后通过 `node.reconcile_jobs` 对齐。
7. 长任务需要按 lease 续租；短任务可以不续租。

短任务应在 manifest 中给出紧凑 `timeout_sec`。长任务必须声明 `supports_progress`、`supports_cancel` 和对应 progress contract，并在执行期间续租；yq-croc send/receive 按本文 transfer 合同处理。

## 7. 错误传播

Node 不应静默 fallback。

如果本地 function 不存在、输入不合法、系统调用失败，应直接返回失败终态：

```json
{
  "job_id": "job_...",
  "status": "failed",
  "error": {
    "code": "function_execution_failed",
    "message": "ps command failed",
    "details": {
      "exit_code": 1,
      "stderr": "..."
    }
  }
}
```

不要伪造成功 output。不要把本地异常吞掉后返回空对象。

## 8. 多 Node 验证步骤

1. 在 Center 预配置 Linux node，生成 token。
2. Linux node 启动并发送 `node.hello`。
3. Console Nodes 页面应看到 Windows node 与 Linux node。
4. Console Node detail 或 `/admin/capabilities?node_id=<linux-node-id>` 应看到 `linux.exec.run`、artifact 和 transfer capabilities。
5. Agent target node 选择对应 Linux node id。
6. 调用 `linux.exec.run` 执行只读命令，例如 `uname -a`。
7. Linux node poll 到 Job，执行并 `job.finished`。
8. Center Job/Invocation/Timeline 页面能看到完整链路。

## 9. 当前明确不做

- 不提供绕过 Center policy/approval/audit 的任意 shell 执行入口。
- 不自动发现或自动注册未预配置 Node。
- 不让 Linux Node 主动发起 transfer、retry、resume、relay switch 或 code rotation。
- 不把 artifact bytes、croc code、relay password、Node token 或 Center token 写入 Agent prompt、tool JSON、普通日志或 Timeline。
- 不对权限不足的路径做静默 fallback。

新增高风险能力必须通过 `docs/node-capability-contract.md` 的 manifest、preflight、错误传播、审批和 runtime requirement 规则后才能注册。
