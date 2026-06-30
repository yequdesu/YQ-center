# Operation Bus / Execution Admission 提案

状态：accepted proposal  
日期：2026-06-30  
主待办：`docs/todos/2026-06-30-center-execution-runtime-v2.md`

## 1. 提案结论

YeQu Center 需要加入 Operation Bus，但不能以“固定 execution_class 创建 Operation”的方式实现。

正确设计是：

```text
Execution Admission
  -> ExecutionPlan
  -> Inline Execution 或 Operation Bus
```

Operation Bus 是 Center Execution Runtime 的一部分，不是 Agent 的附属模块，也不是 transfer 的补丁。

## 2. 为什么原始 Operation Supervisor 设计需要修正

原始设想中有一个风险：

```text
capability.execution_class == waitable
=> 一定创建 Operation
```

这过于死板。它不能处理同一个 capability 因输入不同而需要不同调度方式的情况。

例如：

| 调用 | 合理执行方式 |
|---|---|
| `file.hash(small.txt)` | 同步等待结果。 |
| `file.hash(100GB.iso)` | 后台 Operation。 |
| `artifact.upload(300KB)` | YQP inline artifact 上传。 |
| `artifact.upload(2GB)` | croc / waitable。 |
| `transfer.create` | workflow Operation。 |
| `system.info` | inline。 |

因此，capability 可以提供执行提示，但不能决定最终调度。

## 3. Execution Admission

`ExecutionAdmissionService` 在每次调用前动态生成 `ExecutionPlan`。

输入包括：

- actor；
- tool/function intent；
- capability metadata；
- input payload；
- target node；
- runtime availability；
- policy / approval；
- resource lock；
- historical stats；
- data plane；
- 是否需要 fan-out/fan-in；
- 是否需要 artifact；
- 是否支持 progress / cancel / resume。

输出示例：

```json
{
  "decision": "workflow_operation",
  "reason": "transfer.create requires concurrent receiver and sender jobs",
  "sync_wait_budget_sec": 5,
  "requires_operation": true,
  "resume_policy": "manual",
  "cancel_supported": true
}
```

这让 Center 能动态选择：

- inline；
- sync wait；
- waitable operation；
- workflow operation；
- detached operation；
- approval pause；
- reject。

## 4. Operation 的精确定义

Operation 表达的是运行时外壳：

```text
可等待、可取消、可恢复、可订阅、可审计关联的 Center 运行过程。
```

它不保存领域细节。

| 细节 | 归属 |
|---|---|
| 传输路径、两端 Job、hash | `TransferSession` |
| 维护步骤、回滚建议 | `MaintenanceRun` / `MaintenanceStep` |
| Node 执行输入输出 | `Job` |
| LLM 上下文与 checkpoint | `AgentRun` / `AgentRunStep` |
| 审批输入 hash 与状态 | `ApprovalRequest` |
| 审计事实 | `TimelineEvent` |
| 运行时投影与唤醒 | `OperationEvent` |

Operation 只保存：

- `operation_id`;
- `kind`;
- `status`;
- `owner`;
- `ref_type` / `ref_id`;
- `admission_decision`;
- `progress`;
- `resume_policy`;
- `cancel_policy`;
- `error`;
- 时间戳。

## 5. OperationEvent / Outbox

OperationEvent 是运行时事件流。它不是 Timeline 的替代品。

区别：

| 对象 | 目的 |
|---|---|
| `TimelineEvent` | 审计事实，不可绕过。 |
| `OperationEvent` | UI projection、Agent resume、future MQ/outbox。 |

第一阶段使用 PostgreSQL 作为事实源：

```text
operations
operation_events
operation_waiters
```

未来需要时，`OperationEventDispatcher` 可以把事件投递到：

- Redis Streams；
- NATS；
- RabbitMQ；
- WebSocket/SSE fanout。

外部 MQ 不能成为新的领域事实源。

## 6. Agent 解耦

Operation Bus 属于 Center Runtime。

Agent 只做：

- 表达意图；
- 接收 tool result；
- 接收 wait_handle；
- 被 resume 后读取 operation observation；
- 生成最终自然语言。

Agent 不做：

- 决定同步/异步；
- 管理 Operation 状态机；
- 轮询长任务；
- 直接订阅 MQ；
- 了解 croc/Node poll/Job lease 细节。

## 7. Transfer 的定位

Transfer 现在是单独 application service，是因为 croc 传输需要：

- 创建 `TransferSession`；
- 生成 croc code；
- 创建 receiver job；
- 创建 sender job；
- 聚合两端状态；
- 一端失败时取消另一端。

长期应收敛为：

```text
TransferWorkflow
  -> TransferSession
  -> Job fan-out/fan-in
  -> Operation Bus
```

`TransferSession` 仍是传输领域模型，Operation 只负责等待、事件、取消、恢复。

## 8. 是否是补丁

如果只为了 croc 创建一张 Operation 表，那是补丁。

如果把它定义为 Center Execution Runtime 的统一等待/事件/恢复层，它就是主架构升级。

判断标准：

| 标准 | 必须满足 |
|---|---|
| CLI/Console/MCP 能复用 | 是 |
| 不依赖 Agent prompt | 是 |
| 不替代领域模型 | 是 |
| 不绕过 policy/job/timeline | 是 |
| 可支持 transfer/maintenance/approval/subagent | 是 |
| 可在未来接 MQ | 是 |

满足这些条件时，Operation Bus 有长期结构价值。

