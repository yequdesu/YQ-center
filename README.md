# YeQu Center

个人基础设施控制中心 — 连接设备、收集状态、调度能力、记录审计时间线，为 LLM Agent、Web 控制台、CLI 提供统一入口。

当前架构主线：Center 正在从 Capability Runtime v1 演进为 Center Execution Runtime v2。目标是把 Agent、Console、CLI、未来 MCP 的意图转成可审计、可调度、可等待、可取消、可恢复、可观察的 Center 执行过程。详见 `docs/todos/2026-06-30-center-execution-runtime-v2.md`。

## 架构

```
Agent / Web / CLI ──→ Center (FastAPI :9800)
                         │
                         ├── Policy Engine ──── execution mode × risk matrix + L2 write gate
                         ├── Capability Resolver ── 选择最佳在线 Node + 运行时匹配
                         ├── Execution Admission ── 判定 inline / sync wait / Operation / workflow
                         ├── Operation Bus ── 长任务等待、事件、取消、恢复、future MQ 边界
                         ├── Job State Machine ── 严格状态转换 + lease + timeout
                         ├── Resource Lock Service ── 资源冲突控制
                         ├── Approval System ── L2 写操作人工审批
                         ├── Timeline / Audit ── global_seq 不可绕过审计
                         └── PostgreSQL / SQLite

Node Daemon ←── YQP Protocol (POST /yqp/) ──→ Plugin → Function / Signal
                  │
                  └── Runtime Instances (privileged / interactive / WASM / Docker)
```

## 分层与职责边界

| 层 | 职责 | 不负责 |
|---|---|---|
| **Center** | 认证、注册、策略、调度、状态、审计、Job 生命周期 | 业务实现 |
| **Node** | 可接入的执行环境（设备/VM/容器） | 决策、权限 |
| **Daemon** | Node 上常驻进程，执行 Job、上报 Signal | LLM 推理 |
| **Plugin** | 将本地系统/服务适配为 Function + Signal | 跨用户授权 |
| **Agent** | LLM 驱动，**必须通过 Center 标准路径** | 直接调用 Node |

## 快速开始

### 安装

```bash
pip install -e ".[dev]"
```

要求 Python 3.11+。

### 启动数据库

```bash
docker compose up -d postgres
```

### 启动服务

```bash
python -m yequ.main
# 或
uv run python -m yequ.main
```

`http://0.0.0.0:9800` — API 端点

`http://127.0.0.1:9800/console` — Web 控制台 SPA

### CLI

```bash
yequ config-init                     # 初始化 CLI 配置 (~/.config/yequ/config.toml)
yequ health                          # 健康检查
yequ nodes list                      # 列出节点
yequ nodes show <node_id>            # 节点详情（含存活状态）
yequ capabilities list [-n node_id]  # 能力列表
yequ invoke <func> -n <node> [--wait]  # 创建调用
yequ jobs list [-n node_id] [-s status]  # 作业列表
yequ jobs show <job_id>              # 作业详情
yequ invocations show <inv_id>       # 调用详情
yequ timeline tail [-n node_id]      # 时间线
yequ agent session --create          # 创建 Agent 会话
yequ agent invoke <session_id> "<prompt>"   # Agent 调用
```

### 测试

```bash
pytest                   # 全部测试（SQLite，无需 PostgreSQL）
pytest -v                # 详细输出
pytest tests/test_job_state_machine.py  # 单文件
```

### 代码质量

```bash
ruff check .             # Lint
ruff format .            # 格式化
mypy src/                # 类型检查
```

## 配置

所有配置通过 `YEQU_` 前缀环境变量或 `.env` 文件：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `YEQU_DATABASE_URL` | `postgresql+asyncpg://yequ:yequ@127.0.0.1:5432/yequ` | PostgreSQL 连接串 |
| `YEQU_DEEPSEEK_API_KEY` | — | DeepSeek API Key |
| `YEQU_DEEPSEEK_MODEL` | `deepseek-chat` | 模型 |
| `YEQU_DEBUG` | `false` | 调试模式 |
| `YEQU_DEBUG_TIMELINE` | `false` | 写入诊断事件 |
| `YEQU_LOG_FORMAT` | `json` | 日志格式（json / console） |
| `YEQU_REQUIRE_ADMIN_AUTH` | `true` | 强制 token 认证 |

## API 端点总览

