# YCR 清理、重构与文档一致性计划

状态：当前实施计划
日期：2026-07-06
适用范围：YeQu Center / Agent / YCR / Console

本文是当前 YCR 下一轮工作的实施入口。它不再把现有 YCR 实现视为可渐进修补的 MVP，而是按“先清理、再重构、最后文档收口”的三阶段推进。旧实现、临时代码、错误设计和不符合本轮决策的路径可以直接删除，不保留 fallback、compatibility 或 shim。

关联文档：

- `docs/ycr-current-state.md`：当前代码已经实现了什么，以及和目标态的差距。
- `docs/discussions/2026-07-05-ycr-capability-search-design.md`：本轮 YCR 行为决策记录。
- `docs/proposals/2026-07-04-yequ-context-router-ycr.md`：早期目标态设计，完成本计划后需要复核并吸收或归档。
- `docs/agent-sse-contract.md`：Agent SSE 前端合同，第三阶段必须更新。

## 1. 总目标

本轮目标是把 YCR 从当前“混合了 MVP 投影、字段白名单、懒索引、TTL/ref rehydrate、局部补丁”的状态，重建为一条清晰链路：

```text
tool raw result
  -> YCR raw ContextRef
  -> Agent history raw_ref shell
  -> build_turn 统一生成 provider-visible projection
  -> Provider 只接收 meta tools 和 projected observations
```

最终行为：

1. YCR 不按字段名、depth、业务语义判断投影。
2. YCR 使用局部 size-based `$ycr_ref` projection，带 bounded prefix preview。
3. YCR 不做 provider total budget hard fail，只输出 context estimate。
4. Tool raw result 不进入 Agent provider history，只进入 YCR raw ref。
5. Provider 只注入固定 meta tools，不注入具体 Node capability tools。
6. `capability.search(query)` 使用 ready index + reranker，不在搜索请求内建索引。
7. `context.search` 支持 ref 内搜索和当前 session 级 Result RAG。
8. 前端能清楚展示 YCR storage、provider projection、token estimate 和 refs。

## 2. 硬约束

1. **先清理再重构**：不在旧投影模型上继续堆逻辑。
2. **不保留兼容路径**：当前项目处于快速迭代期，旧字段、旧 endpoint、旧 prompt 和旧 tests 直接删除或重写。
3. **无静默 fallback**：embedding/reranker/search 不可用时返回明确错误，不改用字符串匹配、registry 伪结果或未 rerank 粗排。
4. **raw 存储不依赖 embedding**：embedder 挂了不能阻断普通工具结果保存、expand、tail、schema 和 build_turn projection。
5. **Provider 不接触 raw**：provider 只能看到 build_turn 生成的 projected observation。
6. **每阶段主链路可启动**：每阶段完成后 Center/YCR 主路径必须能启动，至少通过窄主路径验证。

## 3. 阶段一：清理当前不合格 YCR 代码

阶段目标：删除不符合本轮决策的旧实现，解除后续重构阻力。该阶段不追求新功能完整，只保证删除后代码边界更干净，失败更明确。

### 3.1 删除旧 projection 模型

删除或重写：

- `SUMMARY_FIELDS`
- `LARGE_FIELD_NAMES`
- `SENSITIVE_FIELD_NAMES`
- depth-based projection
- list -> `{count, sample}` 投影
- string prefix + `[...truncated by YCR...]`
- `_REF_STORE` transient ref 内存仓库
- `transient_ref_payload`
- `ensure_projected_tool_message` 的 fallback 投影语义
- `prompt_with_projected_context` 的独立字符截断策略

验收：

- `rg "SUMMARY_FIELDS|LARGE_FIELD_NAMES|SENSITIVE_FIELD_NAMES|_REF_STORE|truncated by YCR|transient_ref_payload"` 不再命中生产代码。
- projection 不再按字段名和 depth 决定保留内容。

### 3.2 删除硬预算语义

删除或改名：

- `BudgetProfile.max_input_tokens`
- `BudgetProfile.reserved_response_tokens`
- `context_budget_exceeded`
- `usable_budget`
- build_turn 中的总 token hard fail
- SSE 中暗示硬预算的 `budget` 语义

保留：

- token/size estimate 函数，但只用于观测。

验收：

- YCR 不因 provider input 估算超限而拒绝调用。
- 相关输出改为 `context_estimate`。

### 3.3 删除 TTL / rehydrate / source adapter

删除：

- `YcrContextRef.expires_at`
- `ttl_sec`
- `/v1/context/rehydrate`
- `YcrClient.rehydrate`
- `ref_store.rehydrate_ref`
- `src/yequ/ycr/source_adapter.py`
- rehydrate 相关 tests 和文档描述

