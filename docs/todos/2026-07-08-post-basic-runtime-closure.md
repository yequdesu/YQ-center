# Agent Runtime 基础实现后收口待办

状态：active todo，部分收口实现已完成
日期：2026-07-08
范围：Center / Agent Runtime / YCR / Console / Linux Node / Windows Node

本文回答“基本完成但未完全完成”的剩余工作。当前不是推翻重做阶段：Run/Turn、Plan、Operation notification、YCR Session State、Tool Candidate Loader、Center meta tool 分组、`exec.run` 和基础前端面板已经可用。未完成的是复杂任务稳定收敛、真实任务验收、少量前端业务残留、`exec.run` profile 使用链路、系统提示词与当前实现一致性、以及耗时归因硬化。

本文是收口执行清单，不重新定义架构。架构来源仍为：

- `docs/todos/2026-07-07-agent-runtime-plan-operation-ycr-state.md`
- `docs/todos/2026-07-08-exec-profile-controlled-exec-design.md`
- `docs/todos/2026-07-06-ycr-agent-routing-and-transfer-corrections.md`
- `docs/ycr-current-state.md`

## 1. 当前结论

“基本完成”表示以下基础闭环已经落地：

1. AgentRun / AgentTurn / AgentRunEvent / AgentPlan 已经存在并进入主链路。
2. Operation 终态已进入服务端 notification queue，前端不再负责自动唤醒 Agent。
3. YCR 已经作为独立 HTTP 服务进入 provider 前置上下文边界。
4. YCR Session State、ContextRef、projection、Tool RAG candidate loader、token accounting 已可用。
5. Center meta tools 和 Node capabilities 已统一进入 capability registry。
6. Provider 默认工具面已收窄为 `capability.groups` / `capability.group.open` / `capability.invoke`。
7. Windows/Linux 低质量 primitive 小能力已删除，普通命令统一走 `exec.run`。

“未完全完成”表示以下事项仍未达到稳定收口标准：

1. 复杂任务仍可能绕路、重复打开分组、重复 search/describe。
2. `exec.run` 的 profile contract 虽然存在，但 Agent 仍可能传 `admin` 或漏传 `profile`。
3. Replanner 还没有完全接管“继续、修正参数、询问用户、完成、失败”的边界。
4. TaskState / PlanStep 对 artifact、operation、approval、tool failure 的归属仍需细化。
5. Console 仍保留少量 operation context / tool card 局部 patch 状态，未完全事件源渲染。
6. 系统提示词落后于当前实现，尤其是 `exec.run` profile、Center meta tool 分组、artifact 读取和 Operation 后端恢复语义。
7. 真实任务验收矩阵尚未全部跑完并固化为回归测试。
8. 耗时归因已经有 session audit，但 first token、DB lock、provider streaming 和 operation wait 的细分仍不够完整。

## 1.1 用户问题覆盖矩阵

本节把最近讨论中明确提出的问题逐项映射到本文待办，避免“看似记录了、实际没有执行入口”。

| 问题 | 当前覆盖 | 待办 |
|---|---|---|
| Agent 只使用目标 Node 实际在线 runtime 上报的 profile。 | 已覆盖，但需强调由 prompt/tool contract 优先解决。 | C01、C09 |
| 只读任务权限失败后应自动尝试 `admin.readonly`，而不是停下询问。 | 已覆盖。自动尝试由系统提示词指导 LLM；Runtime 只校验和阻止错误终答。 | C01、C02、C09 |
| 前端不应承担业务推进。 | 已覆盖。 | C05 |
| Operation 完成后由后端 Agent Runtime 自动恢复，不靠前端 Append。 | 基础实现已完成，仍需验收并清理前端残留入口。 | C02、C04、C05、C06 |
| Approval 后前端闪现/卡住。 | 已覆盖，但需要真实验收。 | C05、C06 |
| 断点继续没有完全做到 event/tool/operation 级别。 | 已覆盖。 | C03、C04、C11 |
| Runtime/Plan/YCR 面板有基础显示，但非最终验收态。 | 已覆盖。 | C04、C05、C07 |
| YCR 不能重新承担任务流程决策。 | 需要持续审查，补充明确边界。 | C10、C11 |
| Result RAG 只覆盖已索引 ContextRef，复杂任务收益待验收。 | 已补充为独立验收项。 | C10 |
| Tool candidate loading 是否减少重复 search/group/open 需真实会话判断。 | 已补充为独立验收项。 | C10、C06 |
| Console 真实任务验收未完成。 | 已覆盖。 | C06 |
| `admin.readonly` / `admin.write` sudo/提权稳定性需继续测。 | 已覆盖。 | C01、C06 |
| 低质量 primitive 已删，但 exec 模式复杂文件/日志/权限任务未完全验证。 | 已覆盖。 | C06 |
| Replanner 尚未完全接管 ReAct loop 继续/停止边界。 | 已覆盖。 | C02 |
| completion criteria 弱，任务完成判定不稳定。 | 已覆盖，但需要细化完成契约。 | C02、C03、C04 |
| PlanStep 对 artifact、多分支任务、operation/approval 归属仍需收敛。 | 已覆盖。 | C04 |
| 系统提示词粗糙且落后于实现。 | 已补充为独立待办。 | C09 |
| 是否展示 CoT / 思考过程。 | 不展示原始 CoT；补充可审计 decision trace 和 reasoning summary。 | C09、C11 |
| 把 artifact 写到指定 Node 路径时选错 source，例如 Windows 目标路径被发送到 Linux source。 | 已补充结构性修复。`capability.invoke` 会拒绝 `source_id` / `node_id` 冲突；`artifact.download_file` 直调必须显式 `node_id`；语义 RAG 返回同一 capability 的 sources 时按 query 命中的 node/platform/source 字段排序；prompt 明确 artifact placement 必须走 `artifact.deploy`。 | C02、C06、C09、C10 |

