# Windows Node 传输能力重构计划

状态：待执行  
日期：2026-07-01  
范围：`E:\yequdesu_project\YeQu-Gateway-Win\Node-winClient`

## 1. 结论

Windows Node 删除全部 croc capability 和配置，新增 rclone SFTP transfer capability。Windows Node 不调用系统 OpenSSH，不要求用户配置 Windows OpenSSH Server；传输接收端由 `rclone serve sftp` 临时启动，发送端由 `rclone copy` / `rclone copyto` 推送。

## 2. 删除范围

以下 Windows Node 代码全部删除或替换：

| 文件 | 处理 |
|---|---|
| `node_win_client/config.py` | 删除 `TransferCrocConfig`，新增 `TransferRcloneConfig`。 |
| `node_win_client/plugins.py` | 删除 `windows.transfer.croc.status/send/receive/reconcile` manifest 和实现。删除 croc subprocess、stderr parser、ledger JSON 中的 croc 字段。 |
| `node_win_client/cli.py` | status/diagnostic 输出从 croc 改为 rclone。 |
| `node_win_client/daemon.py` | 插件构造参数从 `settings.transfer.croc` 改为 `settings.transfer.rclone`。 |
| `node_win_client/user_worker.py` | 用户态 worker 构造参数从 croc config 改为 rclone config。 |
| 所有日志/文档文案 | 删除 `croc` 字样。 |

## 3. 配置合同

`config.local.yaml` 改为：

```yaml
transfer:
  rclone:
    enabled: true
    binary_path: null
    advertise_host: null
    bind_host: null
    listen_port: 42981
    temp_dir: "./data/transfers"
    allow_send: true
    allow_receive: true
    max_concurrent_transfers: 1
```

字段语义：

| 字段 | 语义 |
|---|---|
| `binary_path` | 为空时使用 `shutil.which("rclone")`。 |
| `advertise_host` | source Node 访问 target Node 的 overlay 地址。为空时 runtime 不可用。 |
| `bind_host` | `rclone serve sftp` 监听地址。为空时使用 `advertise_host`。 |
| `listen_port` | `rclone serve sftp` 固定监听端口。第一版 `max_concurrent_transfers=1`，因此不做端口池。 |
| `temp_dir` | staging、ledger、临时 rclone config 所在目录。 |
| `max_concurrent_transfers` | 第一版固定验收值为 1。 |

## 4. 新增 dataclass

`node_win_client/config.py` 新增：

```python
@dataclass(frozen=True)
class TransferRcloneConfig:
    enabled: bool = True
    binary_path: str | None = None
    advertise_host: str | None = None
    bind_host: str | None = None
    listen_port: int = 42981
    temp_dir: str = "./data/transfers"
    allow_send: bool = True
    allow_receive: bool = True
    max_concurrent_transfers: int = 1
```

`TransferConfig` 改为：

```python
@dataclass(frozen=True)
class TransferConfig:
    rclone: TransferRcloneConfig = field(default_factory=TransferRcloneConfig)
```

不保留 `transfer.croc` 解析分支。

## 5. Capability manifest

Windows Node 注册以下能力：

### `windows.transfer.rclone.status`

effect: `read`  
risk: `safe`  
supports_progress: `false`  
supports_cancel: `false`

输出：

```json
{
  "transport": "rclone_sftp",
  "enabled": true,
  "installed": true,
  "executable": true,
  "binary_path": "C:\\Tools\\rclone\\rclone.exe",
  "version": "rclone vX.Y.Z",
  "advertise_host": "100.64.0.20",
  "bind_host": "100.64.0.20",
  "listen_port": 42981,
  "temp_dir": "E:\\...\\data\\transfers",
  "allow_send": true,
  "allow_receive": true,
  "limits": {
    "max_concurrent_transfers": 1,
    "byte_resume_supported": false
  },
  "error_code": null,
  "error_message": null
}
```

### `windows.transfer.rclone.receive`

effect: `external`  
risk: `maintenance`  
execution_context: `user`  
resource_keys: `["node.transfer"]`  
conflict_policy: `serialize`  
supports_progress: `true`  
supports_cancel: `true`  
supports_resume: `false`  
progress_contract: `transfer_progress_v1`

输入：

```json
{
  "transfer_id": "trf_x",
  "output_dir": "D:\\Downloads",
  "target_path": "D:\\Downloads\\file.bin",
  "username": "yequ_trf_x",
  "password": "secret",
  "conflict_mode": "fail_if_exists",
  "timeout_sec": 3600,
  "cleanup_on_failure": false,
  "expected_size_bytes": 123,
  "expected_sha256": null
}
```

行为：

1. 创建 staging 目录：`<temp_dir>/<transfer_id>/incoming`。
2. 启动：

