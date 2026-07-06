# 待办文档

状态：当前待办目录
更新时间：2026-07-06

本目录只保留仍会指导下一阶段工作的待办。已经完成、被吸收或不再作为当前约束的路线图必须移动到 `docs/archive/todos/`。

## 活跃待办

| 文档 | 职责边界 | 不负责 |
|---|---|---|
| `2026-07-03-documentation-and-architecture-quality-gate.md` | 全局质量门禁：文档系统收口、架构边界审查、Node/capability 插拔性、错误传播、无静默 fallback。 | 不承载具体 YCR、transfer、provider 的实现步骤。 |
| `2026-07-06-ycr-clean-rebuild-and-docs-plan.md` | YCR 核心数据路径：raw ContextRef、provider-visible projection、Result RAG、YCR API、启动/部署脚本和 YCR 文档一致性。 | 不定义 Agent 工具面最终形态；不修具体 transfer bug；不定义 provider registry。 |
| `2026-07-06-ycr-agent-routing-and-transfer-corrections.md` | 行为缺陷修正：Tool RAG 通用排序质量、递归 projection 粒度、Linux yq-croc receive 误报失败、Center transfer fact 继承、meta tool 默认输出和空错误传播。 | 不设计统一 capability registry 数据模型；不做 YCR core storage 重建；不做 provider 系统。 |
| `2026-07-06-unified-capability-registry-plan.md` | 能力目录与 Agent 工具面：Center meta tools 与 Node capabilities 统一注册、`agent_visible/invocation_surface` 分层、固定 bootstrap tools、统一 `capability.invoke`；同时承载 Tool RAG cache、precompute 和前后台资源调度。 | 不处理具体 Linux receive bug；不处理 Provider registry。 |
| `2026-06-29-agent-provider-system.md` | Provider 系统：provider registry、模型发现、连通性/tool/stream probe、Console 动态 provider/model 选择和显式 provider 错误报告。 | 不定义 capability registry；不处理 YCR projection；不处理 Node transfer 逻辑。 |

## 当前最近执行顺序

1. `2026-07-06-unified-capability-registry-plan.md` 的 T01-T16 主线已完成：
   Center capabilities 已统一入 registry，Provider 默认工具面已收窄到 `capability.search/describe/invoke`，前端已显示 registry 命中、index-not-ready 和整数 token accounting。
2. 下一步按 `2026-07-06-ycr-agent-routing-and-transfer-corrections.md` 处理剩余行为缺陷：
   Linux yq-croc receive 成功误判失败、TransferSession fact 继承、复杂任务过度探索等。
3. 再回到 `2026-07-06-ycr-clean-rebuild-and-docs-plan.md` 里未完成的 YCR core data path
   项，清理已被前两份文档吸收或取代的描述。
4. `2026-07-06-ycr-agent-routing-and-transfer-corrections.md` 只在新的截图/传输验收暴露行为回归时继续更新。

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

## 维护规则

1. 新待办必须写明状态、日期、适用阶段和验收标准。
2. 同一职责边界只能有一个 active todo；跨主题依赖必须在本文件声明 owner，不允许多个文档同时拥有同一整改项。
3. 完成或被替代后，移动到 `docs/archive/todos/`，并更新本文件和 `docs/documentation-index.md`。
4. 活跃待办如果经过多轮实现后出现“部分已完成、部分未完成”，必须在文档顶部维护当前实现核对表，列明代码事实、完成状态和剩余动作。
5. 代码改动完成某个待办项时，同一提交必须同步更新对应待办状态；不得让已完成项继续以未完成口吻留在 active todo 中。
