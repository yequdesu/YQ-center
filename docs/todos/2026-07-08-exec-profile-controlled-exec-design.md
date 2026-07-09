# Exec Profile 与受控 exec.run 待办

状态：active todo  
日期：2026-07-08  
范围：Center / Windows Node / Linux Node / YCR / Agent Runtime  

本文是 Node 简单能力收敛的执行待办。目标是删除低质量小工具，只保留少量高价值 Product/Core Capability，引入 `exec.run` 承载普通命令，并由 Center 通用 approval、audit、YCR projection 统一约束。

进度更新：2026-07-08 已完成 Windows/Linux `exec.run` 基础实现、`capability.describe` 实时 profile 展示、YCR 字段级 projection 验证，并已从 Windows/Linux Node 删除低质量 primitive capability。已删除 Center 针对 `exec.run` 的动态 intent policy；统一门控层另起设计。尚未完成 Console 真实任务验收。

第一版不引入 WASI、不引入 filesystem transaction、不引入 overlay preview、不引入 diff/commit/rollback 门控、不保留重复 fallback。

## 1. 当前代码事实

### 1.1 Center

- Center 已统一维护 capability registry。
- Provider 默认工具面已经收窄为 `capability.groups` / `capability.group.open` / `capability.invoke`；`capability.search` / `capability.describe` 位于 `capabilities` 分组，需要显式打开后再通过 `capability.invoke` 使用。
- Center canonical name 规则会移除平台前缀：`windows.everything.find` 在 Center 中归为 `everything.find`，`linux.exec.run` 和 `windows.exec.run` 在 Center 中归为 `exec.run`。Agent 可通过 source projection 看到 `registered_name`、`node_id`、`source_id` 和 `platform_os`。
- Node capability 默认通过 Invocation / Job 执行，不允许 Agent 直连 Node。
- `transfer.croc.send` 和 `transfer.croc.receive` 已在 registry 默认归为 `center_internal`，不作为 Agent 默认可见工具。
- `*.status` 和 `*.reconcile` 已默认归为 diagnostic。

### 1.2 Windows Node

Windows Node 当前位于本仓库 `nodes/windows/winnode`，与 Linux Node 一样作为子目录管理。capability 结构已经是一个能力一个文件，入口为：

```text
nodes/windows/winnode/node_win_client/capabilities/registry.py
```

当前注册能力包括：

```text
windows.screen.capture
windows.everything.find
windows.exec.run
windows.file.upload_artifact
windows.artifact.download_file
windows.transfer.local.stat
windows.transfer.croc.status
windows.transfer.croc.send
windows.transfer.croc.receive
windows.transfer.croc.reconcile
```

`windows.everything.find` 当前实现通过 Everything 搜索文件。该能力是 Windows 文件发现的 Core Capability，不能合并进 `exec.run`。

### 1.3 Linux Node

Linux Node 当前位于本仓库：

```text
nodes/linux/yequnode/yequnode-core/src/registry.rs
```

当前生产能力由 `production::collect_manifests()` 确定，包括：

```text
linux.exec.run
linux.artifact.upload_file
linux.artifact.download_file
linux.artifact.upload_log
linux.artifact.diagnostics
linux.artifact.upload_proc
linux.artifact.screenshot_via_fb
linux.transfer.croc.status
linux.transfer.croc.send
linux.transfer.croc.receive
linux.transfer.local.stat
linux.transfer.croc.reconcile
```

Linux Node 已内置 yq-croc 二进制路径：

```text
nodes/linux/yequnode/tools/yq-croc/linux-amd64/yq-croc
```

## 2. 目标能力面

目标能力面只保留三类：

```text
Product / Workflow Capability
Core Capability
Primitive Capability
```

### 2.1 Product / Workflow Capability

Product / Workflow Capability 表示稳定业务能力或跨节点编排能力。它们必须保留，不被 `exec.run` 替代。

| 能力 | 决策 |
|---|---|
| `transfer.preflight` / `transfer.create` / `transfer.resume` | 保留在 Center workflow。 |
| `*.transfer.croc.status` / `*.transfer.croc.send` / `*.transfer.croc.receive` / `*.transfer.croc.reconcile` | 保留为 yq-croc transfer runtime 能力；send/receive 继续为 Center internal。 |
| `*.transfer.local.stat` | 保留为 transfer preflight / local fact 能力。 |
| `artifact.present` / `artifact.deploy` / `*.artifact.*` | 保留为 artifact 能力。 |
| `*.screen.capture` / `linux.artifact.screenshot_via_fb` | 保留为屏幕/图像采集能力。 |
| 业务插件能力，例如教务处信息爬取、邮箱代理 | 保留为 Product Capability。 |

