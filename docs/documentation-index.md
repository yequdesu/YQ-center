# Documentation Index

状态：当前文档入口
更新时间：2026-06-29

本文说明当前哪些文档是权威依据，哪些文档已经归档，避免后续开发引用过期计划。

## 权威文档

| 文档 | 用途 |
|---|---|
| `YQP-Node-Protocol.md` | Node/Center 协议合同。开发 Windows node、Linux node 必须以此为准。 |
| `YeQu-Architecture-Design.md` | 高层架构与设计意图。若与代码或 YQP 当前合同冲突，以代码和 YQP 当前合同为准。 |
| `docs/linux-node-development-contract.md` | Linux node POC 的实现约束与能力范围。 |
| `docs/current-project-overview-2026-06-28.md` | 当前项目全貌与能力边界。 |
| `docs/agent-sse-contract.md` | Agent SSE 前后端事件合同。 |
| `docs/yqp-production-reliability-report-2026-06-25.md` | YQP 可靠性修复记录，作为排障参考。 |
| `docs/debug-blog/2026-06-28-yqp-deadlock-postmortem.md` | DB 连接池/事务阻塞事故复盘，作为部署排障参考。 |

## 设想与路线文档

| 文档 | 用途 |
|---|---|
| `docs/proposals/README.md` | 设想/提案/路线文档目录说明。 |
| `docs/proposals/artifact-media-blob-layer.md` | Artifact / Media / Blob 基础层提案，覆盖文件传输、截图、摄像头和多模态资产。 |

## 历史诊断与交接类文档

| 文档 | 状态 |
|---|---|
| `docs/archive/refactor/architecture-handoff-2026-06-27.md` | 历史交接记录，不能作为当前计划来源。 |
| `docs/archive/refactor/refactor-completion-report-2026-06-25.md` | 历史完成报告。 |
| `docs/archive/refactor/center-agent-refactor-execution-plan.md` | 已执行过的 Center/Agent 解耦计划，当前只作背景。 |
| `docs/archive/refactor/mypy-debt-plan-2026-06-27.md` | 历史 mypy 债务计划。 |

## 已归档文档

以下目录和文件已经归档，不再作为当前开发约束：

```text
docs/archive/superpowers/
docs/archive/refactor/
```

归档内容主要是早期分阶段实现计划、旧 UX spec、以及已过时的架构诊断。它们可以解释项目演化，但不能覆盖当前协议、代码和测试。

## 文档一致性规则

1. 协议实现以 `src/yequ/api/routes/yqp.py`、`src/yequ/services/node_service.py` 和 YQP 测试为事实来源。
2. Node 开发以 `YQP-Node-Protocol.md` 为合同。
3. Agent 前端事件以 `docs/agent-sse-contract.md` 为合同。
4. 旧计划文档只保留历史意义，发现冲突时应更新权威文档，而不是让实现迁就旧计划。
5. 新增 Node capability 前，应先确认 capability name、risk、effect、runtime requirement 和 output 形态。
