# 文档与架构质量门禁

状态：active todo
日期：2026-07-03
适用阶段：Runtime v2、yq-croc、Win/Linux Node 接入主线基本稳定之后

## 1. 目标

进入下一阶段前，先完成文档系统收口、代码质量审查和架构边界复核。目标不是继续堆新功能，而是让项目具备可维护的下一阶段基线：

- 当前权威文档数量少、职责清晰、入口唯一；
- 旧计划、旧提案、历史复盘全部归档，不再和当前合同混在一起；
- Center / Agent / Runtime / Node capability 边界由代码和测试共同守住；
- 新 Node、新平台和新 capability 必须能按协议接入，不要求同步修改 Center 或 Agent 行为；
- 错误传播、无静默 fallback、能力发现治理和大输出治理进入质量门禁；
- 所有审查项都有确定的整改入口，不靠口头约定。

## 2. 当前权威文档边界

当前有效文档只承担以下职责：

| 文档 | 职责 |
|---|---|
| `README.md` | 项目入口、快速启动、API 概览和开发命令。 |
| `YQP-Node-Protocol.md` | Node 接入 Center 的协议合同。 |
| `docs/current-project-overview.md` | 当前架构全貌、能力边界和下一阶段主线。 |
| `docs/ycr-current-state.md` | YCR 当前实现现状和运行边界。 |
| `docs/node-capability-contract.md` | 面向多平台 Node 接入的 capability manifest、preflight、progress、error、artifact/transfer 合同。 |
| `docs/linux-node-development-contract.md` | Linux Node 当前实现合同。 |
| `docs/agent-sse-contract.md` | Agent SSE 事件和 Console 流式交互合同。 |
| `docs/documentation-index.md` | 文档入口、归档说明和一致性规则。 |
| `docs/documentation-policy.md` | 文档编码、状态、归档和事实来源规则。 |
| `docs/todos/2026-07-03-documentation-and-architecture-quality-gate.md` | 本阶段质量门禁。 |
| `docs/todos/2026-07-07-agent-runtime-plan-operation-ycr-state.md` | Agent Runtime、Plan、Operation Event Queue、YCR Session State、Snapshot Cache 和 Candidate Loader 架构收敛。 |
| `docs/todos/2026-07-08-exec-profile-controlled-exec-design.md` | Exec Profile、`exec.run` 和 Node primitive 能力收敛。 |
| `docs/todos/2026-06-29-agent-provider-system.md` | Provider registry、模型发现、probe 和 provider/model 前端选择。 |
| `docs/todos/README.md` | 当前活跃待办职责边界。 |

其他历史计划、提案、复盘和展示材料只作为背景材料阅读，不能覆盖当前合同。

## 3. 文档整理门禁

完成标准：

1. `docs/` 根目录只保留当前权威文档，不放临时展示页、事故复盘、过期计划或已完成提案。
2. `docs/todos/` 只保留当前阶段仍需执行的待办；已完成或被吸收的路线图移入 `docs/archive/todos/`。
3. `docs/archive/` 下按来源分目录：`todos/`、`proposals/`、`discussions/`、`overview/`、`postmortems/`、`visuals/`、`refactor/`、`superpowers/`。
4. 主索引明确每份 active 文档的职责，不让多个文档同时作为同一职责边界的权威来源。
5. 新增或迁移文档后，必须检查 active 文档中的旧路径引用。
6. 归档是移动，不是删除；历史信息必须保留。

## 4. 代码质量门禁

下一轮代码审查优先级如下：

| 优先级 | 范围 | 判定标准 |
|---|---|---|
| P0 | Node 接入插拔性 | 新增任意平台 Node 后，只要完成 provisioning、YQP hello、runtime 上报和 capability 注册，Center 不需要新增平台分支即可调度。 |
| P0 | Capability 插拔性 | 新 capability 只要按 manifest 合同注册，Agent 就能通过分组目录、session working set、`capability.search` / `capability.describe` / `capability.invoke` 发现和调用，不需要改 Agent prompt。Center meta tool 分组本身是 Center 内部目录，不应随 Node capability 增加而修改。 |
| P0 | 错误传播 | Provider、Runtime、Tool、YQP、Operation 统一稳定错误码和 problem projection；前端不得收到伪成功或空失败。 |
| P0 | 无静默 fallback | 生产路径不得保留旧 croc CLI、rclone、直连传输、未注册 capability 执行或 provider 自动切换。 |
| P1 | 模块职责 | 拆分过大的 `CenterExecutionRuntime`、`TransferApplicationService` 和 Agent route，避免新大总管形成。 |
| P1 | Agent 上下文预算 | 工具 schema、tool result、history、context_refs、resume prompt 都必须有预算、投影和摘要策略。 |
| P1 | 架构边界测试 | 扩展 import boundary、fallback residue、meta tool output size、capability onboarding 和 node onboarding 测试。 |
| P2 | 文档生成物 | HTML、图表、报告等展示产物默认归档到 `docs/archive/visuals/` 或 `docs/archive/reports/`，不作为 active 合同。 |

## 5. 架构审查清单

审查时逐项给出结论：`pass`、`needs work` 或 `blocked`。

| 维度 | 必须确认的问题 |
|---|---|
| Node 接入无关 | 增加新 Node 是否只需要 Center provisioning、Node token、YQP hello/heartbeat、runtime 上报和 capability 注册。 |
| 平台接入无关 | Center 是否可以接入任意平台的 Node；平台差异是否只由 Node daemon、plugin adapter、capability manifest 和 runtime requirements 表达。 |
| Capability 即插即用 | 新 capability 注册后是否自动进入 Center registry、structured discovery 和 Agent meta tool 调用路径，不需要新增 Center/Agent 分支。 |
| Agent 解耦 | services/application/runtime 是否不反向依赖 `yequ.agent`。 |
| Runtime 边界 | Agent、Admin、Maintenance、Transfer 是否统一进入 `CenterExecutionRuntime` 或明确 handler。 |
| 错误透明 | 每个失败是否有稳定 error_code、message、details，并能投影到 Agent/Console。 |
| 无 fallback | 失败是否显式失败，而不是换路线、换 provider、换目录或降级到旧实现。 |
| 文档一致 | active 文档是否都指向当前实现，没有把 archive 当成当前计划。 |

## 6. 本阶段输出物

本阶段结束时必须留下：

1. 整理后的 `docs/documentation-index.md`。
2. 更新后的 `docs/current-project-overview.md`。
3. 更新后的 `docs/todos/README.md`。
4. 归档后的历史文档，不丢失原始内容。
5. 一份代码质量审查报告或对应整改 PR。
6. 一组新增/更新测试，覆盖边界、fallback、错误传播、Node onboarding 和 capability onboarding。

## 7. 非目标

本阶段不引入新业务能力，不扩展 SubAgent，不重写 YQP 协议，不处理生产部署安全、Docker 独立部署或完整部署闭环，不把历史文档全部删除。归档的目标是让当前开发者不被旧计划误导，同时保留完整演进记录。
