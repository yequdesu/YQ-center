# Agent Runtime / Plan / Operation / YCR State 架构收敛待办

状态：active todo  
日期：2026-07-07  
范围：Center / Agent Runtime / YCR / Operation / Registry / 文档系统  

本文基于当前代码库和最近真实 Agent 会话审计结果，负责把 Agent Runtime、Plan、Operation Event Queue、YCR Session State、Tool RAG Candidate Loader、渐进式 Center meta tool 分组和 Registry/YCR Snapshot Cache 收敛成统一主线。

本文是当前阶段架构收敛的权威待办。它吸收 2026-07-07 架构状态评估的发现，并将评估结论扩展为可执行实施计划。

## 0. 职责边界

本文负责：

- Agent Run / Turn 生命周期不变量；
- 通用 Agent Plan 中间层；
- Operation terminal event -> Agent Runtime event queue；
- YCR Session State；
- Tool RAG 重新定位为 candidate loader；
- Center meta tool 分组与渐进式展开；
- Task Runtime / Replanner / 完成判定；
- Registry/YCR Snapshot Cache；
- 前端状态绑定到 Plan / Turn / Operation；
- session audit span 化；
- 与上述主线直接相关的文档一致性。

本文不负责：

- Provider registry、模型发现、probe 和 provider/model 前端选择，归属 `2026-06-29-agent-provider-system.md`；
- 具体 Win/Linux Node 工具质量审查和工具重构；
- yq-croc 传输实现细节、Linux receive 误判复验和 transfer fact 继承，归属 `2026-07-06-ycr-agent-routing-and-transfer-corrections.md`；
- task-level approval 和 ExecutionIntent；
- Workflow Capability。当前明确不引入高阶业务 workflow capability。

## 0.1 当前实现核对

| 项 | 当前状态 | 代码/文档事实 | 剩余动作 |
|---|---|---|---|
| AgentRun / AgentRunStep | 已完成基础实现 | `src/yequ/models/agent_run.py` 已有持久 run/step；`agent_stream.py` 已写入 `building_context`、`model_running`、`validating_tools` 中间状态；provider 成功/失败、tool observation、final 都会写入 AgentRunStep；AgentRun 终态已禁止回退。 | 后续只做更细 first-token/DB span 归因，不再阻塞主链路。 |
| AgentTurn / AgentTurnEvent | 已完成基础实现 | `src/yequ/models/agent_turn.py` 和事件记录已存在；`stream.close` 已把未终止半状态落为失败；internal turn 创建前会关闭同 session 旧 internal 半状态；等待态不会被后续 observing/open 事件覆盖。 | 后续只做观测字段补强。 |
| Plan | 已完成基础实现 | 新增通用 `AgentPlan` / `AgentPlanStep`，与 MaintenancePlan 分离；`/agent/invoke/stream` 每次 run 创建 Plan 并同步成功、失败、等待 operation/approval 状态；主 Plan 查询会过滤 internal operation report plan。 | 后续把更细的 tool step 映射到 PlanStep。 |
| Operation Event | 已完成基础实现 | `OperationEvent.dispatch_status` 只负责 outbox/MQ 派发；Agent 自动汇报已新增独立 `agent_operation_notifications` 队列，避免混用 outbox 状态。 | 继续把自动 report 的 UI 展示完全绑定到后端队列状态。 |
| YCR Session State | 已完成基础实现 | 新增 `YcrSessionState`，由 tool observation 和 Operation event 写入 capability/artifact/operation/node working set；artifact 输出会维护 `focus.last_artifact`，`artifact.present` 会维护 `focus.current_artifact`；build-turn 注入 provider context。 | 后续补 task working set 和更完整的前端状态展示。 |
| Tool RAG | 主链路具备但定位需调整 | Tool RAG 已有 ready index、BGE-M3、reranker、多层 cache。 | 从“智能工具选择”降级为 candidate loader；候选不足时仍允许 `capability.search` 扩检索。 |
| Snapshot Cache | 已完成基础实现 | RAG cache 已有；`build_capability_context()` 已新增版本化 `YcrCapabilityContextSnapshot`，普通对话命中快照时不再 joinedload 全量节点关系。 | 后续继续把 registry snapshot 统计暴露到前端状态。 |
| 前端状态绑定 | 已完成基础实现 | OperationCard、YCR panel、tool card 均存在；YCR panel 已显示 snapshot/candidate/session-state 状态；Operation 自动汇报已接后端 queue；`/agent/sessions/{session_id}/runtime-state` 返回最新非 internal AgentRun + AgentPlan；右侧 Runtime/Plan 面板读取后端投影。 | 后续只做交互细节、视觉密度和更多事件时间线展示优化。 |
| Audit | 已完成基础实现 | session JSONL 已记录 request/context/YCR/provider/tool/operation 关键 span 和 elapsed_ms。 | 后续按真实慢点继续补更细 DB/LLM first-token span。 |

## 0.1.1 当前必须系统性解决的问题

以下问题来自真实 Console Agent 会话，不再分别归因于单个工具、单个 UI 组件或单个 YCR
检索结果。它们共同说明当前 ReAct Loop 的生命周期控制仍由 LLM 自行判断，Agent Runtime
尚未完全接管任务推进：

| 问题 | 表象 | 根因 | 归属待办 |
|---|---|---|---|
| Agent 绕路 | 已有明显下一步时仍绕到 search/describe/context.expand。 | 任务事实、已发现能力和当前目标没有被 Runtime 收敛为强状态。 | T21、T22、T23、T28 |
| 重复搜索工具 | 同一 session 内反复搜索相同能力或重新打开无关分组。 | Tool discovery 仍偏“每轮重新发现”，缺 task working set 与 PlanStep 绑定。 | T21、T25、T28 |
| Operation 完成后不自动继续 | 长任务完成后需要用户 Append/Resume，或自动汇报与主任务脱节。 | Operation 终态没有作为 AgentRunEvent 驱动 TaskState 和 Replanner。 | T21、T22、T24、T26 |
| Approval 后状态错乱 | 批准后前端闪现、Plan 等待态不稳定、Agent 不继续。 | Approval 是外部状态变化，但未统一进入运行时事件源和后端恢复路径。 | T21、T22、T24、T25、T26 |
| 前端闪现/卡 waiting | UI 临时推断 waiting 状态，后端状态与事件终态不同步。 | 前端仍承担了部分业务拼接职责，缺统一事件源和 PlanStep 状态。 | T21、T25、T26 |
| 断点续跑粒度粗 | Resume 只能按 run/operation 粗粒度恢复，无法精确回到任务步骤。 | PlanStep 不是完整运行时结构，tool/operation/approval/artifact 未全部挂载。 | T22、T25、T26 |
| 长任务无法稳定收敛 | 任务没做完就问用户，或重复执行已完成动作。 | 缺 completion criteria、blocker 分类和 deterministic Replanner。 | T22、T24、T27 |

