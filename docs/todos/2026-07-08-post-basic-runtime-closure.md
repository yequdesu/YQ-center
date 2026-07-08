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

## 3. 可执行待办

### C01 `exec.run` profile contract 闭环

状态：待实现  
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

- [ ] “帮我看看 Linux 节点 ssh 日志”使用 `linux.exec.run` + `admin.readonly` 或可用只读 profile，不能传裸 `admin`。
- [ ] “可能需要使用admin”作为用户补充后，下一次调用必须修正为合法 profile。
- [ ] profile 不可用时错误文本说明可用 profile 列表。

### C02 Replanner 继续/完成边界收紧

状态：待实现  
归属：Center Agent Runtime  

目标：

- LLM 不再独占任务生命周期判断。
- 工具失败、operation 终态、approval 通过后，先进入 Replanner，再决定继续 LLM、等待、询问用户、完成或失败。
- Replanner 不负责理解 Linux 命令、选择业务 profile 或生成新命令；这类决策由系统提示词、工具 schema 和 LLM 完成。

实施项：

- [x] 在 provider final candidate 之前执行 Replanner completion gate。
- [x] 当 TaskState 存在 repairable blocker 时，禁止 complete。
- [ ] 当用户明确要求“自主完成/直到完成/不要问下一步”时，`ask_user` 只能用于缺少用户才知道的信息，不能用于权限不足、profile 错误、工具失败这类可自动修复路径。
- [x] 对 `invalid_input`、`missing_required_slot`、`unsupported_enum_value`、`permission_denied` 建立通用 blocker 分类。
- [x] Replanner 输出必须写入 AgentRunEvent，便于审计。
- [x] 系统提示词每次重大能力面变化后必须同步更新，避免 LLM 按旧工具面、旧 profile 名或旧 direct tool 习惯行动。

验收：

- [ ] WireGuard/SSH 日志读取任务遇到 `permission denied` 后自动尝试更高只读 profile。
- [ ] 工具返回 schema 错误时不直接给用户“建议下一步”，而是修正后继续。
- [ ] 无可执行路径时才终止为 failed 或 ask_user。

### C03 TaskState / Reducer 事实抽取增强

状态：待实现  
归属：Center Observation Reducer  

目标：

- TaskState 不只记录工具成败，还记录对下一步有用的结构化事实。

实施项：

- [x] `exec.run` 结果抽取：profile、command、exit_code、permission_denied、unsupported_profile/schema_error、可用 profile。
- [ ] `exec.run` 结果抽取继续增强：stdout_ref、stderr_tail、file_not_found。
- [x] `artifact.read_text` 结果抽取：artifact_id、line_range、matched_lines、truncated、read_ref。
- [x] `operation` 终态抽取：operation_id、kind、domain_status、error_code、error_message、artifacts。
- [x] `transfer` 状态抽取：transfer_id、source/target、size/hash、status、failed_side、resumable。
- [ ] Reducer 只使用 typed result shape，不解析 provider projection 文本。

验收：

- [ ] Runtime 面板 facts/blockers 能解释为什么继续、等待或失败。
- [ ] 自动汇报和后续 LLM 不需要重新 search/describe 才能知道上一工具的关键事实。

### C04 PlanStep 归属细化

状态：待实现  
归属：Agent Plan / Observation Reducer / Console  

目标：

- tool call、approval、operation、artifact 都挂到正确 PlanStep。
- 断点续跑和前端 Plan 面板不再只是粗粒度文本。

实施项：

- [ ] `capability.invoke` 创建的 approval_wait、job operation 必须回填同一个 PlanStep。
- [ ] operation terminal event 必须更新对应 PlanStep，而不是只更新 run-level TaskState。
- [ ] artifact 产物必须绑定产生它的 tool call / operation / PlanStep。
- [ ] 多个并行或连续 operation 不得全部显示在同一个 step。

验收：

- [ ] 前端 Plan 面板能看出哪个 step 在等待 approval、哪个 step 已产生 artifact、哪个 step 失败。
- [ ] 刷新页面后 Plan 状态一致。

### C05 Console 前端剩余业务状态清理

状态：待实现  
归属：Console  

目标：

- 前端只展示后端事实，不推进业务。

已完成：

- 输入栏上方只显示 approval 请求。
- completed/failed operation 的 Append 提示不再显示在输入栏上方。
- Runtime objective 不再显示 `[object Object]`。

剩余实施项：