## 2. 近期真实问题归档

### 2.1 `exec.run` profile 误用

最新 session `sess_334f37cc3bf6485f` 暴露：

- 用户要求“可能需要使用admin”；
- Agent 第一次调用 `linux.exec.run` 时缺少 `profile`；
- Agent 第二次调用时传入裸 `admin`；
- Linux Node 正确拒绝，错误为 `unsupported exec profile: admin`；
- 当前可用 profile 合同要求使用 `admin.readonly` 或 `admin.write`。

结论：

- 这不是 Linux Node 离线、sudo 不可用或 YCR projection 错误。
- 这是 Agent 对 capability schema 和 runtime profile contract 的使用不稳定。
- 首要修复点是系统提示词和工具合同：LLM 必须知道 `admin` 不是合法 profile，并在只读诊断权限不足时选择目标 Node 实际上报的 `admin.readonly`。
- Runtime 只负责校验和错误传播；不在 Node 或 Center 中添加 `admin -> admin.readonly` 之类兼容 fallback。

### 2.2 主要耗时来源

同一 session 的审计显示：

- `agent.invoke.context_loaded` 只有约 5 到 22ms，不是主要慢点；
- LLM provider 每步约 3.8 到 11.2s；
- 复杂任务慢的主要原因是多步 ReAct 循环、重复工具发现和失败后重新让 LLM 判断；
- approval 等待和用户间隔会拉长 session 时间，但不属于系统执行耗时。

结论：

- 热路径优化重点不是继续压 YCR build-turn，而是减少无效 LLM 轮次。
- Replanner 必须阻止“可继续修复的问题被当成成功终答”，但不应替代 LLM 做复杂命令规划。profile 选择这类有清晰工具合同的问题优先由系统提示词和 provider-visible schema 解决。

### 2.3 Artifact 目标 Node 绑定错误

最新 session `sess_fa110030cc0e43f9` 暴露：

- 用户要求把已生成的 Windows screenshot artifact 放到 Windows `F:\Desktop`；
- Agent 选用了 `linux-node-01` 的 `artifact.download_file` source；
- Center 正常把任务调度到 Linux；
- Linux 正确拒绝 Windows 路径，错误为 `output_path must be absolute`。

结论：

- 这不是 Windows Node 写文件能力缺失，也不是 Linux 路径校验错误；
- 根因是工具候选和调用没有把“目标 Node/目标路径归属”作为绑定约束；
- 结构修复已经落地：`source_id` 与 `node_id` 冲突时返回 `target_node_mismatch`；`artifact.download_file` 直调必须显式 `node_id`；用户级 artifact placement 应使用 `artifact.deploy.preflight` + `artifact.deploy`；YCR semantic search 对同一 capability 的 sources 按 query 命中的 node/platform/source 字段排序。

## 3. 可执行待办

### C01 `exec.run` profile contract 闭环

状态：基础实现已完成，待真实会话验收
归属：Agent prompt policy / capability contract / Center validation / Linux Node / Windows Node

目标：

- Agent 只使用目标 Node 实际在线 runtime 上报的 profile。
- 系统提示词明确 `exec.run` profile 规则，避免 LLM 传裸 `admin`、漏传 `profile` 或把 profile 选择交还给用户。
- 用户说“admin”时，LLM 不能把 `admin` 作为 profile 原样传递；必须根据任务读写意图选择目标 Node 已上报的 `admin.readonly` 或 `admin.write`，或在 profile 不存在时明确失败。

实施项：