因此，本阶段的目标不是给每个现象打补丁，而是把 ReAct Loop 改成：

```text
AgentRunEvent append
  -> Center Observation Reducer updates TaskState / PlanStep
  -> Replanner decides continue_llm | wait_approval | wait_operation | ask_user | complete | fail
  -> YCR build_turn reads TaskState / Plan / working_set and projects provider context
  -> LLM proposes reasoning/tool calls, but does not own lifecycle completion
```

## 0.2 硬性原则

1. 不做 Workflow Capability，不把业务流程固化进 Center 高阶工具。
2. 不做 ExecutionIntent 和 task-level approval；ApprovalRequest 当前机制保持不变。
3. 不用 prompt 补丁解决运行时状态问题。
4. 不用具体工具名、query 词、平台词做 RAG 特判。
5. 所有新增状态必须可审计、可恢复、有终态。
6. 每完成一项实现，必须同步更新本文状态表。

## 0.3 实施待办表

| 编号 | 状态 | 待办 | 目标 | 验收 |
|---|---|---|---|---|
| T01 | 已完成基础实现 | 定义 Agent Runtime 状态不变量 | 明确 Run/Turn/Step/Operation 的状态枚举、互斥规则、终态规则和失败落库规则。 | `stream.close` 不再留下 open turn；internal turn 创建会关闭旧 internal 半状态；AgentRun 终态不能回退。窄测试已覆盖。 |
| T02 | 已完成基础实现 | AgentRun 主状态机接管 `/agent/invoke/stream` | `/agent/invoke/stream` 创建并推进 AgentRun；provider loop、tool call、waiting approval/operation、final 都写 AgentRunStep。 | Run/Turn 关联、Run 中间状态、Plan 关联、waiting checkpoint、provider 成功/失败、tool observation、final step 均已落库。 |
| T03 | 已完成基础实现 | 通用 Agent Plan 数据模型 | 新增通用 AgentPlan/AgentPlanStep；与 MaintenancePlan 明确分离。 | 已新增 `AgentPlan` / `AgentPlanStep` 表和服务；普通 `/agent/invoke/stream` 会创建通用 Plan。 |
| T04 | 已完成基础实现 | Plan 进入 provider context | YCR build-turn 接收当前 Plan 摘要，provider 看到当前目标、已完成步骤、等待项和禁止重复动作。 | build-turn 已接收 `agent_plan`，并以 `YCR Agent Plan` system message 注入 provider messages；测试覆盖。 |
| T05 | 已完成基础实现 | Operation Event Queue | Operation 终态进入 session-level notification queue；按 operation_id 幂等。 | 新增 `agent_operation_notifications` 表；`OperationService.append_event()` 在终态且有 session 时入队；`(session_id, operation_id)` 唯一；approval_wait 这类内部等待壳不进入用户自动汇报队列。 |
| T06 | 已完成基础实现 | Agent Runtime 单消费者 | session 内自动 operation report 使用服务端单消费者；前端不负责 claim 或触发 Agent。 | 新增 `AgentOperationReporter` 后台 worker，由 Center claim notification、拉起 internal AgentRun、完成后 mark reported；前端只展示会话和 Operation 状态。 |
| T07 | 已完成基础实现 | Operation report observation | 自动汇报使用结构化 Operation output，不重新 search/describe 无关工具。 | 自动 report 使用服务端构造的 operation observation prompt，工具面为空，`max_steps=1`；internal run/plan 带 `run_kind=operation_report`，不污染主 Plan。 |
| T08 | 已完成基础实现 | YCR Session State 模型 | 建立 capability/artifact/operation/node working set 的持久存储或明确复用表结构。 | 已新增 `YcrSessionState`；`context.status` 可显示 session state 统计；build-turn 能读取 session state。task working set 和 run-level TaskState 归入 T21-T28。 |
| T09 | 已完成基础实现 | Tool observation 更新 YCR state | capability.search/describe/invoke、artifact.present、operation.status 等结果按 typed metadata 更新 YCR state。 | tool observation 已按 `ycr_entities`、`artifacts`、`operation`、`nodes` 等 typed shape 写入 session state；artifact 输出额外写入 focus，`artifact.present` 写入 current focus；测试覆盖 artifact/capability/operation/focus。 |
| T10 | 已完成基础实现 | Operation event 更新 YCR state | Operation created/running/terminal 事件更新 operation working set。 | `OperationService.append_event()` 已把 operation state 写入 YCR session state；Operation 完成后可通过 build-turn 注入当前任务上下文。 |
| T11 | 已完成基础实现 | Registry Snapshot | 定义 registry snapshot version，包括 capability definition/source、node/runtime readiness 和 provider bootstrap tool schema。 | `build_capability_context()` 已使用 Node/Capability/CapabilityDefinition/CapabilitySource/RuntimeInstance count/max timestamp 与 bootstrap tool fingerprint 生成 registry fingerprint；能力或节点变化会失效 snapshot。 |
| T12 | 已完成基础实现 | Capability Context Snapshot | `build_capability_context()` 结果缓存为版本化 snapshot。 | 新增 `YcrCapabilityContextSnapshot`；首轮 miss 构建，后续 hit 直接读 JSON；`agent.invoke.context_loaded` 审计事件包含 snapshot 状态。 |
| T13 | 已完成基础实现 | Tool RAG Candidate Loader | 每轮从 session working set、snapshot、query cache 读取候选；不在热路径强制 embedding/rerank。 | `build-turn` 已从 TaskState working set、session state 和本轮 projected working set 生成 `provider_context.capability_candidates`；当三者均为空时，YCR 用当前 objective 通过既有 registry/RAG 做一次通用 intent working-set bootstrap，并把命中候选注入本轮 working set。Session 中出现 artifact/focus 等 typed entity 后，YCR 会通过 registry 结构化字段补入对应 entity-input 能力候选。候选仍不足时模型可打开 `capabilities` 分组并调用 `capability.search` 扩检索。 |
| T14 | 已完成基础实现 | Index Not Ready 前端状态 | 候选加载或 search 遇到 index not ready 时，前端明确显示等待/重试状态。 | registry search trace 已显示 `retrieval.index.status=not_ready`、retry 秒数和等待提示；YCR panel 也显示 snapshot/candidate/session-state 状态。 |
| T15 | 已完成基础实现 | 前端 Plan/Operation 状态绑定 | Console 中 operation waiting card、tool card、Runtime/Plan 面板统一读取后端运行时状态。 | Operation 终态后 waiting 卡自动更新、artifact.present 独立展示已具备；新增 `/agent/sessions/{session_id}/runtime-state`，右侧 Runtime 面板读取 AgentRun/TaskState/Event 摘要，Plan 面板读取同一后端投影中的非 internal 通用 AgentPlan。 |
| T16 | 已完成基础实现 | Audit span 化 | 记录 context refs load、available functions、capability context、YCR build-turn、Tool RAG、provider first token、tool execution、operation wait。 | session audit 已记录 context refs / available functions / capability context / YCR build-turn / provider / tool execution elapsed_ms；operation event 已有 session audit。 |
| T17 | 已完成基础实现 | 清理旧自动汇报路径 | 删除或替换临时 internal invoke 自动唤醒逻辑，不保留兼容分支。 | Operation auto-report 只走后端 `AgentOperationReporter` + `agent_operation_notifications` claim/reported/failed 路径；前端不再轮询 notification、不再自行 sendInvoke 自动汇报。 |
| T18 | 已完成基础实现 | 文档一致性更新 | 更新 `docs/current-project-overview.md`、`docs/ycr-current-state.md`、`docs/agent-sse-contract.md` 和 `docs/todos/README.md`。 | active 文档已同步 Operation notification、YCR Session State、Snapshot Cache、candidate loader、SSE `state` 字段和当前剩余缺口。 |
| T19 | 已完成基础实现 | Center meta tool 分组与渐进式展开 | Provider 默认工具面不再常驻 `capability.search`，Center meta tools 按功能分组，LLM 先打开相关分组再调用。 | Provider 默认只直接看到 `capability.groups` / `capability.group.open` / `capability.invoke`；`capability.search` / `capability.describe` 位于 `capabilities` 分组；远端已验证分组打开和 `artifact.read_text`。 |
| T20 | 已完成基础实现 | 文本 artifact 细粒度读取 | 读取日志、配置、txt/json/csv 等文本 artifact 时不能全量塞进 provider。 | `artifact.read_text` 支持 `artifact_id` / `artifact_pattern`、`head` / `tail` / `range` / `full`、负数行号、`line_glob` 和 `max_bytes`；本地与远端窄测试已通过。 |
| T21 | 已完成基础实现 | AgentRunEvent 统一事实源 | 所有 LLM、tool、approval、operation、artifact、plan 状态变化都写入持久 AgentRunEvent；前端按事件/派生状态渲染，不再自己拼业务状态。 | 已新增 `AgentRunEvent` 表和迁移；provider 输出、tool observation、approval 决议、operation 终态、run 完成都进入事件流；`runtime-state` API 已向 Console 暴露最新 run 的 TaskState 与 events。剩余动作是继续减少旧 ChatTimeline 局部 patch 状态。 |
| T22 | 已完成基础实现 | TaskState | 在 `AgentRun.metadata_json["task_state"]` 中维护 run-level 任务状态，记录 objective、facts、blockers、pending_operations、pending_approvals、artifacts、working_set、completion。 | 已新增 `runtime/task_state.py`；Reducer 每次处理 AgentRunEvent 后更新 TaskState；YCR build-turn 已可读取 TaskState。剩余动作是补更强的 completion criteria 和 blocker 分类。 |
| T23 | 已完成基础实现 | Observation Reducer 拆分 | 拆分 Center Reducer 与 YCR Projection Reducer：Center Reducer 把工具/operation/approval/artifact 结果转成任务事实和决策输入；YCR Reducer 只生成 provider 可见 projection/ref。 | 已新增 `runtime/observation_reducer.py`，Center 决策不读取 provider projection 文本；YCR 继续只负责 projection/ref。剩余动作是扩展 reducer 对更多 domain result 的结构化事实抽取。 |
| T24 | 已完成基础实现 | Replanner 生命周期控制 | 每次工具、operation、approval 结果后，由确定性 Replanner 决定 `continue_llm`、`wait_approval`、`wait_operation`、`ask_user`、`complete`、`fail`。 | 已新增 `runtime/replanner.py`，pending approval/operation、terminal blockers、completion 状态均由确定性规则输出。剩余动作是把 Replanner 决策更深地接入主 loop 的继续/停止边界。 |
| T25 | 已完成基础实现 | PlanStep 运行时化 | PlanStep 成为真实运行时结构，tool call、operation、approval、artifact 都挂到 step；Plan 面板从后端状态渲染，不展示松散文本计划。 | Reducer 已能按 `tool_call_id`、`operation_id`、`approval_id` 匹配或创建 PlanStep；同一 tool call 后续 approval/operation 终态会回填同一 step，不再全部写首个 step。剩余动作是继续扩展 artifact 与复杂多分支任务的 step 归属。 |
| T26 | 已完成基础实现 | Operation/Approval 后端自动恢复 | Operation 完成、Approval 通过/拒绝后，由 Center Runtime 更新 TaskState 并恢复 AgentRun；前端只显示状态和用户操作入口，不负责推进业务。 | Operation terminal event 已 append AgentRunEvent 并触发 TaskState/Replanner；approval approve/deny/approve-and-run 已写回 AgentRunEvent；既有 `AgentOperationReporter` 继续负责后端自动汇报。剩余动作是继续削减前端残留业务触发入口。 |
| T27 | 已完成基础实现 | YCR 回归上下文层 | YCR 只保留 build_turn、ContextRef、projection、search、token accounting；输入增加 TaskState/Plan/working_set；不负责继续/等待/完成决策。 | `/v1/context/build-turn` 已接收 TaskState，YCR context packet 已投影 TaskState/Plan/working_set，但 lifecycle decision 仍保存在 Center。剩余动作是持续审查 YCR 不重新承担流程决策。 |
| T28 | 已完成基础实现 | Working Set Tool Loading | Tool discovery 改为 Runtime 维护 task working set：默认只给少量 bootstrap meta tools；任务相关工具由 working set 注入；`capability.search` 只作为补充扩检索，不作为常规路径。 | 默认工具面已收窄到 `capability.groups`、`capability.group.open`、`capability.invoke`；TaskState 已记录 reducer 识别到的 working capabilities/artifacts；YCR build-turn 会把 TaskState working capabilities 合并进 working set/capability candidates；空 working set 会基于 objective 做通用 intent bootstrap；输出 `tool_strategy`：`reuse_working_set` 或 `bootstrap_discovery`。剩余动作是通过真实会话量化是否降低 group/open/search 与无关 invoke。 |

