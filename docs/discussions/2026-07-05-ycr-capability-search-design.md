# YCR capability.search 设计讨论

状态：讨论中
日期：2026-07-05
关联设计：`docs/proposals/2026-07-04-yequ-context-router-ycr.md`

## 背景

YCR 的目标是让进入 Agent 的信息是有效信息，通过筛选或压缩控制上下文质量。本文聚焦 `capability.search` meta tool 的设计健壮性问题。

当前实现有两条路径：

- 无 query → 结构化过滤（registry_filter_v1）
- 有 query → Tool RAG（tool_rag_bge_m3_rrf_v1）

## 问题清单

### P1：capability.search 的 limit 和返回策略

**结论**：去掉 limit，直接返回全部匹配的 capability。

每个 capability summary 采用纯 size-based 投影（与 P5 一致）：值 <= 阈值原样保留，值 > 阈值整个替换成 ref（不截断）。短值字段（`canonical_name`、`risk`、`effect`、`source_count`、`capability_id`）基本不会超阈值，Agent 始终能看到"这是什么能力、能不能调、风险多大"，足够做初步筛选。需要细节时再 expand。

token 估算：50 个 capability，每个 ~100 token = 5000 token，占 64K context window 的 8%，可接受。capability 数量到几百个时再考虑分页。

### P2：有 query 时 RAG 流程的鲁棒性

**现状**：`capability_gateway.py` 中三个硬编码常量：

```python
TOOL_RAG_CANDIDATE_LIMIT = 500
RETRIEVAL_TOP_K = 50
RRF_RELATIVE_SCORE_FLOOR = 0.70
```

**问题**：
1. 0.7 相对分数阈值过滤后可能返回 0 条，前面的候选拉取和索引构建全部浪费。
2. 没有降级策略。embedding 不可用时直接抛错，0.7 过滤为空时返回空列表，都没有 fallback。
3. 500 候选拉取 + 逐个 embedding（每次最多 12 个）在 capability 数量增长后可能成为性能瓶颈。

**方案：两级降级策略**

```python
ranked = _rrf_fusion(dense_rows, sparse_rows, top_k=RETRIEVAL_TOP_K)

# 第一轮：相对分数过滤
filtered = _apply_relative_score_floor(ranked, ratio=0.70)

if not filtered:
    # 第二轮：放宽到 dense-only top 1
    if dense_rows:
        best_dense = max(dense_rows, key=lambda x: x[1])
        if best_dense[1] >= DENSE_ONLY_MIN_SCORE:
            filtered = [(best_dense[0], best_dense[1], {"dense_rank": 1, "fallback": "dense_only"})]

# 没结果就不返回，不降级到结构化过滤
# embedding 不可用 → 直接报错 capability_rag_unavailable
```

核心原则：
- 结构化过滤（registry_filter_v1）只在无 query 时使用，不作为 RAG 的降级路径。
- RAG 挂了就挂了，报错 `capability_rag_unavailable`，不返回误导性的结构化结果。
- `retrieval.fallback` 字段让调用方知道发生了降级。

### P3：capability.search 返回后再做 YCR projection

**结论**：不应该截断。capability.search 的 summary 返回已经是 Agent 决策的最小信息集，截断任何字段都会导致 Agent 无法选择，反而需要额外 round trip 补信息，浪费 token。

### P5：YCR projection 硬编码字段集合

**现状**：`projection.py` 中维护三个硬编码字段集合：`SUMMARY_FIELDS`（50+ 保留字段）、`LARGE_FIELD_NAMES`（17 个大字段）、`SENSITIVE_FIELD_NAMES`（8 个敏感字段）。新 capability 注册后，其输出字段如果不在这些集合中，投影行为不可预测。

**结论**：去掉 `SUMMARY_FIELDS`、`LARGE_FIELD_NAMES`、`SENSITIVE_FIELD_NAMES`。投影规则简化为纯 size-based：

- 所有字段一视同仁
- 值短 → 保留
- 值长 → ref 化
- 不看字段名，不做业务语义判断

`SENSITIVE_FIELD_NAMES`（password、token 等）如需保留，改为由 capability manifest 声明，而不是 YCR 硬编码。

### 补充：Ref 系统无断裂

**澄清**：`project_tool_observation` 生成的 transient ref 会在 YCR HTTP endpoint 层通过 `transient_ref_payload` + `upsert_ref` 落库到 `ycr_context_refs` 表（`ycr_app.py:355-383`）。后续 `context.expand` 查数据库能找到。只要走 YCR HTTP 服务（当前唯一形态），ref 系统端到端可用。

### P4：capability.search 与 node.list 职责区分

