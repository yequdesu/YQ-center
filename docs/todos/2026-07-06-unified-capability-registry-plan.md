# Unified Capability Registry 实施计划

状态：活跃待办
日期：2026-07-06
适用范围：YCR Tool RAG / Agent 工具面收敛

本文记录当前决策：不采用“隐藏工具 + tool lease + missing-tool 自愈”的动态工具加载路线。YeQu 将把 Center meta tools 与 Node capabilities 统一纳入 capability registry，由 `capability.search` / `capability.invoke` 作为 Agent 面向能力的稳定协议入口。

本文只负责能力目录和 Agent 工具面最终形态：Center meta tools 与 Node capabilities
统一注册、`agent_visible/invocation_surface` 分层、固定 Provider bootstrap tools 和统一
`capability.invoke`。YCR raw ContextRef / Result RAG core data path、Linux yq-croc
receive 误报失败、Provider registry 分别由对应活跃待办负责。

RAG cache 相关项已经进入实现：`query embedding cache` 与 `rerank cache` 已落地；
precompute 热路径收敛和前后台资源调度已落地；retrieval candidate cache 继续按本文待办推进。

## 0. 当前实现差距

本文定义目标态，不描述当前已完成事实。2026-07-06 当前代码中，provider 仍直接看到
`src/yequ/application/meta_tools.py` 登记的 Center meta tools，包括 `node.*`、
`capability.search/describe`、`context.*`、`artifact.*`、`operation.*` 和
`transfer.*`。`capability.invoke` 当前已经可执行具体 Node capability；本文未完成的是
把 Center meta tools 也统一注册为 capability，并最终收窄为
`capability.search` / `capability.describe` / `capability.invoke` 这三个
bootstrap protocol tools。

因此本文 T01-T16 在未显式标记完成前均视为待办；执行时必须逐项改代码、验收并
更新本表状态，不能把目标态文字当作当前现状引用。

## 1. 背景问题

当前 Agent 可见工具分成两套：

- Center meta tools：`node.list`、`capability.search`、`transfer.create`、`artifact.present`、`context.expand` 等。
- Node capabilities：Windows/Linux Node 注册的业务能力，当前通过 Center capability
  discovery、workflow 和 job dispatch 路径间接执行；最终目标是统一通过
  `capability.search` / `capability.describe` / `capability.invoke` 访问。

这导致三个问题：

1. Provider 每轮看到的 meta tools 偏多，首包上下文和工具选择复杂度上升。
2. Center workflow 工具和 Node runtime capability 没有统一合同层，`transfer.create` 与 `linux.transfer.croc.receive` 这类能力容易在检索语义上混杂。
3. 如果采用动态隐藏工具，需要额外设计 tool lease、missing-tool recovery、按轮装载等机制，工程复杂度高，且理论上仍依赖预测正确性。

当前决策是改为：

```text
Static Protocol Tools + Unified Capability Registry
```

LLM 常驻只拥有少量稳定协议工具；Center meta tools 和 Node capabilities 都作为 registry 中的 capability 被检索、描述和调用。

## 2. 目标

### 2.1 统一能力目录

所有 Agent 可调用能力都进入 capability registry：

- Center control capability：如 `node.list`、`capability.search`、`context.inspect`。
- Center workflow capability：如 `transfer.preflight`、`transfer.create`、`artifact.deploy`、`operation.cancel`。
- Node capability：如 `windows.screen.capture`、`linux.filesystem.stat`。
- Center internal capability：如 `linux.transfer.croc.receive` 这类由 Center workflow 调度、但不应直接暴露给 Agent 的底层能力。

### 2.2 固定 Provider 协议工具面

最终 Provider 默认只暴露最小 bootstrap protocol tools：

```text
capability.search
capability.describe
capability.invoke
```

所有其他 Center meta tools，包括 `node.list`、`operation.status`、`artifact.present`、`context.inspect`，都必须注册为 Center capabilities，并通过 `capability.search` / `capability.invoke` 进入 Agent 能力面。