## 0.3.1 T21-T28 确定实施路线

本节把 T21-T28 固定为可直接开工的实现路线。实现时不得再引入
`or equivalent`、二选一存储、前端业务推进、YCR 生命周期决策或 prompt-only 补丁。

### A. 数据模型与迁移

必须新增 `AgentRunEvent` 表，放在 `src/yequ/models/agent_run.py` 或拆到
`src/yequ/models/agent_run_event.py` 后由 `models/__init__.py` 导出。字段固定为：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | internal pk | 数据库主键。 |
| `event_id` | string unique | `arevt_` 前缀全局事件 ID。 |
| `run_record_id` | FK -> `agent_runs.id` | 所属 run。 |
| `run_id` | string indexed | 外部 run id，便于查询。 |
| `session_id` | string indexed | 所属 session。 |
| `turn_id` | string nullable indexed | 所属 turn。 |
| `step_id` | string nullable indexed | 关联 AgentRunStep/PlanStep/tool step。 |
| `plan_id` | string nullable indexed | 关联 AgentPlan。 |
| `plan_step_id` | string nullable indexed | 关联 AgentPlanStep。 |
| `event_type` | string indexed | 固定枚举字符串。 |
| `source` | string | `llm`、`tool`、`approval`、`operation`、`artifact`、`plan`、`runtime`。 |
| `payload_json` | JSON | 事件事实，不放 provider 大文本。 |
| `occurred_at` | timestamptz | 事件发生时间。 |
| `seq` | integer | run 内单调序号。 |
| `created_at` | timestamptz | 落库时间。 |