- [x] 系统提示词加入 `exec.run` profile 规则：只读诊断/日志/文件读取优先 readonly，权限不足且 `admin.readonly` 可用时继续尝试 `admin.readonly`；写操作才使用 write profile；禁止裸 `admin` / `sudo` / `root` / `user` / `default`。
- [x] `capability.search` / `capability.describe` 的 `exec.run` invoke-ready 输出必须清晰暴露 `profile` enum、当前 Node 可用 profiles、read/write 选择规则和最短调用示例。`capability.group.open` 只负责 Center meta tool 分组，不直接承载 Node `exec.run` 合同。
- [x] Center validation 对缺少 required slot 和 enum 不合法返回稳定错误，错误中包含 schema required 字段、合法 enum 和目标 Node 可用 profiles。
- [x] Replanner 对 `missing_required_slot` / `unsupported_enum_value` 只做生命周期控制：禁止直接成功终答，要求继续一轮 LLM 或进入明确 failed；不在 Runtime 中替 LLM 拼命令或选择业务 profile。
- [x] 如果没有可用交集，返回稳定 blocker：`profile_unavailable`。
- [x] 不新增 `admin -> admin.readonly` alias，不在 Node 端兼容裸 `admin`。

验收：

- [x] “帮我看看 Linux 节点 ssh 日志”使用 `linux.exec.run` + `admin.readonly` 或可用只读 profile，不能传裸 `admin`。prompt contract 已覆盖，待真实会话验收。
- [x] “可能需要使用admin”作为用户补充后，下一次调用必须修正为合法 profile。prompt contract 已覆盖，待真实会话验收。
- [x] profile 不可用时错误文本说明可用 profile 列表。Runtime validation 已返回合法 enum / available profiles。

### C02 Replanner 继续/完成边界收紧

状态：基础实现已完成，待真实会话验收
归属：Center Agent Runtime

目标：

- LLM 不再独占任务生命周期判断。
- 工具失败、operation 终态、approval 通过后，先进入 Replanner，再决定继续 LLM、等待、询问用户、完成或失败。
- Replanner 不负责理解 Linux 命令、选择业务 profile 或生成新命令；这类决策由系统提示词、工具 schema 和 LLM 完成。

实施项：

- [x] 在 provider final candidate 之前执行 Replanner completion gate。
- [x] 当 TaskState 存在 repairable blocker 时，禁止 complete。
- [x] 当用户明确要求“自主完成/直到完成/不要问下一步”时，`ask_user` 只能用于缺少用户才知道的信息，不能用于权限不足、profile 错误、工具失败这类可自动修复路径。prompt contract 和 final-candidate gate 已覆盖。
- [x] 对 `invalid_input`、`missing_required_slot`、`unsupported_enum_value`、`permission_denied`、`target_node_mismatch`、`target_node_required` 建立通用 blocker 分类。
- [x] Replanner 输出必须写入 AgentRunEvent，便于审计。
- [x] 系统提示词每次重大能力面变化后必须同步更新，避免 LLM 按旧工具面、旧 profile 名或旧 direct tool 习惯行动。
- [x] Operation 终态自动恢复入口不能绕开 working set。`agent_operation_reporter` 已从 report-only 一步汇报器改为后端 resume loop：使用与普通 Agent turn 相同的 bootstrap tool surface、capability context、YCR build-turn 和 Replanner；`tool_strategy=reuse_working_set` 时 provider 仍只看到 `capability.invoke`。
- [x] Provider final answer 出现 raw tool-call protocol 文本时，不能作为任务完成。Runtime final-candidate gate 会将其视为未执行工具调用协议污染，要求模型使用正式 tool call 或给出干净终答。

验收：

- [x] WireGuard/SSH 日志读取任务遇到 `permission denied` 后自动尝试更高只读 profile。prompt contract 已覆盖，待真实会话验收。
- [x] 工具返回 schema 错误时不直接给用户“建议下一步”，而是修正后继续。prompt contract 和 repairable blocker gate 已覆盖。
- [x] 无可执行路径时才终止为 failed 或 ask_user。Replanner/final-candidate gate 已覆盖，待真实会话验收。
- [x] Operation/Approval 完成后的后端自动恢复 turn 必须继续使用 working set，不能出现 `provider_tools=[]` 或把 tool call 协议文本输出成普通回答。2026-07-10 远端 session `sess_dc5967941a2a4510` 复验通过：operation report turn 中 `bootstrap_tool_count=3`、`provider_tool_count=1`、`provider_tools=["capability.invoke"]`。

### C03 TaskState / Reducer 事实抽取增强

状态：基础实现已完成，待真实会话验收
归属：Center Observation Reducer

目标：

- TaskState 不只记录工具成败，还记录对下一步有用的结构化事实。

实施项：