### 健康检查（无认证）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/healthz` | 返回服务 + 数据库状态 |

### YQP 协议（Node Bearer Token）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/yqp/` | 统一节点端点，按 `message_type` 分发 |

message_type 处理器：

| message_type | 方向 | 处理器 |
|---|---|---|
| `node.hello` | Node→Center | `handle_hello()` — 上线 + 协商参数 + 同步运行时 |
| `node.heartbeat` | Node→Center | `handle_heartbeat()` — 更新心跳 + 同步运行时 |
| `node.register_capabilities` | Node→Center | `handle_register_capabilities()` — 全量能力快照 |
| `signal.report` | Node→Center | `handle_signal_report()` — 定期 Signal 上报 |
| `job.poll` | Node→Center | `handle_job_poll()` — 拉取作业 |
| `job.accepted` | Node→Center | `handle_job_accepted()` — 确认领取 |
| `job.finished` | Node→Center | `handle_job_finished()` — 上报终态 |
| `job.lease_renew` | Node→Center | `handle_job_lease_renew()` — 续租 |
| `job.event` | Node→Center | `handle_job_event()` — 进度/日志 |
| `job.cancel` | Center→Node | `handle_job_cancel()` — 取消作业 |
| `node.reconcile_jobs` | Node→Center | `handle_reconcile_jobs()` — 断连恢复仲裁 |

### Agent API（Agent Token）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/agent/sessions` | 创建会话（指定执行模式、Actor） |
| `POST` | `/agent/invoke` | 同步 Agent 调用 |
| `POST` | `/agent/invoke/stream` | SSE 流式 Agent 调用（25 种事件类型） |
| `POST` | `/agent/plan` | 同步生成维护计划 |
| `POST` | `/agent/plan/stream` | SSE 流式计划生成 |

### Admin API（Admin Token）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/admin/nodes` | 列出全部节点 |
| `GET` | `/admin/nodes/{id}` | 节点详情（含存活状态、心跳年龄） |
| `POST` | `/admin/nodes` | 预配新节点（node_id + token + role + locality） |
| `POST` | `/admin/nodes/{id}/mark-offline` | 强制下线 |
| `POST` | `/admin/nodes/{id}/capabilities/refresh-state` | 刷新能力活跃状态 |
| `DELETE` | `/admin/nodes/{id}/stale-capabilities` | 删除过期能力 |
| `GET` | `/admin/runtimes` | 列出全部运行时实例 |
| `GET` | `/admin/nodes/{id}/runtimes` | 某节点的运行时列表 |
| `GET` | `/admin/capabilities` | 列出活跃能力（可按 node 过滤） |
| `GET` | `/admin/jobs` | 列出作业（按 node/status/invocation 过滤） |
| `GET` | `/admin/jobs/{id}` | 作业详情 |
| `GET` | `/admin/invocations` | 列出调用 |
| `GET` | `/admin/invocations/{id}` | 调用详情（含关联 Job） |
| `POST` | `/admin/invocations` | 创建调用 + 作业（含 L2 审批流） |
| `GET` | `/admin/sessions` | 列出 Agent 会话 |
| `GET` | `/admin/sessions/{id}` | 会话详情（含消息 + turns） |
| `GET` | `/admin/sessions/{id}/messages` | 会话历史消息 |
| `GET` | `/admin/sessions/{id}/turns` | 会话 Turn 列表 |
| `GET` | `/admin/agent-turns/{id}/events` | Turn 的 SSE 事件列表 |
| `PATCH` | `/admin/sessions/{id}` | 重命名会话 |
| `DELETE` | `/admin/sessions/{id}` | 删除会话 + 消息 + turns |
| `GET` | `/admin/timeline` | 查询时间线（多重过滤 + cursor） |
| `GET` | `/admin/locks` | 列出资源锁 |
| `POST` | `/admin/tokens` | 创建 API Token（admin/agent scope） |
| `POST` | `/admin/approvals` | 创建审批 |
| `GET` | `/admin/approvals` | 列出审批 |
| `GET` | `/admin/approvals/{id}` | 审批详情 |
| `POST` | `/admin/approvals/{id}/approve` | 批准 |
| `POST` | `/admin/approvals/{id}/deny` | 拒绝 |
| `POST` | `/admin/approvals/{id}/approve-and-run` | 批准并立即消费执行 |