保留：

- `source_type`
- `source_id`
- `source_path`

这些字段只用于审计、debug、前端 metadata 和清理关联，不再表示可重建 source。

验收：

- `rg "rehydrate|expires_at|ttl_sec|source_adapter"` 不再命中 YCR 生产路径。

### 3.4 清理搜索请求内建索引

删除：

- `MAX_INDEX_BUILDS_PER_SEARCH`
- `deferred_count`
- search 请求内 `_ensure_capability_indexes()` 调 embedder 建索引
- search 请求路径里的 index build lock/deferred 逻辑

保留临时行为：

- 阶段一可以让 `capability.search(query)` 在 index 未 ready 时返回 `capability_index_not_ready`。
- 后台 builder 在阶段二实现。

验收：

- query search 不再在用户请求路径构建 capability index。

### 3.5 清理 SQLite 关键词加分

删除：

```text
if query.lower() in chunk.text.lower():
    score += 1.0
```

验收：

- SQLite/local 和 PostgreSQL 都只使用 query embedding 与 chunk embedding 的 cosine similarity 语义。

### 3.6 清理 provider 注入具体 capability 的路径

阶段一先定位并删除明显旧路径：

- provider function list 中直接注入 `windows.*`、`linux.*`、具体 Node capability 的逻辑。
- prompt 中鼓励直接调用具体 capability tool 的文本。

如果阶段一删除后导致 capability 调用暂时不可用，必须在阶段二用 `capability.invoke` 补齐，不保留旧具体工具注入。

验收：

- provider tool schema 中只出现固定 meta tools。
- `windows.*` / `linux.*` 不作为 provider tools 出现。

## 4. 阶段二：按新决策实施 YCR 重构

阶段目标：在清理后的代码上实现新的 YCR 主链路。

### 4.1 ProjectionProfile 与 size-based `$ycr_ref`

新增 `ProjectionProfile`，替代 `BudgetProfile` 的硬预算语义。

规则：

1. 每个 source kind 有 `inline_bytes` 和 `preview_chars`。
2. 值在 `inline_bytes` 内，原样保留。
3. 值超限时，不截断、不 sample、不改结构；只考虑整体替换为 `$ycr_ref`。
4. 只有 `$ycr_ref + preview` 至少节省 25% 时才 ref 化。
5. preview 是 raw prefix，不是摘要；默认上限按以下公式：

```text
min(source_kind.preview_chars, raw_size * 0.20, inline_limit * 0.30)
```

ref object 固定格式：

```json
{
  "$ycr_ref": "ctxref_xxx",
  "kind": "context_ref",
  "value_type": "string|array|object|number|boolean|null",
  "path": "$.stdout",
  "stats": {
    "bytes": 123456,
    "chars": 50000,
    "items": 1000,
    "keys": 80
  },
  "preview": "...",
  "preview_kind": "prefix",
  "preview_complete": false,
  "available_ops": ["inspect", "expand", "tail", "search", "schema"]
}
```

### 4.2 raw_ref shell 生命周期

工具完成后：

```text
raw result -> ycr_context_refs
agent history -> tool_observation_shell
```

history shell：

```json
{
  "ycr": {
    "kind": "tool_observation_shell",
    "raw_ref": "ctxref_xxx",
    "name": "capability.invoke",
    "status": "succeeded",
    "target_node_id": "winClient"
  }
}
```

build_turn 行为：

1. 识别 tool observation shell。
2. 读取 `raw_ref` 的 raw value。
3. 按当前 `ProjectionProfile` 生成 provider-visible projected observation。
4. provider 永远看不到 shell 和 raw。

失败语义：

- raw_ref 不存在：`context_ref_not_found`，Agent fail-closed。
- projection 失败：`context_projection_failed`，Agent fail-closed。
- raw_ref 存储失败：不写 history shell，不进入下一轮 provider。

### 4.3 raw ref + shell 原子一致

工具 observation 写入必须原子化：

```text
begin transaction
  insert/update ycr_context_refs(raw)
  insert agent history shell
  insert AgentRunStep / telemetry
commit
```

任何一步失败：

```text
rollback
agent failed
```

raw ref 写入不调用 embedding，不构建 chunks embedding。chunk embedding 可以在搜索需要时或后台任务中处理。

### 4.4 Provider meta-tools-only

Provider 只注入固定 meta tools：

```text
node.list
node.status
capability.search
capability.describe
capability.invoke
context.inspect
context.expand
context.tail
context.schema
context.search
artifact.list
artifact.get
artifact.present
artifact.deploy.preflight
artifact.deploy
transfer.preflight
transfer.create
transfer.status
operation.status
operation.wait
approval.status
approval.submit
```