- [x] `exec.run` 结果抽取：profile、command、exit_code、permission_denied、unsupported_profile/schema_error、可用 profile。
- [x] `exec.run` 结果抽取继续增强：stdout_ref、stderr_tail、file_not_found、raw_ref_id。
- [x] `artifact.read_text` 结果抽取：artifact_id、line_range、matched_lines、truncated、read_ref。
- [x] YCR tool observation typed entities 进入 Center TaskState：`artifact` / `capability` 轻量实体从 YCR shell 写入 AgentRunEvent，Reducer 不再依赖 provider projection 文本，也不把 `capability.invoke` wrapper 误记为 `exec.run`。
- [x] `operation` 终态抽取：operation_id、kind、domain_status、error_code、error_message、artifacts。
- [x] `transfer` 状态抽取：transfer_id、source/target、size/hash、status、failed_side、resumable。
- [x] Reducer 只使用 typed result shape，不解析 provider projection 文本。
- [x] Operation terminal event 会继承 waiting metadata 并清理已被成功操作修复的 repairable blocker；operation report reconcile 会追加 `run.completed` 事件，使 TaskState completion、last decision 和 run.status 一致。

验收：

- [x] Runtime 面板 facts/blockers 能解释为什么继续、等待或失败。
- [ ] 自动汇报和后续 LLM 不需要重新 search/describe 才能知道上一工具的关键事实，需要真实任务验收。代码层已补 YCR typed entities -> TaskState artifacts/working_set，待远端 Console 验证 Runtime 面板 artifacts 不再为 0。
- [x] Operation 完成后的原始 waiting run 不保留过期 repairable blocker。2026-07-10 远端 session `sess_9a012c21bc5740cd` 复验通过：最终 `completion=complete`、`blockers=0`、`last_decision=complete`。

### C04 PlanStep 归属细化

状态：基础实现已完成，待真实会话验收
归属：Agent Plan / Observation Reducer / Console

目标：

- tool call、approval、operation、artifact 都挂到正确 PlanStep。
- 断点续跑和前端 Plan 面板不再只是粗粒度文本。

实施项：

- [x] `capability.invoke` 创建的 approval_wait、job operation 必须回填同一个 PlanStep。
- [x] operation terminal event 必须更新对应 PlanStep，而不是只更新 run-level TaskState。
- [x] artifact 产物必须绑定产生它的 tool call / operation / PlanStep。
- [x] 多个并行或连续 operation 不得全部显示在同一个 step。

验收：

- [x] 前端 Plan 面板能看出哪个 step 在等待 approval、哪个 step 已产生 artifact、哪个 step 失败。
- [ ] 刷新页面后 Plan 状态一致，需要 Console 真实任务验收。

### C05 Console 前端剩余业务状态清理

状态：基础实现已完成，待真实 Console 验收
归属：Console

目标：

- 前端只展示后端事实，不推进业务。

已完成：

- 输入栏上方只显示 approval 请求。
- completed/failed operation 的 Append 提示不再显示在输入栏上方。
- Runtime objective 不再显示 `[object Object]`。
- ApprovalQueueBar 已改为以后端 Runtime TaskState 的 pending approvals 为唯一来源，旧聊天块不再驱动输入栏审批队列。
- Plan objective / step title 使用稳定文本渲染，避免对象直接显示为 `[object Object]`。
- approve / consumed / denied / expired 的处理不再 patch 本地 tool block 状态，只隐藏当前审批条并刷新后端 Runtime/Session 状态。
- operation 状态变化不再 patch 本地 tool block，聊天流后续以 Center Operation / AgentRunEvent / PlanStep 为事实源。
- Chat timeline 渲染 tool call 时使用 AgentRunStep / AgentRunEvent / PlanStep 派生的只读状态覆盖表，优先显示后端事实，不写回本地 transcript。

剩余实施项：

- [x] 审查 `AgentChatPage.tsx` 中 `patchToolCall` / `patchOperation` 的局部状态用途，只保留 UI 乐观显示，不作为业务事实来源。
- [x] Chat timeline 的 waiting/running/failed 展示优先使用 AgentRunEvent / Operation / PlanStep 后端状态。
- [x] Operation context chip 保留为手动引用入口，但不能触发自动推进。
- [x] ApprovalQueueBar 只显示 pending approval；approved/denied/consumed 后必须从输入栏消失。
- [x] `useAgentChat` 不再向页面暴露 `patchToolCall` / `patchOperation`，前端不能通过 hook 本地改写 tool/operation 事实。

验收：

- [ ] approve 后输入栏上方不闪现已处理 approval，需要真实 Console 验收。
- [ ] operation 完成后右侧 Activity 和聊天流状态一致。
- [x] 手动 Append 只添加引用上下文，不重复执行 operation。

### C06 真实任务验收矩阵

状态：执行中
归属：端到端验收

目标：

- 把“基本可用”推进为“可稳定使用”。

验收任务：

