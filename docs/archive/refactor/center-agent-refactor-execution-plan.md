# Center/Agent 解耦与代码质量重构执行计划

日期：2026-06-25

目标：在不推倒重写、不破坏当前已通过测试能力的前提下，解决当前项目中 Center 与 Agent 耦合过深、业务执行路径重复、路由层过重、代码质量门禁失效的问题。

本计划依赖当前诊断文档：`docs/current-project-diagnosis.md`。

## 当前执行状态

2026-06-25 已完成第一轮结构性重构：

1. 新增 `src/yequ/application/` 应用层。
2. 新增 `ToolInvocationApplicationService`，统一 safe/read、L2 approval、approved execution、resource lock、Invocation/Job 创建、可选等待结果。
3. Agent 非流式 tool execution 已接入 application service。
4. Agent SSE 单 tool execution 已接入 application service，SSE event contract 保持兼容。
5. Admin `/admin/invocations` 已接入 application service。
6. Admin `/admin/approvals/{approval_id}/approve-and-run` 已接入 application service。
7. Maintenance run 增加未审批写步骤门禁，未带 approval 时不再创建 Job。
8. 新增 `SignalState` 当前状态表，`signal.report` 成功后会更新 State Store。
9. 新增 import boundary 测试，防止 `services -> agent` 和 Agent 核心执行路径重新依赖 Job/Invocation/Approval/Resolver 内部服务。
10. 新增 application service 测试和 Signal State 断言。

验证结果：

- `pytest -q`：232 passed, 17 skipped。
- `alembic heads`：`d8e9f0a1b2c3 (head)`。
- 新增 application/model/boundary 测试相关 lint：通过。

仍保留的债务：

- `agent_stream.py` 的并发调度预检仍直接读取 resolver/policy/capability，用于执行前分组；后续应抽成 application query service。
- `agent_service.py` 仍通过 maintenance service 创建计划；后续应抽成 Maintenance application service。
- `api/routes/admin.py` 仍是巨型文件；本轮先收敛执行入口，尚未完成路由拆分。
- 全仓 `ruff check .` 仍会因历史 Agent/Admin/tests/migration 债务失败，本轮没有做全仓格式化和历史 lint 清理。

2026-06-25 第二轮继续完成：

1. 新增 `ToolPreflightApplicationService`，把 Agent SSE 并发调度前的 resolver/policy/capability 查询迁出 Agent 层。
2. `agent_stream.py` 不再直接 import `yequ.services.*` 或 `yequ.models.capability`。
3. 新增 `MaintenancePlanApplicationService`，Agent 普通计划与计划流式创建不再直接 import `maintenance_service.create_plan`。
4. 扩展 import boundary 测试，禁止 `agent_service.py` / `agent_stream.py` 直接 import `yequ.services.*`。
5. 删除 `api/routes/admin.py` 中被统一 application 入口取代的不可达旧 Invocation/Job 创建逻辑。
6. `api/routes/admin.py` 经过 ruff 清理后已单文件 lint 通过。

第二轮验证结果：

- `pytest -q`：234 passed, 17 skipped。
- `ruff check src/yequ/application src/yequ/api/routes/admin.py src/yequ/models/signal_state.py tests/test_import_boundaries.py tests/application/test_tool_invocation_application.py`：通过。

第二轮后仍保留的债务：

- `agent_service.py` / `agent_stream.py` 仍存在历史长行和少量旧 lint 债务，需要单独清理。
- `api/routes/admin.py` 已删除不可达旧逻辑并通过 lint，但仍是一个聚合型大文件，后续可按领域物理拆分。
- 全仓 `ruff check .` 仍未作为质量门禁恢复，主要受历史 Agent/tests/migration 债务影响。

2026-06-25 第三轮继续完成：

1. 清理 `agent_service.py` / `agent_stream.py` 中本轮触达后遗留的未使用变量和长行问题。
2. 对 `agent_service.py` / `agent_stream.py` 执行 `ruff format`，并用 `ruff check` 验证通过。
3. 确认新增 application 层、Admin 接入点、SignalState、import boundary 和 application service 测试相关文件继续通过 lint。

第三轮验证结果：