### 维护计划（Admin Token, `/admin/maintenance/`）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/admin/maintenance/plans` | 创建计划（含步骤定义） |
| `GET` | `/admin/maintenance/plans` | 列表 |
| `GET` | `/admin/maintenance/plans/{id}` | 详情（含步骤） |
| `POST` | `/admin/maintenance/plans/{id}/approve` | 批准计划 |
| `POST` | `/admin/maintenance/plans/{id}/run` | 执行计划 |
| `GET` | `/admin/maintenance/runs/{id}` | 运行详情（含步骤 + 制品） |
| `GET` | `/admin/maintenance/runs/{id}/artifacts` | 运行制品列表 |
| `GET` | `/admin/maintenance/runs/{id}/events/stream` | 运行事件 SSE 流 |

## Web 控制台（SPA）

基于 Vue 3 + TypeScript + TanStack Router + TailwindCSS，由 `/console` 路径访问。

前端路由：

| 路径 | 页面 | 说明 |
|---|---|---|
| `/` | DashboardPage | 仪表盘 |
| `/chat` | AgentChatPage | Agent 对话（SSE 流式 + 会话管理 + 审批卡） |
| `/nodes` | NodesPage | 节点列表 |
| `/nodes/:nodeId` | NodeDetailPage | 节点详情 |
| `/approvals` | ApprovalsPage | 审批列表 + 操作 |
| `/maintenance` | MaintenancePlansPage | 维护计划列表 |
| `/maintenance/runs/:runId` | MaintenanceRunPage | 维护运行详情 |
| `/invocations` | InvocationsPage | 调用列表 |
| `/jobs` | JobsPage | 作业列表 |
| `/timeline` | TimelinePage | 审计时间线 |

### 前端 SSE 渲染契约

| SSE Event | ChatBlock 类型 | 渲染行为 |
|---|---|---|
| `agent.output.delta` | `assistant_text` | 追加或新建文本块 |
| `agent.fallback_synthesis` | `assistant_text` | 追加或新建文本块 |
| `agent.tool_call.created` | `tool_group` | 追加到当前工具组或新建 |
| `agent.tool_call.completed` | 更新 tool_group | 工具状态 → succeeded |
| `agent.tool_call.failed` | 更新 tool_group | 工具状态 → failed |
| `agent.tool_call.waiting_approval` | 更新 tool_group + `system_event` | 渲染 ApprovalCard 交互卡片 |
| `agent.completed` | `system_event` | subtle label，非大气泡 |

关键约束：
- `tool_group` 和 `assistant_text` 不可互相嵌套
- `agent.completed` 不可渲染为大内容气泡
- 并发工具：所有 `created` 先于任何 `invocation.created` / `job.queued`
- 用户输入"确认"、"批准"文本**不自动批准**，必须点击 ApprovalCard 按钮
- 页面刷新后从 `/admin/sessions/{id}/messages` 重建对话历史

## 核心概念

| 概念 | 定义 |
|---|---|
| **Node** | 可接入执行环境，通过 YQP 协议与 Center 通信 |
| **RuntimeInstance** | Node 上的平台无关执行上下文（privileged / interactive / WASM / Docker） |
| **Function** | 主动调用型能力（查日志、重启服务等） |
| **Signal** | 持续上报型状态（CPU、内存、服务健康等） |
| **Invocation** | Actor 发起的一次语义调用，可产生 1+ Job |
| **Job** | 调度到 Node 的具体执行任务，严格状态机管理 |
| **TimelineEvent** | 全局审计记录，`global_seq` 单调自增 |
| **Session** | Agent 对话上下文，关联消息 + turns |
| **ApprovalRequest** | L2 写操作的人工审批闸门 |

## 数据模型关系

```
Node ──1:N──→ Capability (25 列，Function/Signal 注册信息)
Node ──1:N──→ RuntimeInstance (9 列，执行上下文)
Node ──1:N──→ Job (25 列)
Invocation (17 列) ──1:N──→ Job
Session (10 列) ──1:N──→ AgentMessage (6 列)
Session ──1:N──→ AgentTurn ──1:N──→ AgentTurnEvent
ApprovalRequest (17 列) ──1:1──→ Invocation
MaintenancePlan ──1:N──→ MaintenanceStep ──1:1──→ Job
MaintenancePlan ──1:N──→ MaintenanceRun ──1:N──→ MaintenanceArtifact
MaintenanceRun ──1:N──→ RollbackHint
Job ──1:N──→ ResourceLock (8 列)
TimelineEvent (13 列，global_seq 自增) — 全局审计
ApiToken (3 列) — admin / agent scoped
```