约束：

- `(run_record_id, seq)` 唯一。
- `event_id` 唯一。
- `payload_json` 不保存未投影大 raw output；大值必须引用 ContextRef 或 artifact id。
- 所有 schema 变化必须生成 Alembic migration。

`TaskState` 第一版固定存储在 `AgentRun.metadata_json["task_state"]`，不新增独立表。
结构固定为：

```json
{
  "objective": {"text": "...", "created_from": "user_prompt"},
  "facts": [],
  "blockers": [],
  "pending_operations": [],
  "pending_approvals": [],
  "artifacts": [],
  "working_set": {"capabilities": [], "nodes": [], "artifacts": []},
  "completion": {
    "status": "in_progress",
    "criteria": [],
    "satisfied": [],
    "missing": []
  },
  "last_decision": {
    "action": "continue_llm",
    "reason_code": "initial"
  }
}
```

`completion.status` 固定枚举：

```text
in_progress | waiting_approval | waiting_operation | ask_user | complete | failed
```

以后如果需要跨 run 查询性能，再迁移为独立表；本阶段不做。

### B. 新增服务文件

新增并固定以下服务边界：

| 文件 | 职责 |
|---|---|
| `src/yequ/runtime/agent_run_events.py` | `append_agent_run_event()`、`list_agent_run_events()`、run 内 seq 分配。 |
| `src/yequ/runtime/task_state.py` | TaskState 初始化、读取、更新、事件重放、schema 校验。 |
| `src/yequ/runtime/observation_reducer.py` | Center Observation Reducer；把 tool/operation/approval/artifact/plan 事件转成 TaskState facts、blockers、working_set 和 PlanStep 状态。 |
| `src/yequ/runtime/replanner.py` | Deterministic Replanner；读取 TaskState/Plan/Operation/Approval，输出固定决策。 |
| `src/yequ/runtime/agent_run_resume.py` | 后端恢复入口；由 approval/operation 终态触发，按 Replanner 决策继续 run。 |

禁止把上述逻辑继续堆进 `agent_stream.py`、`api/routes/agent.py`、前端组件或 YCR。

### C. Agent 主循环改造点

`src/yequ/agent/agent_stream.py` 保留 provider streaming 和 tool-call 执行编排，但必须把生命周期事实写入事件源：

1. run 创建后 append `run.created`，初始化 TaskState。
2. provider 开始 append `llm.started`。
3. provider 输出 tool call append `llm.tool_call_requested`。
4. tool call 入队/开始/结束 append `tool.started` / `tool.completed` / `tool.failed`。
5. artifact present/read/upload 结果 append `artifact.created` / `artifact.presented` / `artifact.read`。
6. approval required append `approval.waiting`。
7. operation waiting append `operation.waiting`。
8. final candidate append `llm.final_candidate`，必须经过 Replanner 判定后才能落为 `run.completed`。
9. run failed append `run.failed`，必须包含 stable error_code/message。

每次 append 后执行：

