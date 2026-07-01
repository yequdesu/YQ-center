# 待办文档

本目录存放与当前实现直接相关的近期执行待办。这里的文档比 proposal 更具体，比 archive 中的历史计划更接近当前代码形态。

适合放在本目录的内容：

- 下一轮 Node / 平台接入的阻塞事项；
- 需要立即执行的短期计划；
- 必须贴近当前仓库结构的待办。

## 活跃待办

| 文档 | 范围 |
|---|---|
| `2026-06-30-center-execution-runtime-v2.md` | 当前主路线图。将 Capability Runtime v1 升级为 Center Execution Runtime v2，新增 Execution Admission、Operation Bus、OperationEvent/Waiter、workflow handlers、Agent wait/resume 和 future MQ 边界。 |
| `2026-06-30-pre-phase6-agent-operation-polish.md` | 阶段 6 前强制优化待办。覆盖 Operation 进度透明、ExecutionGuard、Agent 工具选择治理、transfer preflight、capability projection 查询、Node capability 描述合同、prompt diagnostics 和 Linux Node 能力扩展。 |
| `2026-06-29-agent-provider-system.md` | 活跃 provider 路线图。将 DeepSeek-only 接线升级为 ProviderRegistry、OpenAI-compatible adapter、模型发现、连通性/tool/stream probe、Console 动态 provider/model 选择和显式 provider 错误报告。 |

## 已归档基线

| 文档 | 范围 |
|---|---|
| `../archive/todos/2026-06-28-agent-multinode-routing.md` | 已验收的 Agent runtime 重建与 Win/Linux 多 Node UX 历史基线。 |
| `../archive/todos/2026-06-28-center-capability-runtime-v1.md` | v1 capability identity、元工具执行、artifact 基线历史记录。 |
| `../archive/todos/2026-06-28-agent-tool-rag-roadmap.md` | 旧 Tool RAG 路线，当前第一阶段已收敛到 capability projection。 |
| `../archive/todos/2026-06-29-artifact-transfer-strategy.md` | 旧 artifact/transfer 路线，当前由 Node capability 合同和阶段 5H-5K 承接。 |
