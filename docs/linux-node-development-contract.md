# Linux Node 开发合同

状态：当前开发约束
依赖协议：`YQP-Node-Protocol.md`
目标：在部署 Center 的 Linux 服务器或其他 Linux 主机上实现一个最小 Node，用于验证多 Node 调度与 Linux 能力接入。

## 1. 开发目标

第一版 Linux node 只做一件事：证明 Center 可以同时管理 Windows node 与 Linux node，并能把 Job 正确调度到目标 Node。

第一版不追求通用远控、不追求复杂插件市场、不追求二进制 artifact。截图/摄像头属于 Windows interactive node 的下一阶段能力，Linux node POC 不应被这些需求阻塞。

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

- `signal.report`：如果第一版没有周期 Signal，可以暂缓；但推荐至少上报 load/memory。
- `job.cancel`：poll 模式下没有独立 push cancel 通道，第一版可只支持本地取消标记和 cancelled 终态。

## 3. 配置约定

建议配置文件：

```yaml
node_id: linuxServer
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

第一版 Linux node 默认以普通用户运行，不要求 root。

Node 启动时必须对 capability 做 permission probe。未通过 probe 的能力不得注册为可用能力。Center 只根据 Node 上报的 runtime 和 capability 做调度，不替 Node 解决本地权限问题。

第一版策略：

| capability | 权限策略 |
|---|---|
| `linux.system.info` | 普通用户可执行。 |
| `linux.metrics.snapshot` | 普通用户可执行。 |
| `linux.process.list` | 普通用户可执行，但输出可能因权限受限而不完整，必须在 output 中标明 partial/limited。 |
| `linux.filesystem.stat` | 仅允许白名单路径，且必须当前用户可访问。不可访问时返回 `permission_denied`。 |

普通用户 runtime 示例：

```json
{
  "runtime_id": "user",
  "kind": "privileged",
  "status": "online",
  "interactive": false,
  "privilege": "user",
  "labels": ["linux", "filesystem:limited"],
  "metadata": {
    "uid": 1000,
    "user": "yequ",
    "visible_roots": ["/home/yequ", "/tmp"],
    "namespace": "host"
  }
}
```

后续如果需要 sudo/root 能力，必须新增独立 runtime 和独立 capability，不得混入普通 capability。

sudo/root runtime 示例：

```json
{
  "runtime_id": "sudo-limited",
  "kind": "privileged",
  "status": "online",
  "interactive": false,
  "privilege": "root",
  "labels": ["linux", "sudoers:yequnode", "filesystem:host"],
  "metadata": {
    "sudoers_file": "/etc/sudoers.d/yequnode",
    "allowed_commands": ["stat", "journalctl"]
  }
}
```

高权限 capability 示例：

```json
{
  "name": "linux.filesystem.stat.privileged",
  "risk": "maintenance",
  "effect": "read",
  "execution_requirements": {
    "runtime_kind": "privileged",
    "privilege": "root",
    "labels": ["filesystem:host"]
  }
}
```

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

## croc 传输工具部署合同

`croc` 属于 Node 本地运行时依赖，不属于 Center 的隐式能力。Center 可以随仓库提供 release 包，方便部署；但每个 Node 是否可用，必须由 Node 自己在启动和 capability 执行时探测并上报。

仓库内当前随附包位置：

```text
third_party/croc/v10.4.4/
  croc_v10.4.4_checksums.txt
  croc_v10.4.4_Linux-64bit.tar.gz
  croc_v10.4.4_Windows-64bit.zip
```

Linux Node 第一版推荐安装到 `/usr/local/bin/croc`：

```bash
cd /tmp
tar -xzf /path/to/croc_v10.4.4_Linux-64bit.tar.gz
sudo install -m 0755 croc /usr/local/bin/croc
croc --version
```

如果不能使用 `sudo`，可以安装到 Node daemon 用户自己的 bin 目录：

```bash
mkdir -p "$HOME/.local/bin"
tar -xzf /path/to/croc_v10.4.4_Linux-64bit.tar.gz -C "$HOME/.local/bin" croc
chmod 0755 "$HOME/.local/bin/croc"
"$HOME/.local/bin/croc" --version
```

Node 配置中应允许显式指定 croc 路径，不应只依赖 `PATH`：

```yaml
transfer:
  croc:
    enabled: true
    binary_path: /usr/local/bin/croc
    relay_url: null
    temp_dir: /tmp/yequ-transfer
    allow_send: true
    allow_receive: true