- `ruff check src/yequ/agent/agent_service.py src/yequ/agent/agent_stream.py`：通过。
- `ruff check src/yequ/application src/yequ/api/routes/admin.py src/yequ/models/signal_state.py tests/test_import_boundaries.py tests/application/test_tool_invocation_application.py`：通过。
- `pytest -q tests/test_import_boundaries.py tests/application/test_tool_invocation_application.py tests/test_agent_tool_execution.py tests/test_agent_parallel_tools.py tests/test_agent_plan_ir.py tests/test_approval_api.py::test_stream_waiting_approval_persists_approval_id_for_console_refresh`：19 passed。
- `pytest -q`：234 passed, 17 skipped。

第三轮后仍保留的债务：

- `api/routes/admin.py` 已接入统一 application service 并通过单文件 lint，但物理拆分尚未完成。
- Maintenance approval resume/reject 的完整恢复 API 仍需补齐；当前已完成未审批写步骤的阻断语义。
- SignalState 已可写入当前状态，但 TTL stale/degraded scanner 与查询 API 仍需继续实现。
- 全仓 `ruff check .` 尚未恢复为统一质量门禁；当前保证本轮新增和触达文件通过 ruff。

2026-06-25 第四轮继续完成：

1. 清理生产代码剩余 lint 债务，覆盖 `src/yequ/agent`、`src/yequ/api`、`src/yequ/models`、`src/yequ/services`。
2. 对 `src/yequ` 执行统一格式化，让生产代码恢复为可执行质量门禁。
3. 保留历史 migration 和 tests lint 债务作为单独基线，避免在本轮结构重构中混入自动生成文件和大量测试排版改写。

第四轮验证结果：

- `ruff check src/yequ`：通过。
- `pytest -q`：234 passed, 17 skipped。
- `ruff check .`：仍有 254 项，集中在 `alembic/versions/*` 与 `tests/*`。

第四轮后仍保留的债务：

- 全仓 lint 还未完成，下一步应分别处理 migration 豁免策略和 tests 格式化。
- Admin route 物理拆分仍未完成。
- Maintenance approval 恢复/拒绝闭环、SignalState TTL stale/degraded 查询仍待实现。

2026-06-25 第五轮继续完成：

1. 清理 tests 目录 lint 债务，包括 import 排序、未使用变量、长行、`zip(strict=...)`、JSON parse 的 `contextlib.suppress`。
2. 修复 `alembic/env.py` import 排序。
3. 在 `pyproject.toml` 中为历史 Alembic migration 文件增加明确 per-file ignore，避免自动生成迁移文件阻断全仓质量门禁。

第五轮验证结果：

- `ruff check tests`：通过。
- `ruff check .`：通过。
- `pytest -q`：234 passed, 17 skipped。
- `mypy src/`：失败，当前 252 项类型错误，主要集中在历史 `dict` 泛型缺失、CLI 未标注函数、SQLAlchemy Result 类型、Agent/provider 动态 payload、Admin/Maintenance route DTO 类型。

第五轮后仍保留的债务：

- Admin route 物理拆分仍未完成。
- Maintenance approval 恢复/拒绝闭环、SignalState TTL stale/degraded 查询仍待实现。
- `mypy src/` 严格类型检查尚未恢复为质量门禁，需要单独分批治理。

2026-06-25 第六轮继续完成：

1. 将 Admin session / agent-turn endpoints 拆到 `src/yequ/api/routes/admin_sessions.py`。
2. 将 Admin node / runtime / capability endpoints 拆到 `src/yequ/api/routes/admin_nodes.py`。
3. 将 Admin jobs / invocations / timeline / locks endpoints 拆到 `src/yequ/api/routes/admin_activity.py`。
4. 将 Admin tokens / approvals endpoints 拆到 `src/yequ/api/routes/admin_approvals.py`。
5. 将 Admin read DTO 和 response converter 抽到 `src/yequ/api/routes/admin_schemas.py`。
6. 保留 `src/yequ/api/routes/admin.py` 作为 provisioning 与 manual invocation 兼容入口，文件规模从 1200+ 行降到 221 行。

第六轮验证结果：

- `ruff check .`：通过。
- `pytest -q`：234 passed, 17 skipped。
- Admin/session/node/activity/approval 相关定向测试均通过。

第六轮后仍保留的债务：

- Maintenance approval 恢复/拒绝闭环、SignalState TTL stale/degraded 查询仍待实现。
- `mypy src/` 严格类型检查尚未恢复为质量门禁，需要单独分批治理。
- `admin.py` 仍可进一步拆为 `admin_provisioning.py` 和 `admin_invocations.py`，但当前已经不再是巨型聚合文件。

2026-06-25 第七轮继续完成：