```text
ObservationReducer.reduce(event)
Replanner.decide(run_id)
apply decision
```

### D. Replanner 决策合同

`ReplannerDecision` 固定为：

```json
{
  "action": "continue_llm | wait_approval | wait_operation | ask_user | complete | fail",
  "reason_code": "string",
  "target_operation_id": null,
  "target_approval_id": null,
  "missing_slots": [],
  "blocked_evidence": [],
  "next_prompt": null
}
```

确定规则：

1. `pending_approvals` 非空且未决议 -> `wait_approval`。
2. `pending_operations` 非空且未终态 -> `wait_operation`。
3. operation terminal 后有未满足 completion criteria -> `continue_llm`。
4. approval approved 后有可继续动作 -> `continue_llm`。
5. approval rejected 且无替代路径 -> `ask_user` 或 `fail`，取决于用户目标是否还能无该权限完成。
6. 工具失败先由 Reducer 记录 blocker；如果 working_set 中存在替代 capability/profile -> `continue_llm`。
7. 用户明确要求自主完成时，`ask_user` 只有在缺少用户私有信息、目标歧义或所有执行路径已失败时允许。
8. LLM final candidate 只有满足 `completion.criteria` 时才能 `complete`；否则改为 `continue_llm` 或 `ask_user/fail`。

### E. PlanStep 绑定

`AgentPlanStep` 必须成为运行时结构。实现要求：

- tool call 创建时绑定或创建 PlanStep。
- operation_id、approval_id、artifact_id、tool_call_id 写入 PlanStep metadata。
- PlanStep 状态由 AgentRunEvent 推进，固定为：

```text
pending | running | waiting_approval | waiting_operation | succeeded | failed | skipped
```

- 前端 Plan 面板只读后端 Plan/PlanStep 状态，不解析 assistant 文本。
- Resume 时根据 TaskState + PlanStep 找到未完成 step，不从聊天历史猜。

### F. Operation / Approval 后端恢复

接入点固定：

| 事件 | 接入点 | 行为 |
|---|---|---|
| Operation terminal | `OperationService.append_event()` 或 `AgentOperationReporter` claim 前 | append `operation.completed/failed/cancelled/timeout` AgentRunEvent，Reducer 更新 TaskState，Replanner 决定是否继续。 |
| Approval approved/rejected | `admin_approvals.py` 决议路径 | append `approval.approved/rejected` AgentRunEvent，Reducer 更新 TaskState，Replanner 决定是否创建真实 operation 或继续 run。 |

前端只负责：

- 展示 approval 操作入口；
- 展示 operation 状态；
- 展示 Plan/TaskState 派生状态；
- 用户手动 Append 只作为引用上下文，不作为自动恢复机制。

### G. YCR 边界

YCR 继续负责：

- `build_turn`；
- ContextRef；
- projection；
- search；
- token accounting；
- provider-visible messages/tool definitions。

YCR 新增输入：

- `task_state`；
- `agent_plan`；
- `working_set`。

YCR 禁止负责：

- `continue_llm`；
- `wait_approval`；
- `wait_operation`；
- `ask_user`；
- `complete`；
- `fail`。

`build_turn` 只把 TaskState/Plan/working_set 投影进 provider context，不改变 Center 状态。

### H. Working Set Tool Loading

Runtime 维护 task working set，来源固定：

1. 用户 prompt 目标解析出的 node/platform/file/artifact/transfer 线索；
2. `capability.groups` / `capability.group.open` 返回的分组 capability；
3. `capability.search` 命中的 capability；
4. `capability.describe` 返回的 schema/profile；
5. 成功调用过的 capability；
6. Operation/Artifact 事件中出现的 related capability。

规则：

- provider 默认仍只看到 `capability.groups` / `capability.group.open` / `capability.invoke`。
- working set 中的 capability 通过 YCR provider context 注入，不必每轮 search。
- `capability.search` 只在 working set 不足、目标新变化、或 Replanner 判定候选不足时使用。
- 不为具体 query、工具名、平台词写特判。

### I. 前端改造

前端只读后端状态：

- Chat timeline 渲染 AgentRunEvent 派生消息和 tool cards；
- OperationCard 状态来自后端 operation + PlanStep，不自行判断 waiting；
- Approval 面板只显示 pending/approved/rejected，不触发业务 resume；
- Plan 面板读取 PlanStep 运行时状态；
- YCR 面板显示 context/token/projection/working set，但不显示生命周期决策。

### J. 测试与验收

最小测试集：

1. `AgentRunEvent` seq 单调、事件可按 run 查询。
2. tool succeeded -> Reducer 写 TaskState fact 和 working_set。
3. tool failed + 替代 capability/profile 存在 -> Replanner 返回 `continue_llm`。
4. approval required -> Replanner 返回 `wait_approval`，PlanStep 为 `waiting_approval`。
5. approval approved -> 后端 append event 并触发 `continue_llm`。
6. operation waiting -> Replanner 返回 `wait_operation`。
7. operation succeeded + completion 未满足 -> `continue_llm`。
8. LLM final candidate 但 completion 未满足 -> 不能 complete。
9. 用户明确自主完成时，普通 blocker 不允许直接 ask_user。
10. 前端刷新后 waiting card/Plan 状态由后端恢复。

真实验收场景：

- WireGuard 配置读取：`user.readonly` 权限不足后自动尝试 `admin.readonly`，不能停下来问下一步。
- Windows 截图：只做必要步骤，展示 artifact 后 complete。
- Windows -> Linux 传输：Operation 完成后自动汇报并根据 TransferSession 状态 complete。
- Approval 后继续：用户 approve 后无需 Append，后端自动推进。

## 0.4 验收场景

实现完成后必须用以下真实交互验收：

1. `帮我给 win 设备截个图，只做必要步骤，成功展示后结束。`
2. `把上一张截图放到 winClient 的 F:\Desktop。`
3. `把 Win 上一个大文件传到 linux-node-01 /home/yequdesu/，overwrite。`
4. `查询 winClient 的 C 盘容量。`
5. 在 operation 运行中刷新前端，OperationCard 状态仍正确。
6. Operation 终态后由 Center/Agent Runtime 服务端自动汇报一次；用户手动 Append 同一 operation 不会重复执行。
7. YCR index 未就绪时前端显示等待/重试，而不是让 Agent 胡乱搜索。
8. YCR 服务停止时，Agent fail-closed，错误清晰。