```powershell
rclone serve sftp "<staging>" --addr "<bind_host>:<listen_port>" --user "<username>" --pass "<password>"
```

3. 对 `<bind_host>:<listen_port>` 做 TCP connect probe 成功后上报 `rclone_receiver_ready`。
4. 监听 `.yequ-transfer/<transfer_id>/done.json`。
5. 收到 done marker 后终止 rclone SFTP 子进程。
6. 校验 payload size/sha256。
7. 根据 `conflict_mode` 提交到 `target_path` 或 `output_dir`。
8. 返回实际 `received_path`、`received_kind`、`size_bytes`、`sha256`。

### `windows.transfer.rclone.send`

effect: `external`  
risk: `maintenance`  
execution_context: `user`  
resource_keys: `["node.transfer"]`  
conflict_policy: `serialize`  
supports_progress: `true`  
supports_cancel: `true`  
supports_resume: `false`  
progress_contract: `transfer_progress_v1`

输入：

```json
{
  "transfer_id": "trf_x",
  "source_path": "D:\\large.bin",
  "target_endpoint": {
    "host": "100.64.0.10",
    "port": 42981,
    "username": "yequ_trf_x",
    "password": "secret"
  },
  "target_payload_path": "payload/large.bin",
  "timeout_sec": 3600
}
```

行为：

1. 对 source 做 stat，目录递归计算总大小。
2. 写入临时 rclone config，ACL 限制为当前用户。
3. 对文件执行 `rclone copyto`，对目录执行 `rclone copy`。
4. rclone 参数固定包含：

```text
--config <temp-config>
--stats 1s
--stats-log-level NOTICE
--use-json-log
--retries 3
--low-level-retries 10
--transfers 4
--checkers 8
```

5. 解析 JSON log 中的 stats，转换为 `job.event`：

```json
{
  "phase": "transferring",
  "progress_source": "rclone_stats",
  "bytes_transferred": 123,
  "total_bytes": 456,
  "progress_pct": 26,
  "rate_bytes_per_sec": 789
}
```

6. 上传 `done.json` 到 `.yequ-transfer/<transfer_id>/done.json`。
7. 删除临时 rclone config。

凭据处理：

- target receive job 的 password 明文会出现在正在运行的 `rclone serve sftp --pass ...` 子进程 argv；
- source send job 的 password 明文只写入 `<temp_dir>/<transfer_id>/rclone.conf`；
- 临时 rclone config ACL 限制为当前用户；
- send job 成功、失败或取消后必须删除临时 rclone config；
- ledger、普通日志、Center observation 中只保存 `credential_hash`，不保存 password 明文。

### `windows.transfer.rclone.reconcile`

读取 `<temp_dir>/transfer-ledger.json`，返回 transfer 本地状态。ledger 中不保存 password 明文，只保存 `credential_hash`。

## 6. Ledger 合同

Windows Node ledger 文件仍为 JSON，但字段改为通用 transfer：

```json
{
  "transfer_id": "trf_x",
  "transport": "rclone_sftp",
  "role": "sender | receiver",
  "status": "created | running | interrupted | succeeded | failed | cancelled",
  "credential_hash": "sha256",
  "source_path": "D:\\large.bin",
  "target_path": "D:\\Downloads\\large.bin",
  "output_dir": "D:\\Downloads",
  "staging_dir": "...",
  "attempt_count": 1,
  "pid": 1234,
  "started_at": "...",
  "last_progress_at": "...",
  "completed_at": "...",
  "last_error_code": null,
  "last_error_message": null
}
```

## 7. 取消合同

`windows.transfer.rclone.receive` 取消：

- terminate rclone SFTP 子进程；
- 3 秒未退出则 kill；
- `cleanup_on_failure=true` 时删除 staging；
- 上报 `cancelled`。

`windows.transfer.rclone.send` 取消：

- terminate rclone copy 子进程；
- 3 秒未退出则 kill；
- 删除临时 config；
- 上报 `cancelled`。

## 8. 验收

Windows Node 修改完成后执行：

```powershell
python -m pytest
python -m ruff check .
python -m node_win_client.cli health
```

手工验收：

1. `windows.transfer.rclone.status` 返回 `installed=true`、`advertise_host` 非空。
2. Windows 作为 source 向 Linux target 传输 1 个大文件成功。
3. Windows 作为 target 接收 Linux source 传输 1 个大文件成功。
4. 传输过程中 Center Console 能看到 `bytes_transferred`、`rate_bytes_per_sec`、`progress_pct`。
5. 取消时 rclone 子进程退出，Job 不停留在 `cancelling`。

## 9. 外部依据

- rclone SFTP server 官方文档：`https://rclone.org/commands/rclone_serve_sftp/`
- rclone JSON log 官方文档：`https://rclone.org/docs/#use-json-log`
- rclone SFTP backend 官方文档：`https://rclone.org/sftp/`
