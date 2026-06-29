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

- `artifact-media-blob-layer.md`：Artifact / Media / Blob 基础层，用于文件传输、截图、摄像头、多模态输入输出和审计附件。
- `2026-06-30-operation-bus-execution-admission.md`：已采纳的 Operation Bus / Execution Admission 提案，作为 Center Execution Runtime v2 的调度与等待设计依据。

已取代提案：

- 原 `2026-06-30-operation-supervisor-agent-runtime.md`：已移除并被 `2026-06-30-operation-bus-execution-admission.md` 取代。原提案过度强调 Operation Supervisor，本次修正后以 Execution Admission + Operation Bus 为核心。