RAG 不依托具体业务 tool call 才能存在。YCR 的 retrieval 是 Center/YCR 的内部能力解析服务，可以在 provider 调用前、`capability.search` 执行时、或前端/YCR status 查询时运行。`capability.search` 只是 Agent 访问能力目录的 bootstrap protocol tool，不是 RAG 的唯一载体。

### 2.3 Agent 不直接拥有动态业务工具

Agent 不直接看到 `screen.capture(...)`、`transfer.create(...)` 这类动态业务工具 schema。Agent 通过：

```text
capability.search -> capability.describe(optional) -> capability.invoke
```

调用能力。新增 Node / 新 capability 时，只需注册合同，不修改 Center/Agent provider 行为。

## 3. Capability 分层字段

为避免“全能力混搜导致底层实现泄漏给 Agent”，registry 必须新增或规范以下字段：

| 字段 | 含义 |
|---|---|
| `scope` | `center` 或 `node`。 |
| `plane` | `control`、`workflow`、`data`、`node_runtime`、`diagnostic`。 |
| `provider` | `center`、`windows-node`、`linux-node` 等能力提供者。 |
| `dispatch_kind` | `inline`、`node_job`、`workflow`、`operation`、`diagnostic`。 |
| `agent_visible` | 默认 `capability.search` 是否能返回给 Agent。 |
| `invocation_surface` | `agent`、`center_internal`、`diagnostic`。 |
| `workflow_kind` | `transfer`、`artifact_deploy`、`operation`、`context_ref` 等可选分类。 |
| `canonical_name` | 跨平台规范能力名。 |
| `source_id` | 具体注册源。Center 能力也要有稳定 source id。 |
| `node_id` | Node capability 所属节点；Center capability 为 null。 |
| `risk/effect` | 沿用现有策略字段。 |
| `input_schema/output_schema` | 能力调用合同。 |
| `artifact_contract` | 输入/输出 artifact 合同。 |
| `operation_contract` | 是否创建/等待/取消 Operation。 |

默认 `capability.search` 只检索：

```text
agent_visible = true
invocation_surface = agent
```

诊断模式必须显式请求才允许看到 `center_internal` 或 `diagnostic` 能力。

## 4. 去重与排序规则

### 4.1 去重 key

Center capability：

```text
scope=center + canonical_name
```

Node capability：

```text
scope=node + canonical_name + node_id
```

同一 canonical capability 有多个 source 时，按 dispatchable、runtime readiness、node online、policy 选择 preferred source。

### 4.2 排序输入

Tool RAG / lightweight retrieval 的索引文档必须来自 capability 合同：

- identity：canonical_name、aliases、registered_name。
- intent：display_name、description、agent_description、tags。
- IO：input_schema、output_schema、artifact_contract、operation_contract。
- runtime：scope、plane、dispatch_kind、provider、platform_os、execution_requirements。
- policy：risk、effect、agent_visible、invocation_surface。

不允许写具体能力的同义词白名单、黑名单或 query 特判。

### 4.3 workflow 优先级

对于同一任务，如果同时命中 Center workflow 和底层 Node runtime capability，默认返回 Agent 可见 workflow capability。

例：

```text
用户：把 Windows 文件传到 Linux
默认应返回：transfer.preflight / transfer.create / transfer.status
不应默认返回：windows.transfer.croc.send / linux.transfer.croc.receive
```

这不是工具名特判，而是由 `invocation_surface=center_internal` 和 `plane=node_runtime` 控制。

## 5. 硬性待办表

本计划不按大块里程碑描述交付，不保留长期兼容路线。以下每一项都是硬性待办；实现时按顺序逐项完成、验收、标记完成，再进入下一项。每项结束时系统必须可运行，且必须向最终形态收敛。