- [ ] 审查 `AgentChatPage.tsx` 中 `patchToolCall` / `patchOperation` 的局部状态用途，只保留 UI 乐观显示，不作为业务事实来源。
- [ ] Chat timeline 的 waiting/running/failed 展示优先使用 AgentRunEvent / Operation / PlanStep 后端状态。
- [ ] Operation context chip 保留为手动引用入口，但不能触发自动推进。
- [ ] ApprovalQueueBar 只显示 pending approval；approved/denied/consumed 后必须从输入栏消失。

验收：

- [ ] approve 后输入栏上方不闪现已处理 approval。
- [ ] operation 完成后右侧 Activity 和聊天流状态一致。
- [ ] 手动 Append 只添加引用上下文，不重复执行 operation。

### C06 真实任务验收矩阵

状态：待执行  
归属：端到端验收  

目标：

- 把“基本可用”推进为“可稳定使用”。

验收任务：

- [ ] Windows：按文件名查找文件，必须命中 `windows.everything.find`。
- [ ] Windows：已知目录列一层内容，必须使用 `windows.exec.run`。
- [ ] Windows：截图并展示 artifact，只做必要步骤，成功后 complete。
- [ ] Linux：查 SSH 日志，必须使用 `linux.exec.run`，需要 root 时使用 `admin.readonly`。
- [ ] Linux：查文件 hash，必须使用 `linux.exec.run`。
- [ ] Artifact：读取文本 artifact 必须使用 `artifact.read_text`，不能全量展开 raw ref。
- [ ] Transfer：Windows -> Linux 传输仍走 `transfer.preflight` / `transfer.create` / yq-croc runtime，不走 `exec.run`。
- [ ] 任意 `exec.run`：必须触发通用 approval。
- [ ] Approval 通过后：后端自动推进或自动汇报，不要求用户手动 Append。

记录要求：

- [ ] 每个场景保存 session_id。
- [ ] 每个失败场景写明失败层：LLM 决策、Replanner、YCR、Center runtime、Node runtime、前端展示。
- [ ] 验收通过后更新本文件状态。

### C07 耗时归因硬化

状态：待实现  
归属：session audit / Agent Runtime / YCR  

目标：

- 能明确回答一次 session 慢在哪里。

实施项：

- [x] provider span 拆分为 request_start、first_delta、completed。
- [ ] YCR build-turn 拆分为 shell load、projection、session state load、token accounting。
- [ ] DB lock / retry / timeout 在 session audit 中记录独立事件。
- [ ] operation wait 区分用户审批等待、Node job 运行、Center projection、Agent operation report LLM。
- [ ] Console Runtime/YCR 面板显示最近一次 run 的主要耗时分解。

验收：

- [ ] 对任意最新 session 能列出前三个耗时段。
- [ ] 首次消息慢、工具链慢、approval 等待慢可以被区分。

### C08 Meta tool 默认输出继续审计

状态：待执行  
归属：Center meta tools / YCR behavior  

目标：

- meta tool 默认输出始终是 provider decision view，不是管理详情 dump。

实施项：

- [ ] 复查 `node.status` 默认输出。
- [ ] 复查 `operation.status` 默认输出。
- [ ] 复查 `transfer.status` 默认输出。
- [ ] 复查 `artifact.list` / `artifact.get` / `artifact.present` 默认输出。
- [ ] 复查 `capability.describe` 默认 sections。
- [ ] 复查 `context.expand` 对根路径和大对象的行为。

验收：

- [ ] 普通 Agent 任务中 meta tool raw result 不随 capability 数、artifact metadata、domain object 无界膨胀。
- [ ] 需要 detail/diagnostics 时必须显式请求。
- [ ] 失败错误必须包含稳定 code/message。

### C09 Prompt Policy 与 Decision Trace 收敛

状态：待实现  
归属：Agent prompt policy / Provider adapter / AgentRunEvent / Console  

目标：

- 系统提示词必须与当前能力面一致，不能让 LLM 继续按旧 direct tool、旧 profile、旧 artifact 展示方式行动。
- 不展示或依赖模型原始 CoT；改为展示可审计、可压缩、可验证的 decision trace。
- Prompt policy 只描述行为规则和工具使用合同，不承担运行时状态机职责。

当前已完成：

- [x] 系统提示词已补充 `exec.run` profile 规则：禁止裸 `admin`，只读诊断权限不足且 `admin.readonly` 可用时继续尝试 `admin.readonly`。

实施项：

