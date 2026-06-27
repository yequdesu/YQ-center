# YeQu Center 当前架构诊断

日期：2026-06-25

本文档基于当前仓库代码、测试、迁移、前端产物，以及以下原始设计资料：

- `YeQu-Architecture-Design.md`
- `YQP-Node-Protocol.md`
- `docs/agent-sse-contract.md`
- `docs/superpowers/plans/*`

## 结论摘要

当前项目已经实现了 Center、Node、YQP、Agent、Maintenance、Approval、Timeline、Runtime Context、Console 前端等大量能力，但代码结构已经出现明显的演进债务。

核心判断：

1. **Center 和 Agent 存在过度耦合，但不是服务层完全互相引用的灾难状态。** 当前主要问题是 Agent 运行时代码直接编排 Center 内部服务、模型、事务、审批、Job、Maintenance Plan，而不是通过一个稳定的 Center 应用层接口作为“外部 Actor”调用 Center。
2. **项目结构有混乱迹象。** `api/routes/admin.py`、`agent/agent_service.py`、`agent/agent_stream.py` 已经成为巨型模块，承担了路由适配、业务编排、事务处理、状态轮询、兼容逻辑和展示转换等多种职责。
3. **多轮新增功能和修复确实带来了代码质量下降。** 同类逻辑在 Agent 普通调用、Agent SSE、Admin、Maintenance Executor 中重复出现，部分行为依赖内存状态，完整 `ruff check .` 目前不能作为有效质量门禁。
4. **当前实现与最初架构设计总体方向一致，但协议和边界实现存在明显偏差。** YQP REST 主流程可用，但 State Store、信号新鲜度、WebSocket 推送、持久去重、完整 Reconcile 仲裁等仍未达到协议文档预期。

## 当前项目全貌

### 后端主要分层

当前后端位于 `src/yequ/`：

- `api/routes/`：HTTP 路由层。包含 admin、agent、maintenance、yqp、health 等入口。
- `agent/`：LLM Provider、Agent 会话、SSE 流式执行、工具调用编排。
- `services/`：Node、Job、Invocation、Approval、Policy、Maintenance、Timeline、Resource Lock、Token Auth 等业务服务。
- `protocol/`：YQP envelope、枚举、schema、校验。
- `models/`：SQLAlchemy 持久化模型。
- `db/`：session、migration 相关基础设施。

### 主要复杂度热点

按当前代码粗略统计，复杂度最高的模块是：

| 文件 | 代码行数级别 | 主要问题 |
|---|---:|---|
| `src/yequ/api/routes/admin.py` | 1400+ | 路由、查询、编排、状态转换、锁处理、统计混在一起 |
| `src/yequ/agent/agent_service.py` | 1200+ | Agent 循环、工具执行、审批、Job 轮询、维护计划创建混在一起 |
| `src/yequ/agent/agent_stream.py` | 1000 | SSE 事件、Agent 逻辑、Job 编排、会话状态混在一起 |
| `src/yequ/services/node_service.py` | 800+ | Node 生命周期、能力注册、YQP 作业协议、Reconcile、Signal 处理过重 |
| `src/yequ/services/maintenance_executor.py` | 700+ | Maintenance 编排与通用 Job 执行逻辑重复 |

这些文件不是单纯“长”，而是承担了多个边界的职责，导致后续改动容易互相影响。

## Center 与 Agent 耦合诊断

### 当前实际依赖

当前 Agent 模块直接依赖 Center 内部对象：

- `yequ.services.approval_service`
- `yequ.services.capability_resolver`
- `yequ.services.invocation_service`
- `yequ.services.job_service`
- `yequ.services.maintenance_service`
- `yequ.services.policy`
- `yequ.models.*`
- `yequ.db.session`

`agent/agent_service.py` 和 `agent/agent_stream.py` 不只是“调用 Center”，而是直接参与：

- 解析能力和目标 Node
- 执行 Policy 判断
- 创建 Invocation
- 创建 ApprovalRequest
- 创建 Job
- 轮询 Job 终态
- 拼装工具结果
- 创建 Maintenance Plan
- 处理部分会话状态和失败恢复

这说明 Agent 当前更像 Center 内部的一个业务编排模块，而不是设计文档中描述的“通过 Center 标准路径调用能力的 Actor”。

### 耦合风险

当前耦合带来的直接风险：