| 序号 | 待办 | 硬性要求 | 验收 |
|---|---|---|---|
| T01 | 定义 Unified Capability 字段 | 为 Center/Node capability 增加 `scope`、`plane`、`provider`、`dispatch_kind`、`agent_visible`、`invocation_surface`、`workflow_kind`、artifact/operation contract。 | registry 中 Center 和 Node capability 都能用同一结构表达。 |
| T02 | 注册 Center capabilities | 将全部 Center meta tools 注册为 `scope=center` capabilities。 | `node.list`、`transfer.create`、`artifact.present`、`context.inspect` 等都可被 `capability.describe` 描述。 |
| T03 | 标注内部能力边界 | 将底层 send/receive/runtime/diagnostic 能力标记为 `invocation_surface=center_internal` 或 `diagnostic`。 | 默认 `capability.search` 不返回底层 `linux.transfer.croc.receive` 这类内部能力。 |
| T04 | 统一 search 输入 | `capability.search` 默认只搜 `agent_visible=true` 且 `invocation_surface=agent` 的 Center + Node capabilities。 | 搜“传输文件”返回 Center transfer workflow capability，不返回底层 send/receive。 |
| T05 | 统一 describe | `capability.describe` 支持 Center capability 和 Node capability。 | Center workflow 和 Node runtime capability 描述 shape 一致。 |
| T06 | 完善统一 invoke dispatch | 保留当前 `capability.invoke` 的 Node capability 调用能力，并扩展到 `inline`、`workflow`、`node_job`、`operation` 的统一 dispatch。 | Agent 可通过 `capability.invoke(capability_ref="transfer.create")` 创建 transfer。 |
| T07 | 收窄 Provider bootstrap tools | Provider 默认只暴露 `capability.search`、`capability.describe`、`capability.invoke`。 | 默认 tools 数量降到 3，其他 Center tools 不再直接暴露。 |
| T08 | 前端显示 registry 命中 | 前端右侧显示本轮 registry search 命中的能力、scope、plane、surface、cache 状态。 | 用户能看到 Agent 为什么拿到某能力。 |
| T09 | RAG 层 query embedding cache（已完成） | `ycr_query_embedding_cache` 持久缓存 normalized query 的 dense/sparse embedding，供 Tool RAG 和 Context RAG 复用。 | 重复 query 不再重复调用 embedding 模型；`context.status` 暴露 hit/miss。 |
| T10 | RAG 层 rerank cache（已完成） | `ycr_rerank_cache` 持久缓存 query hash、document hashes、rerank model/version、top_n 对应的 rerank 结果。 | 重复候选集不再重复调用 reranker；`capability.search` 返回 cache hit/miss。 |
| T11 | Precompute capability index（已完成） | capability document、hash、index_text、projection、embedding、fingerprint 全部后台生成。 | `capability.search` 热路径不再生成 capability document 或 schema projection。 |
| T12 | 索引未就绪等待报告（后端已完成，前端待接入） | 索引未就绪时返回稳定状态，由前端显示并等待后台完成。 | 后端不在前台同步 precompute；`capability.search` 返回 `retrieval.index.status=not_ready`、`retryable` 和 `retry_after_seconds`；`context.status` 返回 capability index 的 indexes 与 queued/running/succeeded/failed 统计。前端显示仍待接入。 |
| T13 | 前后台资源调度（已完成） | 所有 embedding/rerank 调用进入 YCR scheduler，前台优先，后台让路。 | Tool RAG、Context RAG、capability index precompute、ref chunk indexing 均通过 scheduler；embedder 侧有 embedding/rerank 并发阀门；`context.status` 暴露 scheduler 观测指标。 |
| T14 | 精确 token accounting | 用 provider/model 对应 tokenizer 计算精确 prompt token；provider 返回 usage 后回填 actual。 | 前端不再显示 `16k/24k` 这类粗略值，显示精确 prompt/completion/total 及 per-block breakdown。 |
| T15 | Retrieval candidate cache | 缓存向量/稀疏检索候选集，key 必须包含 query embedding hash、corpus fingerprint、filters hash、retrieval algorithm version。 | 重复 query 在 corpus 未变时不重新跑完整 retrieval。 |
| T16 | 清理旧直接 meta tool 路径 | 删除不再需要的直接 meta tool provider 暴露路径，不保留 fallback/shim。 | Agent 能力调用只依赖 bootstrap tools + registry capability。 |

## 6. RAG 层缓存设计