## 0.5 推荐实施顺序

1. T01-T02：先锁住 AgentRun/Turn 生命周期。
2. T05-T07：再做 Operation Event Queue，解决重复自动汇报。
3. T03-T04：引入通用 Plan，让运行时有任务骨架。
4. T08-T10：补 YCR Session State。
5. T11-T13、T19-T20：做 Snapshot Cache、Candidate Loader、分组工具面和细粒度文本读取，解决首包慢和重复探索。
6. T21-T28：补 AgentRunEvent / TaskState / Reducer / Replanner / PlanStep runtime / 后端恢复 / YCR 边界 / Working Set Tool Loading，系统性解决绕路、重复搜索、Operation/Approval 恢复、前端 waiting 和长任务收敛。
7. T14-T16：前端和审计观测闭环。
8. T17-T18：清理旧路径并同步文档。

## 1. 总体结论

当前项目没有失控到不可维护，但已经进入一个明显的架构临界点：

1. 核心构件已经具备雏形：AgentRun、AgentTurn、Operation、YCR ContextRef、Capability Registry、session audit log 都存在。
2. Run/Turn、Plan、Operation Event Queue、YCR Session State 和 Snapshot Cache 已完成基础实现，但复杂任务仍缺少 AgentRunEvent 事实源、TaskState、Observation Reducer、任务级 completion criteria、Replanner 和受控 ask-user 动作。
3. 文档多处宣称“主链路完成”，但真实会话暴露出 Operation 自动汇报重复、internal turn 卡在 building_context、首包 context_loaded 慢等系统性问题。
4. 当前最危险的不是某个工具没命中，而是 Agent Runtime 尚未接管“继续、询问、最终回答、失败”的任务级判定，导致 LLM 可能在可继续执行时过早把控制权交还给用户。

因此，下一阶段不应继续添加局部功能，而应先收敛运行时主线：

```text
Session
  -> AgentRun / AgentTurn
  -> Plan
  -> YCR Session State
  -> Capability Candidate Working Set
  -> Tool Call / Approval / Operation
  -> Operation Event Queue
  -> Final Report
```

## 2. 当前代码事实

### 2.1 已存在的关键模型

当前代码已经有这些基础模型：

| 模型 | 位置 | 当前职责 |
|---|---|---|
| `AgentRun` / `AgentRunStep` | `src/yequ/models/agent_run.py` | 持久化 Agent run 和 provider/tool/final step；AgentRun 终态不可回退。 |
| `AgentTurn` / `AgentTurnEvent` | `src/yequ/models/agent_turn.py` | 记录用户可见 turn 和 SSE 事件；stream close 会收敛未终止 turn。 |
| `Operation` / `OperationEvent` | `src/yequ/models/operation.py` | 表达 waitable runtime shell 和事件；终态 Operation 会进入 Agent notification queue。 |
| `YcrContextRef` / chunks / cache / state / snapshot | `src/yequ/models/ycr.py` | 保存 raw ref、chunk、Tool RAG index、多层 RAG cache、Session State 和 capability context snapshot。 |
| `session_audit` JSONL | `src/yequ/services/session_audit.py` | 已能按 session 落盘审计事件，并记录 context/YCR/provider/tool/operation 关键 span。 |

这些模型说明项目已经具备当前阶段的统一控制面。后续重点是用真实会话验收其稳定性，而不是继续扩展临时状态。

### 2.2 体量和职责集中度

当前最大文件和职责热点如下：

| 文件 | 行数 | 观察 |
|---|---:|---|
| `src/yequ/services/capability_registry.py` | 1685 | registry、search、definition/source 同步、诊断和多类转换集中，已经成为高耦合热点。 |
| `src/yequ/agent/agent_stream.py` | 1501 | Agent 主循环、YCR build-turn、tool execution、maintenance plan、SSE、history 等混在同一文件。 |
| `src/yequ/services/node_service.py` | 1488 | Node 注册、capability sync、YQP 处理边界过大。 |
| `src/yequ/application/transfer.py` | 1253 | transfer preflight/create/status/cancel/resume 和 job 协作集中。 |
| `src/yequ/runtime/execution_runtime.py` | 863 | capability invoke、artifact deploy、transfer create、node job、approval 等路径集中。 |
| `src/yequ/ycr/capability_gateway.py` | 810 | Tool RAG、registry filter、cache、rerank、index readiness 混合。 |

这些文件不是马上必须拆，但它们说明当前模块边界已经接近维护上限。下一阶段的架构收敛应优先把“状态机、上下文、事件队列、候选加载”从这些热点中抽出，而不是继续在热点文件里追加逻辑。

## 3. 原始结构性问题与当前处置

本节保留问题来源，但状态以第 1、2 节表格为准。下列问题已经完成基础实现，不再作为阻塞项；后续只围绕真实会话验收和细节优化继续收敛。

### 3.1 Agent Runtime 主状态机

原始问题是 `AgentRun`、`AgentTurn`、Operation 自动汇报和前端状态各自拼接，导致 internal turn 卡住、operation 重复汇报和失败路径终态不稳定。

当前处置：

- `stream.close` 会关闭未终止 turn；
- session 内创建 internal turn 前会关闭旧 internal 半状态；
- AgentRun 终态不可回退；
- `/agent/invoke/stream` 已推进 AgentRun 中间状态；
- provider 成功/失败、tool observation、final 已写入 AgentRunStep。

后续观察：只补更细 first-token、DB span 和更多真实慢点归因，不再改主模型。

### 3.2 通用 Agent Plan

原始问题是旧 `agent_plan()` 属于 MaintenancePlan 路径，普通 Agent 任务没有通用 Plan/PlanStep。

当前处置：

