# Linux Node 传输能力重构计划

状态：待执行  
日期：2026-07-01  
范围：`E:\yequdesu_project\YQ-center-review\nodes\linux\yequnode`

## 1. 结论

Linux Node 删除全部 croc 模块和配置，新增 rclone SFTP transfer capability。Linux Node 与 Windows Node 使用同一 capability 合同、同一数据面协议、同一 progress/error 字段。

## 2. 删除范围

以下文件删除：

```text
nodes/linux/yequnode/yequnode-core/src/capability/croc_command.rs
nodes/linux/yequnode/yequnode-core/src/capability/croc_progress.rs
nodes/linux/yequnode/yequnode-core/src/capability/linux_transfer_croc_status.rs
nodes/linux/yequnode/yequnode-core/src/capability/linux_transfer_croc_send.rs
nodes/linux/yequnode/yequnode-core/src/capability/linux_transfer_croc_receive.rs
nodes/linux/yequnode/yequnode-core/src/capability/linux_transfer_croc_reconcile.rs
```

以下文件修改：

| 文件 | 处理 |
|---|---|
| `yequnode-core/src/capability/mod.rs` | 删除 croc module，新增 rclone module。 |
| `yequnode-core/src/registry.rs` | 删除 croc capability 注册，注册 rclone capability。 |
| `yequnode-core/src/config.rs` | 删除 `CrocConfig`，新增 `RcloneConfig`。 |
| `yequnode-core/src/daemon.rs` | runtime snapshot 从 croc 改为 rclone。 |
| `yequnode-core/src/transfer_ledger.rs` | 注释和字段改为通用 transfer，不出现 croc 字样。 |
| `yequnode-core/src/capability/linux_transfer_local_stat.rs` | 输出字段与 Center 合同对齐。hash 改为流式。 |

## 3. 新增模块

新增文件：

```text
yequnode-core/src/capability/rclone_command.rs
yequnode-core/src/capability/rclone_progress.rs
yequnode-core/src/capability/linux_transfer_rclone_status.rs
yequnode-core/src/capability/linux_transfer_rclone_receive.rs
yequnode-core/src/capability/linux_transfer_rclone_send.rs
yequnode-core/src/capability/linux_transfer_rclone_reconcile.rs
```

## 4. 配置合同

`~/.yequnode/config.yaml` 改为：

```yaml
transfer:
  rclone:
    enabled: true
    binary_path: "/usr/bin/rclone"
    advertise_host: null
    bind_host: null
    listen_port: 42981
    temp_dir: "/tmp/yequ-transfer"
    allow_send: true
    allow_receive: true
    max_concurrent_transfers: 1
```

`config.rs` 新增：

```rust
#[derive(Debug, Clone, Deserialize)]
pub struct RcloneConfig {
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default = "default_rclone_binary_path")]
    pub binary_path: String,
    #[serde(default)]
    pub advertise_host: Option<String>,
    #[serde(default)]
    pub bind_host: Option<String>,
    #[serde(default = "default_listen_port")]
    pub listen_port: u16,
    #[serde(default = "default_temp_dir")]
    pub temp_dir: PathBuf,
    #[serde(default = "default_true")]
    pub allow_send: bool,
    #[serde(default = "default_true")]
    pub allow_receive: bool,
    #[serde(default = "default_max_concurrent_transfers")]
    pub max_concurrent_transfers: u32,
}
```

默认 rclone 路径：

```rust
fn default_rclone_binary_path() -> String {
    "/usr/bin/rclone".into()
}

fn default_listen_port() -> u16 {
    42981
}
```

不保留 `transfer.croc` 解析。

## 5. Runtime snapshot

`daemon.rs` 中 transfer runtime 生成逻辑改名为 `build_transfer_runtime`，判断条件为：

1. `transfer.rclone.enabled == true`
2. `rclone binary_path` 存在且 `rclone version` 返回 0
3. `advertise_host` 非空
4. `listen_port` 未被本机占用
5. `temp_dir` 可创建且可写

runtime metadata：

```json
{
  "transport": "rclone_sftp",
  "rclone_binary_path": "/usr/bin/rclone",
  "advertise_host": "100.64.0.10",
  "bind_host": "100.64.0.10",
  "listen_port": 42981,
  "temp_dir": "/tmp/yequ-transfer",
  "allow_send": true,
  "allow_receive": true
}
```

日志文案从 `croc transfer runtime available` 改为 `rclone transfer runtime available`。

## 6. Capability manifest

Linux Node 注册：

- `linux.transfer.rclone.status`
- `linux.transfer.local.stat`
- `linux.transfer.rclone.receive`
- `linux.transfer.rclone.send`
- `linux.transfer.rclone.reconcile`

manifest 字段与 Windows Node 保持一致。

### `linux.transfer.rclone.status`

执行：

```text
rclone version
rclone serve sftp --help
```

输出与 Center 合同一致，`transport="rclone_sftp"`。

### `linux.transfer.rclone.receive`

行为：

1. 创建 staging：`<temp_dir>/<transfer_id>/incoming`。
2. 启动：

```bash
rclone serve sftp "$staging" --addr "$bind_host:$listen_port" --user "$username" --pass "$password"
```

3. 本机 TCP probe 成功后上报：

