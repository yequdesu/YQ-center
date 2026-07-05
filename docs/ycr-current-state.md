# YCR 当前实现现状

状态：当前权威实现说明
更新时间：2026-07-06

本文只描述当前代码已经落地的 YeQu Context Router（YCR）行为。早期设计提案和历史计划只能作为背景；当它们与本文冲突时，以本文和代码为准。

## 1. 当前结论

YCR 已经进入 Agent 主链路，定位是 **provider 前置上下文边界**，不是 Center 调度器，也不是 Node 运行时。当前链路为：

```text
Agent Runtime
  -> YcrClient HTTP
  -> YCR service
  -> raw ContextRef / build-turn / Tool RAG / Result RAG
  -> provider-visible projected messages
  -> LLM Provider
```

当前实现采用三条硬规则：

1. Tool raw result 只进入 YCR raw ContextRef，不直接进入 provider history。
2. Agent history 中只保存 `tool_observation_shell`，provider 调用前由 `build-turn` 统一展开成 projected observation。
3. YCR 不再按字段名、业务语义或 depth 做投影；只按 size-based `$ycr_ref` 规则保留 bounded preview。

## 2. 运行构件

| 构件 | 入口 | 默认端口 | 职责 |
|---|---|---:|---|
| Center | `scripts/start-center.sh` / `python -m yequ.main` | 9800 | Agent、YQP、调度、DB、SSE、业务事实。 |
| YCR service | `scripts/start-ycr.sh` / `yequ.ycr_app:app` | 9810 | ContextRef、build-turn、Tool RAG、Result RAG、投影、YCR API。 |
| YCR embedder | `scripts/start-ycr-embedder.sh` / `yequ.ycr_embedder_app:app` | 9820 | 启动即加载 BGE-M3 embedding 与 reranker，提供 OpenAI-compatible embedding/rerank API。 |

关键配置：

| 配置 | 含义 |
|---|---|
| `YEQU_YCR_BACKEND` | 当前主路径为 `http`。 |
| `YEQU_YCR_BASE_URL` | Center 调用 YCR 的内部地址，默认 `http://127.0.0.1:9810`。 |
| `YEQU_YCR_SERVICE_TOKEN` | Center/YCR 内部 Bearer token。 |
| `YEQU_YCR_TIMEOUT_SEC` | YCR HTTP 与 embedding/rerank 请求超时。 |
| `YEQU_YCR_EMBEDDING_PROVIDER` | 当前为 `openai_compatible`。 |
| `YEQU_YCR_EMBEDDING_BASE_URL` | 本地 embedder 地址，例如 `http://127.0.0.1:9820`。 |
| `YEQU_YCR_EMBEDDING_MODEL` | 默认 `BAAI/bge-m3`。 |
| `YEQU_YCR_RERANK_MODEL` | 默认 `BAAI/bge-reranker-base`。 |
| `YEQU_YCR_PROJECTION_INLINE_BYTES` | 默认投影 inline 上限。 |
| `YEQU_YCR_PROJECTION_PREVIEW_CHARS` | `$ycr_ref` preview 字符数。 |
| `YEQU_YCR_MAX_RAW_REF_BYTES` | 单个 raw ref 最大写入大小。 |

## 3. YCR HTTP API

| 接口 | 行为 |
|---|---|
| `GET /healthz` | 无鉴权健康检查。 |
| `GET /v1/context/status` | 返回 YCR 模式、向量后端、embedding/rerank 模型、投影配置和能力列表。 |
| `POST /v1/context/refs` | 写入 raw ContextRef，提交后异步索引 ref chunks。 |
| `POST /v1/tool-observations` | 保存 tool raw result，返回 `raw_ref` 和 provider history 可保存的 shell。 |
| `POST /v1/context/build-turn` | 将 Agent history 中的 shell 统一转换为 provider-visible projected messages。 |
| `POST /v1/context/inspect` | 返回 ref metadata、schema 和 preview。 |
| `POST /v1/context/expand` | 展开 ref 指定 path。 |
| `POST /v1/context/tail` | 返回 ref 指定 path 的 tail。 |
| `POST /v1/context/schema` | 返回 ref 指定 path 的 schema summary。 |
| `POST /v1/context/search` | 支持 `ref_id + query` 的 ref 内搜索，或 `session_id + query` 的当前 session Result RAG。 |
| `POST /v1/context/index` | 手动触发指定 ref chunk 索引。 |
| `POST /v1/tool/search` | capability discovery；无 query 时必须有结构化过滤条件，有 query 时使用 ready index + reranker。 |
| `POST /v1/tool/describe` | 读取 registry 中的 capability detail。 |
| `POST /v1/tool/index/run` | 手动执行 capability index job，便于部署和调试。 |

已删除旧 public 入口：`/v1/project/tool-observation`、`/v1/project/tool-message`、`/v1/project/context-blocks`、`/v1/project/prompt-with-context`、`/v1/project/operation-resume-prompt`、`/v1/project/agent-run-resume-prompt`、`/v1/context/rehydrate`。