- [ ] Windows：按文件名查找文件，必须命中 `windows.everything.find`。
- [ ] Windows：已知目录列一层内容，必须使用 `windows.exec.run`。
- [x] Windows：截图并展示 artifact，只做必要步骤，成功后 complete。2026-07-10 远端 Console session `sess_5b6` 验收通过：6 messages、2 次 `capability.invoke`、无 `capability.groups` / `capability.group.open` / `capability.search`、`Completed (succeeded)`。
- [ ] Artifact：把上一张 screenshot artifact 写到 `winClient` 的 Windows 绝对路径时必须走 `artifact.deploy.preflight` / `artifact.deploy`，不能选择 Linux source。
- [ ] Linux：查 SSH 日志，必须使用 `linux.exec.run`，需要 root 时使用 `admin.readonly`。
- [x] Linux：查文件 hash，必须使用 `linux.exec.run`。2026-07-10 远端 session `sess_9a012c21bc5740cd` 通过：`/etc/hostname` SHA256 为 `a839b15402605681cb77250cb318dcdf34e406f07ad3349472cf0552eb2c4884`，profile 为 `user.readonly`，provider tool surface 全程为 `["capability.invoke"]`。
- [ ] Artifact：读取文本 artifact 必须使用 `artifact.read_text`，不能全量展开 raw ref。
- [ ] Transfer：Windows -> Linux 传输仍走 `transfer.preflight` / `transfer.create` / yq-croc runtime，不走 `exec.run`。
- [x] 任意 `exec.run`：必须触发通用 approval。2026-07-10 远端 session `sess_9a012c21bc5740cd` 中 `linux.exec.run` 生成 `apv_f577079e9afa49e6` 并通过 `approve-and-run` 执行。
- [ ] Approval 通过后：后端自动推进或自动汇报，不要求用户手动 Append。
- [x] Approval/Operation 自动恢复：验证恢复 turn 的 audit 中 `agent.ycr.build_turn.completed.tool_surface.provider_tool_count` 非 0，且 `tool_strategy=reuse_working_set` 时 provider tools 为 `["capability.invoke"]`。2026-07-10 远端 session `sess_dc5967941a2a4510` 通过。

记录要求：

- [ ] 每个场景保存 session_id。已记录：Windows 截图展示 `sess_5b6`；Operation resume `sess_dc5967941a2a4510`；Linux hash `sess_9a012c21bc5740cd`。
- [ ] 每个失败场景写明失败层：LLM 决策、Replanner、YCR、Center runtime、Node runtime、前端展示。
- [ ] 验收通过后更新本文件状态。

### C07 耗时归因硬化

状态：基础实现已完成，待真实 session 读日志验收
归属：session audit / Agent Runtime / YCR

目标：

- 能明确回答一次 session 慢在哪里。

实施项：

- [x] provider span 拆分为 request_start、first_delta、completed。
- [x] YCR build-turn 拆分为 message_projection、history_compaction、session_state_load、state_projection、capability_context_projection、token_accounting，并写入 `context_estimate.timing_ms`。
- [x] `/agent/sessions/{session_id}/runtime-state` 返回 session audit timing summary，包括 category totals、top segments、YCR build-turn timing、provider planned tool call counts。
- [x] DB lock / timeout 在 session audit 中记录独立 `agent.db.error` 事件，并在 Runtime timing 面板显示最近错误。
- [x] operation wait 从 session audit 的 `operation.created` 到终态事件派生等待段，并按 operation kind 区分 approval wait / job / transfer / maintenance。
- [x] Agent operation report LLM 记录 `agent.operation_report.started/completed` 和 `elapsed_ms`。
- [x] Console Runtime 面板显示最近一次 run 的主要耗时分解。

验收：

- [x] 对任意最新 session 能列出前三个耗时段。
- [x] 首次消息慢、工具链慢、operation 等待慢、operation report LLM 可以从 `context_load` / `ycr` / `provider` / `tools` / `operation_wait_segments` / `operation_report` 中区分。
- [ ] approval wait、Node job wait、transfer wait 在真实 Console 会话中完成一次人工验收。已通过 API/远端会话验证 approval wait + Node job wait：`sess_9a012c21bc5740cd`；仍需 Console 人工刷新展示验收和 transfer wait。

### C08 Meta tool 默认输出继续审计

状态：基础实现已完成，待真实任务验收
归属：Center meta tools / YCR behavior

目标：

- meta tool 默认输出始终是 provider decision view，不是管理详情 dump。

实施项：

- [x] 复查 `node.status` 默认输出。默认 `summary` 只返回节点基本状态和 capability name preview，detail 需显式请求。
- [x] 复查 `operation.status` 默认输出。默认 `summary` 使用 Operation summary，不返回完整 domain dump。
- [x] 复查 `transfer.status` 默认输出。默认 `summary` 返回传输决策字段和 job status summary，不返回完整 job/domain raw。
- [x] 复查 `artifact.list` / `artifact.get` / `artifact.present` 默认输出。默认 `summary` 不含 blob metadata/raw content。
- [x] 复查 `capability.describe` 默认 sections。默认 `invoke_ready` 不再隐式包含完整 schema/examples/diagnostics；显式 `projection=detail/schema/diagnostics` 或 sections 才展开。
- [x] 复查 `context.expand` 对根路径和大对象的行为。大 root 返回 `path_required`、schema preview 和 available paths；具体 path 才展开。