具体 Node capability 不再作为 provider function 注入。

`capability.invoke` 锚点优先级：

```text
source_id > node_id + capability_ref > capability_ref
```

歧义时返回：

```text
ambiguous_capability_source
```

### 4.5 capability.search 新规则

1. 必须有 query 或至少一个 filter。
2. 无条件浏览使用 `node.list` / `node.status`。
3. 无 query 有 filters：走 `registry_filter_v1`。
4. 有 query：走 ready capability index + reranker。
5. query search 不降级成 registry filter。
6. `projection=invoke_ready` 返回可直接调用的 `capability.invoke` 参数。

### 4.6 session 级 Result RAG

`context.search` 支持：

```text
ref_id present -> 搜指定 ref
ref_id absent -> 搜当前 session 所有 refs
```

返回 snippet，不返回全文：

```json
{
  "ref_id": "ctxref_abc",
  "path": "$.disks[0]",
  "score": 0.87,
  "snippet": "...",
  "source": {
    "kind": "tool_observation",
    "tool_name": "capability.invoke",
    "status": "succeeded"
  }
}
```

embedding 不可用：

```text
context_search_unavailable
```

### 4.7 后台 capability index builder

新增或等价实现：

```text
ycr_capability_index_jobs
```

Node capability 注册/更新后：

1. 计算 capability index document hash。
2. hash 变化则入队。
3. 后台 builder 调 embedder 写 `ycr_capability_index`。
4. search 请求只读 ready index，不建索引。

search 无 ready index：

```text
capability_index_not_ready
```

### 4.8 ycr-embedder 启动即加载 embedding + reranker

`ycr-embedder` 启动时加载：

```text
BAAI/bge-m3
BAAI/bge-reranker-base
```

新增：

```text
POST /v1/rerank
```

Tool RAG：

```text
BGE-M3 dense+sparse RRF top 20
  -> /v1/rerank
  -> top N
```

reranker 不可用或超时：

```text
capability_rerank_unavailable
capability_rerank_timeout
```

不返回未 rerank 的粗排结果。

### 4.9 SSE / 前端可观测

新增或规范 SSE：

```text
agent.tool_observation.stored
agent.ycr.projection
agent.ycr.error
agent.ycr.context
```

`agent.ycr.context` 使用 `context_estimate`：

```json
{
  "raw_estimated_tokens": 120000,
  "projected_estimated_tokens": 8000,
  "saved_estimated_tokens": 112000,
  "ref_count": 6,
  "preview_estimated_tokens": 400
}
```

前端工具卡片分三层：

```text
Execution
YCR Storage
Provider Projection
```

### 4.10 prompt_policy 重写

重写 Agent prompt：

1. `capability.search` 必须有 query/filter，不用于浏览。
2. 浏览用 `node.list` / `node.status`。
3. Node capability 只能通过 `capability.invoke` 执行。
4. `$ycr_ref.preview` 是 prefix preview，不是完整事实。
5. 不根据 preview 做“某信息不存在”的否定结论。
6. 需要完整内容时用 `context.expand/search/tail/schema`。
7. 检索不可用时报告基础设施问题，不换词循环 retry。

## 5. 阶段三：文档一致性审查与更新

阶段目标：重构完成后，文档系统必须和代码一致，不能继续留下目标态、旧 MVP 和当前实现互相冲突的描述。

### 5.1 更新当前权威文档

必须更新：

- `docs/ycr-current-state.md`
- `docs/agent-sse-contract.md`
- `docs/current-project-overview.md`
- `docs/node-capability-contract.md`
- `README.md` 中启动 YCR / embedder 的说明

更新内容：

1. YCR raw_ref shell 生命周期。
2. Provider meta-tools-only。
3. SSE 新事件。
4. `context_estimate` 字段。
5. session Result RAG。
6. Tool RAG 后台索引 + reranker。
7. 删除 TTL / rehydrate / source adapter。

### 5.2 收口讨论和提案文档

处理：

- `docs/discussions/2026-07-05-ycr-capability-search-design.md`
- `docs/proposals/2026-07-04-yequ-context-router-ycr.md`
- `docs/proposals/2026-07-04-ycr-design-review-and-scenario-drill.md`

规则：

1. 已实施结论吸收到当前权威文档。
2. 未实施但仍有效的内容留在 active todo。
3. 被本计划取代的内容归档或标注过期。
4. 不允许 proposal 继续声称已被当前代码满足。

### 5.3 文档索引更新

更新：

- `docs/documentation-index.md`
- `docs/todos/README.md`

确保：

1. 当前 YCR 实施计划只保留一个 active todo。
2. 旧 Tool RAG / YCR 路线图不作为当前入口。
3. archive 文档只作为历史背景。