缓存必须落在 RAG pipeline 层，不以 `tool.search -> capabilities[]` 最终业务结果作为核心缓存。最终 response assembly 每次仍 live validate node online、source dispatchable、policy、projection 和 limit。

### 6.1 Query embedding cache

缓存对象：

```text
normalized_query
+ embedding_provider
+ embedding_model
+ embedding_version
+ normalize_version
-> dense vector + sparse vector
```

适用范围：

- Tool RAG / unified capability search。
- Context RAG `context.search`。
- 其他需要 query embedding 的 YCR 检索。

失效条件：

- embedding provider/model 变化。
- embedding algorithm version 变化。
- query normalize version 变化。

已实现缓存表：

```text
ycr_query_embedding_cache
- cache_key
- normalized_query
- query_hash
- normalize_version
- embedding_provider
- embedding_model
- embedding_version
- dense_json
- sparse_json
- created_at
- last_used_at
- hit_count
```

### 6.2 Rerank cache

缓存对象：

```text
normalized_query_hash
+ rerank_model
+ rerank_version
+ ordered_document_hashes_hash
+ top_n
-> reranked document indexes + scores
```

适用范围：

- Tool RAG / unified capability search 的 cross-encoder rerank 步骤。

失效条件：

- rerank model 变化。
- rerank algorithm version 变化。
- 任一 candidate document_hash 变化。
- candidate document 顺序或 top_n 变化。

已实现缓存表：

```text
ycr_rerank_cache
- cache_key
- normalized_query_hash
- rerank_model
- rerank_version
- document_hashes_hash
- top_n
- result_json
- created_at
- last_used_at
- hit_count
```

### 6.3 Retrieval candidate cache

缓存对象：

```text
query_embedding_hash
+ corpus_fingerprint
+ filters_hash
+ retrieval_algorithm_version
-> candidate index_ids + dense/sparse/RRF scores
```

这层收益低于 query embedding cache 和 rerank cache，但 unified registry 能力数量上升后会变得有价值。

### 6.4 不缓存最终业务结论

默认不持久缓存最终 `capability.search` 结果。最终返回必须实时组装，因为以下事实会频繁变化：

- Node online / heartbeat freshness。
- capability source dispatchable。
- runtime readiness。
- policy / risk / approval。
- projection / sections / limit。

可选地只允许极短 L1 in-memory cache 用于同一请求风暴去重，但不能作为核心正确性机制。

## 7. Precompute Everything

Precompute 的边界是：凡是只依赖 capability 合同、registry 状态、schema、index version，而不依赖当前用户 query 的内容，都必须在后台预计算并持久化。前台 `capability.search` 只读取 ready index、处理 query 侧检索、做实时状态校验和 response assembly。

### 7.1 当前缺口

当前已经存在 `ycr_capability_index` 和 `ycr_capability_index_jobs`，并持久化了：

- capability search document。
- `document_hash`。
- `index_text`。
- dense embedding。
- sparse embedding。
- PostgreSQL pgvector `embedding_vector`。

但热路径仍存在以下工作：

- `capability.search` 每次从 registry 拉取 `projection=schema` candidates。
- `_load_ready_capability_indexes` 每次重新生成 `_capability_index_document(candidate)`。
- 每次重新计算 `_stable_hash(document)` 判断 index 是否 ready。
- 每次再拉一遍 return projection candidates。
- `invoke_ready` / `summary` / `schema` 返回 shape 仍由 live registry projection 生成。

这些工作不是当前用户 query 的语义计算，必须迁移到后台预计算产物。

### 7.2 预计算产物

Unified registry 的最终 search index 产物应包含：

```text
ycr_capability_search_index
- index_id
- capability_id
- source_id
- scope
- plane
- provider
- dispatch_kind
- agent_visible
- invocation_surface
- workflow_kind
- canonical_name
- node_id
- platform_os
- risk
- effect
- document_hash
- index_text
- document_json
- summary_projection_json
- invoke_ready_projection_json
- schema_projection_json
- diagnostic_projection_json
- embedding_provider
- embedding_model
- embedding_version
- embedding_json
- sparse_json
- embedding_vector
- registry_fingerprint
- corpus_fingerprint
- index_version
- status
- indexed_at
- last_error_code
- last_error_message
```

