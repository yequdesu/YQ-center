# Center 传输能力重构计划

状态：待执行  
日期：2026-07-01  
范围：`E:\yequdesu_project\YQ-center-review`

## 1. 结论

Center 的 Node-to-Node 大文件传输主路径切换为 `rclone_sftp`。`croc` 从 Center 代码、测试、文档和 bundled third_party 包中彻底删除，不保留 fallback、不保留兼容字段、不保留 Agent 描述中的 croc 文案。

目标数据面：

```text
Center -> target Node: 启动临时 rclone SFTP 接收服务
Center -> source Node: 使用 rclone 推送 payload 和完成标记
source Node -> target Node: overlay 网络直连传输
Node -> Center: job.event 上报进度、校验和终态
```

Center 仍只做控制面，不接收、不转发大文件 bytes。

## 2. 当前删除范围

以下 Center 文件中的 croc 逻辑全部删除或改名为 `rclone_sftp` 语义：

| 文件 | 处理 |
|---|---|
| `third_party/croc/` | 整个目录删除。Center 仓库不再分发 croc。 |
| `src/yequ/application/transfer.py` | 删除 croc code 生成、sender ready gate、croc 错误分类、`transfer.croc.*` capability 调用。重写为 rclone SFTP workflow。 |
| `src/yequ/models/transfer.py` | `transport` 默认值改为 `rclone_sftp`。删除 `relay_url`、`code_hash` 字段。新增 `conflict_mode`。 |
| `alembic/versions/*add_transfer_sessions*` 后续 migration | 新增迁移，移除 croc 字段，新增 rclone 字段。 |
| `src/yequ/services/capability_registry.py` | 将 transfer contract 诊断从 `transfer.croc.send/receive` 改为 `transfer.rclone.send/receive`。删除 croc 字样。 |
| `src/yequ/services/node_service.py` | 删除 `croc_sender_ready` 粘性投影逻辑。保留通用 progress read model。 |
| `src/yequ/api/routes/agent.py` | Agent 工具描述从 croc 改为 Center-managed rclone SFTP transfer。 |
| `src/yequ/agent/prompt_policy.py` | 删除 “unavailable croc runtime” 文案，改为 “unavailable transfer runtime”。 |
| `tests/test_transfer_session.py` | 全量改写为 rclone SFTP workflow 测试。 |
| `tests/test_node_progress_projection.py` | 删除 croc stderr / sender ready 测试，新增 rclone stats / receiver ready / output marker 测试。 |
| `tests/test_capability_runtime_registry.py` | croc capability 注册和搜索测试改为 rclone capability。 |
| `docs/current-project-overview.md` | 将 croc 状态改为废弃，更新为 rclone_sftp 主路径。 |
| `docs/node-capability-contract.md` | 删除 croc 专章，新增 rclone SFTP transfer 合同。 |

## 3. Center API 合同

Center 保留 Agent/Console 面向的元工具名：

- `transfer.preflight`
- `transfer.create`
- `transfer.status`
- `transfer.cancel`

`transfer.create` 输入删除：

- `code`
- `relay_url`
- `resume_mode`

`transfer.create` 输入新增：

```json
{
  "conflict_mode": "fail_if_exists | overwrite | reuse_complete",
  "cleanup_on_failure": false
}
```

字段语义：

| 字段 | 语义 |
|---|---|
| `fail_if_exists` | 目标路径已存在时阻断。 |
| `overwrite` | 目标路径已存在时以 staging 完成后原子替换。 |
| `reuse_complete` | 目标已存在且 size/sha256 匹配时直接完成；不匹配时重新传输并覆盖 staging。 |
| `cleanup_on_failure` | 失败后是否删除 target Node staging 目录。默认 `false`，保留现场用于诊断。 |

Center 不提供 byte-level resume 承诺。第一版提供幂等重试和原子提交：同一 `transfer_id` 重试时复用 target staging，已完成且校验匹配的文件不重复落盘；未完成文件重新传输。

## 4. Capability 合同

Center 通过 canonical capability ref 调用以下能力。Node 注册时仍带平台前缀：

| canonical ref | Windows 注册名 | Linux 注册名 | 用途 |
|---|---|---|---|
| `transfer.local.stat` | `windows.transfer.local.stat` | `linux.transfer.local.stat` | 源/目标路径事实。 |
| `transfer.rclone.status` | `windows.transfer.rclone.status` | `linux.transfer.rclone.status` | rclone 和 overlay endpoint 运行时事实。 |
| `transfer.rclone.receive` | `windows.transfer.rclone.receive` | `linux.transfer.rclone.receive` | target 端临时 SFTP 接收服务。 |
| `transfer.rclone.send` | `windows.transfer.rclone.send` | `linux.transfer.rclone.send` | source 端推送 payload 和完成标记。 |
| `transfer.rclone.reconcile` | `windows.transfer.rclone.reconcile` | `linux.transfer.rclone.reconcile` | 读取本地 transfer ledger。 |

`transfer.rclone.status` 输出必须包含：

```json
{
  "transport": "rclone_sftp",
  "enabled": true,
  "installed": true,
  "executable": true,
  "version": "rclone vX.Y.Z",
  "binary_path": "/usr/bin/rclone",
  "advertise_host": "100.64.0.10",
  "bind_host": "100.64.0.10",
  "listen_port": 42981,
  "allow_send": true,
  "allow_receive": true,
  "temp_dir": "/tmp/yequ-transfer",
  "limits": {
    "max_concurrent_transfers": 1,
    "byte_resume_supported": false
  },
  "error_code": null,
  "error_message": null
}
```

