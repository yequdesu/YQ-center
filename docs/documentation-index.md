# 文档索引

状态：当前文档入口
更新时间：2026-07-03

本文是 YeQu Center 文档系统的唯一入口。当前开发只应引用“当前权威文档”和“活跃待办”；`docs/archive/` 下内容只解释历史演进，不能覆盖当前代码、协议和合同。

## 1. 当前权威文档

| 文档 | 职责 |
|---|---|
| `README.md` | 项目入口、快速启动、API 概览、开发命令和配置说明。 |
| `YQP-Node-Protocol.md` | Node 接入 Center 的权威协议合同。Windows/Linux/未来 Node Daemon 必须以此为准。 |
| `docs/current-project-overview.md` | 当前项目全貌、架构边界、能力状态和下一阶段主线。 |
| `docs/node-capability-contract.md` | 面向多平台 Node 接入的 capability 合同。约束 manifest、runtime requirements、preflight、progress、cancel/resume、错误码、artifact/transfer 能力和 Guard/Policy 边界。 |
| `docs/linux-node-development-contract.md` | Linux Node 当前实现合同、权限模型和能力范围。 |
| `docs/agent-sse-contract.md` | Agent SSE 前后端事件合同。 |
| `docs/documentation-policy.md` | 文档编码、状态、归档、一致性和事实来源规则。 |
| `docs/todos/2026-07-03-documentation-and-architecture-quality-gate.md` | 下一阶段前的文档与架构质量门禁。 |

## 2. 活跃待办

| 文档 | 范围 |
|---|---|
| `docs/todos/README.md` | 当前待办目录说明。 |
| `docs/todos/2026-07-03-documentation-and-architecture-quality-gate.md` | 文档系统收口、代码质量审查、架构边界复核、Node/capability 插拔性和错误传播门禁。 |
| `docs/todos/2026-06-29-agent-provider-system.md` | Provider registry、模型发现、连通性探测、Console 动态 provider/model 选择和显式 provider 错误报告。 |

## 3. 已归档内容

以下内容已经归档，只能作为背景材料阅读。

| 归档目录 | 内容 |
|---|---|
| `docs/archive/todos/` | 已完成、已被吸收或已被替代的路线图和阶段计划。 |
| `docs/archive/proposals/` | 已采纳或已废弃的设计提案。 |
| `docs/archive/overview/` | 旧架构概览、旧项目全貌和历史交接材料。 |
| `docs/archive/postmortems/` | 事故复盘、可靠性报告和排障记录。 |
| `docs/archive/reports/` | 阶段性审查报告和质量门禁结论。 |
| `docs/archive/visuals/` | 临时展示页、架构图和非合同型展示材料。 |
| `docs/archive/refactor/` | 历史重构计划、完成报告和 mypy 债务记录。 |
| `docs/archive/superpowers/` | 早期分阶段实现计划和旧 UX spec。 |

重点归档说明：

| 文档 | 当前状态 |
|---|---|
| `docs/archive/overview/yequ-architecture-design-v0.1.md` | 早期架构草案，已被 `docs/current-project-overview.md` 和当前代码取代。 |
| `docs/archive/todos/2026-06-30-center-execution-runtime-v2.md` | Runtime v2 实施路线和历史记录，核心内容已落地并由当前 overview 承接。 |
| `docs/archive/todos/2026-06-30-pre-phase6-agent-operation-polish.md` | 阶段 6 前置优化历史计划，已被质量门禁文档吸收。 |
| `docs/archive/todos/2026-07-01-yq-croc-plugin-runtime-plan.md` | yq-croc 插件运行时方案历史记录；当前执行约束以 capability 合同和 transfer 实现为准。 |
| `docs/archive/postmortems/yqp-production-reliability-report-2026-06-25.md` | YQP 可靠性修复记录，作为排障背景。 |
| `docs/archive/postmortems/2026-06-28-db-lock-saga.md` | DB 锁事故复盘。 |
| `docs/archive/postmortems/2026-06-28-yqp-deadlock-postmortem.md` | YQP/DB 阻塞事故复盘。 |
| `docs/archive/visuals/architecture-five-nodes-2026-07-03.html` | 五节点架构静态展示页，不作为合同。 |

## 4. 一致性规则

1. 协议事实以 `YQP-Node-Protocol.md`、`src/yequ/api/routes/yqp.py`、`src/yequ/services/node_service.py` 和 YQP 测试为准。
2. Node capability 开发以 `docs/node-capability-contract.md` 和具体平台合同为准。
3. Agent 前端事件以 `docs/agent-sse-contract.md` 为准。
4. 当前架构状态以 `docs/current-project-overview.md` 为准。
5. 下一阶段工作以 `docs/todos/2026-07-03-documentation-and-architecture-quality-gate.md` 为入口。
6. 新增 Node capability 前，必须明确 capability name、risk、effect、runtime requirements、preflight、progress、cancel/resume、output schema 和错误码。
7. 所有文档必须使用 UTF-8 编码；详见 `docs/documentation-policy.md`。
8. 发现当前文档与代码冲突时，优先修正文档或归档旧文档，不让实现迁就过期计划。