验收：

- [ ] 普通 Agent 任务中 meta tool raw result 不随 capability 数、artifact metadata、domain object 无界膨胀。
- [ ] 需要 detail/diagnostics 时必须显式请求。
- [ ] 失败错误必须包含稳定 code/message。

### C09 Prompt Policy 与 Decision Trace 收敛

状态：基础实现已完成，待真实 session 验收
归属：Agent prompt policy / Provider adapter / AgentRunEvent / Console

目标：

- 系统提示词必须与当前能力面一致，不能让 LLM 继续按旧 direct tool、旧 profile、旧 artifact 展示方式行动。
- 不展示或依赖模型原始 CoT；改为展示可审计、可压缩、可验证的 decision trace。
- Prompt policy 只描述行为规则和工具使用合同，不承担运行时状态机职责。

当前已完成：

- [x] 系统提示词已补充 `exec.run` profile 规则：禁止裸 `admin`，只读诊断权限不足且 `admin.readonly` 可用时继续尝试 `admin.readonly`。
- [x] 系统提示词已补充目标 Node 绑定规则：用户指定 Node、平台或 node-local path 时，search/describe/invoke 必须携带匹配 `node_id`，多 source capability 只能选择目标 Node 的 source。
- [x] 系统提示词已补充 artifact placement 规则：用户要把 Center artifact 写入 Node 文件系统时使用 `artifact.deploy.preflight` / `artifact.deploy`，不直接调用 node-local `artifact.download_file` source。

实施项：

- [x] 重写 `render_agent_system_prompt()` 的结构，把规则分为固定章节：routing、tool discovery、exec profile、artifact、operation/approval、YCR refs、completion。
- [x] 每条规则必须对应当前真实工具面：`capability.groups` / `capability.group.open` / `capability.invoke`，不能再暗示 direct tool 调用。
- [x] 增加任务完成规则：当用户要求“自主完成/直到完成”时，除缺少用户独有信息、approval pending、所有可用路径失败外，不得把“建议下一步”作为最终答复。
- [x] 增加 operation 规则：operation 完成由后端自动汇报；Append 只是手动引用，不是继续任务的必要步骤。
- [x] 增加 decision trace 事件：provider 每轮输出后，Runtime 记录一条短结构化 `decision.summary`，字段包含 `goal`、`chosen_action`、`why`、`next_state`、`blocked_by`。该摘要由 Runtime/LLM 可见输出和工具事实生成，不保存隐藏 CoT。
- [x] Console Runtime/Plan 面板显示 decision trace 摘要，不显示模型原始 hidden thought。
- [x] Provider adapter 如果未来收到 `reasoning_content` 或类似字段，默认不向普通聊天正文展示；如需诊断，只能作为受控 debug telemetry，并受配置开关限制。

验收：

- [ ] 系统提示词中不存在与当前工具面冲突的旧规则。
- [ ] 最新 session 能看到“为何继续/为何等待/为何失败”的短 decision trace。
- [ ] 不需要暴露原始 CoT 也能审计 Agent 的关键决策。
- [ ] 任务失败时，final answer 和 Runtime decision trace 指向同一错误事实。

### C10 YCR / RAG 收益与边界验收

状态：执行中
归属：YCR / Agent Runtime / Console / session audit

目标：

- 验证 YCR 只做上下文，不重新承担流程决策。
- 验证 Tool candidate loading 和 Result RAG 在真实复杂任务中有正收益，不制造额外绕路。

实施项：

