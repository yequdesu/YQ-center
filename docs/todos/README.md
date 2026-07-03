# 待办文档

状态：当前待办目录
更新时间：2026-07-03

本目录只保留仍会指导下一阶段工作的待办。已经完成、被吸收或不再作为当前约束的路线图必须移动到 `docs/archive/todos/`。

## 活跃待办

| 文档 | 范围 |
|---|---|
| `2026-07-03-documentation-and-architecture-quality-gate.md` | 下一阶段前的文档系统收口、代码质量审查、架构边界复核、Node/capability 插拔性和错误传播门禁。 |
| `2026-06-29-agent-provider-system.md` | Provider registry、模型发现、连通性/tool/stream probe、Console 动态 provider/model 选择和显式 provider 错误报告。 |

## 已归档待办

| 文档 | 状态 |
|---|---|
| `../archive/todos/2026-06-30-center-execution-runtime-v2.md` | Runtime v2 历史路线图，核心内容已落地。 |
| `../archive/todos/2026-06-30-pre-phase6-agent-operation-polish.md` | 阶段 6 前优化历史计划，已被当前质量门禁吸收。 |
| `../archive/todos/2026-07-01-yq-croc-plugin-runtime-plan.md` | yq-croc 插件运行时历史方案，当前约束以 capability 合同和实现为准。 |
| `../archive/todos/2026-06-28-agent-multinode-routing.md` | 已验收的 Agent runtime 重建与 Win/Linux 多 Node UX 历史基线。 |
| `../archive/todos/2026-06-28-center-capability-runtime-v1.md` | v1 capability identity、元工具执行、artifact 基线历史记录。 |
| `../archive/todos/2026-06-28-agent-tool-rag-roadmap.md` | 旧 Tool RAG 路线，当前第一阶段已收敛到 capability projection。 |
| `../archive/todos/2026-06-29-artifact-transfer-strategy.md` | 旧 artifact/transfer 路线，已被 Node capability 合同和当前 transfer 实现吸收。 |

## 维护规则

1. 新待办必须写明状态、日期、适用阶段和验收标准。
2. 同一主题只能有一个 active todo；历史推演放 archive，不和当前入口并列。
3. 完成或被替代后，移动到 `docs/archive/todos/`，并更新本文件和 `docs/documentation-index.md`。