### 5.4 文档一致性验收

执行检查：

```text
rg "context_budget_exceeded|rehydrate|ttl_sec|expires_at|SUMMARY_FIELDS|LARGE_FIELD_NAMES|SENSITIVE_FIELD_NAMES|MAX_INDEX_BUILDS_PER_SEARCH|truncated by YCR" docs src
```

验收标准：

- 生产代码不再出现旧实现关键字。
- 当前权威文档不再描述旧 YCR 行为。
- 旧 proposal 不再被文档索引标为已落地事实。

## 6. 阶段完成标准

### 阶段一完成标准

1. 旧 projection 字段白名单和截断逻辑删除。
2. TTL / rehydrate / source_adapter 删除。
3. hard budget fail 删除。
4. search 请求内建索引删除。
5. provider 不再注入具体 Node capability tools。

### 阶段二完成标准

1. 工具结果走 raw_ref shell。
2. build_turn 动态生成 provider projection。
3. `$ycr_ref + preview` 投影可用。
4. Provider 只使用 meta tools。
5. `context.search` 支持 session scope。
6. Tool RAG 使用后台 ready index + reranker。
7. YCR status 能区分 projection ready 与 retrieval degraded。
8. 前端能展示 YCR storage/projection/context estimate。

### 阶段三完成标准

1. 当前权威文档和代码一致。
2. 旧 YCR/MVP/Tool RAG 文档被吸收或归档。
3. 文档索引只指向当前有效入口。
4. 本计划完成后归档到 `docs/archive/todos/`。

## 7. 建议验证范围

每阶段只跑窄测试：

1. YCR projection unit tests。
2. raw_ref shell happy path。
3. Agent stream 一次工具调用 happy path。
4. `capability.search -> capability.invoke` happy path。
5. `context.search -> context.expand` happy path。
6. YCR embedder health/rerank smoke test。
7. Console 前端 build。

不做全量测试，除非 DB migration 或 Agent loop 改动后出现结构性失败。

## 8. 工程实现任务书

本节把前三章的阶段计划落到具体文件、接口、迁移和测试边界。实现时按本节逐项执行；如果代码现状与本节冲突，以本节为准，删除旧路径后再实现新路径。

### 8.1 代码入口清单

YCR 后端入口：

| 文件 | 当前职责 | 本轮处理 |
|---|---|---|
| `src/yequ/ycr_app.py` | YCR HTTP API。 | 删除 rehydrate/project-tool-observation 旧语义；新增 raw observation store、projection endpoints/status。 |
| `src/yequ/ycr/client.py` | Center 调 YCR 的 HTTP client。 | 删除 `rehydrate()`；新增 raw observation store、session search、rerank/status 所需 client 方法。 |
| `src/yequ/ycr/projection.py` | 当前 generic projection。 | 重写为 size-based `$ycr_ref` projection，不保留字段白名单/depth/sample/truncate。 |
| `src/yequ/ycr/budget.py` | 当前 BudgetProfile。 | 改为 `ProjectionProfile` / `ContextEstimate`，删除 hard budget。 |
| `src/yequ/ycr/ref_store.py` | ContextRef store/search。 | 删除 TTL/rehydrate/source_adapter 依赖；拆分 raw ref 写入和 embedding chunk 构建；支持 session search。 |
| `src/yequ/ycr/capability_gateway.py` | capability registry + Tool RAG。 | 删除 search 内建索引；改为 ready index + rerank；无 query/filter 校验。 |
| `src/yequ/ycr/embedding.py` | embedding HTTP client。 | 增加 rerank client；标准化 embedding/rerank 错误码。 |
| `src/yequ/ycr_embedder_app.py` | 本地 embedding 服务。 | 启动时加载 BGE-M3 + reranker；新增 `/v1/rerank`；health 暴露 ready 状态。 |
| `src/yequ/ycr/source_adapter.py` | rehydrate source 读取。 | 删除。 |

Agent/Runtime 入口：

| 文件 | 当前职责 | 本轮处理 |
|---|---|---|
| `src/yequ/agent/agent_stream.py` | Agent SSE 主循环。 | build_turn 前后发新 YCR SSE；history 写 shell；provider functions 改为 meta-tools-only。 |
| `src/yequ/agent/runtime_state.py` | tool observation collector。 | 不再 project tool result；改为 raw ref store + shell。 |
| `src/yequ/agent/prompt_policy.py` | Agent system prompt。 | 按第 15 项重写 YCR/meta-tool/ref/search 规则。 |
| `src/yequ/agent/deepseek_provider.py` | provider tool message 校验。 | 继续拒绝 raw/unprojected；接受 build_turn 输出的 projected observation。 |
| `src/yequ/api/agent_tool_catalog.py` | provider tools catalog。 | 只暴露固定 meta tools；删除具体 capability tool 注入。 |
| `src/yequ/runtime/meta_tools.py` | inline meta tool 执行。 | `capability.invoke` 稳定化；`context.search` ref_id 可选；删除 rehydrate。 |

