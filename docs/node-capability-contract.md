# Node Capability 合同

状态：当前 Node 能力开发硬约束  
日期：2026-06-30  
适用对象：Windows Node、Linux Node、未来 macOS/容器/浏览器 Node  
依赖协议：`YQP-Node-Protocol.md`

## 1. 目的

本合同约束 Node capability 的精细程度，避免实现时出现“能用就行”、描述含糊、schema 缺失、错误不可诊断、Agent 只能靠猜的问题。

YQP 只定义 Center 与 Node 的通信协议。具体业务能力必须通过 capability manifest 表达清楚：

- 能力能做什么；
- 不能做什么；
- 需要哪些输入；
- 输出稳定包含哪些字段；
- 需要什么 runtime / 权限；
- 是否需要 preflight；
- 是否支持 progress / cancel / resume；
- 失败时如何稳定上报错误。

如果 capability 合同不完整，Center 不应把它作为 Agent 可可靠使用的能力。

Center registry 当前提供非破坏性合同诊断：`node.status`、`capability.describe`
和 diagnostics projection 会在每个 source 上返回 `contract_issues`。第一版不会拒绝
Node 注册，但新增或修改能力前必须处理这些问题，不能把 warning/error 长期留给 Agent
运行时试错。

开发期可以直接运行：

```bash
yequ capabilities lint
yequ capabilities lint --node linux-node-01
yequ capabilities lint --platform linux --warnings-as-errors
```

该命令读取 Center `/admin/meta/capabilities/search?projection=diagnostics` 的结果：

- 存在 `severity=error` 时退出码为 1；
- 默认 warning 只输出不失败；
- 加 `--warnings-as-errors` 后 warning 也会使退出码为 1；
- `--format json` 可输出稳定 JSON，供 CI 或其他 Agent 解析。

## 2. Manifest 必填精度

每个面向 Agent 或 Center workflow 的 function manifest 必须包含：

| 字段 | 要求 |
|---|---|
| `name` | 必须平台/领域清晰。Windows 用 `windows.*`，Linux 用 `linux.*`，Center workflow 用无平台前缀如 `transfer.create`。 |
| `description` | 面向人类/API，说明能力边界。不能只写 “run command” 或 “file operation”。 |
| `agent_description` | 面向 Agent，必须短、明确、带限制。 |
| `input_schema` | 必须完整声明 required、类型、枚举、范围、additionalProperties。 |
| `output_schema` | 必须声明稳定输出字段。允许 JSON 值为 object/list/string，但语义字段必须稳定。 |
| `risk` | 必须是 `safe`、`maintenance`、`destructive`、`catastrophic`。 |
| `effect` | 必须是 `read`、`write`、`destructive`、`external`。 |
| `timeout_sec` | 必须符合实际执行预期。长任务不能伪装成 5 秒短任务。 |
| `resource_keys` | 写操作、外部传输、服务操作、共享硬件操作必须提供。 |
| `conflict_policy` | 有共享资源时必须声明 `serialize` 或 `reject_if_running`。 |
| `execution_requirements` | 必须声明 runtime_kind、labels、interactive、privilege 等可调度事实。 |
| `preflight_supported` | 需要先决事实的能力必须声明。 |
| `supports_progress` | 长任务必须声明。 |
| `supports_cancel` | 长任务或外部子进程能力必须声明。 |
| `supports_resume` | 断点续传或可恢复任务必须声明。 |

Center registry 会读取以下合同字段，并用于 `capability.search` / `capability.describe`
结构化筛选和投影：

```json
{
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
  ],
  "progress_contract": "transfer_progress_v1",
  "error_contract": "node_error_v1",
  "examples": [
    {
      "input": {"path": "/tmp/a.txt"},
      "output_summary": "returns exists/readable/writable/size/sha256"
    }
  ]
}
```

## 3. ExecutionGuard 与 PolicyEngine 边界

Node manifest 必须提供足够事实，让 Center 的执行门禁能分层判断。

推荐调用链：

```text
normalize command
  -> resolve capability
  -> ExecutionGuard
  -> PolicyEngine
  -> ExecutionAdmissionService
  -> runtime handler
```

职责边界：

| 构件 | 负责 | 不负责 |
|---|---|---|
| `ExecutionGuard` | 先决条件、硬约束、事实依赖。例如写前必须读、目标目录必须可写、源文件必须存在。 | 不判断用户是否有权批准高风险操作，不创建 ApprovalRequest。 |
| `PolicyEngine` | execution mode、risk、effect、L2 写操作审批、allow/ask/deny。 | 不探测文件路径、磁盘空间、runtime 是否匹配。 |
| `ExecutionAdmissionService` | inline / sync_wait / waitable_operation / workflow_operation。 | 不做权限审批，不做 preflight。 |

不建议把 `PolicyEngine` 合并进 `ExecutionGuard`。原因：

- Guard 是事实门禁：缺事实或事实不满足就阻断。
- Policy 是授权门禁：事实满足后仍可能需要审批或拒绝。
- 合并后会形成新的大总管，既管事实、又管授权、又可能管调度，重蹈 v1 大入口问题。

