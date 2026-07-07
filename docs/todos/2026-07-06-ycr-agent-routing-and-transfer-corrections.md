# YCR / Agent 工具路由与传输状态修正待办

状态：活跃待办（传输链路已复验通过；剩余项以 meta tool 边界审计和复杂任务行为验收为主）
日期：2026-07-06
适用阶段：YCR 清理重建后的行为质量修正

本文记录 2026-07-06 复盘复杂 Agent 交互后确认的问题和修复路线。本文只负责
已暴露的行为缺陷修正和验收：Tool RAG 通用排序质量、递归 projection 粒度、Linux
yq-croc receive 误报失败、Center transfer fact 继承、meta tool 默认输出和空错误
传播。Tool RAG、projection、transfer fact 继承和 Linux receive 判定的代码主线已经完成；
后续执行以本文状态表中的剩余动作和端到端验收为准。

职责边界：

- YCR raw ContextRef、provider-visible projection、Result RAG、YCR API 和启动/部署
  的当前事实归属 `../ycr-current-state.md`；原清理重建计划已归档。
- Center meta tools 与 Node capabilities 统一入 registry、Agent bootstrap tools
  收窄和 `capability.invoke` 已完成，当前事实归属 `../ycr-current-state.md` 和
  `../current-project-overview.md`；原 unified registry 计划已归档。
- Provider registry、模型发现和 provider 错误展示归属
  `2026-06-29-agent-provider-system.md`。

## 0. 当前实现核对

本表按 2026-07-06 当前代码核对。本文后续章节保留问题背景和目标约束；执行时以
本表的“剩余动作”为准，不能把已完成项重复当作待办。

| 项 | 当前状态 | 代码事实 | 剩余动作 |
|---|---|---|---|
| 递归 projection 粒度 | 已完成 | `src/yequ/ycr/projection.py` 已递归处理 dict/list，只把超限子值替换为 `$ycr_ref`；`tests/test_ycr_context_router.py` 已覆盖大 stdout、medium meta tool output、多小字段对象不 root-ref。 | 无。 |
| `transfer.preflight` 关键事实 inline | 已完成 | 测试已覆盖 `allowed`、`preflight_id`、`source.path` 等关键字段直接可见，根对象不会因多个小字段累计到 5KB 而整体 ref。 | 无。 |
| `node.status` 默认 summary | 已完成 | `src/yequ/runtime/meta_tools.py` 默认 projection 为 `summary`；registry summary 返回 node facts、capability 数量和名称预览，不返回完整 `capability_sources`。 | 只需继续审计描述与前端展示是否一致。 |
| `artifact.*` / `operation.status` / `transfer.status` 默认 summary | 已完成当前审计 | runtime meta tools 已为 artifact、operation、transfer 状态类工具设置 summary 默认值；artifact summary 不返回完整 metadata/blob 细节；`capability.invoke` 当前已实现且必须保留，用于执行具体 Node capability。Center meta tools 已统一注册为 capability，provider 默认工具面已收窄。 | 继续用真实交互审计默认 summary 是否仍有过宽输出。 |
| YCR/meta tool 错误传播 | 已完成当前审计 | `YcrClient` 已把 HTTP/网络/JSON 异常转成 `YcrError(code, message)`；runtime meta tools 和 Agent stream 会向前端传播 code/message；已补测试覆盖 YCR error code/message 不为空。 | 端到端交互继续观察前端展示。 |
| Tool RAG 通用排序质量 | 已完成主链路，待持续验收 | 当前索引文档由 capability 合同字段生成，没有截图类同义词特判；统一 capability registry、provider 三件套工具面、BGE-M3 dense/sparse retrieval、reranker、query/rerank cache、registry-version-aware retrieval candidate cache 均已落地。 | 用截图、文件搜索、transfer 等真实任务持续验收排序质量和过度探索；不得新增具体 query/capability 特判。 |
| Center transfer fact 继承 | 已完成 | `transfer.create` 会从 preflight source fact 继承 size/hash，写入 `TransferSession.size_bytes/sha256`，并向 receive input 下发 `expected_size_bytes` / `expected_sha256`。 | 无。 |
| Linux receive 成功误判失败 | 已验证通过 | Linux receive wrapper 不再使用 `target_mtime >= receive_started_at`；overwrite 指定目标会在启动 yq-croc 前清理旧目标，runtime 非零退出后只用 size/hash 校验判定是否可恢复为 succeeded。真实 Windows -> Linux yq-croc 传输已复验：Operation、TransferSession、目标文件 size/hash 和 Agent 结论一致。 | 无。 |
| `context.expand` 默认全量展开风险 | 已完成 | `context.expand` 对过大的根路径 `$` 返回 `path_required`、schema、preview shape 和 available paths；指定子路径仍可展开。 | 无。 |