Registry/Node 入口：

| 文件 | 当前职责 | 本轮处理 |
|---|---|---|
| `src/yequ/services/capability_registry.py` | capability registry/search/describe。 | 注册/更新后触发 index job enqueue；describe/search projection 与 meta-tools-only 对齐。 |
| `src/yequ/services/node_service.py` | Node 注册能力。 | capability snapshot 同步后 enqueue index jobs，不阻塞 YQP。 |
| `src/yequ/models/ycr.py` | YCR ORM models。 | 删除 expires_at；ledger 加 session_id；新增 index job model。 |
| `alembic/versions/*` | DB migrations。 | 新增本轮 YCR cleanup/rebuild migration。 |

前端入口：

| 路径 | 本轮处理 |
|---|---|
| `frontend` / Console Agent 页面实际目录 | 展示 `agent.tool_observation.stored`、`agent.ycr.projection`、`agent.ycr.error`、`context_estimate`。 |
| Agent tool call 卡片组件 | 改成 Execution / YCR Storage / Provider Projection 三层。 |
| Agent turn/message store | 存储 raw_ref、projection preview、context estimate。 |

### 8.2 数据库迁移明细

新增迁移 `alembic/versions/<rev>_ycr_clean_rebuild_schema.py`。

#### 8.2.1 修改 `ycr_context_refs`

删除：

```text
expires_at
```

保留并强化：

```text
ref_id unique not null
ref_type not null
source_type not null
source_id not null
source_path not null
session_id nullable in DB but required by Agent write path
value_json
metadata_json
projection_policy
projection_version
```

新增 metadata 约定：

```json
{
  "value_type": "object",
  "raw_size_bytes": 1234,
  "raw_estimated_tokens": 300,
  "preview": "...",
  "preview_complete": false,
  "created_by": "agent_tool_observation"
}
```

#### 8.2.2 修改 `ycr_context_ledgers`

新增：

```text
session_id varchar(32) index nullable
```

写入规则：

```text
Agent 路径 ledger 必须写 session_id。
非 Agent 系统路径可以为空，但本轮不新增系统 ref。
```

#### 8.2.3 新增 `ycr_capability_index_jobs`

字段：

```text
id primary key
job_id unique not null
capability_id index not null
canonical_name index not null
document_hash not null
status index not null              -- queued|running|succeeded|failed
attempt integer not null default 0
last_error_code nullable
last_error_message nullable
locked_at nullable
started_at nullable
finished_at nullable
created_at
updated_at
```

唯一约束：

```text
(capability_id, document_hash)
```

实现允许同一 capability 的旧 hash job 失败或被新 job 替代；search 只读取 `ycr_capability_index` ready 记录，不读取 job 表。

#### 8.2.4 删除/保留 pgvector 列

保留：

```text
ycr_context_chunks.embedding_vector
ycr_capability_index.embedding_vector
```

不再在 raw ref 写入事务中强制填充 `embedding_vector`。embedding_vector 可以由 search/index builder 后续写入。

### 8.3 新 YCR API 合同

#### 8.3.1 `POST /v1/context/refs`

用途：写普通 ContextRef。保留，但去掉 TTL。

Request：

```json
{
  "ref_type": "tool_observation_raw",
  "source_type": "tool_observation",
  "source_id": "call_xxx",
  "path": "$",
  "value": {},
  "summary": "Raw tool observation",
  "session_id": "sess_xxx",
  "metadata": {}
}
```

Response：

```json
{
  "ref_id": "ctxref_xxx",
  "ref_type": "tool_observation_raw",
  "source_anchor": {
    "type": "tool_observation",
    "id": "call_xxx"
  },
  "path": "$",
  "summary": "Raw tool observation",
  "available_ops": ["inspect", "expand", "tail", "schema", "search"]
}
```

#### 8.3.2 `POST /v1/tool-observations`

新增。Agent 工具完成后用这个接口原子写 raw observation ref 所需 payload；如果实现选择复用 `/v1/context/refs`，也必须保留此语义边界对应的 client 方法。

Request：

```json
{
  "session_id": "sess_xxx",
  "tool_call_id": "call_xxx",
  "name": "capability.invoke",
  "status": "succeeded",
  "target_node_id": "winClient",
  "result": {},
  "error": null
}
```

Response：