## YQP 协议

Node ↔ Center 通信协议。统一信封格式：

```json
{
  "yqp_version": "0.1",
  "message_id": "msg_...",
  "message_type": "node.heartbeat",
  "trace_id": "tr_...",
  "node_id": "debian-home",
  "session_id": null,
  "timestamp": "2026-06-19T12:00:00Z",
  "payload": {}
}
```

### 认证

Node token 通过 `Authorization: Bearer <token>` 携带。`node_id` 必须与 token 绑定一致。首次 Node 需管理面预配（`POST /admin/nodes`）。

### 安全防护

- `message_id` 去重：内存 TTL 缓存 5 分钟，命中不重复执行副作用
- `timestamp` 防重放：±30s 窗口，超时拒绝
- 去重实现在 `message_dedup.py`：`MessageDedup` 单例，lazy 过期清理

### Node 启动流程

```
Daemon → node.hello (身份+版本+平台+runtimes)
Center → node.accepted (协商参数: 心跳间隔/投递模式等)
Daemon → node.register_capabilities (Function+Signal manifest 全量快照)
Daemon → node.heartbeat (周期性心跳)
Daemon → signal.report (持续状态上报)
```

### 断连重连与仲裁

Daemon 重连后发送 `node.reconcile_jobs`，Center 返回 `job.reconciliation`：

| action | Daemon 行为 |
|---|---|
| `continue` | 继续执行，新 lease |
| `cancel` | 停止并上报 cancelled/failed |
| `accept_result` | Center 接受本地结果 |
| `discard_result` | 记录审计，Daemon 清理 |
| `forget` | Center 不认该 Job，停止并清理 |

**Center 是 Job 控制状态的事实来源**。Daemon 结果不可无条件覆盖 Center 终态。

## 运行时系统（Runtime Instance）

### 模型

Node 拥有关联的 `RuntimeInstance` 列表（`1:N`），表示不同执行上下文：

| 字段 | 说明 |
|---|---|
| `runtime_id` | 全局唯一 ID |
| `kind` | `privileged` / `interactive` 等 |
| `status` | `online` / `degraded` / `offline` |
| `labels` | JSON 标签数组 |
| `owner` | 所属用户 |
| `privilege` | 权限级别 |
| `interactive` | 是否交互式 |

### 同步机制

在 `handle_hello()` 和 `handle_heartbeat()` 中调用 `_sync_runtime_instances()`：

- Node 上报自己的 runtime 列表（全量快照）
- Center 做 upsert：已有 runtime 更新状态；新增 runtime 创建
- **减法去重**：本次未上报的 runtime 标记为 `offline`
- 只存储平台无关属性，不绑定具体容器引擎

### 运行时匹配（capability_resolver）

Capability 声明 `execution_context`，Resolver 匹配 Node 上的 RuntimeInstance：

- `system` → `runtime_kind="privileged"`
- `user` → 交互式运行时
- `hybrid` → 优先交互式，回退到特权
- 支持按 `privilege`、`labels`、`interactive` 精确匹配

匹配在 `_select_runtime()` 中完成，返回 `(runtime_id, requirements, reason)`。

## Capability 解析器

`capability_resolver.resolve_function()` 是**选节点的唯一权威入口**。解析优先级：

1. 若指定 `target_node_id`，只检查该节点
2. Node 必须可调度（`effective_status == online`）
3. Node 必须有该 Function 注册且 `is_active=True`
4. 无指定时扫描全部在线节点
5. 多节点按优先级排序：在线优先 → locality（local > lan > wan）→ 运行中 Job 最少 → 心跳最新 → node_id 字典序
6. 无匹配时返回 `available=False` + 诊断原因

## Node 存活管理

### 有效状态计算（effective_status）

`compute_effective_status()` 算法：

```
heartbeat_age > interval × multiplier        → offline  (默认 10×3=30s)
heartbeat_age > interval × (multiplier-1)    → degraded (默认 10×2=20s)
存储状态已是 offline                           → offline
从未心跳 + provisioned/online                 → online
否则                                          → online
```

### 后台扫描

`NodeLivenessScanner` 每 5 秒扫描一次，调用 `mark_timed_out_nodes()`：

