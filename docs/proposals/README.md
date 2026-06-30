# 设想与提案

状态：设想与路线文档目录

本目录存放尚未进入实现阶段、但可能影响项目架构方向的设计设想、路线和待办。

文档状态约定：

| 状态 | 含义 |
|---|---|
| `proposal` | 设计提案，尚未实施。 |
| `accepted` | 已决定采用，但可能尚未完成。 |
| `in_progress` | 正在实现。 |
| `completed` | 已实现，可转移到正式文档或保留为历史记录。 |
| `rejected` | 已明确不采用。 |

当前提案：

当前没有仍需作为活跃入口的 proposal。新的设计如果已经进入执行，应直接写入
`docs/todos/` 或权威合同文档；仅保留尚未进入执行的设想在本目录。

已归档提案：

- 原 `2026-06-30-operation-supervisor-agent-runtime.md`：已移除并被 `2026-06-30-operation-bus-execution-admission.md` 取代。原提案过度强调 Operation Supervisor，本次修正后以 Execution Admission + Operation Bus 为核心。
- `docs/archive/proposals/2026-06-30-operation-bus-execution-admission.md`：已采纳，内容并入 `docs/todos/2026-06-30-center-execution-runtime-v2.md`。
- `docs/archive/proposals/artifact-media-blob-layer.md`：已被当前 Artifact 实现、`docs/node-capability-contract.md` 和阶段 5H-5K 吸收。