```json
{
  "raw_ref": "ctxref_xxx",
  "shell": {
    "ycr": {
      "kind": "tool_observation_shell",
      "raw_ref": "ctxref_xxx",
      "name": "capability.invoke",
      "status": "succeeded",
      "target_node_id": "winClient"
    }
  },
  "stats": {
    "bytes": 1200,
    "estimated_tokens": 300
  },
  "preview": "...",
  "preview_kind": "prefix",
  "preview_complete": false
}
```

#### 8.3.3 `POST /v1/context/build-turn`

保留 endpoint，重写内部行为。

输入 messages 允许 tool observation shell：

```json
{
  "role": "tool",
  "tool_call_id": "call_xxx",
  "content": "{\"ycr\":{\"kind\":\"tool_observation_shell\",\"raw_ref\":\"ctxref_xxx\"}}"
}
```

输出 messages 必须包含 provider-visible projected observation：

```json
{
  "role": "tool",
  "tool_call_id": "call_xxx",
  "content": "{\"name\":\"capability.invoke\",\"status\":\"succeeded\",\"result\":{},\"ycr\":{\"projected\":true,\"source_ref\":\"ctxref_xxx\"}}"
}
```

同时输出 projection events：

```json
{
  "projections": [
    {
      "tool_call_id": "call_xxx",
      "raw_ref": "ctxref_xxx",
      "projection_policy": "size_ref_projection_v1",
      "projected_estimated_tokens": 300,
      "saved_estimated_tokens": 2000,
      "ref_count": 1,
      "projection_preview": {}
    }
  ]
}
```

#### 8.3.4 `POST /v1/context/search`

Request：

```json
{
  "query": "C drive capacity",
  "ref_id": null,
  "limit": 10
}
```

YCR 从 auth/runtime context 或 Center 调用 payload 注入 `session_id`；LLM 不填写 session_id。

Response：

```json
{
  "query": "C drive capacity",
  "scope": {
    "type": "session"
  },
  "matches": [
    {
      "ref_id": "ctxref_xxx",
      "path": "$.disks[0]",
      "score": 0.87,
      "snippet": "...",
      "source": {
        "kind": "tool_observation",
        "tool_name": "capability.invoke",
        "status": "succeeded"
      }
    }
  ]
}
```

#### 8.3.5 `POST /v1/tool/search`

Request 必须满足：

```text
query 非空，或至少一个 filter 非空。
```

无条件请求返回：

```text
invalid_capability_search_request
```

有 query：

```text
ready index -> RRF coarse -> rerank -> matches
```

无 query 有 filters：

```text
registry_filter_v1
```

#### 8.3.6 删除 endpoint

删除：

```text
POST /v1/context/rehydrate
POST /v1/project/tool-observation
POST /v1/project/tool-message
```

`project/context-blocks`、`project/prompt-with-context`、resume prompt endpoints 在阶段一审查：若仍服务旧路径，删除；若被 build_turn 新路径吸收，迁移到内部函数，不作为 public YCR API 暴露。

### 8.4 新内部类型

新增或重写在 `src/yequ/ycr/projection.py` / `src/yequ/ycr/profile.py`：

```python
@dataclass(frozen=True, slots=True)
class ProjectionLimit:
    inline_bytes: int
    preview_chars: int

@dataclass(frozen=True, slots=True)
class ProjectionProfile:
    source_limits: dict[str, ProjectionLimit]
    min_ref_saving_ratio: float = 0.25

@dataclass(frozen=True, slots=True)
class ProjectionStats:
    raw_size_bytes: int
    projected_size_bytes: int
    raw_estimated_tokens: int
    projected_estimated_tokens: int
    saved_estimated_tokens: int
    ref_count: int
    preview_estimated_tokens: int
```

新增投影函数：

```python
async def project_value_for_provider(
    db: AsyncSession,
    *,
    value: object,
    session_id: str,
    source_type: str,
    source_id: str,
    source_kind: str,
    path: str = "$",
    profile: ProjectionProfile,
) -> ProjectedValue
```

该函数负责：

1. 计算 raw JSON size。
2. 判断是否 inline。
3. ref 化时直接写 DB，不使用 transient memory store。
4. 返回 projected value 和 stats。

### 8.5 Agent history shell 格式

Agent history 的 tool message content 固定为 JSON string：

```json
{
  "ycr": {
    "kind": "tool_observation_shell",
    "raw_ref": "ctxref_xxx",
    "name": "capability.invoke",
    "status": "succeeded",
    "target_node_id": "winClient",
    "projection_required": true,
    "version": 1
  }
}
```

禁止 history 写入：

```text
raw result
projected observation
partial projection
非 JSON tool content
```

Provider 层只接受 build_turn 输出的 projected observation；如果直接收到 shell，视为 Agent runtime bug，返回 `unprojected_tool_observation`。