1. Maintenance run 遇到未审批 `requires_approval` 写步骤时，现在会自动创建 pending `ApprovalRequest`，并把 `approval_id` 保存在 run summary 与 plan 上。
2. 新增 `POST /admin/maintenance/runs/{run_id}/resume`，要求关联 approval 已 approved/consumed，然后从等待步骤继续执行。
3. 新增 `POST /admin/maintenance/runs/{run_id}/reject`，拒绝 pending approval 并将 run/plan 标记为 cancelled。
4. `execute_plan_run()` 现在会跳过已 succeeded/skipped 的步骤，支持从 waiting step 恢复而不是从头重跑。
5. `finalize_run()` 保留 waiting/cancelled 状态，并合并 executor 写入的 approval context。

第七轮验证结果：

- `pytest -q tests/test_l2c.py`：14 passed。
- `ruff check .`：通过。
- `pytest -q`：236 passed, 17 skipped。

第七轮后仍保留的债务：

- SignalState TTL stale/degraded 查询仍待实现。
- `mypy src/` 严格类型检查尚未恢复为质量门禁，需要单独分批治理。
- `admin.py` 仍可进一步拆为 `admin_provisioning.py` 和 `admin_invocations.py`。

2026-06-25 第八轮继续完成：

1. 新增 `src/yequ/services/signal_state_service.py`，统一 SignalState 查询、TTL freshness 计算、expired signal stale 持久化。
2. 新增 Admin 查询入口：
   - `GET /admin/signals`
   - `GET /admin/nodes/{node_id}/signals`
3. Node summary/detail 增加信号摘要字段：
   - `fresh_signal_count`
   - `stale_signal_count`
   - `signal_stale`
4. 查询 SignalState 或 Node summary/detail 前会刷新 expired signal 的 `freshness_status=stale` 和 `quality=stale`。
5. 新增 `tests/test_signal_state.py` 覆盖 signal.report 后当前状态查询、TTL stale 刷新、未知 node 404、节点摘要 stale 反映。

第八轮验证结果：

- `pytest -q tests/test_signal_state.py tests/test_node_liveness.py::test_admin_nodes_api_liveness_fields tests/test_yqp_protocol.py::test_signal_report_validates_schema`：4 passed。
- `ruff check .`：通过。
- `pytest -q`：238 passed, 17 skipped。

第八轮后仍保留的债务：

- SignalState 已完成查询和 stale 摘要，但还没有后台 scanner 主动写入 stale timeline/告警。
- `mypy src/` 严格类型检查尚未恢复为质量门禁，需要单独分批治理。
- `admin.py` 仍可进一步拆为 `admin_provisioning.py` 和 `admin_invocations.py`。

## 总体原则

1. **先收敛入口，再拆模块。** 不先做大规模文件搬迁，先抽出统一的能力执行入口，让 Agent、Admin、Maintenance 共用同一条业务路径。
2. **行为不漂移。** 每一阶段都以现有测试通过为基础，新增测试锁定当前语义，再重构实现。
3. **应用层隔离 Center 内部细节。** Agent 不再直接拼装 Invocation、Job、Approval、Resource Lock、Timeline。
4. **路由层变薄。** API route 只负责认证、解析请求、调用 application service、返回响应。
5. **逐步清理 lint 债务。** 不把历史 lint 问题和结构重构混在一次提交中解决，但新增/触达代码必须保持干净。
6. **每个阶段可单独回滚。** 每一阶段都应该有清晰边界和测试验收，不依赖“最后一起修好”。

## 目标架构

重构后的核心依赖方向：

```text
api/routes/*
  -> application/*
       -> services/*
            -> models/*
            -> protocol/*

agent/*
  -> application/*
  -> provider implementations

maintenance/*
  -> application/*
```

禁止方向：

```text
services/* -> agent/*
application/* -> api/routes/*
services/* -> api/routes/*
```

Agent 的目标职责：

- 接收用户 prompt。
- 调用 LLM provider。
- 将 tool call 转换为 Center 应用层命令。
- 消费 Center 返回的结构化执行结果。
- 生成最终回复或 SSE 事件。

Agent 不应直接负责：

- 创建 Invocation。
- 创建 Job。
- 创建 ApprovalRequest。
- 管理 Resource Lock。
- 直接写 Timeline。
- 持有跨等待周期的 DB session。

## 阶段 0：建立安全基线

### 目标

在改动结构前，先确认当前行为和测试基线，避免重构中无法判断问题来自哪里。

### 操作

1. 记录当前 `git status --short`。
2. 运行全量测试：