### 2.2 Core Capability

Core Capability 是高频、结构化、可检索、可分页、能显著降低 Agent 探索成本的基础能力。Core Capability 不合并进 `exec.run`。

| 能力 | 决策 |
|---|---|
| `windows.file.search` | 保留并重命名为 `windows.everything.find`；该能力必须使用 Everything/es.exe；旧名删除，不保留 alias fallback。 |
| Linux 文件、系统、网络、服务、日志诊断 | 不保留独立 Core 小能力；统一通过 `linux.exec.run` 执行受 profile/policy 约束的命令。 |

### 2.3 Primitive Capability

第一版只允许一个 primitive：

```text
exec.run
```

平台注册名：

```text
windows.exec.run
linux.exec.run
macos.exec.run
android.exec.run
```

Center canonical name：

```text
exec.run
```

`exec.run` 支持 command string。Center 不解析 command string 的语义安全性。第一版将 `exec.run` 静态声明为 `maintenance/write`，因此执行走现有通用 approval/audit；真正的命令语义门控以后由统一门控层实现。

## 3. Execution Profile

固定四个 profile：

| Profile | 含义 | 第一版行为 |
|---|---|---|
| `user.readonly` | 普通用户执行环境。 | 仅表达执行身份；不作为安全隔离承诺。 |
| `user.write` | 普通用户执行环境。 | 仅表达执行身份；不做命令语义判断。 |
| `admin.readonly` | 管理员/root 执行环境。 | 仅在 Node 真实具备提权运行时上报。 |
| `admin.write` | 管理员/root 执行环境。 | 仅在 Node 真实具备提权运行时上报。 |

Profile 是 Node 上报的执行能力上限。Policy 是 Center 对本次请求的授权决策。Audit 是执行后的完整记录。

第一版不声明 `readonly` 是强隔离安全边界。所有 `exec.run` 都按 manifest 的 `maintenance/write` 进入通用 approval/audit。危险写入、删除、提权、服务变更的命令语义门控以后统一设计，当前不保留局部伪策略。

## 4. Runtime Metadata

Node 必须通过 `RuntimeInstance.metadata_json.execution_profiles` 上报 profile。第一版不新增数据库表。

示例：

```json
{
  "runtime_id": "user",
  "kind": "privileged",
  "status": "online",
  "interactive": false,
  "privilege": "user",
  "labels": ["linux", "exec"],
  "metadata": {
    "execution_profiles": [
      {
        "profile": "user.readonly",
        "filesystem_intent": "readonly",
        "timeout_sec": 10,
        "max_stdout_bytes": 65536,
        "max_stderr_bytes": 8192
      },
      {
        "profile": "user.write",
        "filesystem_intent": "write",
        "timeout_sec": 10,
        "max_stdout_bytes": 65536,
        "max_stderr_bytes": 8192
      }
    ]
  }
}
```

Admin profile 只有在 Node 具备对应运行时事实时上报：

- Linux：`sudo -n true` 成功才上报 `admin.*`。
- Windows：elevated daemon/runtime 存在才上报 `admin.*`。
- 其他平台：具备真实 admin/root 能力才上报 `admin.*`。

不存在的 profile 不注册、不降级、不伪装。

## 5. exec.run 合同

### 5.1 输入

```json
{
  "node_id": "linux-node-01",
  "profile": "user.readonly",
  "command": "journalctl -u ssh.service -n 100 --no-pager",
  "cwd": "/home/yequdesu",
  "timeout_sec": 10,
  "reason": "Inspect SSH service logs"
}
```

字段规则：

| 字段 | 规则 |
|---|---|
| `node_id` | 必填。 |
| `profile` | 必填，必须由目标 Node 在线 runtime 上报。 |
| `command` | 必填，Node 原生命令字符串。 |
| `cwd` | 可选；缺省使用 Node profile 默认工作目录。 |
| `timeout_sec` | 可选；不能超过 profile 上限。 |
| `reason` | 必填，写入 audit，用于说明执行原因；不参与准入决策。 |

### 5.2 输出

```json
{
  "status": "succeeded",
  "profile": "user.readonly",
  "exit_code": 0,
  "stdout_preview": "...",
  "stdout_ref": "ctxref_xxx",
  "stderr_tail": "",
  "truncated": false,
  "duration_ms": 231
}
```

失败示例：

```json
{
  "status": "failed",
  "profile": "admin.readonly",
  "error_code": "runtime_unavailable",
  "error_message": "admin.readonly profile is not available on linux-node-01"
}
```

