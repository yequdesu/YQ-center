# Linux Node Development Contract

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