实现必须一次性朝“热路径只读 ready index”收敛。可以复用并扩展现有表，但不能留下前台临时生成文档的长期路径。

### 7.3 后台触发源

以下事件只入队后台 precompute，不在 Agent 前台请求内同步完成：

- Node `hello` / capability registration。
- Node capability source 状态变化。
- Center capability manifest 变更。
- schema / artifact contract / operation contract 变更。
- `CAPABILITY_INDEX_VERSION` 变更。
- embedding model / embedding version 变更。
- YCR 启动 reconcile。
- admin 手动 rebuild。

后台任务必须幂等：同一 `index_id + document_hash + index_version + embedding_model` 已成功时不重复计算。

### 7.4 前台热路径

目标热路径：

```text
capability.search
-> normalize query
-> query embedding cache / embed query
-> read ready capability search index
-> lexical/vector retrieval
-> rerank cache / rerank if needed
-> live validate node/source/runtime/policy
-> assemble response from precomputed projection json
```

前台不得执行：

- 完整 capability document 生成。
- capability document embedding。
- schema projection 构造。
- index repair。
- 大批量 index job。

### 7.5 索引未就绪策略

前台遇到 index 未就绪时，不允许同步 precompute，不允许静默 fallback 到粗糙结果。必须返回稳定状态，并由前端显示等待进度。

返回结构：

```json
{
  "status": "not_ready",
  "error_code": "capability_index_not_ready",
  "message": "Capability index is still being built.",
  "index": {
    "queued": 12,
    "running": 1,
    "failed": 0,
    "succeeded": 84
  },
  "retry_after_sec": 3
}
```

前端必须：

- 在 Agent/Console 中显示“YCR capability index 正在构建”。
- 显示 queued/running/failed/succeeded。
- 允许等待后自动重试或用户手动重试。
- 不把该状态伪装成工具失败或普通模型失败。

YCR status 必须包含同样信息，便于判断当前慢/不可用是 index not ready、cache miss、rerank、还是后台队列拥塞。

### 7.6 Corpus fingerprint

每次成功预计算后，更新 corpus fingerprint。fingerprint 必须能用于 RAG cache 安全失效。

建议组成：

```text
hash(
  sorted(active index_id + document_hash + index_version + embedding_model),
  registry_generation,
  center_capability_manifest_version
)
```

RAG cache 中的 dense retrieval cache / rerank cache 必须携带对应 fingerprint 或 document_hashes_hash。

## 8. 前后台资源调度

YCR 需要把所有会调用 embedding/rerank 模型的任务纳入统一调度，避免后台 precompute 或 Result RAG 抢占前台 Agent search。

### 8.1 任务优先级

定义优先级：

| 优先级 | 任务 |
|---|---|
| P0 | foreground capability.search query embedding |
| P1 | foreground capability.search rerank |
| P2 | foreground context.search query embedding |
| P3 | operation resume / active wait 所需的即时 context retrieval |
| P5 | capability index precompute |
| P6 | current session result ref indexing |
| P7 | old history / archive indexing |

P0-P3 为前台交互任务；P5-P7 为后台任务。后台任务必须让路，不允许造成前台 ReadTimeout。

### 8.2 YCR 侧 scheduler

已实现 `src/yequ/ycr/scheduler.py`：

- `scheduled_embed_text_full`
- `scheduled_embed_text`
- `scheduled_rerank_documents`
- `scheduler_status`

行为：

- 所有 `embed_text_full`、`embed_text`、`rerank_documents` 调用必须经过 scheduler。
- scheduler 输入包含 `priority`、`purpose`、`deadline_sec`、`cache_key`。
- 同一 cache key 的并发 miss 合并为一个 in-flight future。
- 后台任务分批执行，单批数量受限。
- 前台队列非空时，后台任务暂停提交新模型请求。

当前已覆盖调用点：