```json
{
  "phase": "receiver_ready",
  "progress_source": "rclone_receiver_ready",
  "receiver_ready": true,
  "endpoint": {
    "host": "advertise_host",
    "port": 42981,
    "username": "yequ_trf_x"
  }
}
```

4. 等待 `.yequ-transfer/<transfer_id>/done.json`。
5. 收到 marker 后 terminate rclone serve。
6. 校验 payload。
7. staging 原子提交到目标路径。
8. 返回 `received_path`、`received_kind`、`size_bytes`、`sha256`。

### `linux.transfer.rclone.send`

行为：

1. source stat 使用流式 hash。
2. 写临时 rclone config 到 `<temp_dir>/<transfer_id>/rclone.conf`，权限 `0600`。
3. 文件使用 `rclone copyto`；目录使用 `rclone copy`。
4. rclone 参数：

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

5. `rclone_progress.rs` 解析 JSON log stats，转换为 `transfer_progress`。
6. 上传 `done.json`。
7. 删除临时 config。

凭据处理：

- target receive job 的 password 明文会出现在正在运行的 `rclone serve sftp --pass ...` 子进程 argv；
- source send job 的 password 明文只写入 `<temp_dir>/<transfer_id>/rclone.conf`；
- 临时 rclone config 权限固定为 `0600`；
- send job 成功、失败或取消后必须删除临时 rclone config；
- SQLite ledger、普通日志、Center observation 中只保存 `credential_hash`，不保存 password 明文。

### `linux.transfer.rclone.reconcile`

读取 SQLite ledger 并输出通用 transfer record。输出不包含 password 明文。

## 7. local.stat 修正

`linux_transfer_local_stat.rs` 输出改为：

```json
{
  "path": "/tmp/a.bin",
  "exists": true,
  "found": true,
  "kind": "file",
  "is_file": true,
  "is_dir": false,
  "readable": true,
  "writable": false,
  "parent": "/tmp",
  "parent_exists": true,
  "parent_writable": true,
  "size_bytes": 123,
  "mtime": "...",
  "modified_at": "...",
  "sha256": null,
  "free_space_bytes": 123456,
  "free_bytes": 123456,
  "error_code": null,
  "error_message": null
}
```

`sha256=true` 时使用流式读取：

```rust
let mut file = std::fs::File::open(path)?;
let mut buffer = [0_u8; 1024 * 1024];
loop {
    let n = file.read(&mut buffer)?;
    if n == 0 { break; }
    hasher.update(&buffer[..n]);
}
```

不再使用 `std::fs::read()` 读取完整大文件。

## 8. Ledger 合同

`transfer_ledger.rs` 保留 SQLite 实现，表字段改为通用 transfer：

| 字段 | 说明 |
|---|---|
| `transfer_id` | Center session id。 |
| `transport` | 固定 `rclone_sftp`。 |
| `role` | `sender` 或 `receiver`。 |
| `status` | `created/running/interrupted/succeeded/failed/cancelled`。 |
| `credential_hash` | SFTP password hash。 |
| `source_path` | source path。 |
| `target_path` | final target path。 |
| `output_dir` | final output dir。 |
| `staging_dir` | staging path。 |
| `attempt_count` | 尝试次数。 |
| `pid` | rclone 子进程 pid。 |
| `last_error_code` | 稳定错误码。 |
| `last_error_message` | 稳定错误消息。 |

SQLite migration 在 Node 启动时执行：

1. 旧 croc ledger 表存在时丢弃旧表；
2. 创建新 `transfer_ledger`；
3. 不迁移旧 croc 记录。

开发探索阶段不保留 croc 历史记录。

## 9. 错误码

Linux Node rclone transfer 使用以下错误码：

| 条件 | error code |
|---|---|
| rclone binary 不存在 | `rclone_not_installed` |
| rclone 不可执行 | `rclone_not_executable` |
| advertise_host 缺失 | `transfer_endpoint_unavailable` |
| source 不存在 | `source_not_found` |
| source 不可读 | `source_not_readable` |
| target 不可写 | `target_not_writable` |
| 空间不足 | `insufficient_space` |
| SFTP 服务启动失败 | `receiver_start_failed` |
| SFTP 监听端口被占用 | `receiver_port_unavailable` |
| receiver ready 超时 | `receiver_ready_timeout` |
| rclone copy 失败 | `rclone_copy_failed` |
| done marker 缺失 | `transfer_marker_missing` |
| 校验失败 | `integrity_mismatch` |
| 超时 | `operation_timeout` |
| 取消 | `cancelled` |

## 10. 验收

Linux Node 修改完成后执行：

```bash
cargo fmt --all
cargo clippy --all-targets -- -D warnings
cargo test
```

手工验收：

1. `linux.transfer.rclone.status` 返回 `installed=true`、`advertise_host` 非空。
2. Linux 作为 target 接收 Windows source 传输成功。
3. Linux 作为 source 向 Windows target 传输成功。
4. 大文件 hash 不造成进程内存按文件大小增长。
5. 目录传输返回 `received_kind="directory"` 和目录 size。
6. 取消后 rclone 子进程退出，ledger 为 `cancelled`。

## 11. 外部依据

- rclone SFTP server 官方文档：`https://rclone.org/commands/rclone_serve_sftp/`
- rclone JSON log 官方文档：`https://rclone.org/docs/#use-json-log`
- rclone SFTP backend 官方文档：`https://rclone.org/sftp/`