**结论**：`capability.search` 必须传 query 或至少一个 filter，不允许无条件调用。纯浏览系统能力用 `node.list`（meta tool，返回节点列表及各节点 capability 数量）。

- `capability.search` — 带条件搜索 Node capabilities，用于"找到特定能力"
- `node.list` — 浏览系统概况，用于"看看有什么节点和能力"

prompt 中需明确区分两者职责，提醒 LLM 不要用 search 当 list 用。

---

## 扩展问题清单

### P6：projection.py 阈值硬编码

**结论**：阈值由 YCR 界定，不是 Node。按数据类型设不同阈值：

- tool result — 结构不可预测，阈值可较大
- operation observation — 中等大小，有领域结构
- capability context — 结构固定，通常很小
- context blocks — Operation 恢复上下文
- agent run resume — checkpoint 历史
- messages — 消息历史

不按字段名区分，按数据类型区分。具体阈值在实现时确定。

### P7：context_packet.py 的 max_caps = 48

**结论**：去掉 `max_caps=48`。每个 capability summary 按 size-based 投影：description 超阈值的变 ref，短的保留。100 个 capability 的 description 都短时约 5000-10000 token；description 长时被 ref 化后每个回到 50-100 token，总量仍在 5000-10000 token。size-based 投影自然控制了总量，不需要额外数量限制。

### P8+P9：投影操作只能由 YCR 负责

**结论**：投影只在 YCR `build_turn` 一处执行，逻辑不分散。

- `runtime_state.py` — 只记录 raw result，不做投影
- `context_packet.py` — 不再检查 `_is_projected_tool_observation`，由 build_turn 自己处理
- `agent_stream.py` — 不关心投影逻辑，只消费 build_turn 返回的 context 投影流程：

```
raw result（含大值）
  → build_turn 调投影
  → 大值替换成 ref_id，小值保留
  → 大值写入 DB: ycr_context_refs[ref_id].value_json = 原始值
  → 投影后的 observation（小值 + ref_id）写入 history
  → raw result 丢弃
```

投影只做一件事：值超阈值 → 整个替换成 ref，其余原样保留。不做字段名过滤、不做截断、不做敏感字段移除。加 `ycr.projected=true` 元数据标记。

信息不丢失：小值在 history 里，大值在 DB 里，ref_id 是两者之间的指针。

### P10：BudgetProfile 不感知模型

**现状**：provider 和 model 硬编码为 deepseek，预算默认 24000 input / 4000 response。

```python
def budget_profile_from_settings(settings):
    return BudgetProfile(provider="deepseek", model=settings.deepseek_model, ...)
```

**问题**：切换模型时预算不会自动调整。设计文档提到的 `ModelBudgetProfile` 未实现。

### P11：Ref TTL 全局 86400 秒，无按类型区分

**结论**：去掉 TTL，ref 持久化，跟着 session 走。session 删了 ref 才删。Operation resume 跨天场景不受影响，只要 session 没删 ref 就还在。

### P12：embedding 系统无备选方案

**结论**：embedding 不可用时直接报错，不做降级。capability RAG 报 `capability_rag_unavailable`，context.search 报 `context_search_unavailable`。embedding 是基础设施，挂了就修。

### P13：ref_store.search_ref 中 SQLite 和 PostgreSQL 检索行为不一致

**结论**：统一两条路径的检索逻辑，去掉 SQLite 的关键词加分（`score += 1.0`），与 PostgreSQL 的 pgvector cosine distance 行为一致。

### P14：capability index 并发控制不完整

**结论**：去掉搜索时懒重建，改为后台进程主动重建。

触发时机：
- Node 注册/更新 capability → 计算新 document_hash，和现有索引对比，hash 不同才排队重建，hash 相同跳过
- Node 下线 → 索引标记 inactive 或删除
- 新 Node 加入 → 为该 Node 的所有 capability 生成索引

防风暴机制：hash 对比避免无变化的重复重建；批量注册时限流排队。`MAX_INDEX_BUILDS_PER_SEARCH` 和 deferred 机制去掉。

### P15：_METRICS 只增不减，无暴露接口

**结论**：YCR health endpoint 暴露基础 metrics，至少包含投影次数、ref 创建数、当前 transient ref 数量。

### P16：prompt_policy.py 中 YCR 指令过时

**结论**：重写第 17 条，新增第 18 条：