- [x] 审查 YCR service 入口和 `build-turn` 输出，确认不输出 `continue`、`wait`、`complete`、`fail` 等生命周期决策。已移除 `tool_strategy.wait_approval` / `tool_strategy.wait_operation`，pending 状态只保留在 TaskState，由 Runtime/Replanner 决策。
- [x] 在 session audit 中记录每轮 `capability.groups`、`capability.group.open`、`capability.search`、`capability.describe` 次数。实现为 `agent.tool_calls.planned.counts`，记录每轮 provider 计划调用的所有工具名计数。
- [x] 对比真实任务中 Tool candidate loading 命中情况：空 working set 时是否由 objective bootstrap 生成候选并直接进入 invoke，后续轮次是否使用 session/task working set，还是仍重复 group/open/search。2026-07-10 远端 Console 验收发现 `sess_5b6` 可直接使用 working set 候选，但后续 `sess_89c` 在截图已生成后仍调用 `capability.groups` / `capability.group.open` 寻找 `artifact.present`，说明“提示优先候选”不稳定。已改为通用工具面控制：`tool_strategy=reuse_working_set` 且存在候选时，本轮 provider 只暴露 `capability.invoke`；空 working set 或候选不足时才保留 `capability.groups` / `capability.group.open` 渐进发现。
- [x] 对 operation 自动恢复入口做一致性修复。2026-07-10 远端 session `sess_092d39df07474c80` 暴露普通 turn 已使用 working set，但 `agent.operation_reporter` 入口传入 `available_functions=[]`，导致 YCR 候选无法变成 provider 可调用工具面，provider 只能输出 raw tool-call protocol 文本。已修复为与普通 turn 共用 bootstrap functions + capability context，并把 report prompt 改成 resume prompt。
- [x] 语义 Tool RAG 返回同一 capability 的多个 sources 时，按 query 命中的 `node_id`、`platform_os`、`platform_arch`、`registered_name`、`plugin_id` 排序，避免正确 capability 命中但错误 Node source 排前。
- [x] 对 Result RAG 记录索引状态：`context.search` 返回 `index_status` 和 `result_rag.status`，覆盖 `hit` / `miss` / `not_indexed` / `no_refs`。
- [ ] 对 Result RAG 真实使用效果做 session 验收：命中内容是否被 LLM 使用，miss 后是否转向确定性读取。
- [ ] 当 Result RAG 未命中或 ref 未索引时，Agent 必须能通过 `context.inspect` / `context.tail` / `artifact.read_text` 等确定性工具继续，不得假装 RAG 成功。
- [x] 前端 YCR trace 显示 Result RAG `hit` / `miss` / `not_indexed` / `no_refs` 和 chunk 索引计数。
- [x] 前端 YCR/Runtime 面板显示：candidate count、working set 数量、search/group/open/describe/invoke 次数、Result RAG 状态。

验收：

- [x] Windows 截图任务不需要重复 search/group/open。`sess_5b6`：`capability.invoke:2`，`capability.groups/group.open/search:0`；`sess_89c` 暴露 provider 仍可绕回 group/open。工具面收窄修复后，2026-07-10 远端 Console `sess_289` 验收通过：每轮 `provider_tool_count=1`，实际 provider tools 仅 `capability.invoke`，计划工具调用为 `capability.invoke:2`，无 `capability.groups` / `capability.group.open` / `capability.search`，`ARTIFACTS=1`。
- [x] Operation resume 入口复验：approval 通过后自动恢复 turn 的 provider tool surface 必须与普通 turn 一致，不能再出现 `provider_tool_count=0`。2026-07-10 远端 session `sess_dc5967941a2a4510` 通过。
- [ ] Linux 日志读取任务在定位 `exec.run` 后，不重复打开无关工具组。
- [x] Linux hash 任务在定位 `exec.run` 后不重复打开无关工具组。`sess_9a012c21bc5740cd` 中计划工具调用为 `capability.invoke`、`capability.invoke`、report turn 无工具调用；无 `capability.groups` / `capability.group.open` / `capability.search`。
- [x] Result RAG 未命中或未索引时状态可见。
- [ ] Result RAG 未命中不影响 deterministic read/tail 路径，需要真实会话验收。
- [x] YCR 代码中没有生命周期决策分支。

### C11 代码清理与架构干净门禁

状态：待执行
归属：Center / Agent Runtime / YCR / Console / Nodes / 文档

目标：

- 完成本文后，代码路径必须收敛、职责边界清晰、无临时兼容、无静默 fallback、无前端业务推进。

实施项：

- [x] 清理 `AgentChatPage.tsx` / `useAgentChat` 中前端业务 patch 出口；剩余 session id、operation context chip、approval dismissed set 均限定为 UI-only 展示/输入辅助。
- [x] 清理旧 direct meta tool 暴露路径；provider 默认工具面只保留 `capability.groups` / `capability.group.open` / `capability.invoke`，并由 `test_agent_tool_contract` / `test_agent_console_ux` 覆盖。
- [x] 清理过时 prompt 规则、测试 fixture、mock/fallback 文案；当前系统提示词只描述 bootstrap 三件套工具面，测试静态函数文案限定为 isolated provider tests。
- [x] 清理 Node 端被删除 primitive capability 的残留 manifest、测试、README 旧描述。
  - [x] Windows Node 实际 capability 文件只剩 `windows.exec.run`、`windows.everything.find`、screen capture、artifact、transfer/yq-croc 与 transfer local stat；旧 `windows.file.*`、services/process/eventlog/display/installed-apps 等调用型 primitive 不存在。
  - [x] Linux Node production registry 只注册 `linux.exec.run`、artifact、transfer/yq-croc、transfer local stat；旧 `linux.filesystem.*`、system/network/service/log/user/package/dmesg 等调用型 primitive 不存在。
  - [x] `windows.disk.usage`、`windows.cpu.usage`、`windows.memory.usage` 仅为 Signal 上报，不是 Agent 可调用 capability；保留该事实以免后续误删状态信号。
  - [x] 测试中的 `system.info`、`system.metrics.snapshot` 等旧名属于 registry canonical、approval、provider adapter 和 isolated fake fixtures 的结构测试，不代表当前 Node 生产 capability 面。