```bash
pytest -q
```

3. 运行当前可接受的局部 lint：

```bash
ruff check src/yequ/services src/yequ/models src/yequ/protocol src/yequ/api/routes/yqp.py
```

4. 记录完整 lint 债务：

```bash
ruff check .
```

5. 增加 import boundary 测试，至少覆盖：

- `src/yequ/services` 不允许 import `yequ.agent`
- `src/yequ/application` 不允许 import `yequ.api`
- `src/yequ/agent` 后续不允许 import `yequ.models`

### 验收

- 全量 pytest 通过。
- 当前 lint 债务有记录。
- import boundary 测试加入测试集。

## 阶段 1：新增 Application 层骨架

### 目标

引入稳定的 Center 应用层接口，暂不迁移大块逻辑，只建立后续迁移落点。

### 新增目录

```text
src/yequ/application/
  __init__.py
  tool_invocation.py
  schemas.py
  errors.py
```

### 核心对象

建议新增：

```python
ExecuteToolCommand
ExecuteToolResult
ApprovalRequiredResult
ToolExecutionEvent
ToolInvocationApplicationService
```

`ExecuteToolCommand` 建议字段：

- `actor_id`
- `actor_type`
- `function_name`
- `input_data`
- `target_node_id`
- `runtime_id`
- `mode`
- `risk_override`
- `correlation_id`
- `wait_for_result`
- `timeout_seconds`

`ExecuteToolResult` 建议字段：

- `status`
- `invocation_id`
- `job_id`
- `approval_id`
- `node_id`
- `output_data`
- `error_code`
- `error_message`
- `timeline_event_ids`

### 验收

- 新 application 层不改变现有行为。
- 新增单元测试覆盖 schema 初始化和错误类型。
- 没有 route 或 agent 逻辑迁入前，所有测试仍通过。

## 阶段 2：抽取统一能力执行入口

### 目标

把当前分散在 Agent、Admin、Maintenance 中的能力执行流程抽进 `ToolInvocationApplicationService`。

### 迁移内容

统一以下步骤：

1. capability resolve。
2. node/runtime 目标解析。
3. policy evaluation。
4. approval required 判断。
5. approval request 创建。
6. invocation 创建。
7. resource lock 获取。
8. job 创建。
9. job 状态机启动。
10. timeline 同步写入。
11. 可选等待 job 终态。
12. resource lock 释放。

### 需要重点保持的语义

- L2 write 操作始终需要审批。
- destructive/catastrophic 风险按 policy matrix 执行。
- node offline 时不能创建可执行 job。
- resource conflict 时不能遗留 lock。
- job 终态不可变。
- 所有状态变化必须有 timeline。

### 新增测试

建议新增：

```text
tests/application/test_tool_invocation_application.py
```

覆盖：

- safe function 直接创建 Invocation + Job。
- ask policy 返回 approval required，不创建 Job。
- deny policy 不创建 Invocation/Job。
- L2 write 即使 safe risk 也要求审批。
- resource conflict 返回冲突错误并释放已拿到的锁。
- wait_for_result=True 能拿到 terminal result。
- node offline 返回明确错误。

### 验收

- 新 application service 测试通过。
- 原有 Agent/Admin/Maintenance 行为暂不迁移或只迁移一条低风险路径。
- 全量 pytest 通过。

## 阶段 3：Agent 非流式路径接入 Application 层

### 目标

先处理 `agent_service.py`，让非流式 Agent 不再直接创建 Invocation/Job/Approval。

### 操作

1. 找出 `agent_service.py` 中直接调用以下服务的位置：

- `CapabilityResolver`
- `InvocationService`
- `JobService`
- `ApprovalService`
- `MaintenanceService`
- `PolicyService`

2. 将普通工具调用替换为 `ToolInvocationApplicationService.execute()`。
3. 保留 Agent loop、provider 调用、tool call validation。
4. 删除 Agent 内重复的 policy、approval、job 创建逻辑。
5. 保留结果格式兼容，避免前端/API 响应变化。

### 暂不处理

- SSE 流式路径。
- Maintenance Plan 自动生成逻辑。
- Provider registry。

### 新增/调整测试

- Agent 调用 safe tool 成功。
- Agent 调用需要审批 tool 返回 approval required。
- Agent 调用 unknown function 仍按原行为失败。
- Agent 纯文本响应不走 tool execution。

### 验收

- `agent_service.py` 对 `yequ.models` 的直接依赖显著减少。
- `agent_service.py` 不再直接创建 Job。
- Agent 相关测试通过。
- 全量 pytest 通过。