- [x] 重写 `render_agent_system_prompt()` 的结构，把规则分为固定章节：routing、tool discovery、exec profile、artifact、operation/approval、YCR refs、completion。
- [x] 每条规则必须对应当前真实工具面：`capability.groups` / `capability.group.open` / `capability.invoke`，不能再暗示 direct tool 调用。
- [x] 增加任务完成规则：当用户要求“自主完成/直到完成”时，除缺少用户独有信息、approval pending、所有可用路径失败外，不得把“建议下一步”作为最终答复。
- [x] 增加 operation 规则：operation 完成由后端自动汇报；Append 只是手动引用，不是继续任务的必要步骤。
- [x] 增加 decision trace 事件：provider 每轮输出后，Runtime 记录一条短结构化 `decision.summary`，字段包含 `goal`、`chosen_action`、`why`、`next_state`、`blocked_by`。该摘要由 Runtime/LLM 可见输出和工具事实生成，不保存隐藏 CoT。
- [x] Console Runtime/Plan 面板显示 decision trace 摘要，不显示模型原始 hidden thought。
- [ ] Provider adapter 如果未来收到 `reasoning_content` 或类似字段，默认不向普通聊天正文展示；如需诊断，只能作为受控 debug telemetry，并受配置开关限制。

验收：

- [ ] 系统提示词中不存在与当前工具面冲突的旧规则。
- [ ] 最新 session 能看到“为何继续/为何等待/为何失败”的短 decision trace。
- [ ] 不需要暴露原始 CoT 也能审计 Agent 的关键决策。
- [ ] 任务失败时，final answer 和 Runtime decision trace 指向同一错误事实。

### C10 YCR / RAG 收益与边界验收

状态：待执行  
归属：YCR / Agent Runtime / Console / session audit  

目标：

- 验证 YCR 只做上下文，不重新承担流程决策。
- 验证 Tool candidate loading 和 Result RAG 在真实复杂任务中有正收益，不制造额外绕路。

实施项：

- [ ] 审查 YCR service 入口和 `build-turn` 输出，确认不输出 `continue`、`wait`、`complete`、`fail` 等生命周期决策。
- [ ] 在 session audit 中记录每轮 `capability.groups`、`capability.group.open`、`capability.search`、`capability.describe` 次数。
- [ ] 对比真实任务中 Tool candidate loading 命中情况：是否使用 session working set 直接进入 invoke，还是仍重复 group/open/search。
- [ ] 对 Result RAG 记录索引状态：ref 是否已索引、搜索是否命中、命中内容是否被 LLM 使用。
- [ ] 当 Result RAG 未命中或 ref 未索引时，Agent 必须能通过 `context.inspect` / `context.tail` / `artifact.read_text` 等确定性工具继续，不得假装 RAG 成功。
- [ ] 前端 YCR 面板显示：candidate count、working set source、search/group/open 次数、Result RAG index ready/miss/hit。

验收：

- [ ] Windows 截图任务不需要重复 search/group/open。
- [ ] Linux 日志读取任务在定位 `exec.run` 后，不重复打开无关工具组。
- [ ] Result RAG 未命中时错误或 miss 状态可见，且不影响 deterministic read/tail 路径。
- [ ] YCR 代码中没有生命周期决策分支。

### C11 代码清理与架构干净门禁

状态：待执行  
归属：Center / Agent Runtime / YCR / Console / Nodes / 文档  

目标：

- 完成本文后，代码路径必须收敛、职责边界清晰、无临时兼容、无静默 fallback、无前端业务推进。

实施项：

- [ ] 清理 `AgentChatPage.tsx` 中已不再承担业务职责的状态、函数和本地 storage。保留 UI-only 状态必须有明确注释或命名。
- [ ] 清理旧 direct meta tool 暴露路径；provider 默认工具面只保留当前 bootstrap 三件套。
- [ ] 清理过时 prompt 规则、测试 fixture、mock/fallback 文案。
- [ ] 清理 Node 端被删除 primitive capability 的残留 manifest、测试、README 旧描述。
- [ ] 检查 Center/YCR 是否存在 string fallback、mock embedder、query/capability 特判、profile alias、静默 raw-output fallback。
- [ ] 对新增 Replanner/Reducer/TaskState 逻辑补最小单元测试；对真实任务补少量端到端验收记录，不追求大规模测试。
- [ ] 每个 active todo 必须更新状态；已完成或被本文吸收的文档必须归档或标注 owner，不能保留互相冲突的执行路线。

验收：

- [ ] `rg "fallback|mock|alias|admin ->|NotImplemented|TODO" src nodes console-frontend docs/todos` 中与当前主线冲突的项清零或有明确保留理由。
- [ ] Provider 默认工具面、YCR 职责、Replanner 职责、Console 职责在文档和代码中一致。
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