- 新增通用 `AgentPlan` / `AgentPlanStep`；
- 每个普通 `/agent/invoke/stream` run 会创建 AgentPlan；
- Plan 被注入 YCR build-turn；
- Plan 状态跟随 succeeded、failed、waiting_operation、waiting_approval 更新；
- 前端右侧 Plan 面板读取后端最新 AgentPlan。

后续观察：PlanStep 仍保持粗粒度，不引入 Workflow Capability，不写死截图、传输等业务流程。

### 3.3 Operation Event Queue

原始问题是 Operation 终态后自动汇报缺少幂等队列，前端也会自行推断 waiting 状态。

当前处置：

- 新增 `agent_operation_notifications`；
- Operation 终态且有 session 时入队；
- `(session_id, operation_id)` 幂等；
- `AgentOperationReporter` 服务端后台 worker claim 单条 notification 并触发 internal AgentRun 自动汇报；
- 汇报完成后服务端标记 reported，失败后标记 failed；
- Append Operation Context 保留为手动引用入口。

后续观察：真实刷新恢复和多 operation 连续完成场景仍需继续验收。

### 3.4 YCR Session State

原始问题是 YCR 主要做 provider 前置投影，不能持久维护会话工作集。

当前处置：

- 新增 `YcrSessionState`；
- tool observation 按 typed metadata 写入 capability、artifact、operation、node working set；
- Operation event 写入 operation working set；
- build-turn 读取 session state 并注入 provider context；
- `context.status` 可返回 session state 统计。

后续观察：task facts 仍可继续增强，但当前主链路已不再只依赖历史消息恢复任务状态。

### 3.5 Tool RAG Candidate Loader

原始问题是 Tool RAG 被期待为“工具智能”，导致检索层和流程规划职责混淆。

当前处置：

- Tool RAG 定位为候选加载器；
- provider 默认工具面已收窄为 `capability.groups` / `capability.group.open` / `capability.invoke`；
- Center meta tools 按 `nodes`、`capabilities`、`context`、`artifacts`、`operations`、`transfers` 分组渐进式展开；
- build-turn 从 session state 和本轮 working set 生成 `capability_candidates`；
- 候选不足时模型仍可打开 `capabilities` 分组并调用 `capability.search` 扩大检索。

后续观察：RAG 不决定 Job、Operation、Approval，也不替代 runtime state。

### 3.8 ReAct Runtime / TaskState / Replanner

最新真实会话暴露出新问题：用户明确要求“自主进行直到完成”，Agent 在读取
WireGuard 配置这类任务中已经定位到目录和候选文件，但在权限不足后停下来询问下一步，
而不是继续使用已可用的更高权限只读 profile。

结论：

- 这不是 RAG 命中问题，也不是具体 `exec.run` 工具问题。
- 当前缺口是 Agent Runtime 缺少统一事件源、TaskState、Observation Reducer、
  任务级 completion criteria 和 Replanner。
- LLM 普通文本不能自由决定“任务未完成但询问用户”；询问用户必须变成受控 runtime 动作。

目标路径：

```text
AgentRunEvent append
  -> Center Observation Reducer
  -> TaskState / PlanStep update
  -> Replanner
  -> continue_llm | wait_approval | wait_operation | ask_user | complete | fail
  -> YCR build_turn reads TaskState / Plan / working_set
```

硬规则：

1. 用户明确要求自主完成时，`ask_user` 默认禁止。
2. 只有缺少用户才知道的信息、approval 未完成、或所有可执行路径都失败时才能 ask user。
3. final answer 必须满足任务完成条件；否则继续执行或进入受控 blocked 状态。
4. 工具失败先进入 replanner，不能直接把“建议下一步”作为成功终答。
5. Operation / Approval / Artifact / Plan 状态变化必须先写入 AgentRunEvent，再由 Reducer
   更新 TaskState 和 PlanStep；前端只读后端状态，不自行推进业务。
6. YCR 不参与 continue/wait/complete/fail 决策，只读取 TaskState / Plan / working_set
   并生成 provider-visible context/ref。

### 3.6 Registry/YCR Snapshot Cache

原始问题是每轮 invoke 热路径可能同步重建 capability context。

当前处置：

- 新增 `YcrCapabilityContextSnapshot`；
- `build_capability_context()` 按 target node、provider bootstrap tools 和 registry fingerprint 缓存结果；
- registry fingerprint 覆盖 Node、Capability、CapabilityDefinition、CapabilitySource、RuntimeInstance 的 count/max timestamp/liveness；
- 快照命中状态进入 YCR 前端面板和 audit。

后续观察：节点注册高并发时仍需继续观察 DB 锁等待和 snapshot 失效频率。

### 3.7 文档状态一致性

原始问题是旧文档宣称“主链路完成”，但真实行为暴露出新的 Agent Runtime / YCR State 主线。

当前处置：

- 本文成为当前阶段权威待办；
- 旧 YCR/registry 设计与待办已归档或改为引用；
- `docs/current-project-overview.md`、`docs/ycr-current-state.md`、`docs/agent-sse-contract.md`、`docs/todos/README.md` 已同步当前实现边界。

后续观察：后续每次结构性改动必须同步本文或对应权威文档，避免再次产生“完成状态”和真实行为不一致。

## 4. 当前仍然健康的部分

### 4.1 Center 与 Node 能力协议方向仍然正确

Provider 默认只看到三件套：

```text
capability.groups
capability.group.open
capability.invoke
```

Center meta tools 和 Node capabilities 统一入 registry。Center meta tools 通过分组目录渐进式展开；
Node/Product capability 通过 `capabilities` 分组中的 search/describe 进入候选。这个方向符合
“新增 Node / 新 capability 不调整 Center 和 Agent 行为”的目标。后续不应引入 Workflow Capability
破坏这个方向。

### 4.2 YCR 的 raw ref / projection / cache 基础可继续使用

YCR 当前实现虽然还不是 Session Context State，但 raw ContextRef、size-based projection、Result RAG 和多层 cache 是有效基础，不需要推翻。

### 4.3 Operation 模型基础可复用

Operation 和 OperationEvent 已有 waitable runtime shell、dispatch 字段和 progress 字段。Operation Event Queue 可以在现有模型上演进，不需要重建 Operation。