- 心跳超时的 online/degraded 节点 → `offline`
- 节点上 `queued` 状态的 Job → 自动取消（`NODE_OFFLINE_BEFORE_CLAIM`）
- 每次扫描写入 `node.offline` 时间线事件

### API 呈现

每个 Node API 返回中包含 `get_node_liveness_snapshot()` 的结果：

- `effective_status` — 有效状态
- `heartbeat_age_sec` — 心跳年龄
- `heartbeat_stale` — 是否过期
- `schedulable` — 是否可调度
- `unavailable_reason` — 不可调度原因

## Job 状态机

```
created → queued → claimed → running → (succeeded | failed | timeout)
                ↘ cancelled   ↘ cancelling → (cancelled | failed | timeout)
```

### 四条铁律

1. 所有状态变更**必须**通过 `job_state_machine.transition()` — 不可直接设 `job.status`
2. 每次转换**自动写入** TimelineEvent（审计不可绕过，`global_seq` 单调自增）
3. 终端态不可变：`succeeded` / `failed` / `cancelled` / `timeout` 一旦锁定
4. 非法转换被拒绝并写入 `job.invalid_transition` 审计事件

### 禁用转换列表（VALID_TRANSITIONS）

| 当前状态 | 允许目标 |
|---|---|
| created | queued |
| queued | claimed, cancelled |
| claimed | running, timeout |
| running | succeeded, failed, cancelling, timeout |
| cancelling | cancelled, failed, timeout |
| terminal | （无） |

### Deal 创建流程

`create_job()` 完整流程：

1. 创建 Job（CREATED）
2. 判定是否需串行化（`_requires_serialization()`）
   - write/destructive 效果 → 需锁
   - maintenance+ 风险 → 需锁
   - `conflict_policy=serialize` → 需锁
   - 有 approval_id → 需锁
   - safe + read + allow_parallel → **不需要锁**
3. 若需要锁 → `acquire_lock()` 获取所有 resource_keys
4. `transition(created → queued)` — 写入审计事件

### 取消流程

- `queued` → 直接 `cancelled`
- `claimed` / `running` → `cancelling`（Daemon 应停止并上报终态）
- 其他状态拒绝取消

### 超时流程

- `find_expired_jobs()` 查找 lease 过期的 claimed/running Job
- `timeout_job()` → `transition → TIMEOUT` + 释放所有资源锁

## 资源锁

### 锁机制

`acquire_lock(resource_key, job_id, ...)`：
- 检查 resource_key 是否有 HELD 状态的锁 → 冲突则抛 ValueError
- 获取成功 → 创建锁（HELD，TTL 默认为 5 分钟）
- 每次获取/释放/冲突都写入 TimelineEvent

`release_lock(job_id)`：释放该 Job 持有的所有锁。

### 资源键计算

`compute_resource_keys()`：支持模板和规则两种模式。

模板替换：`{node_id}`、`{name}`、`{pid}`、`{task_name}` 等。

规则推导：
- `service.*` → `node:{node_id}:service:{name}`
- `process.*` → `node:{node_id}:process:{pid}`
- `task.*` → `node:{node_id}:task:{task_name}`
- `network.dns.*` → `node:{node_id}:maintenance:dns`
- `temp.cleanup` → `node:{node_id}:maintenance:temp:{path_hash}`

### 三种冲突策略

| 冲突策略 | 行为 |
|---|---|
| `allow_parallel` | 允许并行（纯读或无共享资源） |
| `serialize` | 同一资源键串行，需获取锁 |
| `reject_if_running` | 已有运行中 Job 占锁时拒绝 |

未声明 resource_keys 的写操作按高风险处理。

## Invocation 聚合

当一个 Invocation 有多个 Job 时，调用 `aggregate_invocation_status()` 聚合状态（优先级从高到低）：

| 条件 | 聚合结果 |
|---|---|
| 无 Job | `PENDING` |
| 任一 FAILED | `FAILED` |
| 任一 TIMEOUT | `TIMEOUT` |
| 任一 CANCELLED | `CANCELLED` |
| 全部 SUCCEEDED | `SUCCEEDED` |
| 全终端但混合 | `PARTIAL` |
| 其他 | `RUNNING` |

## 策略引擎

### L1：执行模式 × 风险矩阵