stdout/stderr 大值必须进入 YCR ref。Provider 只能看到 preview、ref、exit code、duration、truncated 和结构化 error。

## 6. 删除和重命名规则

第一版执行以下规则：

1. `windows.file.search` 重命名为 `windows.everything.find`。
2. `windows.file.*` 读类小能力删除；已知路径 stat/hash/list/read 通过 `windows.exec.run` 承载。
3. Linux `linux.filesystem.*`、`linux.system.*`、`linux.network.*`、`linux.service.*`、`linux.log.*` 等小能力删除；对应普通诊断通过 `linux.exec.run` 承载。
4. 仅包装一条 shell 命令且无结构化业务价值的能力删除，改由 `exec.run` 承载。
5. 输出不可控、无分页、无 max_bytes、无 ref 合同的能力必须重写；未重写前不得进入 Agent 默认候选。
6. schema 错误、命名含糊、Agent-facing 描述误导的能力直接修正或删除，不保留兼容路径。
7. 被删除能力不得保留 alias、shim、fallback 或隐藏 direct invoke 路径。

## 7. 待办清单

### T01 Windows Capability 分层和重命名

- [x] 将 `windows.file.search` 文件和 manifest 重命名为 `windows.everything.find`。
- [x] 输入 schema 固定为 `query`、`root`、`mode`、`recursive`、`include_files`、`include_dirs`、`case_sensitive`、`limit`、`timeout_sec`。
- [x] 后端固定为 Everything/es.exe；删除 `backend` 输入字段。
- [x] 删除旧名 `windows.file.search`，不保留 alias。
- [x] 更新 Windows Node 测试。

验收：Agent 搜索 Windows 文件时只看到 `everything.find` Core Capability；Center registry 不再出现 `file.search` 旧名来源。

### T02 Windows primitive 工具删除

- [x] 删除 `windows.file.stat`、`windows.file.hash`、`windows.file.read_text`、`windows.file.list`。
- [x] 删除 `windows.info`、disk、network、process、service、eventlog、clipboard、window、display、installed-apps 等低价值小能力。
- [x] 保留 `windows.everything.find`、`windows.exec.run`、screenshot、artifact、transfer/yq-croc 能力。

验收：Windows 简单系统任务不再同时由旧小能力和 `windows.exec.run` 重复暴露。

### T03 Linux primitive 工具删除

- [x] 删除 `linux.filesystem.*`、`linux.system.*`、`linux.metrics.*`、`linux.process.*`、`linux.disk.*`、`linux.network.*`、`linux.service.*`、`linux.log.*`、`linux.user.*`、`linux.package.*`、`linux.dmesg`。
- [x] 保留 `linux.exec.run`、artifact、transfer/yq-croc 能力。
- [x] `linux.capabilities` 插件上报与权限探测只覆盖保留能力。

验收：Linux 普通诊断和文件操作由 `linux.exec.run` 统一承载。

### T04 exec.run 引入

- [x] 新增 `linux.exec.run`。
- [x] 新增 `windows.exec.run`。
- [x] 两端 manifest 声明 `execution_profiles`、`reason`、timeout、stdout/stderr 上限。
- [x] Center `capability.describe(exec.run)` 返回目标 Node 可用 profile。
- [x] YCR 为 `exec.run` 添加 preview + ref projection。

验收：Agent 能通过打开 `capabilities` 分组后使用 `capability.search` 找到 `exec.run`，并能在 `user.readonly` profile 下执行只读命令。

### T05 Approval / Audit 接入

- [x] 删除 Center 对 `exec.run` 的 profile/effect 动态 policy。
- [x] `exec.run` 静态声明为 `maintenance/write`，统一走通用审批路径。
- [x] 每次 `exec.run` 写入 Invocation、Job、Timeline；session audit log 复用当前 Agent session log 机制。
- [x] approval required 必须以结构化结果进入 Agent，不允许空错误。

验收：`exec.run` 不再存在伪安全检测层；执行前由通用 approval/audit 接管，后续统一门控层另行设计。

### T06 删除低质量 primitive 工具面

- [x] 审查 Windows Node 全部 capability。
- [x] 审查 Linux Node 全部 capability。
- [x] 每个能力标注为 Product / Core / Primitive / Delete。
- [x] Delete 项从 manifest 和 registry 中删除。
- [x] Primitive 项只保留 `exec.run`。
- [x] 更新文档和测试。

验收：不存在同一简单系统任务同时由旧小能力和 `exec.run` 重复暴露的情况。