可以新增 `ExecutionGate` 作为轻薄编排门面：

```text
ExecutionGate.evaluate(command)
  -> guard_decision
  -> policy_decision
  -> admission_plan
```

`ExecutionGate` 只组合结果，不吞并 `ExecutionGuard` / `PolicyEngine` / `Admission` 的职责。

## 4. Preflight 合同

需要 preflight 的 capability 必须满足：

- preflight 能在不产生目标副作用的情况下获取执行先决事实；
- preflight 输出必须结构化；
- preflight 失败不得被 Node 静默降级；
- Center 可以根据 preflight 结果决定 `allow`、`needs_input`、`preflight_required`、`preflight_failed` 或 `blocked`。

标准 preflight 输出：

```json
{
  "ok": false,
  "facts": {
    "source.exists": true,
    "source.readable": true,
    "source.runtime.installed": true,
    "source.runtime.allow_send": true,
    "target.parent_writable": false,
    "target.runtime.allow_receive": true
  },
  "missing_facts": [],
  "failed_preconditions": [
    {
      "name": "target.parent_writable",
      "error_code": "permission_denied",
      "message": "target parent is not writable"
    }
  ],
  "observed_at": "2026-06-30T12:00:00Z"
}
```

## 5. Progress 合同

长任务必须上报 `job.event`。事件只表示过程事实，不改变 Job 终态。

标准进度事件：

```json
{
  "event_type": "job.progress",
  "sequence": 12,
  "data": {
    "phase": "transferring",
    "message": "transferring file",
    "progress_pct": 42,
    "bytes_transferred": 52428800,
    "total_bytes": 120945608,
    "rate_bytes_per_sec": 4194304,
    "eta_sec": 16
  }
}
```

如果无法提供准确百分比：

- `progress_pct` 必须为 `null` 或省略；
- 可以上报 `status=running` keepalive；
- keepalive 事件必须标记 `progress_source="process_keepalive"`，并尽量携带 `process_pid`、`phase`、`role`、`transfer_id`；
- 不得伪造百分比。

croc 传输真实进度合同：

- Node 必须优先使用 croc 自身的传输进度输出作为真实进度源，标记 `progress_source="croc_stderr"`；
- sender 端读取到 croc `Code is:` 后，必须等待一个 relay settle 窗口后再上报 `progress_source="croc_sender_ready"`、`phase="sender_ready"`、`sender_ready=true` 的 `transfer_progress` 事件；该事件不得泄漏 croc code 明文；
- `Code is:` 不是 relay room ready 信号。croc v10.4.4 源码中该输出发生在 `sendCollectFiles()` 之后、sender 连接 relay 之前；实测在 `Code is:` 后立即启动 receiver 会触发 `room (secure channel) not ready, maybe peer disconnected`，约 0.5 秒后启动可成功。合同规定默认 settle 窗口为 1 秒；
- Node 以子进程方式启动 croc 时必须传入 `--ignore-stdin`，避免 croc 在 stdin 非字符设备时把 stdin 当作发送内容而忽略文件路径；
- Node 托管的 croc sender 必须使用确定性 relay 模式：`croc ... send --no-local --no-multi <path>`。不得启用 croc 默认 local discovery/local relay/multiplex 多路径行为，原因是这些交互式默认行为会引入 Center 无法观测的连接竞态；
- receive 端遇到 `room (secure channel) not ready` 或 `could not secure channel` 时，必须按瞬态握手失败处理，在同一个 Job 内使用相同 code/relay/output_dir 重试，不得直接把 Job 标记为最终失败；只有超过重试次数、总 timeout、收到取消或出现非瞬态错误时才允许失败；
- `sender_ready` 是粘性事实，不是瞬时 UI 状态。sender 后续上报 `croc_stderr` 进度时必须继续携带 `sender_ready=true`，避免 Center 等待同步点时被普通进度覆盖；
- `croc_stderr` 进度事件应尽量携带 `progress_pct`、`bytes_transferred`、`total_bytes`、`rate_bytes_per_sec`、`eta_sec`；
- `total_bytes` 应优先使用 Node/Center 预检得到的精确 stat 值，不得用 croc 终端显示中的四舍五入大小覆盖精确字节数；
- `process_keepalive` 只表示子进程仍在运行，不得携带 `progress_pct`；
- 接收端输出目录大小不等价于已传输字节。croc 可能提前创建或扩展目标文件，因此目录大小只能作为 observation 上报，必须标记 `progress_source="receiver_output_size_observation"`，不得携带 `bytes_transferred` / `progress_pct` / `eta_sec`；
- 如果当前 croc 版本或平台无法提供可解析的真实进度，Node 只能上报 keepalive，Console 显示不确定进度条。

Center 行为：

- 完整 `job.event` 永远写入 Timeline；
- `job.progress`、`transfer_progress` 等进度事件会同步投影到 `Job.progress_pct` / `Job.progress_message`；
- `Job.progress_*` 只表示最新进度 read model，不改变 Job 终态；
- Operation Runtime 从 Job read model 聚合长任务进度，Console 从 Operation 投影展示进度。