| Mode \ Risk | safe | maintenance | destructive | catastrophic |
|---|---|---|---|---|
| **auto** | allow | allow | ask | deny |
| **assist** | allow | conditional | ask | deny |
| **readonly** | allow | deny | deny | deny |
| **manual** | allow | ask | ask | deny |

`check_policy()` 决策语义：
- `allow` → 直接放行
- `deny` → 拒绝
- `ask` → 需人工确认 → 创建 ApprovalRequest
- `conditional` → 仅白名单中的 Function 放行

### L2：写操作增强门

`check_policy_l2()` —— 所有写操作（effect = `write` / `destructive`）额外规则：

- `readonly` 模式 → 直接拒绝
- 其他所有模式 → 返回 `ask`，**必须创建 ApprovalRequest**

L2 叠加在 L1 之上——写操作先过 L1 矩阵，再过 L2 写门。

## 审批系统

### 生命周期

```
PENDING ──(超时)──→ EXPIRED
PENDING ──(approve)──→ APPROVED ──(consume)──→ CONSUMED
PENDING ──(deny)──→ DENIED
```

### 创建与校验

`create_approval()`：
- 自动计算 `resource_keys`
- 对 input_data（不含 approval_id 字段）做 SHA-256 hash → `input_hash`
- 默认 5 分钟 TTL

`verify_approval()`：消费前必须通过 6 项校验：
1. 审批存在
2. 状态为 APPROVED（非 PENDING/DENIED/EXPIRED）
3. 未过期
4. `actor_id` 匹配
5. `function_name` + `target_node_id` 匹配
6. `input_hash` 匹配（**防篡改**）

### 快捷操作

`approve-and-run`：批准 + 消费 + 创建 Invocation/Job 一步完成。

### 过期自动扫描

后台任务每 60 秒将 `pending` 且超时的审批批量标记为 `expired`。

## Agent 子系统

### 调用链路

```
Prompt → LLM (DeepSeek) → tool_calls
  → validate (known function? loop? depth?)
  → check_policy() + check_policy_l2() 策略检查
  → resolve_function() 选择最佳 Node + 匹配运行时
  → L2 write → create ApprovalRequest (pending)
  → approve → consume_approval() 验证
  → create Invocation + create Job
  → poll Job 终态 → collect result
  → 写回 LLM 上下文 → 下一轮 ReAct 或完成
  → save Session 历史消息
```

### 安全约束

| 约束 | 说明 |
|---|---|
| `max_depth` | Agent 嵌套调用深度上限 |
| `max_steps` | 单次 invoke 最大 tool call 步骤数 |
| `max_total_duration_sec` | 单次 invoke 总耗时上限 |
| `call_path` | 动态跟踪已调用过的 Function，检测循环 |
| 并发上限 | **4**，安全读取工具可并发执行 |

任一超限 → 返回受控错误（`call_depth_exceeded`、`max_steps_exceeded`、`max_duration_exceeded`、`circular_dependency`）。

### Provider 架构

`AgentProvider` 抽象接口：
- `DeepSeekProvider` — DeepSeek API（OpenAI-compatible），支持流式
- `FakeAgentProvider` — 确定性测试，支持预设响应序列

### Plan 模式

Agent 不直接执行，先生成计划：

1. LLM 分析 prompt → 推断 check + repair 步骤
2. 自动选择回滚函数
3. 生成 `MaintenancePlan` + `MaintenanceStep`
4. 审批后执行

### SSE 流式事件（25 种类型）

参见 `docs/agent-sse-contract.md`。每次 invoke/stream 创建 `AgentTurn` + `AgentTurnEvent` 记录完整事件流，支持前端刷新后重建 UI。

### 回退合成（fallback_synthesis）

LLM 最终迭代未产生文本时，系统自动从 tool_call 结果合成为可读摘要。

## 维护计划

### 步骤定义

| 字段 | 说明 |
|---|---|
| `kind` | `check` / `repair` / `verify` / `write` / `rollback` |
| `condition` | `always` / `if_previous_unhealthy` / `after_repair` / `if_previous_failed` / `manual` |
| `continue_on_failure` | 失败后是否继续后续步骤 |
| `requires_approval` | 是否需要审批 |
| `rollback_hint` | 回滚信息 |

### 三步流水线：Check → Repair → Verify

每个步骤在边界点写入 artifact：