### 8.6 `capability.invoke` 详细执行规则

`capability.invoke` input schema：

```json
{
  "type": "object",
  "properties": {
    "source_id": {"type": "string"},
    "node_id": {"type": "string"},
    "capability_ref": {"type": "string"},
    "input": {"type": "object"}
  }
}
```

解析顺序：

1. `source_id` 存在：按 `CapabilitySource.source_id` 查找 active source。
2. `node_id + capability_ref` 存在：限定 node 后解析 canonical_name/alias/source。
3. 只有 `capability_ref`：全局解析；多 source 返回 `ambiguous_capability_source`。

执行仍走 Center 标准路径：

```text
ExecutionGuard
Policy
Admission
Invocation
Job
Operation if needed
```

禁止 `capability.invoke` 直接绕过 policy/job 调 Node。

### 8.7 后台 index builder 详细任务

新增 service：

```text
src/yequ/ycr/capability_index_jobs.py
```

函数：

```python
async def enqueue_capability_index_job(db, capability_id, canonical_name) -> None
async def enqueue_all_capability_index_jobs(db) -> dict[str, int]
async def run_capability_index_worker_once(db, *, limit: int = 10) -> dict[str, int]
```

触发点：

1. `sync_capability_runtime_snapshot()` 完成 capability definition/source upsert 后 enqueue。
2. 管理端新增 rebuild endpoint 或 maintenance tool。
3. YCR startup 不自动全量 rebuild，避免启动阻塞；由脚本或 admin 命令触发。

worker 错误：

| 场景 | 错误码 |
|---|---|
| embedder 不可达 | `embedding_service_unavailable` |
| embedding model 未 ready | `embedding_model_not_ready` |
| DB 写入失败 | `capability_index_write_failed` |
| capability 已删除 | `capability_not_found` |

### 8.8 ycr-embedder 详细改动

启动阶段：

```text
FastAPI lifespan
  -> load BGE-M3
  -> load bge-reranker-base
  -> both ready
  -> healthz status ready
```

环境变量：

```text
YEQU_EMBEDDER_MODEL=BAAI/bge-m3
YEQU_YCR_RERANK_MODEL=BAAI/bge-reranker-base
YEQU_EMBEDDER_DEVICE=cpu
YEQU_EMBEDDER_USE_FP16=false
YEQU_RERANKER_MAX_CONCURRENCY=1
```

`POST /v1/rerank` request：

```json
{
  "model": "BAAI/bge-reranker-base",
  "query": "查 C 盘容量",
  "documents": [
    {"id": "capidx_1", "text": "identity: disks.list ..."}
  ],
  "top_n": 5
}
```

response：

```json
{
  "model": "BAAI/bge-reranker-base",
  "results": [
    {"id": "capidx_1", "score": 0.91}
  ]
}
```

### 8.9 SSE 实现任务

在 `agent_stream.py` 中输出：

1. `agent.tool_observation.stored`
2. `agent.ycr.projection`
3. `agent.ycr.error`
4. `agent.ycr.context`

`agent.ycr.context` 数据结构：

```json
{
  "kind": "provider_context",
  "phase": "provider_input",
  "packet_id": "ctxpkt_xxx",
  "provider_name": "deepseek",
  "model": "deepseek-chat",
  "context_estimate": {
    "raw_estimated_tokens": 120000,
    "projected_estimated_tokens": 8000,
    "saved_estimated_tokens": 112000,
    "ref_count": 6,
    "preview_estimated_tokens": 400
  }
}
```

禁止继续输出暗示 hard budget 的字段作为主语义。

### 8.10 前端实现任务

定位 Console Agent 页面实际目录后执行：

1. SSE event parser 增加 `agent.tool_observation.stored`、`agent.ycr.projection`、`agent.ycr.error`。
2. Turn/message model 增加：

```text
contextEstimate
toolObservationStorage[]
ycrProjections[]
ycrErrors[]
```

3. 消息气泡下方显示：

```text
up estimated / down estimated or actual / saved estimated / refs
```

4. Tool card 增加三层：

```text
Execution
YCR Storage
Provider Projection
```

5. YCR panel 增加：

```text
raw_ref
preview
projection preview
expand/search/tail/schema action
```

6. 前端 build 必须产出构建产物。

### 8.11 错误码清单

新增/规范：