## 6. Cancel 合同

长任务、外部子进程、传输能力必须支持 cancel。

要求：

- 收到 Center 的取消信号后尽快 terminate 子进程；
- terminate 失败时升级 kill；
- 取消后必须上报 `cancelled`、`failed` 或 `interrupted` 终态；
- 不得让 Job 长期卡在 `cancelling`；
- 取消默认不删除部分文件，除非 input 明确要求清理。

## 7. Error 合同

错误必须稳定、可诊断、可被 Agent 总结。

标准错误字段：

```json
{
  "error_code": "permission_denied",
  "error_message": "target parent is not writable",
  "error_details": {
    "path": "/root/a.bin",
    "operation": "write",
    "stderr_tail": "...",
    "returncode": 13
  }
}
```

错误码建议：

| 错误码 | 含义 |
|---|---|
| `invalid_input` | input schema 通过后仍发现语义无效。 |
| `source_not_found` | 源路径不存在。 |
| `target_not_found` | 目标或父目录不存在且不能创建。 |
| `permission_denied` | 权限不足。 |
| `insufficient_space` | 空间不足。 |
| `runtime_unavailable` | runtime 不在线或不匹配。 |
| `dependency_missing` | croc、系统命令或库缺失。 |
| `dependency_not_executable` | 依赖存在但不可执行。 |
| `operation_timeout` | 本地执行超时。 |
| `cancelled` | 用户或 Center 取消。 |
| `external_service_failed` | relay、网络、外部服务失败。 |
| `integrity_mismatch` | size/hash 校验失败。 |

## 8. Artifact 与文件流转能力

Node 必须区分三类文件流转：

| 方向 | 能力 |
|---|---|
| Node -> Center | `<platform>.artifact.upload_file` |
| Center -> Node | `<platform>.artifact.download_file` |
| Node -> Node | `transfer.create` workflow 调用 `<platform>.transfer.croc.send/receive` |

`<platform>.artifact.download_file` 合同：

输入：

```json
{
  "artifact_id": "id_x",
  "output_path": "/tmp/a.bin",
  "mode": "overwrite | fail_if_exists"
}
```

输出：

```json
{
  "artifact_id": "id_x",
  "output_path": "/tmp/a.bin",
  "size_bytes": 123,
  "sha256": "...",
  "expected_sha256": "...",
  "content_type": "application/octet-stream",
  "verified": true
}
```

要求：

- 必须先 preflight 目标父目录；
- 必须校验 size/hash；
- 必须走 Center 授权下载 URL 或 YQP artifact fetch 机制，使用 Node Bearer token；
- 不得让 Node 使用 admin token；
- 不得把 artifact bytes 通过 Agent prompt、tool JSON 或日志传递；
- 支持大文件时必须流式写入本地文件，不能一次性把响应读入内存；
- 若尚未支持断点续传，必须声明 `supports_resume=false`，不得伪装为可续传。

Center `contract_issues` 当前会对 `<platform>.artifact.download_file` 做专门诊断：

| issue code | 含义 |
|---|---|
| `artifact_download_effect_must_be_write` | artifact download 必须声明 `effect=write`。 |
| `artifact_download_missing_intent_slots` | 必须声明 `required_intent_slots` 至少包含 `artifact_id` 和 `output_path`。 |
| `artifact_download_missing_preconditions` | 必须声明 artifact 可用性和目标路径写入相关 preconditions。 |
| `artifact_download_resume_not_supported` | 未实现可恢复下载前不得声明 `supports_resume=true`。 |

## 9. Transfer 能力合同

`<platform>.transfer.local.stat` 必须输出：

- `exists`
- `kind`
- `is_file`
- `is_dir`
- `readable`
- `writable`
- `parent_exists`
- `parent_writable`
- `size_bytes`
- `mtime`
- `sha256`
- `free_space_bytes`
- `error_code`
- `error_message`

`<platform>.transfer.croc.status` 必须输出：

- `installed`
- `executable`
- `version`
- `binary_path`
- `allow_send`
- `allow_receive`
- `relay_url`
- `supported_flags`
- `temp_dir`
- `max_concurrent`
- `error_code`
- `error_message`

`send/receive` 必须：

- 支持 `transfer_id` 幂等；
- 写本地 ledger；
- 支持 cancel；
- 支持 progress；
- 完成后输出实际路径、size、sha256；
- stdout/stderr 必须脱敏。

## 10. 合同验收

新增或修改 Node capability 前，必须通过：

- manifest 字段完整性检查；
- Center `contract_issues` 检查；新增能力不得留下与当前能力目标冲突的 error；
- input/output schema 检查；
- risk/effect/resource_keys/conflict_policy 检查；
- runtime requirements 检查；
- preflight/progress/cancel/resume 声明检查；
- 至少一个成功用例；
- 至少一个失败用例；
- 错误码稳定性检查；
- Center `capability.describe` 可解释该能力何时可用、需要什么权限、会产生什么副作用。