- `capability.search` query embedding：P0。
- `capability.search` rerank：P1。
- `context.search` query embedding：P2。
- capability index precompute：P5。
- context ref chunk indexing：P6。

### 8.3 Embedder 侧限制

本地 embedder 仍保持单进程 CPU 模型。embedder 服务内已实现：

- embedding semaphore。
- rerank semaphore。

embedder 不做业务优先级判断；优先级由 YCR scheduler 在调用 embedder 之前完成。embedder
只负责把实际模型推理并发限制在可控范围，默认 embedding/rerank 并发均为 1。

### 8.4 后台 job 运行规则

capability precompute job：

- 每批最多处理少量任务，例如 5-10 个。
- 每个任务处理前检查前台队列是否等待。
- 失败写入 `last_error_code/message`，指数退避重试。
- YCR status 暴露 queued/running/failed/succeeded 计数。

result ref indexing：

- 当前 session ref indexing 优先于历史 ref indexing。
- 大 ref chunk embedding 必须分批。
- 不允许在 tool result 写入路径同步完成全部 embedding。

### 8.5 观测指标

YCR status 和前端右侧栏应显示：

- foreground queue length。
- background queue length。
- embedding cache hit/miss。
- rerank cache hit/miss。
- precompute queued/running/failed/succeeded。
- last foreground wait ms。
- last background batch duration ms。

### 8.6 验收

- capability index rebuild 期间，前台 `capability.search` 不再被后台 embedding job 卡到超时。
- 重复 query 命中 cache 后，不调用 embedder/reranker。
- YCR status 能解释当前慢是 cache miss、rerank、index not ready 还是后台队列拥塞。
- 后台 precompute 可以在服务启动和 Node 注册后自动推进，不需要用户手动触发。

## 9. 精确 Token Accounting

当前 token 展示不得继续使用 `16k`、`24k` 这类粗略折算作为主显示。系统必须提供精确 token accounting：

- Provider request 前按 provider/model 对应 tokenizer 计算 prompt tokens。
- Provider response 后使用 provider usage 回填 actual prompt/completion/total tokens。
- 前端区分 estimated 与 actual；如果 tokenizer 不可用，必须明确标记 `estimated=true`。
- 每个 block 单独统计：
  - system/developer instructions。
  - user message。
  - assistant history。
  - tool schemas。
  - tool observations。
  - YCR refs/projections。
  - operation/context packets。
- 前端显示精确整数，不用粗略 k 值替代主数值；可以同时显示 compact 视觉格式，但 hover/detail 必须显示精确值。
- 超预算裁剪使用保守余量，并记录裁剪原因。

## 10. 非目标

本计划不做：

- 不实现 hidden tools + tool lease + missing-tool recovery 作为主路线。
- 不让 LLM 通过一个“列出元工具”的工具发现元工具。
- 不把底层 send/receive runtime capability 默认暴露给 Agent。
- 不用字段名白名单/黑名单或具体 query 同义词补丁修排序。
- 不让 Center workflow 和 Node runtime capability 在 Agent 默认视角混成一层。

## 11. 完成判定

完成后必须满足：

1. Center meta tools 和 Node capabilities 都可在统一 registry 中描述。
2. Agent 面向能力调用只依赖稳定协议工具和 `capability.invoke`。
3. `capability.search` 默认只返回 Agent 可见能力，不泄漏 center_internal 能力。
4. 新 Node capability 和新 Center workflow 都能通过注册合同进入 Agent 可用能力面。
5. Tool RAG 排序基于 capability 合同，不包含具体工具同义词补丁。
6. Provider 默认 bootstrap tools 固定为 `capability.search`、`capability.describe`、`capability.invoke`。
7. 常见能力搜索的 query embedding 和 rerank 可缓存，且 registry/capability/schema 变更会自动失效。
8. Retrieval candidate cache 以 corpus fingerprint 和 filters hash 安全失效。
9. 索引未就绪时前端显示 YCR index 状态并等待，不静默 fallback。
10. 前端 token 显示具备 provider/model 级精确值和 per-block breakdown。