## 1. 已确认问题

### 1.1 Tool RAG 排序不能靠工具特判

现象：

- 用户要求截取 Windows 屏幕时，Agent 先命中并调用 `workflow.snapshot`，
  之后才找到真正的 `screen.capture`。
- 如果直接写“截图/截屏/screenshot/screen capture 优先 screen.capture”，
  后续其他能力也会继续出现同类补丁，导致 Center/YCR 被具体工具污染。

结论：

- 不允许为某个具体 capability 写同义词白名单、黑名单或优先级特判。
- Tool RAG 必须通过通用能力合同、结构化索引、embedding 和 reranker 解决排序。
- capability 能否被正确检索，必须由 capability 自身合同表达：动作、对象、
  输入、输出、artifact 类型、平台、风险、effect、runtime requirements。

### 1.2 YCR projection ref 粒度过粗

现象：

- `transfer.preflight` 没有单个巨大字段，但整个 JSON 约 5KB，当前实现会把根对象
  直接替换成 `$ycr_ref`。
- Agent 为读取 `allowed`、`preflight_id`、`failed_preconditions` 等关键事实
  继续调用 `context.expand`，增加工具轮次和 token 消耗。

结论：

- projection 必须递归处理 JSON object/list。
- 对 JSON 中每个键值对，如果某个值本身过大，只把该值替换为 ref。
- 根对象整体超阈值时，不应优先 root ref；应先保留结构并下钻到子值。
- 不允许用字段名白名单、黑名单、敏感字段表或业务字段特判控制 projection。

### 1.3 transfer 状态不一致应修 Linux Node，不新增骑墙状态

现象：

- Linux receive 端报告 `yq-croc receive failed with exit code Some(1)`。
- 后续文件系统检查显示目标文件存在，大小和 SHA256 与源文件一致。
- Agent 最终文字说传输成功，但 Operation 面板显示 failed。
- 已复盘的一次 Windows -> Linux 传输中，Center 显示 receiver failed，但 Linux
  目标文件 `/home/yequdesu/SillyTavern-1.17.0.zip` 实际存在：
  - `size=38399221`；
  - `sha256=870df5d7151edec8d700deaee6144e49415dd6f48e206191874863dcdb97deb2`；
  - size/hash 与 source preflight 完全一致；
  - ctime 正好落在本次传输完成时间；
  - mtime 保留为源文件原始修改时间。
- 这说明 yq-croc/croc 接收完成后可能保留源文件 mtime。Linux receive wrapper
  若用 `target_mtime >= receive_started_at` 判断“本次写入”，会把实际成功误判为
  失败。
- 同次复盘还发现 `TransferSession.size_bytes` / `sha256` 仍为 null，虽然 preflight
  和 receiver input 已经具备 size 信息；receiver input 只带 `expected_size_bytes`，
  没有继承 preflight 计算出的 `expected_sha256`。

结论：

- 不引入 `completed_with_runtime_error`、`succeeded_with_warning` 等中间状态。
- Center transfer 状态继续保持清晰：成功是 `succeeded`，失败是 `failed`。
- 当前问题优先定位为 Linux Node / yq-croc receive 包装逻辑的失败判定错误。
- Linux receive 成功判定不得依赖目标文件 mtime。
- Center `transfer.create` 必须从 preflight 继承 source size/hash，并写入
  `TransferSession`。