- [x] 检查 Center/YCR 是否存在 string fallback、mock embedder、query/capability 特判、profile alias、静默 raw-output fallback。
  - [x] 删除 Windows metrics `_fallback_cpu_percent` 静默伪造；psutil 不可用时省略 CPU/memory 信号并输出 `metrics_unavailable` 事实。
  - [x] 删除 Windows desktop UI 的 pywebview 缺失 mock bridge；无真实 desktop bridge 时返回 `BRIDGE_UNAVAILABLE`，不再展示假服务状态。
  - [x] 当前 `alias` 命中分为三类合法语义：FastAPI/Pydantic 参数别名、CapabilityDefinition aliases、Windows 用户目录路径展开；均不是 exec profile alias 或旧 capability shim。
  - [x] 当前 `mock` 命中分为两类合法语义：单元测试的 fake/mock server、Windows desktop-ui 构建产物依赖；生产 UI mock bridge 已删除，YCR 不存在 mock embedder。
  - [x] 当前 `fallback` 命中分为三类合法语义：SPA 路由回退、UI 文案默认值、文档质量门禁描述；生产执行路径静默 fallback 已删除或显式错误化。
  - [x] `AgentChatPage` 内部 transcript reducer 的 `patchToolCall` 只合并实时流式事件到同一张工具卡；`useAgentChat` 不再向页面暴露 patch API，不能作为前端业务推进入口。
- [x] 对新增 Replanner/Reducer/TaskState 逻辑补最小单元测试；对真实任务补少量端到端验收记录，不追求大规模测试。
- [x] Console YCR trace 显示每轮实际 provider tool count；Prompt Context 面板保留 bootstrap 合同，避免把 bootstrap 三工具误读为 working set 生效后的实际工具面。
- [ ] 每个 active todo 必须更新状态；已完成或被本文吸收的文档必须归档或标注 owner，不能保留互相冲突的执行路线。

验收：

- [x] `rg "fallback|mock|alias|admin ->|NotImplemented|TODO" src nodes console-frontend docs/todos` 中与当前主线冲突的项清零或有明确保留理由。
- [x] Provider bootstrap 工具面、working-set 后实际工具面、YCR 职责、Replanner 职责、Console 职责在文档和代码中一致。当前文档明确：bootstrap 是 `capability.groups` / `capability.group.open` / `capability.invoke`；`reuse_working_set` 且候选存在时实际 provider 工具面收窄为 `capability.invoke`。
- [ ] 刷新前端、approval、operation completion、manual Append、resume 均不依赖前端业务推进。
- [ ] 文档入口只指向当前活跃待办，不出现“已完成但其实未验收”的模糊描述。

## 4. 不做项

本收口阶段不做以下事项：

1. 不引入 Workflow Capability。
2. 不引入 task-level approval / ExecutionIntent。
3. 不引入 WASI、沙箱、文件系统事务预执行。
4. 不给具体 query、具体 capability、具体中文词写硬编码排序补丁。
5. 不在 Node 端兼容裸 `admin` profile。
6. 不让前端承担自动推进 Agent 的职责。

## 5. 推荐执行顺序

1. C09：先修系统提示词与当前实现一致性，因为最新 profile 误用首先是 prompt/tool contract 落后。
2. C01：补 `exec.run` invoke-ready 合同和 Center validation 错误事实，确保 LLM 能看到正确 profile。
3. C02：接入 Replanner completion gate，防止工具失败后直接终答。
4. C03-C04：增强 TaskState 和 PlanStep，让 Runtime 有足够事实继续推进和断点恢复。
5. C05：清理 Console 业务状态残留。
6. C10：验证 YCR/RAG 只做上下文与候选加载，衡量真实收益。
7. C06：跑真实任务验收矩阵。
8. C07-C08：补耗时归因和 meta tool 输出审计。
9. C11：做最终代码清理与文档收口门禁。

## 6. 完成定义

本文完成时必须同时满足：

1. 最新暴露的 `profile is required` / `unsupported exec profile: admin` 类问题不再复现。
2. 复杂任务遇到可修复错误时自动继续，不把“建议下一步”当成成功终答。
3. Approval/Operation 完成后由后端状态驱动继续或汇报，前端只展示。
4. PlanStep 能准确反映 tool、approval、operation、artifact 的状态。
5. 真实任务验收矩阵全部通过。
6. 最新 session 的主要耗时可以通过 audit/前端面板解释。
7. 系统提示词与当前工具面一致，且有 decision trace 解释关键动作，不依赖原始 CoT。
8. YCR/RAG 的收益和边界通过真实 session 验收，YCR 不承担任务流程决策。
9. 文档状态与代码实现一致，没有“已完成但仍未验收”的模糊表述。
10. 代码清理完成，无旧 fallback、旧 direct tool path、profile alias、前端业务推进和过时文档残留。