```
"17. Tool results are YCR-projected. Large values are replaced with
a ref_id instead of being inlined. If a tool result contains a ref_id,
the full value is stored externally and can be retrieved with
context.expand(ref_id). Use context.expand when you need to inspect
a specific ref. Use context.tail(ref_id) for logs or event streams.
Use context.search(ref_id, query) to search within a large result.
Do not call context tools unless a required fact is absent from the
inline fields.

18. capability.search requires a query or at least one filter. Do not
call it without conditions. Use node.list to browse available nodes
and capability counts. Use capability.search only when you need to
find a specific capability."
```

### P17：source_adapter 和 rehydrate_ref

**结论**：新设计下不再需要。P11 已决定 ref 持久化跟着 session 走，不会过期，不存在"过期后重建"的场景。source_adapter 和 rehydrate_ref 可以去掉。

## 决策记录

| 编号 | 问题 | 决策 | 日期 |
|---|---|---|---|
| P1 | limit=5 硬编码 | 去掉 limit，返回全部匹配 capability，每个 summary 采用纯 size-based 投影（值>阈值整个替换为 ref，不截断） | 2026-07-05 |
| P2 | RAG 阈值鲁棒性 | 两级降级：0.7 过滤 → dense-only top 1；不降级到结构化过滤；embedding 不可用直接报错 | 2026-07-05 |
| P3 | YCR projection 截断 capability.search | 不截断。summary 已是最小决策信息集，截断导致额外 round trip | 2026-07-05 |
| P4 | 无 query 无 filter 的定位 | capability.search 必须传 query 或 filter，不允许无条件调用；浏览用 node.list；prompt 中明确区分 | 2026-07-05 |
| P5 | YCR projection 硬编码字段集合 | 去掉 SUMMARY_FIELDS/LARGE_FIELD_NAMES/SENSITIVE_FIELD_NAMES，改为纯 size-based 一视同仁投影 | 2026-07-05 |
| P6 | projection.py 阈值硬编码 | 阈值由 YCR 界定，按数据类型设不同阈值（tool result、operation observation、capability context 等），不按字段名区分 | 2026-07-05 |
| P7 | max_caps=48 按数量截断 | 去掉 max_caps，按 size-based 投影，整体超阈值时整个 list 替换成 ref | 2026-07-05 |
| P8 | build_turn 不处理 tool observations | 投影只在 YCR build_turn 一处执行，runtime_state 只记录 raw result，投影后 raw 丢弃，大值存 DB ref_store | 2026-07-05 |
| P9 | project_tool_observation 两处执行 | 同 P8，投影逻辑收拢到 YCR，不再分散 | 2026-07-05 |
| P10 | BudgetProfile 不感知模型 | 待讨论 | |
| P11 | Ref TTL 全局统一 | 去掉 TTL，ref 持久化，跟着 session 走，session 删了 ref 才删 | 2026-07-05 |
| P12 | embedding 无备选方案 | 不降级，直接报错。capability RAG 报 capability_rag_unavailable，context.search 报 context_search_unavailable | 2026-07-05 |
| P13 | SQLite/PostgreSQL 检索行为不一致 | 统一检索逻辑，去掉 SQLite 关键词加分，与 pgvector 行为一致 | 2026-07-05 |
| P14 | capability index 并发控制不完整 | 去掉懒重建，改为后台主动重建；hash 对比检测变化，变了才重建；批量注册时限流排队 | 2026-07-05 |
| P15 | _METRICS 无暴露接口 | YCR health endpoint 暴露基础 metrics（投影次数、ref 创建数、transient ref 数量） | 2026-07-05 |
| P16 | prompt_policy YCR 指令过时 | 重写第 17 条（refs 使用方式），新增第 18 条（capability.search 必须传条件，浏览用 node.list） | 2026-07-05 |
| P17 | source_adapter 和 rehydrate_ref | 去掉。ref 持久化跟着 session 走，不会过期，不需要重建 | 2026-07-05 |
| Q1 | projection + ref 写入事务性 | 同一事务，全部完成或整体 fail-closed，不返回半成品 | 2026-07-05 |
| Q2 | provider 工具 schema 绕过 YCR | provider 只注入 meta tools，不注入 capability 工具定义，Agent 通过 capability.describe 按需获取 schema | 2026-07-05 |
| Q3 | Result RAG 只能搜单个 ref | ref_id 改为可选，不传时搜当前 session 所有 ref；返回 snippet + ref_id，不返回全文 | 2026-07-05 |
| Q4 | 无 reranker | 加 cross-encoder reranker，RRF 粗排 top K 后二阶段精排 | 2026-07-05 |
| Q5 | trust_level 字段去留 | 去掉，当前阶段过度设计 | 2026-07-05 |
| Q6 | 权限边界 | 当前不管，单 Agent 单 session 无实际风险，多 Agent 场景后续再加 | 2026-07-05 |
| R1 | _project_dict depth 规则 | 去掉 depth 规则和 SUMMARY_FIELDS，纯 size-based | 2026-07-05 |
| R2 | _project_list list→dict 转换 | 超阈值整个 list 替换成 ref，不改数据结构 | 2026-07-05 |
| R3 | _project_string 截断 | 超阈值整个 string 替换成 ref，不截断 | 2026-07-05 |
| R4 | ensure_projected_tool_message fallback | 去掉，未投影内容直接报错 | 2026-07-05 |
| R5 | prompt_with_projected_context 独立截断 | 统一为 size-based 投影规则 | 2026-07-05 |
| R6 | _make_ref 内存+DB 双写 | 去掉内存 transient store，ref 直接写 DB | 2026-07-05 |
| R7 | resume prompt trust_level 引用 | 去掉 trust_level 相关文本 | 2026-07-05 |