- receive job input 必须同时带 `expected_size_bytes` 和 `expected_sha256`。
- Agent 不得自行用文件存在或 SHA256 校验覆盖 Center transfer domain 状态。

### 1.4 node.status 职责过宽

现象：

- `node.status` 在 detail 形态会返回完整 `capability_sources`。
- 每个 source 可能包含 schema、execution requirements、resource keys、合同诊断、
  runtime 信息等，节点能力一多就膨胀到几十 KB。
- Agent 普通任务中调用 `node.status` 会把管理诊断视图带入 provider 链路。

结论：

- Agent 默认路径中的 `node.status` 只负责 Node 在线状态、平台、心跳、
  capability 数量和少量名称预览。
- 完整 capability source 细节属于管理诊断，不属于 Agent 普通决策输入。
- 能力发现统一走 `capability.search` / `capability.describe`。

### 1.5 其他 meta tool 也存在职责边界风险

现象：

- `operation.status` 的描述是检查一个 waitable Operation，但实现会同时返回
  Operation shell 和 handler 投影出的完整 domain state；transfer/job/maintenance
  这类 domain state 可能继续携带 job、artifact、step、progress_detail 等大对象。
- `transfer.status` 的描述是检查一个 TransferSession，但实现返回 source/target
  job 投影、summary、错误、路径、runtime 相关信息等完整 session 字典。Agent
  普通路径通常只需要状态、进度、错误和下一步建议。
- `artifact.list` 用于查找已有 artifact，但每项默认带 metadata、sha256、
  blob_id、download_url 等完整字段；当 metadata 较大或数量较多时会超过“列表”
  语义。
- `artifact.get` 和 `artifact.present` 都返回完整 artifact 字典。`artifact.present`
  的主要职责是让 Console 展示 artifact，不应把完整 metadata 当作 Agent
  决策输入反复注入。
- `context.expand` 默认 path 为 `$`，虽然有 limit，但语义上容易诱导 Agent
  展开整个 ref；默认行为应鼓励 path-specific expand。
- `capability.describe` 允许 `detail`、`schema`、`diagnostics`、`sources`、
  `runtime` 等详细 sections；这些适合诊断，不应成为普通调用前的固定步骤。

结论：

- 本次不能只修 `node.status`。所有 Center meta tools 都必须重新审计：
  工具描述、默认 projection、返回 shape、最大返回量、Agent 普通路径和诊断路径
  必须一致。
- meta tool 默认返回必须是 provider decision view，而不是管理 UI detail view。
- 诊断详情必须显式请求，并通过 projection/section/limit/path 受控返回。
- 不允许让 YCR 被动兜底所有 meta tool 大输出；工具自身的默认返回就要符合职责。

### 1.6 错误传播仍有空错误

现象：

- 交互记录中出现 `YCR request failed:`，但错误文本为空。

结论：

- meta tool / YCR client / SSE 前端必须传播稳定错误码和可读 message。
- 不允许出现空失败、伪成功或只有 HTTP 状态没有业务原因的错误展示。

## 2. 修复方案

### 2.1 重写 projection 粒度

修改目标：

- `project_value_for_provider` 保持通用 size-based 策略，但投影顺序改为：
  先递归结构，再对超阈值子值生成 `$ycr_ref`。
- object/list 本身不是天然 ref 边界；只有当子值无法继续拆分或拆分后仍过大，
  才在该结构边界生成 ref。
- 字符串、二进制文本、大数组、大对象均按大小规则处理，不看字段名。

验收标准：

- `transfer.preflight` 的关键字段在 provider-visible facts 中直接可见。
- 大 stdout、大目录列表、大 capability source 数组仍会局部 ref。
- 测试覆盖“多个小字段组成的对象不 root-ref”和“大子字段局部 ref”。