Operation resume、AgentRun resume 和 prompt/context 投影仍由 `src/yequ/ycr/projection.py` 提供内部函数，但不再作为 YCR public HTTP API 暴露。

## 4. Agent 主链路

每轮 provider 调用前，Agent 调用 YCR `build-turn`。YCR 返回：

- `provider_context.messages`：provider 实际接收的 messages；
- `provider_context.capability_context`：provider 可见的上下文；
- `context_estimate`：输入 token、tool schema、message、capability context 和 refs 的估算；
- `projections`：每个 shell 转成 provider observation 的投影记录；
- `refs`：本轮引用到的 ContextRef。

如果 YCR 不可用、shell 找不到 raw ref、projection 失败或 ref 丢失，Agent fail-closed，返回稳定错误，不把 raw result 直接送入 provider。

工具结果完成后，Agent 调用 `/v1/tool-observations`。成功后：

1. YCR 写入 raw ContextRef；
2. YCR 返回 `tool_observation_shell`；
3. Agent history 只保存 shell；
4. YCR 异步索引 raw ref chunks；
5. 下一轮 `build-turn` 根据 shell 读取 raw ref 并生成 projected observation。

## 5. 投影规则

投影实现位于 `src/yequ/ycr/projection.py`。当前规则是确定性的 size-based projection：

1. 值序列化后不超过对应 inline bytes 时原样内联。
2. 超过 inline bytes 时替换为 `$ycr_ref` 对象。
3. `$ycr_ref` 包含 `ref_id`、`path`、`summary`、`preview`、`raw_size_bytes`、`projected_size_bytes` 等元信息。
4. preview 使用 bounded prefix，不能接近完整 raw 大小。
5. 不使用字段名白名单、敏感字段黑名单、业务字段硬编码、depth-based sample 或字符串 `[...truncated by YCR...]`。

YCR 不再设置 provider 总 token hard limit；它只输出 `context_estimate`，由前端和日志用于观测。

## 6. Tool RAG

Tool RAG 位于 `src/yequ/ycr/capability_gateway.py` 与 `src/yequ/ycr/capability_index_jobs.py`。

行为：

1. Capability 注册或更新后，Center 只入队 `YcrCapabilityIndexJob`。
2. YCR service 后台 worker 消费 index job，调用 embedder 写入 `ycr_capability_index`。
3. `capability.search(query)` 只读取 ready index，不在搜索请求内临时构建索引。
4. 搜索流程为 BGE-M3 dense/sparse 召回，然后调用 reranker 二阶段排序。
5. embedding 或 reranker 不可用时返回明确错误，不 fallback 到字符串相似度或 registry 伪结果。
6. 无 query 时只能做 registry list/filter，且必须有结构化过滤条件；它不是语义检索。

Provider 只直接看到固定 Center meta tools。具体 Node capability 通过 `capability.search`、`capability.describe`、`capability.invoke` 发现和调用。

## 7. Result RAG

Result RAG 位于 `src/yequ/ycr/ref_store.py`。

当前支持：

- `ref_id + query`：在指定 raw ref 的 chunks 内检索；
- `session_id + query`：在当前 Agent session 已保存并已索引的 tool result refs 中检索。

Raw ref 写入不依赖 embedding；索引失败只影响 `context.search`，不影响保存、inspect、expand、tail、schema 或 build-turn projection。

## 8. SSE 与前端展示

当前 Agent SSE 中与 YCR 相关的事件：

| 事件 | 含义 |
|---|---|
| `agent.ycr.context` | provider input/output 的 context estimate、refs 和 packet metadata。 |
| `agent.tool_observation.stored` | tool raw result 已保存为 raw ref，并返回 shell。 |
| `agent.ycr.projection` | build-turn 已把某个 shell 转换为 provider-visible projection。 |
| `agent.ycr.error` | YCR fail-closed 错误。 |

Console 当前展示：

- 侧边栏 YCR 面板：upload/download token estimate、saved estimate、provider call count、YCR trace。
- 聊天气泡下方：与该气泡时间窗口相关的 up/down/saved token estimate。
- ToolCallCard：`YCR Storage` 显示 raw ref、raw bytes、shell bytes；`Provider Projection` 显示投影策略、投影 token/bytes 和详情。

## 9. 当前边界与仍需注意的点

1. YCR 进程独立，但仍共享本仓库代码、ORM model 和数据库；它不是单独发布包。
2. YCR 对 Center/Agent/Node 当前按信任域处理，没有做多租户权限隔离。
3. Tool RAG 质量强依赖 capability manifest、schema、description 和 examples 的质量。
4. Result RAG 只覆盖已写入 raw ContextRef 且完成 chunk 索引的数据。
5. embedder 必须提前启动并加载模型；YCR 搜索类能力不提供 mock/fallback。

当前可收工判定：主链路不再把 raw tool result 暴露给 provider；搜索失败不静默降级；前端能看见 storage、projection、refs 和 token estimate；文档不再描述已删除的旧 YCR 路径。