执行记录：2026-07-08 删除 Windows/Linux 低质量 primitive 能力；目录创建、普通文件读写、hash、stat、系统诊断、网络诊断、服务状态查询等简单任务统一通过 `*.exec.run` 执行。`exec.run` 静态归为 `maintenance/write`，由 Center 通用 approval/audit 约束；不保留动态 intent policy。

Windows Node 分类：

| 分类 | 能力 |
|---|---|
| Product / Workflow | `windows.transfer.croc.status`、`windows.transfer.croc.send`、`windows.transfer.croc.receive`、`windows.transfer.croc.reconcile`、`windows.transfer.local.stat`、`windows.artifact.download_file`、`windows.file.upload_artifact`、`windows.screen.capture` |
| Core | `windows.everything.find` |
| Primitive | `windows.exec.run` |
| Delete | `windows.info`、`windows.disks.list`、`windows.disk.detail`、`windows.network.interfaces`、`windows.processes.list`、`windows.process.detail`、`windows.services.list`、`windows.service.detail`、`windows.eventlog.query`、`windows.clipboard.get_text`、`windows.windows.list`、`windows.display.info`、`windows.installed_apps.list`、`windows.file.stat`、`windows.file.read_text`、`windows.file.list`、`windows.file.hash`、旧 `windows.file.search`。 |

Linux Node 分类：

| 分类 | 能力 |
|---|---|
| Product / Workflow | `linux.transfer.croc.status`、`linux.transfer.croc.send`、`linux.transfer.croc.receive`、`linux.transfer.croc.reconcile`、`linux.transfer.local.stat`、`linux.artifact.upload_file`、`linux.artifact.download_file`、`linux.artifact.upload_log`、`linux.artifact.diagnostics`、`linux.artifact.upload_proc`、`linux.artifact.screenshot_via_fb` |
| Core | 无 |
| Primitive | `linux.exec.run` |
| Delete | `linux.system.info`、`linux.metrics.snapshot`、`linux.process.list`、`linux.filesystem.stat`、`linux.filesystem.hash`、`linux.filesystem.disk_usage`、`linux.disk.detail`、`linux.network.interfaces`、`linux.network.routes`、`linux.network.connections`、`linux.network.dns_lookup`、`linux.network.port_check`、`linux.service.list`、`linux.service.status`、`linux.service.restart`、`linux.log.journal`、`linux.user.list`、`linux.package.list`、`linux.dmesg`、`linux.filesystem.read_text`、`linux.filesystem.find`、`linux.filesystem.list_dir`、`linux.filesystem.mkdir`。 |

### T07 真实任务验收

- [ ] Windows：按文件名查找文件，必须命中 `windows.everything.find`。
- [ ] Windows：已知目录列一层内容，必须命中 `windows.exec.run`。
- [ ] Linux：查 SSH 日志，必须使用 `linux.exec.run`。
- [x] Linux：查文件 hash，必须使用 `linux.exec.run`。2026-07-10 远端 session `sess_9a012c21bc5740cd` 通过：`/etc/hostname` SHA256 为 `a839b15402605681cb77250cb318dcdf34e406f07ad3349472cf0552eb2c4884`，profile 为 `user.readonly`。
- [ ] Transfer：Windows -> Linux 传输仍走 `transfer.create` 和 yq-croc runtime，不走 `exec.run`。
- [x] 任意 `exec.run`：必须触发 approval。2026-07-10 远端 session `sess_9a012c21bc5740cd` 中 `linux.exec.run` 创建 approval `apv_f577079e9afa49e6` 并通过 `approve-and-run` 执行。

验收：以上任务在 Console Agent 中完成，YCR projection 正常，provider history 不出现大 raw 输出。

## 8. 非本阶段范围

以下内容不进入本待办：

- WASI executor。
- filesystem transaction。
- overlay preview。
- diff summary。
- commit / rollback。
- command effect detector。
- 强 OS sandbox。
- task-level intent approval 重构。

这些内容只能在本待办完成后另起设计文档，不得在实现 `exec.run` 时夹带。

## 9. 完成定义

本待办完成时必须同时满足：

1. Windows Everything 搜索以 `windows.everything.find` 形式注册。
2. `windows.file.search` 旧名不存在。
3. `exec.run` 在 Windows 和 Linux 均可通过 registry 搜索、描述、调用。
4. `exec.run` 输出经过 YCR projection。
5. `exec.run` 调用进入通用 approval；不存在 Center 特判 intent policy。
6. 低质量 primitive 小能力完成删除或升格。
7. 文档、manifest、测试和前端展示一致。
8. Console Agent 真实任务验收通过。