1. **同一条业务路径有多个实现。** Agent 非流式、Agent SSE、Admin 手工调用、Maintenance Executor 都可能创建 Invocation/Job，并各自处理等待、失败和结果。
2. **审批语义容易不一致。** L2 写操作的审批规则在 Policy/Approval/Agent/Maintenance 多处体现，维护计划运行时仍存在审批门禁不完整的问题。
3. **事务边界不稳定。** Agent 逻辑既持有会话，又在轮询时重新开 session，部分服务也自己提交或刷新状态。
4. **未来多 Provider、多 Agent、多前端会放大重复逻辑。** 如果 Web、CLI、移动端都接入同一能力调用路径，目前缺少一个统一的应用服务入口。

### 当前仍然合理的部分

也需要客观看待：当前并没有出现 `services/` 大量反向依赖 `agent/` 的情况。服务层主体仍然以 Center 域模型为中心。当前最明显的反向泄漏是 `api/routes/admin.py` 读取 `agent_stream.is_session_running` 这种运行态信息。

因此问题不是“Center 已经被 Agent 完全绑死”，而是 **Agent 层拿到了太多 Center 内部拼装权**。

## 项目结构与代码质量问题

### 1. API Route 层过重

`api/routes/admin.py` 已经超过普通路由适配器职责。它包含：

- 请求参数解析
- 数据查询
- 状态转换
- Job 创建
- Resource Lock 冲突处理
- Timeline 写入
- 响应模型拼装
- Dashboard/统计逻辑

这会导致两个后果：

- 相同行为无法被 Agent、CLI、Maintenance、测试统一复用。
- 路由层改动容易破坏业务行为。

### 2. Agent 非流式与 SSE 流式路径重复

`agent_service.py` 与 `agent_stream.py` 都包含工具调用循环、Policy 判断、Job 执行等待、异常转事件/结果等逻辑。SSE 版本需要事件输出是合理的，但底层执行核心不应该复制。

理想结构应是：

- 一个共享的 Agent Runtime Core 产生结构化事件。
- 非流式调用收集事件后返回最终结果。
- SSE 调用把同一批事件实时推送给前端。

### 3. Maintenance Executor 与通用能力调用路径重复

Maintenance 当前通过自己的 executor 编排 check/repair/verify，并直接创建 Invocation/Job。它需要保留计划语义，但底层“执行某个 capability 并等待结果”的逻辑应复用 Center 的统一能力调用服务。

### 4. 内存状态影响生产一致性

当前存在多处内存状态：

- YQP message dedup 是进程内 TTL cache。
- Agent provider registry 是进程内注册。
- SSE active session 状态是进程内状态。

单进程开发模式可用，但在多进程、多实例、重启恢复场景下会丢失语义。

### 5. Lint 债务存在

当前完整 `ruff check .` 不能作为全仓质量门禁。前一轮修复后，已触达的非 Agent 文件可以局部通过 ruff，但全仓仍存在历史 lint 问题，尤其集中在旧迁移、测试和 Agent 相关代码中。

这类债务的实际含义是：

- 新问题会被旧问题淹没。
- CI 难以强制启用 lint。
- 大模块继续变大时，自动化工具无法有效兜底。

## 与原始架构设计的差异

### 1. Center 职责边界被扩大

原始设计中，Center 负责 Auth、Registry、Policy、Scheduling、State、Audit、Job lifecycle，不负责具体服务业务实现。

当前代码中，Center 内部已经包含：

- Agent 工具调用策略
- Agent 对自然语言的维护计划推导
- Maintenance check/repair/verify 编排
- 部分功能名和测试功能的特殊处理

这些能力可以存在于 Center 项目中，但需要放在清晰的应用层或插件层，否则 Center 核心会越来越像“所有业务逻辑的集合”。

### 2. Agent 作为 Actor 的抽象没有完全落地

设计文档要求 Agent 不能直接调用 Node，必须通过 Center 标准路径。这一点对 Node 侧基本满足。

但当前 Agent 不是通过一个稳定的 Center 命令接口调用能力，而是直接调用 Center 内部 services 和 models。也就是说：

- **外部行为上**：Agent 基本走了 Invocation → Job。
- **内部结构上**：Agent 绕过了清晰边界，直接拼装 Center 内部流程。

### 3. Invocation fan-out 能力不完整

设计中 Invocation 是语义调用，可以 fan out 到多个 Job。