## 阶段 4：Agent SSE 路径接入同一执行核心

### 目标

消除 `agent_stream.py` 与 `agent_service.py` 的重复执行逻辑。

### 操作

1. 将 SSE 中的 tool execution 替换为 application service。
2. application service 增加事件回调或 async event iterator：

```python
execute_stream(command) -> AsyncIterator[ToolExecutionEvent]
```

3. SSE 只负责把内部事件转换为 SSE contract。
4. 非流式 Agent 可以复用同一事件流并收集最终结果。

### 事件类型建议

- `tool_resolving`
- `policy_evaluated`
- `approval_required`
- `invocation_created`
- `job_created`
- `job_started`
- `job_event`
- `job_succeeded`
- `job_failed`
- `tool_completed`

### 验收

- `agent_stream.py` 不再直接创建 Invocation/Job/Approval。
- SSE contract 测试通过。
- 非流式与 SSE 对同一 tool call 的最终业务结果一致。

## 阶段 5：修复 Maintenance 审批门禁

### 目标

解决当前 Maintenance write step 可以标记需要审批但运行门禁不完整的问题。

### 操作

1. Maintenance step 执行统一走 `ToolInvocationApplicationService`。
2. 如果返回 `approval_required`：

- plan run 状态进入 `waiting_approval`。
- step 状态进入 `requires_approval`。
- 不创建 Job。
- 写入 timeline。

3. 增加审批恢复入口：

- 审批通过后恢复 plan run。
- 审批拒绝后 plan run 标记 failed/cancelled。

### 新增测试

- repair step 为 L2 write 时，未审批不得创建 Job。
- 审批通过后继续执行 repair。
- 审批拒绝后不执行 repair。
- check-only plan 不受影响。

### 验收

- Maintenance 不再有单独的 Job 创建路径。
- L2 write 审批语义与 Agent/Admin 一致。

## 阶段 6：Admin 路由接入 Application 层并拆分文件

### 目标

降低 `api/routes/admin.py` 复杂度。

### 操作顺序

1. 先让 Admin 的手工 invocation/job 创建路径调用 application service。
2. 确保 URL 和响应结构不变。
3. 再按领域拆分文件：

```text
api/routes/admin_nodes.py
api/routes/admin_invocations.py
api/routes/admin_jobs.py
api/routes/admin_timeline.py
api/routes/admin_approvals.py
api/routes/admin_tokens.py
api/routes/admin_maintenance.py
```

4. 保留 `api/routes/admin.py` 作为聚合 router 或兼容导入层。

### 验收

- Admin API 测试全部通过。
- `admin.py` 行数显著下降。
- 路由层不再直接处理 Resource Lock 和 Job 状态转换。

## 阶段 7：清理 Agent 与 Center 的剩余耦合

### 目标

通过 import boundary 将架构约束固定下来。

### 操作

1. `agent/` 中只允许 import：

- `yequ.application`
- `yequ.agent`
- `yequ.config`
- 轻量 schema/DTO

2. 禁止 `agent/` 直接 import：

- `yequ.models`
- `yequ.services.job_service`
- `yequ.services.invocation_service`
- `yequ.services.approval_service`
- `yequ.services.capability_resolver`

3. 如果 Agent 需要查询状态，新增 application query service，而不是读取 models。

### 验收

- import boundary 测试强制通过。
- Agent 文件职责收敛到 provider/runtime/response。

## 阶段 8：补齐 State Store

### 目标

让 Signal 真正成为 Center 状态能力，而不是只进入 timeline。

### 操作

1. 新增模型：

```text
SignalState
```

建议字段：

- `id`
- `node_id`
- `capability_id`
- `signal_name`
- `value`
- `schema_version`
- `reported_at`
- `expires_at`
- `freshness_status`
- `quality`

2. 新增 migration。
3. `signal.report` 更新 current state。
4. Node liveness/degraded scanner 结合 Signal TTL。
5. Admin/Agent 查询状态时通过 State Store。

### 验收

- Signal report 后可查询当前状态。
- TTL 过期后状态变 stale。
- stale signal 可影响 Node degraded 或 capability degraded。

## 阶段 9：YQP 生产可靠性增强

### 目标

让协议实现更接近 `YQP-Node-Protocol.md`。

### 操作

1. 将 message dedup 从进程内 cache 抽象为 service interface。
2. 默认 DB 实现，保留内存实现用于测试。
3. 明确空 poll 响应：