### 2.2 整理 Tool RAG 排序输入

修改目标：

- capability index 文档使用统一合同生成，不为具体工具写特判。
- reranker 输入包含 capability 的动作、对象、输入输出、artifact 类型、
  平台和 effect，而不是只拼 canonical_name/description。
- `workflow.snapshot` 与 `screen.capture` 这类词面接近但输出对象不同的能力，
  通过通用合同信息拉开排序。

验收标准：

- 查询“截图”“截屏”“screenshot”“screen capture”时，`screen.capture`
  依靠合同语义排在 `workflow.snapshot` 前面。
- 测试不得包含“如果 query 包含截图则提升 screen.capture”之类规则。
- 新增 capability 只要合同完整，不需要改 Center/YCR/Agent 特判代码即可被发现。

### 2.3 修 Linux Node receive 成功误判为失败

修改目标：

- 在 Linux Node 的 yq-croc receive wrapper 中定位 exit code 1 来源。
- Linux receive wrapper 不再使用 mtime 判断目标文件是否由本次传输写入。
- overwrite 模式下，在 receive 前记录目标文件快照或清理/隔离既有目标文件；
  receive 后以 size/hash 校验结果作为成功判定依据。
- `expected_size_bytes` 存在时，接收后 size 必须一致。
- `expected_sha256` 存在时，接收后 sha256 必须一致。
- 如果文件已经完整写入且 size/hash 校验通过，Node 端不得上报 failed。
- 如果确实失败，必须保留失败状态并上报稳定错误码、stderr 摘要和可诊断事件。

验收标准：

- 同一文件从 Windows Node 传到 Linux Node 后，Operation 与 TransferSession
  都进入 succeeded。
- yq-croc 保留源文件 mtime 时，Linux receive 仍能正确判定成功。
- 不再需要 Agent 额外调用文件 stat/hash 来“纠正” Center transfer 状态。
- Linux receive 失败时，前端显示明确错误原因，而不是泛化 exit code。

### 2.4 修 Center transfer fact 继承

修改目标：

- `transfer.create` 基于 preflight 创建 TransferSession 时，必须从 preflight source
  fact 继承 `size_bytes` 和 `sha256`。
- TransferSession 持久化字段必须记录 expected source size/hash，供 UI、恢复、
  校验和错误解释使用。
- 创建 source/target jobs 时，receiver input 必须继承：
  - `expected_size_bytes`；
  - `expected_sha256`。
- 如果用户显式传入 expected hash，则显式参数优先；否则使用 preflight source fact。

验收标准：

- preflight 已计算 sha256 时，TransferSession 中不再出现 `size_bytes=null` /
  `sha256=null`。
- Linux receive job input 同时包含 expected size 和 expected sha256。
- 前端 transfer 详情可以直接展示预期 size/hash，不需要 Agent 再查文件补事实。

### 2.5 收窄 node.status 默认输出

修改目标：

- `node.status` 默认只返回 summary。
- detail/diagnostics 输出必须显式请求，并且不得被 Agent 普通路径误用为能力浏览。
- `capability_sources` 不进入默认 provider-visible result。

验收标准：

- Agent 普通调用 `node.status` 的 raw result 不随 Node capability 数量线性膨胀。
- 查询能力必须走 `capability.search`，查看具体能力必须走 `capability.describe`。
- 管理 UI 仍可通过显式 diagnostics/detail 获取完整诊断信息。

### 2.6 审计并收敛所有 meta tool 的职责边界

修改目标：

- 为所有 Agent 可见 meta tools 建立统一返回分层：
  - `summary`：默认 provider decision view；
  - `invoke_ready`：调用前所需最小信息；
  - `detail`：用户明确要求查看细节；
  - `diagnostics`：管理/排障专用。
- `operation.status` 默认只返回 operation shell、status、progress、error、
  wait/resume/cancel 相关事实和最小 domain summary；完整 domain state 进入 ref
  或显式 `detail`。
