# 待办文档

状态：当前待办目录
更新时间：2026-07-07

本目录只保留仍会指导下一阶段工作的待办。已经完成、被吸收或不再作为当前约束的路线图必须移动到 `docs/archive/todos/`。

## 活跃待办

| 文档 | 职责边界 | 不负责 |
|---|---|---|
| `2026-07-03-documentation-and-architecture-quality-gate.md` | 全局质量门禁：文档系统收口、架构边界审查、Node/capability 插拔性、错误传播、无静默 fallback。 | 不承载具体 YCR、transfer、provider 的实现步骤。 |
| `2026-07-06-ycr-agent-routing-and-transfer-corrections.md` | 行为缺陷修正和验收：Tool RAG / projection 已完成项的验收、Linux yq-croc receive 状态复验、Center transfer fact 继承复验、meta tool 默认输出和空错误传播。 | 不设计统一 capability registry 数据模型；不做 YCR core storage 重建；不做 provider 系统。 |
| `2026-07-07-agent-runtime-plan-operation-ycr-state.md` | Agent Runtime 架构收敛：Run/Turn 状态机、通用 Plan、Operation Event Queue、YCR Session State、Snapshot Cache、Tool RAG Candidate Loader、前端状态绑定和审计 span 化。 | 不处理具体 Linux receive bug；不处理 Provider registry；不引入 Workflow Capability；不改变 ApprovalRequest/task-level approval。 |
| `2026-06-29-agent-provider-system.md` | Provider 系统：provider registry、模型发现、连通性/tool/stream probe、Console 动态 provider/model 选择和显式 provider 错误报告。 | 不定义 capability registry；不处理 YCR projection；不处理 Node transfer 逻辑。 |

## 当前最近执行顺序

1. 当前最高优先级是 `2026-07-07-agent-runtime-plan-operation-ycr-state.md`：
   收敛 Agent Runtime 主状态机、Plan、Operation Event Queue、YCR Session State、Snapshot Cache 和 Tool RAG Candidate Loader，解决重复自动汇报、internal turn 无终态、首包 context load 慢和复杂任务过度探索。
2. `2026-07-06-ycr-agent-routing-and-transfer-corrections.md` 保留为行为修正和端到端验收入口：
   Windows 截图、Windows -> Linux yq-croc 传输复验、meta tool 默认输出边界和错误展示继续在这里追踪。
3. `2026-06-29-agent-provider-system.md` 在 Runtime/YCR 状态主线稳定后继续推进：
   Provider registry、模型发现、probe 和前端 provider/model 选择不抢占当前 P0。
4. `2026-07-03-documentation-and-architecture-quality-gate.md` 继续作为全局约束：
   文档收口、无静默 fallback、错误传播和架构边界审查贯穿所有实现。

## 已归档待办

| 文档 | 状态 |
|---|---|
| `../archive/todos/2026-06-30-center-execution-runtime-v2.md` | Runtime v2 历史路线图，核心内容已落地。 |
| `../archive/todos/2026-06-30-pre-phase6-agent-operation-polish.md` | 阶段 6 前优化历史计划，已被当前质量门禁吸收。 |
| `../archive/todos/2026-07-01-yq-croc-plugin-runtime-plan.md` | yq-croc 插件运行时历史方案，当前约束以 capability 合同和实现为准。 |
| `../archive/todos/2026-06-28-agent-multinode-routing.md` | 已验收的 Agent runtime 重建与 Win/Linux 多 Node UX 历史基线。 |
| `../archive/todos/2026-06-28-center-capability-runtime-v1.md` | v1 capability identity、元工具执行、artifact 基线历史记录。 |
| `../archive/todos/2026-06-28-agent-tool-rag-roadmap.md` | 旧 Tool RAG 路线，已被当前 YCR Tool RAG / Result RAG 实现和 `docs/ycr-current-state.md` 取代。 |
| `../archive/todos/2026-06-29-artifact-transfer-strategy.md` | 旧 artifact/transfer 路线，已被 Node capability 合同和当前 transfer 实现吸收。 |
| `../archive/todos/2026-07-06-ycr-clean-rebuild-and-docs-plan.md` | YCR 清理重建计划，核心数据路径已由 `docs/ycr-current-state.md` 承接。 |
| `../archive/todos/2026-07-06-unified-capability-registry-plan.md` | Unified Capability Registry 计划，T01-T16 已完成；后续行为问题不再由该文档承载。 |

## 维护规则

1. 新待办必须写明状态、日期、适用阶段和验收标准。
2. 同一职责边界只能有一个 active todo；跨主题依赖必须在本文件声明 owner，不允许多个文档同时拥有同一整改项。
3. 完成或被替代后，移动到 `docs/archive/todos/`，并更新本文件和 `docs/documentation-index.md`。
4. 活跃待办如果经过多轮实现后出现“部分已完成、部分未完成”，必须在文档顶部维护当前实现核对表，列明代码事实、完成状态和剩余动作。
5. 代码改动完成某个待办项时，同一提交必须同步更新对应待办状态；不得让已完成项继续以未完成口吻留在 active todo 中。