`advertise_host` 是 Node overlay 地址。`listen_port` 是 target receive job 固定使用的 rclone SFTP 监听端口。`advertise_host` 为空或 `listen_port` 被占用时 runtime 不可用，preflight 返回 `transfer_endpoint_unavailable`。

## 5. TransferSession 模型

`TransferSession.transport` 固定写入 `rclone_sftp`。

模型字段调整：

| 操作 | 字段 |
|---|---|
| 删除 | `relay_url` |
| 删除 | `code_hash` |
| 删除 | `resume_mode` |
| 新增 | `conflict_mode`，`String(32)`，默认 `fail_if_exists` |
| 保留 | `metadata_json`，保存 endpoint 摘要、staging 相对路径、manifest hash，不保存口令明文 |

临时 SFTP 口令明文只允许存在于：

- target receive job input；
- source send job input；
- target Node 正在运行的 `rclone serve sftp --pass ...` 子进程 argv；
- source Node 临时 rclone config 文件。

口令不写入 `TransferSession`、Timeline、普通日志、Agent observation 或 Node ledger。Node ledger 只保存 `credential_hash`。source Node 临时 rclone config 在 send job 结束、失败或取消时删除。

## 6. Center 编排顺序

`TransferApplicationService.create()` 重写为以下确定流程：

1. 校验 input 和 preflight。
2. 创建 `TransferSession(status="created", transport="rclone_sftp")`。
3. 生成一次性 SFTP 凭据：
   - `username = "yequ_" + transfer_id`
   - `password = secrets.token_urlsafe(32)`
4. 调用 target `transfer.rclone.receive`，`wait_for_result=False`。
5. 等待 target job 上报：

```json
{
  "phase": "receiver_ready",
  "progress_source": "rclone_receiver_ready",
  "receiver_ready": true,
  "endpoint": {
    "host": "100.64.0.10",
    "port": 42981,
    "username": "yequ_trf_x"
  }
}
```

6. 调用 source `transfer.rclone.send`，传入 endpoint、password、source_path、target relative payload path。
7. source job 使用 rclone 上传 payload，然后上传完成标记：

```text
.yequ-transfer/<transfer_id>/done.json
```

8. target receive job 发现 `done.json` 后停止 SFTP 服务，校验 payload，执行 staging 到最终目标路径的原子提交。
9. `transfer.status` 聚合 source/target job：
   - 两端均 `succeeded`：`TransferSession.status="succeeded"`；
   - 任一端 `failed`：取消对端非终态 job，`TransferSession.status="failed"`；
   - 任一端 `timeout`：取消对端非终态 job，`TransferSession.status="timeout"`；
   - 任一端 `cancelled`：取消对端非终态 job，`TransferSession.status="cancelled"`。

## 7. Progress 合同

Center 接收通用 progress，不保留 croc 专用投影。

Node 上报以下 progress source：

| progress_source | 来源 | 是否可用于百分比 |
|---|---|---|
| `rclone_receiver_ready` | target SFTP 服务已监听且本机 TCP probe 成功 | 否 |
| `rclone_stats` | source rclone `--use-json-log --stats 1s` stats 行 | 是 |
| `process_keepalive` | 子进程仍运行 | 否 |
| `target_verify` | target 校验和提交阶段 | 可为 95-100 |

`node_service._apply_job_event_projection()` 保留：

- `progress_pct`
- `progress_message`
- `bytes_transferred`
- `total_bytes`
- `rate_bytes_per_sec`
- `eta_sec`
- `phase`
- `progress_source`

删除：

- `sender_ready` 粘性逻辑；
- `croc_sender_ready` 分支；
- `croc_stderr` 特判。

## 8. Preflight 合同

`transfer.preflight` 调用：

1. source `transfer.local.stat(source_path, include_sha256=false)`；
2. target `transfer.local.stat(target_output_dir 或 target_path.parent, include_sha256=false)`；
3. source `transfer.rclone.status`；
4. target `transfer.rclone.status`。

失败码固定为：

| 条件 | error code |
|---|---|
| source stat 失败 | `source_stat_failed` |
| source 不存在 | `source_not_found` |
| source 不可读 | `source_not_readable` |
| target 父目录不存在 | `target_parent_not_found` |
| target 父目录不可写 | `target_not_writable` |
| target 空间不足 | `insufficient_space` |
| rclone 不存在 | `rclone_not_installed` |
| rclone 不可执行 | `rclone_not_executable` |
| overlay host 缺失 | `transfer_endpoint_unavailable` |
| send 禁用 | `send_not_allowed` |
| receive 禁用 | `receive_not_allowed` |

## 9. 测试改造

Center 测试改造项：

- `test_transfer_create_schedules_receiver_and_sender_jobs` 断言 job 顺序为 target receive、source send。
- 新增 `test_transfer_create_waits_for_rclone_receiver_ready`。
- 新增 `test_transfer_status_succeeds_after_done_marker_and_target_verify`。
- 新增 `test_transfer_status_cancels_receiver_when_source_fails`。
- 新增 `test_transfer_preflight_requires_rclone_status`。
- 删除所有 croc secure channel、sender ready、croc stderr 相关断言。
- `capability_runtime_registry` 搜索 `rclone`，不再搜索 `croc`。
- `node_progress_projection` 用 `rclone_stats` 样例覆盖进度投影。

## 10. 验收命令

Center 修改完成后执行：

```bash
pytest tests/test_transfer_session.py
pytest tests/test_node_progress_projection.py
pytest tests/test_capability_runtime_registry.py
pytest
ruff check .
mypy src/
```

## 11. 外部依据

- rclone remote control/API 官方文档：`https://rclone.org/rc/`
- rclone SFTP server 官方文档：`https://rclone.org/commands/rclone_serve_sftp/`
- rclone JSON log 官方文档：`https://rclone.org/docs/#use-json-log`