```

Linux Node 必须提供事实探测能力：

- `linux.transfer.croc.status`

该能力必须返回：

- `installed`：是否能执行 croc；
- `binary_path`：实际使用的二进制路径；
- `version`：`croc --version` 结果，无法获取则为 `null`；
- `relay_url`：当前配置的 relay，未配置则为 `null`；
- `temp_dir`：传输临时目录；
- `daemon_user`：daemon 当前用户；
- `allow_send` / `allow_receive`；
- `limits`：Node 本地限制，例如允许路径、最大并发数；
- `error`：不可用时的明确错误。

未安装或不可执行时，`linux.transfer.croc.status` 不应注册为“成功的传输能力”的替代品，也不允许 fallback 到 YQP artifact upload。它应返回明确失败或明确的 `installed=false` 状态，让 Center 和 Agent 基于事实决策。

后续 send/receive 能力必须基于 status 探测事实：

- `linux.transfer.croc.send`
- `linux.transfer.croc.receive`

如果 `croc` 不可用、目标路径不允许、权限不足、relay 不可用或校验失败，必须让 Job 失败并传播错误，不得静默降级。

### croc 断点续传与 Node 配合

croc 本身支持 interrupted transfer resume，但这不是 Center 单方面能保证的能力。Linux Node 必须配合保存本地传输事实，并在重试时重新构造与上次兼容的 croc 命令。

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
- `attempt_count`
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

Node 必须测试并固定本平台的 croc 调用方式。不能在 `resume` 模式下使用会强制覆盖已有文件的参数。不同 croc 版本的行为差异必须通过 `linux.transfer.croc.status` 暴露。

建议增加辅助能力：

- `linux.transfer.local.stat`：检查本地路径、大小、mtime、sha256、可读/可写、剩余空间。
- `linux.transfer.croc.reconcile`：Node 重启后扫描本地 transfer ledger，返回 interrupted/running/succeeded/failed 状态，供 Center 修复 TransferSession。

这些辅助能力不是替代 send/receive，而是让 Center 在大文件传输前后能建立事实。没有这些能力时，第一版仍可手动传输，但不能声称具备可靠断点续传编排。

长时间 croc Job 必须：

- 定期发送 `job.event` 进度事件，至少包含状态和已知的 stdout/stderr 摘要；
- 定期 `job.lease_renew`，避免 Center 将长传输误判为 timeout；
- 支持 job cancel，取消时只杀进程，不默认删除部分文件；
- 完成后计算 size/sha256 并上报；
- stdout/stderr 中如包含 code，必须脱敏。

## 5. 第一版 Capabilities

建议第一版只注册安全读能力：

```json
{
  "plugin_id": "linux.system",
  "plugin_version": "0.1.0",
  "functions": [
    {
      "name": "linux.system.info",
      "description": "Return basic Linux host information.",
      "agent_description": "Inspect Linux host OS, kernel, uptime, CPU and memory summary.",
      "input_schema": {
        "type": "object",
        "properties": {},
        "additionalProperties": false
      },
      "output_schema": { "type": "object" },
      "risk": "safe",
      "effect": "read",
      "timeout_sec": 5,
      "idempotency": "idempotent",
      "execution_requirements": {
        "runtime_kind": "privileged",
        "labels": ["linux"]
      }
    },
    {
      "name": "linux.metrics.snapshot",
      "description": "Return CPU, memory, disk and load metrics.",
      "agent_description": "Read a Linux metrics snapshot.",
      "input_schema": {
        "type": "object",
        "properties": {},
        "additionalProperties": false
      },
      "output_schema": { "type": "object" },
      "risk": "safe",
      "effect": "read",
      "timeout_sec": 5,
      "idempotency": "idempotent",
      "execution_requirements": {
        "runtime_kind": "privileged",
        "labels": ["linux"]
      }
    },
    {
      "name": "linux.process.list",
      "description": "List Linux processes with pid, user, cpu, memory and command.",
      "agent_description": "List running Linux processes.",
      "input_schema": {
        "type": "object",
        "properties": {
          "limit": { "type": "integer", "minimum": 1, "maximum": 200, "default": 50 }
        },
        "additionalProperties": false
      },
      "output_schema": { "type": "object" },
      "risk": "safe",
      "effect": "read",
      "timeout_sec": 5,
      "idempotency": "idempotent",
      "execution_requirements": {
        "runtime_kind": "privileged",
        "labels": ["linux"]
      }
    },
    {
      "name": "linux.filesystem.stat",
      "description": "Return stat information for a whitelisted path.",
      "agent_description": "Inspect Linux filesystem metadata for an allowed path.",
      "input_schema": {
        "type": "object",
        "properties": {
          "path": { "type": "string" }
        },
        "required": ["path"],
        "additionalProperties": false
      },
      "output_schema": { "type": "object" },
      "risk": "safe",
      "effect": "read",
      "timeout_sec": 5,
      "idempotency": "idempotent",
      "execution_requirements": {
        "runtime_kind": "privileged",
        "labels": ["linux"]
      }
    }
  ],
  "signals": [
    {
      "name": "linux.system.load",
      "scope": "node",
      "ttl_sec": 30,
      "value_schema": { "type": "object" }
    }
  ]
}
```

`linux.filesystem.stat` 必须做路径白名单，第一版建议只允许：

- `/`
- `/tmp`
- `/var/log`
- 项目部署目录

不要在第一版提供任意文件读取。

## 6. Job 执行规则

Node 本地执行必须遵守：

1. `job.poll` 收到 Job 后先持久化本地 Job 记录。
2. 发送 `job.accepted` 成功后再执行本地 function。
3. 执行期间可发送 `job.event`，但不能用 event 改变状态。
4. 成功必须发送 `job.finished(status="succeeded", output=...)`。
5. 失败必须发送 `job.finished(status="failed", error=...)`。
6. 不要重复发送终态；如果网络失败导致不确定，重启后通过 `node.reconcile_jobs` 对齐。
7. 长任务需要按 lease 续租；短任务可以不续租。

第一版建议所有能力都是短任务，执行时间不超过 5 秒。

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
4. Console Node detail 或 `/admin/capabilities?node_id=linuxServer` 应看到 Linux capabilities。
5. Agent target node 选择 `linuxServer`。
6. 调用 `linux.system.info`。
7. Linux node poll 到 Job，执行并 `job.finished`。
8. Center Job/Invocation/Timeline 页面能看到完整链路。

## 9. 第一版明确不做

- 任意 shell 执行。
- 任意文件读取。
- 上传/下载大文件。
- 二进制 artifact。
- WebSocket push。
- 自动发现/自动注册未预配置 Node。
- Linux desktop 截图或摄像头。

这些能力应在多 Node 基础链路稳定后再加。