- 兼容现有 `job.available` 空数组。
- 可选增加 `job.empty`。
- 更新协议文档或兼容测试。

4. response envelope 补充 node context，或明确文档偏差。
5. 补充 reconcile 冲突测试。

### 验收

- 重启后重复 message_id 仍可检测或有明确策略。
- Reconcile terminal conflict 有测试覆盖。
- Node SDK 行为有稳定 contract。

## 阶段 10：Lint 债务治理

### 目标

恢复 lint 作为有效质量门禁。

### 操作

1. 建立 lint baseline 文档，列出现有失败类型。
2. 分批清理：

- application 新代码必须 0 lint。
- agent 重构触达文件必须 0 lint。
- admin 拆分后新文件必须 0 lint。
- tests 按目录清理。
- alembic 旧迁移可单独配置忽略规则。

3. CI 规则建议：

- 全量 pytest 必须通过。
- 新增/触达 Python 文件必须 ruff 通过。
- import boundary 测试必须通过。

### 验收

- `ruff check src/yequ/application src/yequ/services src/yequ/protocol` 通过。
- 最终目标是 `ruff check .` 通过，或只有明确豁免的 migration 规则。

## 推荐执行顺序

严格顺序：

1. 阶段 0：安全基线。
2. 阶段 1：Application 层骨架。
3. 阶段 2：统一能力执行入口。
4. 阶段 3：Agent 非流式接入。
5. 阶段 4：Agent SSE 接入。
6. 阶段 5：Maintenance 审批门禁。
7. 阶段 6：Admin 接入并拆分。
8. 阶段 7：import boundary 固化。
9. 阶段 8：State Store。
10. 阶段 9：YQP 可靠性。
11. 阶段 10：Lint 债务治理。

如果需要压缩周期，最小可执行闭环是：

1. 阶段 0
2. 阶段 1
3. 阶段 2
4. 阶段 3
5. 阶段 5
6. 阶段 7

这条路径能最快解决 Center/Agent 耦合和最危险的业务一致性问题。

## 每阶段固定验收命令

每个阶段完成后至少运行：

```bash
pytest -q
```

根据触达范围补充：

```bash
ruff check src/yequ/application
ruff check src/yequ/agent src/yequ/api/routes src/yequ/services
mypy src/
```

如果触达数据库模型或 migration：

```bash
alembic heads
pytest tests/test_yqp_protocol.py
pytest tests/test_runtime_context.py
```

## 风险控制

### 主要风险

1. 重构过程中 Agent 响应格式变化，导致前端或 SSE contract 破坏。
2. 统一执行入口改变审批/Policy 细节，导致安全语义回退。
3. Admin 路由拆分时 URL 或响应结构不兼容。
4. Maintenance 审批恢复流程需要新增状态，可能影响历史数据。
5. State Store 新增 migration 可能影响 SQLite/PostgreSQL 双环境。

### 控制方式

1. 先加 characterization tests，再改实现。
2. 每阶段控制 diff，不混入格式化全仓。
3. 不在同一阶段同时做“抽象迁移”和“行为优化”。
4. URL、response schema、SSE event name 必须保持兼容，除非单独声明 breaking change。
5. 所有 Job 状态变化仍必须通过 `job_state_machine.transition()`。

## 第一批具体任务清单

下一步可以直接执行以下任务：

1. 新增 `tests/test_import_boundaries.py`。
2. 新增 `src/yequ/application/__init__.py`。
3. 新增 `src/yequ/application/schemas.py`。
4. 新增 `src/yequ/application/errors.py`。
5. 新增 `src/yequ/application/tool_invocation.py` 的空骨架。
6. 为 application schemas/errors 写最小测试。
7. 运行 `pytest -q` 确认无行为变化。
8. 开始把 Agent 非流式 safe tool 路径迁入 application service。

## 完成标准

本轮重构完成后，应满足：

1. Agent 不再直接创建 Invocation/Job/Approval。
2. Maintenance 不再绕过统一执行入口创建 Job。
3. Admin 手工能力调用走统一执行入口。
4. `services/` 不依赖 `agent/`，并由测试强制。
5. `agent_service.py` 与 `agent_stream.py` 逻辑重复明显减少。
6. `admin.py` 不再是 1000+ 行巨型路由文件，或已经进入可拆分状态。
7. L2 write 审批在 Agent/Admin/Maintenance 中一致。
8. 新增和触达代码通过 ruff。
9. 全量 pytest 通过。