当前实现主要是：

- 1 个 Invocation 对 1 个 Job 的路径较完整。
- Maintenance Plan 会串联多个 step，但更像多个顺序 Invocation/Job，而不是一个 Invocation 下的完整 fan-out/fan-in 追踪。

这会影响未来多 Node 并行、聚合结果、跨节点一致性和追踪视图。

### 4. State Store 与 Signal 语义不足

设计中 Signal 应进入 State Store，并支持：

- 当前状态查询
- TTL 新鲜度
- stale/degraded 判断
- Agent 读取状态
- Timeline 审计

当前 YQP 已支持 `signal.report`，但更偏向校验和事件记录，缺少完整的持久当前状态模型和基于 Signal 的 degraded 判定。Node degraded 当前更多依赖 heartbeat/liveness。

### 5. Recovery 仍是部分实现

当前启动时已有 unfinished job recovery、timeout scanner、liveness scanner、timeline writer 等机制，这是正向进展。

但与设计中的完整恢复相比仍有差距：

- Node rejoining/recovered 语义不完整。
- YQP Reconcile 仲裁规则偏简化。
- Timeline cursor 和多实例恢复没有完整协议化。
- 进程内 dedup/session 状态重启后丢失。

## 与 YQP 协议文档的差异

### 已实现的主要部分

当前 `/yqp/` 单 POST endpoint 已支持：

- `node.hello`
- `node.heartbeat`
- `node.register_capabilities`
- `signal.report`
- `job.poll`
- `job.accepted`
- `job.finished`
- `job.lease_renew`
- `job.event`
- `job.cancel`
- `node.reconcile_jobs`

协议 envelope、Bearer token、node 绑定、时间戳偏移、基础去重、Job claim/run/finish 主流程已经可用。

### 主要偏差

| 协议预期 | 当前实现状态 | 风险 |
|---|---|---|
| REST 必须，WebSocket 推荐/未来支持 | 当前只有 REST | 不能主动 push `job.dispatch` |
| 空 poll 可返回 `job.empty` 或 HTTP 204 | 当前返回 `job.available` 且 jobs 为空 | Node SDK 兼容性需要明确 |
| response envelope 包含 node 相关上下文 | 当前 response envelope 不带 `node_id` | 排障和协议一致性弱 |
| dedup/replay 至少 5 分钟，最好可跨进程 | 当前为进程内 TTL cache | 多 worker/重启后重复请求不可可靠去重 |
| Signal 进入 State Store 并支持 TTL | 当前缺少完整 State Store | Agent/Console 读取实时状态能力不足 |
| Reconcile 有明确冲突仲裁 | 当前实现偏简化 | Node 重连和 Center 状态冲突时可能行为不一致 |
| 能力注册包含完整 schema、TTL、资源语义 | 主体支持，但部分语义未强约束 | Policy、资源锁、信号 freshness 可能不一致 |

## 业务逻辑完整性评估

### 已经比较完整的能力

- Node provisioning、hello、heartbeat、capability registration。
- Job 状态机与主路径执行。
- Capability resolver、resource lock、policy matrix。
- ApprovalRequest 基础模型与 approval scanner。
- Timeline 事件记录与全局序列分配。
- Agent provider 抽象、DeepSeek/Fake provider。
- Agent SSE 合约和前端消费路径。
- Maintenance Plan 基础 check/repair/verify。
- 多 runtime context 与 node scoped runtime id。

### 不完整或有风险的能力

1. **Maintenance 写操作审批门禁仍不完整。** 当前计划 step 可以标记需要审批，但运行入口没有完整等待审批后再执行的强约束。
2. **Signal/State Store 未达到设计目标。** 这会影响“控制中心”作为状态中枢的定位。
3. **多 Node fan-out/fan-in 仍偏弱。** 单 Job 主路径成熟，多节点聚合调用还需要应用层设计。
4. **YQP 多实例可靠性不足。** 内存 dedup 和会话状态不能支持严肃的多进程部署。
5. **Admin/Agent/Maintenance 的执行入口没有统一。** 后续越多入口，行为漂移概率越高。
6. **默认 token bootstrap 有生产风险。** 如果生产环境没有显式初始化 token，默认口令逻辑应被严格限制或移除。

## 建议目标结构

建议引入清晰的应用层边界，例如：