---

## 补充问题清单（来自历史分析报告）

### Q1：projection + ref 写入的事务性

**结论**：投影和 ref 写入在同一个事务里，要么全部完成，要么整体 fail-closed。投影逻辑本身是纯内存操作不会失败，但大值写入 ref_store（DB）可能失败。如果写入失败，不能返回有 ref_id 但没值的半成品。DB 挂了整个系统不可用，不需要投影层特殊处理。

### Q2：provider 工具 schema 绕过 YCR

**结论**：provider 只注入 meta tools（capability.search、capability.describe、capability.invoke、context.expand 等），不注入 capability 工具定义。Agent 通过 meta tools 按需发现和调用 capability。工具 schema token 从 N 个 capability × 每个几百 token，变成十几个 meta tools × 每个几十 token。

### Q3：Result RAG 只能搜单个 ref

**结论**：`context.search` 的 `ref_id` 改为可选参数。传了就搜指定 ref，不传就搜当前 session 所有 ref 的 chunks。返回 snippet（匹配片段 + ref_id + path + score），不返回全文。Agent 看 snippet 判断相关性，再用 `context.expand` 取全文。Agent 始终在一个 session 内，不需要显式传 session_id。

### Q4：无 reranker

**结论**：加 reranker。RRF 粗排 top K 后，用 cross-encoder reranker 做二阶段精排。当前已出现 search 准确率低的问题，RRF 粗排不能精确理解意图，reranker 能显著提升召回精度。

### Q5：trust_level 字段去留

**结论**：去掉。当前阶段过度设计，trust_level 只是 metadata 字段，prompt 中没有使用它的指令，LLM 看不到。防 prompt injection 的需求后续再考虑。

### Q6：权限边界（actor/session scope）

**结论**：当前不管。单 Agent 单 session，不存在跨 session 访问的实际风险。多 Agent / SubAgent 场景后续再加 scope 校验。

---

## 代码与决策不一致项（按已有决策修复）

### R1：`_project_dict` 仍有 depth 规则

**现状**：`depth < 2` 递归处理所有字段，`depth >= 2` 只保留 `SUMMARY_FIELDS`，其余 omitted。

**修复**：去掉 depth 规则和 SUMMARY_FIELDS 检查。所有字段一视同仁，递归处理，大值 ref 化。

### R2：`_project_list` 把 list 转成 dict

**现状**：列表超阈值时转成 `{count, sample}` 结构，不是整个替换成 ref。

**修复**：列表超阈值时整个值替换成 ref，不改数据结构。

### R3：`_project_string` 仍然截断

**现状**：字符串超阈值时截断 + `[...truncated by YCR...]`。

**修复**：字符串超阈值时整个值替换成 ref，不截断。

### R4：`ensure_projected_tool_message` fallback 投影

**现状**：`deepseek_provider.py` 中对未投影的 tool content 做 fallback 投影。

**修复**：去掉 fallback，未投影内容直接报错。投影只在 build_turn 执行。

### R5：`prompt_with_projected_context` 独立截断逻辑

**现状**：context blocks 超 `max_prompt_block_chars` 时替换为 summary + ref。

**修复**：统一为 size-based 投影规则，不单独截断。

### R6：`_make_ref` 内存 + DB 双写

**现状**：`_make_ref` 先存内存 `_REF_STORE`，HTTP endpoint 再落库。

**修复**：投影在 build_turn 执行，ref 直接写 DB，去掉内存 transient store。

### R7：resume prompt 硬编码 trust_level 引用

**现状**：`agent_run_resume_prompt` 和 `operation_resume_prompt` 包含 `untrusted_external_content` 引用。

**修复**：去掉 trust_level 相关文本，Q5 已决定去掉 trust_level。