| Artifact 类型 | 说明 |
|---|---|
| `before` | 步骤执行前快照 |
| `after` | 步骤执行后状态 |
| `check_result` | 检查结果 |
| `verify_result` | 验证结果 |
| `error` | 错误详情 |
| `rollback_hint` | 回滚建议 |

### 回滚

repair/write/verify 失败 → 触发 `rollback_recommended` → 生成 `RollbackHint` → 前端 SSE 实时接收。

### 测试故障注入

- Function 名含 `test.maintenance.repair_fail` / `test.maintenance.verify_fail`
- input_data 含 `__test_fail_stage` 字段

## 审计与时间线

### 全局时间线

`TimelineEvent` 是系统的**物理事实来源**和审计骨干，`global_seq` 自增保证全局有序。

### 归属字段

```
global_seq → 全局序号
event_type → 事件类型
actor_type / actor_id → 谁触发
session_id → 哪个交互上下文
invocation_id → 哪次调用
job_id → 哪个作业
node_id → 哪个节点
trace_id → 链路追踪
timestamp → 时间
```

### 双通道写入策略

- **关键审计事件**（Job 状态转换、审批生命周期）：在**事务内同步写入**，不经过队列
- **高频诊断事件**（心跳、Signal）：通过 `TimelineWriter` 异步队列写入

### TimelineWriter

- 内存 `asyncio.Queue`（maxsize 1000）
- 批量 flush：50 条或 1 秒间隔
- `enqueue()` 非阻塞，队列满时 drop 并警告
- 停止时 `force flush` 清空所有剩余

### 时间线视图

- 全局时间线是物理事实来源
- Session 时间线 = 全局时间线按 `session_id` 的投影
- 多 Session 同时操作同一资源：冲突由资源锁处理，审计上各自保留独立记录

## Agent Turn 持久化

`AgentTurn` + `AgentTurnEvent` 记录每次 `/agent/invoke/stream` 的完整 SSE 事件流：

| Turn 状态机 | 触发事件 |
|---|---|
| `received` | `stream.open` |
| `planning` | `agent.provider.started` |
| `tool_selecting` | `agent.tool_call.created` |
| `tool_running` | `agent.invocation.created` / `agent.job.queued` / `agent.job.running` |
| `waiting_approval` | `agent.approval.required` |
| `synthesizing` | `agent.output.delta` / `agent.synthesizing` |
| `completed` / `failed` / `provider_failed` | 对应终态事件 |

前端刷新后通过 API 重放 turn events 完全重建 UI 状态。

## 认证体系

### 三层认证

| 层级 | 方式 | 范围 |
|---|---|---|
| 无认证 | — | `GET /healthz` |
| Node Bearer | SHA-256 token hash + `node_id` 绑定 | `POST /yqp/` |
| Admin Token | SHA-256 token hash, scope=`admin` | `/admin/*` |
| Agent Token | SHA-256 token hash, scope=`agent` | `/agent/*` |

### Token 管理

- 所有 token 存储为 SHA-256 hash
- `(token_hash, scope)` 联合唯一
- 启动时 bootstrap：若无 admin/agent token，自动用 `qq756522327` 创建默认 token
- 测试模式 `test_mode=True` + `require_admin_auth=False` 可跳过认证

## 后台任务

| 任务 | 间隔 | 职责 |
|---|---|---|
| `TimeoutScanner` | 5s | 扫描 lease 过期 Job → `transition(TIMEOUT)` |
| `NodeLivenessScanner` | 5s | 扫描心跳超时 Node → `offline` + 取消 queued Job |
| `TimelineWriter` | batch 50 / 1s | 异步批量写入诊断事件 |
| `ApprovalExpiryScanner` | 60s | 过期审批 → `expired` |

## 恢复流程

Center 重启时（lifespan startup）：

1. 拒绝 SQLite in production（仅 `test_mode=True` 允许）
2. `find_incomplete_jobs()` 查找所有非终态 Job
3. Lease 过期的 claimed/running Job → `timeout_job()`
4. Bootstrap：无 token 则创建默认 admin + agent token
5. 启动四个后台任务

## 数据库

- **生产**：PostgreSQL 16（asyncpg），连接串 `YEQU_DATABASE_URL`
- **测试**：SQLite（aiosqlite），WAL 模式，每个测试函数前 `create_all` / 后 `rollback`
- **Migration**：Alembic（`alembic/versions/`，16 个迁移脚本）

