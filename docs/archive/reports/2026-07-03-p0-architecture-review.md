# P0 架构审查报告

状态：complete
日期：2026-07-03
范围：Node 接入插拔性、Capability 插拔性、Agent/Center/Node 解耦、错误显式传播、无静默 fallback

## 1. 结论

P0 架构审查通过。当前 Center 已满足下一阶段继续迭代所需的核心架构条件：

1. 新 Node 接入不要求 Center 写平台专用分支；Node 通过 provisioning、YQP hello、runtime 上报和 capability 注册进入调度面。
2. 新 capability 注册后进入 Center registry，通过 `capability.search`、`capability.describe`、`capability.invoke` 被 Agent 发现和调用；Agent 不需要新增 raw tool。
3. Agent 生产路径只暴露 Center meta tools，Node capability 不直接暴露给 provider。
4. Runtime 调用统一进入 `CenterExecutionRuntime`，普通 Node 执行必须经过 `resolve_function` 和 Job 创建。
5. 失败按 `error_code`、`error_message` 或明确的 failed/unavailable 结果传播，不以伪成功隐藏失败。
6. 生产源码中已经没有旧 croc CLI、rclone、`advertise_host`、未注册 capability 执行开关或 provider 自动切换残留。

本次审查发现并立即修复了一个 P0 blocker：旧 admin/manual 路径保留的未注册 capability 执行逃逸开关。该开关已经从 command schema、runtime command 和 execution runtime 中删除，并加入回归测试。

## 2. 审查依据

代码路径：

- `src/yequ/services/capability_registry.py`：Capability definition/source 同步、canonical name、structured search/describe/invoke target resolution。
- `src/yequ/services/capability_resolver.py`：Node/function/runtime 统一解析入口。
- `src/yequ/runtime/execution_runtime.py`：Center 执行边界、meta tool、`capability.invoke` 委托到真实 registered capability、Job 创建。
- `src/yequ/api/routes/agent.py`：生产 Agent tool list 只返回 Center meta tools。
- `src/yequ/agent/agent_stream.py`、`src/yequ/agent/tool_stream.py`：Provider/tool 失败事件和错误码投影。
- `src/yequ/services/job_state_machine.py`：Job 失败错误码和 timeline 事件写入。

验证命令：

```powershell
python -m ruff check src/yequ/api/routes/admin_invocations.py src/yequ/application/schemas.py src/yequ/runtime/command.py src/yequ/runtime/execution_runtime.py tests/application/test_tool_invocation_application.py tests/test_capability_runtime_registry.py tests/test_import_boundaries.py
Remove-Item -Force -ErrorAction SilentlyContinue test_yequ.db,test_yequ.db-wal,test_yequ.db-shm; python -m pytest tests/application/test_tool_invocation_application.py tests/test_import_boundaries.py tests/test_runtime_context.py tests/test_capability_runtime_registry.py tests/test_agent_console_ux.py::test_available_functions_filters_to_pinned_node_in_production_mode -q
rg -n "allow_unregistered_function|_resolve_unregistered_admin_function|unregistered_admin|fallback_runtime_kind|agent\.fallback_synthesis|rclone|advertise_host" src console-frontend -g "*.py" -g "*.ts" -g "*.tsx"
```

验证结果：

- Ruff：通过。
- Pytest：`30 passed, 2 warnings`。warning 来自 SQLAlchemy 依赖的 `datetime.utcnow()` deprecation。
- 残留扫描：生产源码无旧 fallback/旧传输/未注册执行残留；唯一相关枚举符号 `FUNCTION_NOT_AVAILABLE` 的值是稳定小写码 `function_not_available`。

## 3. P0 审查项

| 维度 | 结论 | 证据 |
|---|---|---|
| Node 接入插拔性 | pass | Node 通过 YQP hello 上报平台和 runtime；调度依赖 registry 和 runtime requirements，不依赖 Center 平台分支。新增 `test_platform_prefix_uses_reported_platform_os_without_center_enum` 证明 `freebsd.system.info` 可按上报的 `platform.os=freebsd` canonical 成 `system.info`。 |
| Capability 插拔性 | pass | `sync_capability_runtime_snapshot` 将 manifest 写入 definition/source；Agent 通过 Center meta tools 发现和调用；`resolve_capability_invoke_target` 对多 source 明确要求 `source_id` 或 `node_id`，不猜测。 |
| Agent 解耦 | pass | 非 test mode 的 `_available_functions` 只返回 Center meta tools；`test_available_functions_filters_to_pinned_node_in_production_mode` 证明生产模式不暴露 raw Node capability。import boundary 测试阻止 services/application 反向依赖 agent/api。 |
| Runtime 边界 | pass | `capability.invoke` 只解析具体 source，不直接执行；之后委托到真实 registered name，并继续走 `_execute_node_job -> resolve_function -> create_invocation/create_job`。 |
| 错误显式传播 | pass | 未知 function 返回 `unavailable` 和稳定错误码，不创建 Job；ambiguous capability 返回 `capability_source_unresolved`；Provider 异常进入 `agent.provider.failed`/`llm_error`；Job 状态机写入 job/timeline 错误信息。 |
| 无静默 fallback | pass | 已删除 `allow_unregistered_function` 和 `_resolve_unregistered_admin_function`；生产源码扫描无 rclone、`advertise_host`、旧 fallback runtime、agent fallback synthesis、旧未注册执行路径。 |

## 4. 已完成整改

1. 删除 `ExecuteToolCommand.allow_unregistered_function` 和 `RuntimeCommand.allow_unregistered_function`。
2. 删除 `CenterExecutionRuntime._resolve_unregistered_admin_function`，未知 function 只能返回 unavailable，不能绕过 registry 创建 Job。
3. 移除 `capability.invoke`、`artifact.deploy` 委托命令中的旧 escape hatch 参数。
4. 将 admin invocation 默认 function unavailable 错误码统一为 `function_not_available`。
5. 增加 `test_execute_unknown_function_returns_unavailable` 的 Job 零创建断言。
6. 增加 `test_runtime_has_no_unregistered_capability_escape_hatch`，防止逃逸开关回归。
7. 增加任意平台前缀 canonical 化测试，防止平台接入能力退化成 Windows/Linux 写死。

## 5. 非阻塞 P1 项

以下项目不是 P0 blocker，但必须作为下一阶段质量优化继续执行：

1. 拆分 `CenterExecutionRuntime`、`TransferApplicationService` 和 Agent route，降低大模块维护成本。
2. 为 Agent 上下文预算建立强约束，尤其是 meta tool result、`node.list`、capability schema、history、context_refs 和 resume prompt 的投影/截断测试。
3. 进一步统一所有 HTTP route、Agent event、Operation projection 的错误码大小写和 problem shape，形成覆盖更广的错误契约测试。
4. 为 Node onboarding、capability onboarding、fallback residue 和 meta tool output size 建立更完整的持续性测试组。

## 6. 决策

P0 已收敛。当前代码可以作为进入 P1 代码质量和上下文预算治理的基线；不需要为 Node 平台扩展、Capability 扩展或 Agent 调用路径再做 P0 级架构重构。