| 错误码 | 触发 |
|---|---|
| `invalid_capability_search_request` | capability.search 无 query 且无 filter。 |
| `capability_index_not_ready` | query search 没有 ready index。 |
| `capability_rag_unavailable` | Tool RAG embedding 检索不可用。 |
| `capability_rerank_unavailable` | reranker 不可用。 |
| `capability_rerank_timeout` | reranker 超时。 |
| `context_search_unavailable` | Result RAG embedding 检索不可用。 |
| `context_ref_not_found` | raw_ref/ref_id 不存在。 |
| `context_projection_failed` | build_turn 投影失败。 |
| `raw_ref_store_failed` | raw observation ref 写入失败。 |
| `raw_result_too_large` | raw value 超 DB 入库上限。 |
| `ambiguous_capability_source` | capability.invoke source 解析歧义。 |
| `capability_source_unavailable` | source 不 active/不可调度。 |

### 8.12 raw result 入库上限

新增配置：

```text
YEQU_YCR_MAX_RAW_REF_BYTES=104857600
```

默认 100 MB。

行为：

```text
raw JSON bytes > max_raw_ref_bytes
  -> raw_result_too_large
  -> 不写 ref
  -> 不写 history shell
  -> Agent failed
```

后续超大结果通过 artifact 化处理，本轮不把 artifact 化作为 blocking scope。

## 9. 阶段提交边界

为避免一个提交混入过多变更，按以下提交边界执行。

### Commit A：YCR cleanup schema and old path removal

内容：

1. migration 删除 TTL、增加 ledger session_id、增加 index job 表。
2. 删除 rehydrate/source_adapter。
3. 删除 hard budget fail。
4. 删除旧 projection 常量和 transient ref store。

验证：

```text
python -m pytest tests/test_ycr_context_router.py -q
```

测试不通过时，优先更新测试到新合同，不保留旧合同。

### Commit B：ProjectionProfile and `$ycr_ref`

内容：

1. 新 projection profile。
2. size-based `$ycr_ref + preview`。
3. context refs 写入不依赖 embedding。
4. context.expand/inspect/tail/schema 更新。

验证：

```text
python -m pytest tests/test_ycr_context_router.py -q
```

### Commit C：raw_ref shell Agent lifecycle

内容：

1. tool observation raw ref store。
2. Agent history shell。
3. build_turn shell -> projection。
4. fail-closed 错误。

验证：

```text
python -m pytest tests/test_agent_runtime_state.py tests/test_ycr_context_router.py -q
```

### Commit D：meta-tools-only and capability.invoke

内容：

1. provider functions 只保留 meta tools。
2. `capability.invoke` source_id/node_id+capability_ref。
3. capability.search query/filter 校验。
4. prompt_policy 重写。

验证：

```text
python -m pytest tests -k "capability or agent" -q
```

如果范围太大，先跑新增窄测。

### Commit E：Result RAG session search

内容：

1. `context.search` ref_id optional。
2. session refs search。
3. SQLite/PostgreSQL 检索语义一致。

验证：

```text
python -m pytest tests/test_ycr_context_router.py -q
```

### Commit F：capability index jobs and reranker

内容：

1. index job model/migration/service。
2. node capability sync enqueue。
3. search only reads ready index。
4. embedder startup load + `/v1/rerank`。
5. Tool RAG rerank.

验证：

```text
python -m pytest tests/test_ycr_tool_rag_eval.py -q
curl /healthz for ycr-embedder when manually testing
```

### Commit G：SSE and Console

内容：

1. new SSE events。
2. frontend YCR panel/tool card/message estimate。
3. frontend build output。

验证：

```text
frontend build command
manual Agent one-turn smoke test
```

### Commit H：docs consistency

内容：

1. 更新当前权威文档。
2. 归档或降级旧 proposal/discussion。
3. 更新索引。
4. 本计划标记完成后归档。

验证：

```text
rg "context_budget_exceeded|rehydrate|ttl_sec|expires_at|SUMMARY_FIELDS|LARGE_FIELD_NAMES|SENSITIVE_FIELD_NAMES|MAX_INDEX_BUILDS_PER_SEARCH|truncated by YCR" docs src
```

## 10. 实施期间禁止事项

1. 禁止新增 mock embedder、hash embedder 或正则搜索替代真实检索。
2. 禁止 provider 接收具体 Node capability tools。
3. 禁止 tool raw result 写入 Agent provider history。
4. 禁止 projection 失败后把 raw 或 shell 传给 provider。
5. 禁止 `context.search` embedding 不可用时做字符串 fallback。
6. 禁止 search 请求路径构建 capability index。
7. 禁止继续增加字段名白名单。
8. 禁止在前端只展示 raw_ref 而不提供 preview 和 YCR 状态。

## 11. 开工顺序

正式实现从 Commit A 开始。每完成一个 commit 边界，先确认：

```text
git status
窄测试
启动脚本是否还可运行
主要错误码是否按新合同返回
```

然后再进入下一个 commit 边界。不要跨阶段同时改 projection、Agent lifecycle、Tool RAG 和前端。