- `transfer.status` 默认只返回 transfer_id、status、source/target、progress、
  resumable、error、next_action；job 详情和 runtime 详情进入 detail/ref。
- `artifact.list` 默认只返回 artifact_id、title、type、content_type、size、
  node_id、created_at、status 和可展示 download_url；metadata、sha256、blob_id
  进入 detail。
- `artifact.get` 默认返回单个 artifact 的 decision view；完整 metadata 需要
  detail。
- `artifact.present` 返回 presentation receipt 和最小 artifact cards，不把完整
  artifact metadata 作为 provider 事实重复注入。
- `context.expand` 默认必须鼓励 path-specific 使用；当 path 为 `$` 且 ref 过大时，
  返回结构摘要或要求更具体 path，避免“全量展开”成为默认动作。
- `capability.describe` 默认保持 `invoke_ready`，schema/diagnostics/sources/runtime
  只能按需 sections 返回；Agent 普通路径不得固定 describe 全量 schema。

验收标准：

- 每个 meta tool 的 catalog description 与默认返回 shape 一致。
- 每个 meta tool 都有明确默认 projection 和诊断 projection。
- Agent 普通任务中，meta tool raw result 不再随 capability 数、artifact metadata、
  operation domain object 无界膨胀。
- 需要大对象时，通过 `$ycr_ref`、path-specific expand、detail/diagnostics projection
  显式获取。

### 2.7 修空错误传播

修改目标：

- `YcrError`、HTTP client 异常、YCR service 异常统一转换为包含 `code`、
  `message`、`details` 的 runtime error。
- SSE 和前端 ToolCallCard 显示业务 message；没有业务 message 时显示 HTTP 状态、
  endpoint 和异常类型。

验收标准：

- 不再出现 `YCR request failed:` 空文本。
- capability.search、context.expand、context.search 失败均有可读原因。

## 3. 非目标

以下事项本次不做：

- 不新增 transfer 的骑墙状态。
- 不为具体 capability 写 query 同义词补丁。
- 不恢复字段名白名单/黑名单 projection。
- 不让 Agent 绕过 Center transfer domain 自行宣布传输成功。
- 不把 `node.status` 变成 capability 浏览工具。
- 不把任何 meta tool 变成管理 UI detail dump。

## 4. 推荐实施顺序

1. 用一次 Windows 截图任务验收 Tool RAG 排序、`artifact.present` 展示、YCR projection 和前端 token/YCR 侧栏。
2. Windows -> Linux yq-croc 文件传输复验已通过：Linux receive 判定、TransferSession size/hash 继承、Operation 状态和 Agent 最终结论一致。
3. 审计 meta tool 默认输出边界，重点检查真实对话中仍可能导致高 token 的 `node.status`、`capability.describe`、`context.*`、`artifact.*`、`operation.status` 和 `transfer.status`。
4. 复杂任务仍出现过度 search/describe 时，优先修 capability 合同、索引文档、working set 或 Agent prompt policy，不新增 query/capability 特判。

## 5. 完成判定

本待办完成时，必须同时满足：

- Agent 截图任务不再绕到无关 workflow capability。
- transfer.preflight 关键决策事实无需 `context.expand` 即可被 Agent 使用。
- Windows -> Linux yq-croc 传输成功时 Center Operation 显示 succeeded。（已验证通过）
- yq-croc 保留源文件 mtime 时，Linux receive 不再误报 failed。（已验证通过）
- TransferSession 能展示从 preflight 继承的 expected size/hash。（已验证通过）
- receive job input 包含 expected size/hash。（已验证通过）
- Agent 聊天结论与 Operation/TransferSession 状态一致。（已验证通过）
- `node.status` 默认输出稳定小，不随 capability source 详情膨胀。
- `operation.status`、`transfer.status`、`artifact.*`、`context.*`、
  `capability.describe` 的默认输出均符合各自职责边界。
- YCR/meta tool 失败有明确错误码和错误文本。
