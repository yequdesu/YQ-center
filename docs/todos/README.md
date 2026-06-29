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
| `2026-06-29-artifact-transfer-strategy.md` | 活跃传输路线图。将轻量 YQP artifact 上传与 croc 支撑的大文件/跨 Node 传输拆分为两条数据面；TransferSession 负责传输领域事实，等待/事件/恢复应收敛到 Execution Runtime v2。 |
| `2026-06-29-agent-provider-system.md` | 活跃 provider 路线图。将 DeepSeek-only 接线升级为 ProviderRegistry、OpenAI-compatible adapter、模型发现、连通性/tool/stream probe、Console 动态 provider/model 选择和显式 provider 错误报告。 |

## 已验收基线

| 文档 | 范围 |
|---|---|
| `2026-06-28-agent-multinode-routing.md` | 已验收的 Agent runtime 重建与 Win/Linux 多 Node UX 基线。 |
| `2026-06-28-center-capability-runtime-v1.md` | 已部分验收的 capability identity、元工具执行、artifact 上传/列表/下载/展示、Node 级 capability snapshot 注册基线。后续主线已被 `2026-06-30-center-execution-runtime-v2.md` 取代。 |

## 未来架构待办

| 文档 | 范围 |
|---|---|
| `2026-06-28-agent-tool-rag-roadmap.md` | Tool RAG 路线图，是 Center Execution Runtime v2 中 capability discovery/context 子集。 |