```text
api/routes/*
  -> application/*
       -> services/*
            -> models/*
            -> protocol/*

agent/*
  -> application/tool_execution_api
  -> provider only

maintenance/*
  -> application/tool_execution_api
```

关键是抽出一个统一的能力调用入口：

```text
ToolInvocationApplicationService
  - resolve target capability/node
  - evaluate policy
  - create approval or invocation/job
  - acquire/release resource locks
  - drive job state machine
  - wait or return async handle
  - write timeline consistently
```

之后：

- Agent 只把 LLM tool call 转成 `ExecuteToolCommand`。
- Admin 只把 HTTP request 转成 `ExecuteToolCommand`。
- Maintenance Step 只把计划步骤转成 `ExecuteToolCommand`。
- SSE 只订阅同一执行核心产生的事件。

## 建议改造计划

### P0：先统一业务执行入口

1. 新建应用层服务，例如 `src/yequ/application/tool_invocation.py`。
2. 把 Agent、Admin、Maintenance 中重复的 capability resolve、policy、approval、invocation/job 创建、job wait 抽进去。
3. 为该服务补充单元测试，覆盖 safe、ask、deny、L2 write、resource lock、node offline、job timeout。
4. Agent 和 Maintenance 先以最小改动调用这个应用服务，不做大规模重写。

### P0：修复 Maintenance 审批门禁

Maintenance plan 中任何 L2 write/destructive repair step，如果需要审批，应进入 `waiting_approval` 或等价状态，不能继续创建 Job。审批通过后才能恢复执行。

### P1：统一 Agent 非流式与 SSE 执行核心

1. 把 Agent loop 改成事件生成器。
2. 非流式 API 收集事件生成最终响应。
3. SSE API 直接转发事件。
4. 删除重复 job polling、tool result synthesis、fallback 逻辑。

### P1：拆分 Admin 路由

建议按领域拆分：

- `admin_nodes.py`
- `admin_invocations.py`
- `admin_jobs.py`
- `admin_timeline.py`
- `admin_approvals.py`
- `admin_tokens.py`
- `admin_maintenance.py`

拆分时不改变 URL，只移动实现和共享 helper。

### P1：补齐 State Store

1. 新增 signal current state 表。
2. `signal.report` 同步更新 current state，并写 timeline。
3. 支持 TTL/stale/degraded。
4. Console 和 Agent 查询状态时优先读 State Store。

### P1：增强 YQP 生产可靠性

1. 将 message dedup 从进程内 cache 迁移到 DB/Redis 等共享存储，至少保证多 worker 一致。
2. 明确空 poll 响应兼容策略：保留 `job.available` 还是补充 `job.empty`。
3. response envelope 补充 node 上下文或在协议文档中明确不返回。
4. 完善 reconcile 终态仲裁测试。

### P2：整理质量门禁

1. 先建立 ruff baseline，区分历史债务和新增代码。
2. 逐步清理 Agent、tests、migrations 的 lint 问题。
3. 增加 import boundary 测试，防止 `services/` 反向依赖 `agent/`。
4. 在 CI 中至少启用“新增/触达文件必须通过 ruff”的规则。

## 建议的边界规则

建议后续明确以下规则：

1. `services/` 不允许 import `agent/`。
2. `agent/` 不直接 import `models/` 和低层 `services/`，只能调用 `application/` 暴露的命令接口。
3. `api/routes/` 不直接执行业务编排，只做认证、解析、调用 application service、返回响应。
4. `maintenance` 不直接创建 Job，只调用统一能力执行入口。
5. Timeline 写入由应用服务或领域服务负责，路由层不散落写入审计事件。
6. 多实例需要共享的状态不得只放进进程内 dict/cache。

## 当前状态判断

当前项目不是需要推倒重写的状态。底层模型、协议入口、Job 状态机、Policy、Approval、Timeline、测试基础都已经有价值。

但如果继续在当前结构上追加功能，最可能恶化的方向是：

- Agent 和 Maintenance 各自复制更多执行逻辑。
- Admin 路由继续膨胀。
- YQP 文档与实现继续分叉。
- Signal/State Store 迟迟无法成为 Center 核心能力。
- Lint 和测试门禁越来越难恢复。

因此建议下一阶段不要优先新增大功能，而是先完成“统一执行入口 + Agent/SSE 去重 + Maintenance 审批门禁 + State Store”这四件事。它们直接决定项目能否从当前可用原型走向可维护平台。