```bash
# 启动 PostgreSQL
docker compose up -d postgres

# 生成迁移
alembic revision --autogenerate -m "description"

# 执行迁移
alembic upgrade head
```

## 目录结构

```
src/yequ/
├── main.py              # 入口：uvicorn :9800
├── config.py            # 20 个 YEQU_ 环境变量
├── db.py                # SQLAlchemy 异步引擎 + session 工厂
├── logconfig.py         # structlog 结构化日志（JSON/console）
├── cli.py               # CLI 管理工具（14 个命令）
│
├── api/                 # FastAPI 应用层
│   ├── app.py           # 工厂函数 + lifespan（恢复/boot/启动扫描器）+ SPA
│   ├── deps.py          # 5 个依赖注入（db, settings, 3 种 auth）
│   └── routes/
│       ├── health.py    # GET /healthz
│       ├── yqp.py       # POST /yqp/ + 11 种分发
│       ├── agent.py     # 5 个 Agent 端点
│       ├── admin.py     # 28 个 Admin CRUD 端点
│       └── maintenance.py # 8 个维护计划端点
│
├── agent/               # LLM Agent
│   ├── provider.py          # 抽象接口 + 数据类型
│   ├── deepseek_provider.py # DeepSeek API
│   ├── fake_provider.py     # 测试用确定性 Provider
│   ├── agent_service.py     # 核心 orchestrator
│   ├── agent_stream.py      # SSE 流式
│   └── tool_execution.py    # 稳定契约类型
│
├── models/              # 18 个 SQLAlchemy 模型
│   ├── base.py              # Base + TimestampMixin
│   ├── node.py              # Node + RuntimeInstance 关系
│   ├── runtime_instance.py  # 平台无关执行上下文
│   ├── capability.py        # Function/Signal 注册
│   ├── job.py               # 执行任务
│   ├── invocation.py        # 语义调用
│   ├── session.py           # Agent 会话
│   ├── agent_message.py     # LLM 对话消息
│   ├── agent_turn.py        # Turn + TurnEvent
│   ├── approval.py          # 审批请求
│   ├── maintenance_plan.py  # 5 表（plan/step/run/artifact/rollback_hint）
│   ├── resource_lock.py     # 分布式锁
│   ├── timeline.py          # 审计事件
│   └── api_token.py         # API Token
│
├── protocol/            # YQP 协议
│   ├── envelope.py      # 统一消息信封
│   ├── enums.py         # 19 种枚举
│   └── errors.py        # 19 种错误码
│
└── services/            # 18 个业务服务
    ├── node_service.py              # 11 个 YQP 消息处理器 + runtime 同步
    ├── capability_resolver.py       # 选节点 + 运行时匹配
    ├── node_liveness_service.py     # effective_status 计算 + 过期标记
    ├── node_liveness_scanner.py     # 后台扫描器
    ├── node_auth.py                 # Node 认证
    ├── token_auth.py                # Token 认证
    ├── job_service.py               # Job 创建/取消/超时
    ├── job_state_machine.py         # 严格状态转换（唯一入口）
    ├── invocation_service.py        # 调用创建/聚合
    ├── policy.py                    # L1 矩阵 + L2 写门
    ├── approval_service.py          # 审批生命周期 + 过期扫描
    ├── resource_lock_service.py     # 资源锁 + 键计算
    ├── maintenance_service.py       # 计划 CRUD
    ├── maintenance_executor.py      # 计划执行 + 回滚
    ├── agent_turn_service.py        # Turn/Event 持久化
    ├── timeline_writer.py           # 异步批量写入
    ├── timeout_scanner.py           # Job 超时扫描
    └── message_dedup.py             # 消息去重

tests/                   # 33 个测试文件（SQLite，pytest-asyncio）
console-frontend/        # Vue 3 + TypeScript 前端（10 页面）
console-dist/            # 前端构建产物
```

## 文档

- `YeQu-Architecture-Design.md` — 架构设计
- `YQP-Node-Protocol.md` — YQP 协议规范
- `docs/todos/2026-06-30-center-execution-runtime-v2.md` — 当前主架构待办
- `docs/documentation-policy.md` — 文档维护与 UTF-8 编码规范
- `docs/agent-sse-contract.md` — Agent SSE 事件契约
- `CLAUDE.md` — AI 助手指南

## 许可证

MIT