### 4.4 Session audit log 是正确方向

JSONL 审计已能快速定位真实会话问题。下一步是 span 化和把 event 与 Run/Plan/Operation 绑定，而不是替换掉它。

## 5. 当前架构事项状态

以下事项已经纳入本文或对应正交待办，不再散落在旧 YCR/Registry/Transfer 文档中：

| 状态 | 事项 | 当前结论 |
|---|---|---|
| 已完成基础实现 | Agent Run/Turn 生命周期不变量 | 每个 turn/run 有终态收敛规则；session 内 internal 半状态会被关闭；AgentRun 终态不可回退。 |
| 已完成基础实现 | 通用 Agent Plan 中间层 | 每个 AgentRun 有 Plan/PlanStep；Plan 记录任务状态，不写死业务 workflow。 |
| 已完成基础实现 | Operation Event Queue | Operation 终态入队，服务端单消费者，operation_id 幂等汇报。 |
| 已完成基础实现 | YCR Session State | 持久维护 capability/artifact/focus/operation/node working set；task facts 后续增强。 |
| 已完成基础实现 | Registry/YCR Snapshot Cache | 普通对话可读版本化 capability context snapshot。 |
| 已完成基础实现 | Tool RAG Candidate Loader | RAG 负责候选加载；候选不足时仍允许 `capability.search` 扩检索。 |
| 已完成基础实现 | Center meta tool 分组展开 | 默认 provider 工具面为 groups/open/invoke；search/describe 不再常驻。 |
| 已完成基础实现 | `artifact.read_text` | 文本 artifact 可按行、首尾、range、通配符和 line_glob 读取。 |
| 待实现 | ReAct Runtime / TaskState / Replanner | 解决 Agent 绕路、重复搜索、Operation/Approval 恢复、前端 waiting、断点续跑粒度粗和长任务无法稳定收敛的问题。 |
| 已完成基础实现 | 前端状态绑定重构 | UI 已绑定 Operation、YCR state、通用 AgentPlan；后续优化视觉细节。 |
| 已完成基础实现 | 审计 span 化 | 已记录 context refs、available functions、capability context、YCR build-turn、provider、tool execution 耗时。 |
| 正交待办 | meta tool 合同继续审计 | 默认输出必须是 decision view，detail/diagnostics 显式请求。 |
| 正交待办 | Provider Registry | provider/model/probe/能力发现系统化，但不抢在 Runtime 状态机之前。 |
| 正交待办 | transfer 真实复验 | Linux receive 和 Center transfer fact 继承已在 `2026-07-06-ycr-agent-routing-and-transfer-corrections.md` 中复验通过；本文件不再追踪。 |

## 6. 明确暂缓或不做

### 6.1 暂缓 ExecutionIntent

ExecutionIntent 可以作为未来 task-level approval 和审计归组骨架，但现在先不做。当前优先级更高的是 Plan、Operation Queue 和 YCR Session State。

### 6.2 暂缓 task-level approval

task-level approval 方向正确，但它会触及早期 approval 设计、policy、resource key、approve-and-run、audit 和 replay 语义。当前只保持现有 ApprovalRequest 机制。

### 6.3 不做 Workflow Capability

不引入 `artifact.place_on_node`、`screen.capture_and_present` 这类高阶业务 capability。它们会让 Center 开始认识具体业务流程，破坏 Node/capability 插拔性。

### 6.4 不继续扩大 RAG 职责

RAG 不负责流程规划、不负责审批决策、不负责 Operation 状态恢复。它只做候选加载和上下文检索。

## 7. 当前文档职责

本文已经是该主线的活跃待办：

```text
docs/todos/2026-07-07-agent-runtime-plan-operation-ycr-state.md
```

职责边界：

- Agent Run/Turn 生命周期；
- Plan 必经中间层；
- Operation Event Queue；
- YCR Session State；
- Registry/YCR Snapshot Cache；
- Tool RAG Candidate Loader；
- 前端状态绑定；
- 审计 span 化。

不负责：

- Provider Registry；
- Linux/Windows Node 具体工具质量；
- yq-croc 传输实现；
- task-level approval；
- Workflow Capability。

`docs/todos/README.md` 已把下一步从“YCR 行为修正收口”调整为“Agent Runtime / YCR State / Task Runtime 架构收敛”。后续不再新增同职责文档；新发现的问题必须归入本文状态表，或明确拆到正交待办。

## 8. 验收导向

架构收敛完成后，至少应能稳定通过以下真实交互：

1. Win 截图并展示 artifact。
2. 把上一张 artifact 放到 Win 指定路径，只产生必要审批，不重复自动汇报。
3. Win -> Linux 大文件传输，Operation、TransferSession、Agent 结论一致。
4. Linux -> Win 文件传输，失败时错误明确传播。
5. 查 Win 文件/磁盘，meta tool 输出不膨胀。
6. Operation 终态后由服务端自动汇报一次，Append 仍可手动引用。
7. YCR/registry index 未就绪时前端显示等待状态。
8. YCR 不可用或 ref 丢失时 fail-closed，且错误可读。
9. 首包延迟可由 audit span 精确归因。

## 9. 结论

当前代码不是“烂掉”，但已经出现典型快速迭代后的结构漂移：

- 有基础模型，Run/Turn、Plan、Operation Queue、YCR Session State 已完成基础实现；
- 有 YCR 投影、RAG 和 Session State，但 task working set 与 Replanner 仍未完成；
- 有 OperationEvent 和 Agent notification queue，但仍需继续用真实会话验收长任务恢复和连续 operation 汇报；
- 有通用 AgentPlan，但 PlanStep 与实际 tool step 的映射仍偏粗；
- 文档已覆盖最新暴露的架构问题，后续必须跟随代码继续维护状态表。

下一步应停止继续堆局部补丁，先把 Agent Runtime 的主线收敛出来。只要 Plan、Operation Queue、YCR Session State、Snapshot Cache 这四个控制面立住，现有 Registry、YCR ref/projection、Operation、audit log 都可以继续复用，项目不会需要推翻重做。
