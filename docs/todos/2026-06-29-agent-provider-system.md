# Agent Provider 系统待办

状态：活跃待办
日期：2026-06-29
目标：把当前只面向 DeepSeek 的实现升级为可配置、可探测、可诊断的 Agent Provider 系统。

职责边界：本文只负责 Provider registry、模型发现、连通性/tool/stream probe、
Console provider/model 选择和 provider 错误显式展示。不负责 capability registry、
YCR projection/Result RAG、Node transfer 逻辑或 meta tool 默认输出治理。

## 1. 当前确切实现

当前 Center 并不是架构上只能支持 DeepSeek，但实现上存在 DeepSeek 绑定：

- `src/yequ/api/routes/agent.py` 的 `_resolve_provider()` 硬编码 `fake` 和 `deepseek`。
- `src/yequ/config.py` 只有 `deepseek_*` 配置项。
- `src/yequ/agent/deepseek_provider.py` 实际承担了 OpenAI-compatible 调用、DeepSeek 配置、系统提示词构建、工具名转换、重试策略等多种职责。
- Console provider 下拉框硬编码 `deepseek` / `fake`。
- 没有 provider list API。
- 没有模型列表探测。
- 没有 provider 连通性探测。
- 没有 provider 能力声明，例如是否支持 tools、streaming、vision、json mode。
- provider 调试信息只通过 Agent turn prompt context 间接暴露，不足以做运维诊断。

当前最主要的问题不是“缺一个 OpenAIProvider 类”，而是缺 Provider Registry / Model Registry / Probe 这一层。

## 2. 设计原则

1. Provider 是 Agent Runtime 的依赖，不是 API route 的硬编码分支。
2. DeepSeek 只是一个 OpenAI-compatible provider 配置项。
3. 模型探测和连通性探测必须显式执行、显式返回结果，不做静默 fallback。
4. Provider 失败必须传播为明确错误，不自动切换到其他 provider。
5. Console 显示的 provider/model 列表必须来自 Center 后端。
6. 系统提示词构建不属于某个 provider 的私有职责。
7. Provider 能力必须被建模：tools、streaming、vision、audio、json mode、max context 等。
8. 当前是个人项目，不需要复杂租户/配额系统，但需要清晰、可调试、可扩展。

## 3. 目标架构

```text
Agent API / Console
  -> ProviderRegistry
       -> ProviderConfigStore
       -> ProviderProbeService
       -> ModelCatalogService
       -> ProviderFactory
            -> OpenAICompatibleProvider
            -> FakeProvider
            -> future AnthropicProvider
            -> future GeminiProvider
  -> Agent Runtime
       -> PromptBuilder
       -> AgentProvider.invoke / invoke_stream
```

职责边界：

| 组件 | 职责 |
|---|---|
| `AgentProvider` | 统一 invoke / invoke_stream 接口。 |
| `OpenAICompatibleProvider` | OpenAI-compatible Chat Completions 协议适配。 |
| `ProviderRegistry` | 读取配置、构建 provider、缓存实例、列出 provider。 |
| `ProviderProbeService` | 连通性、鉴权、模型列表、工具调用能力探测。 |
| `ModelCatalogService` | 管理可用模型、默认模型、模型能力元数据。 |
| `PromptBuilder` | 构建系统提示词和消息上下文，脱离 DeepSeekProvider。 |
| `Agent API` | 只依赖 registry，不硬编码 provider。 |
| `Console` | 动态展示 provider/model/probe 状态。 |

## 4. 配置模型

第一版使用环境变量配置，不引入数据库。

推荐新增：

```env
YEQU_AGENT_DEFAULT_PROVIDER=deepseek
YEQU_AGENT_DEFAULT_MODEL=deepseek-chat

YEQU_LLM_PROVIDERS_JSON=[
  {
    "name": "deepseek",
    "kind": "openai_compatible",
    "base_url": "https://api.deepseek.com",
    "api_key_env": "YEQU_DEEPSEEK_API_KEY",
    "default_model": "deepseek-chat",
    "enabled": true
  },
  {
    "name": "openai",
    "kind": "openai_compatible",
    "base_url": "https://api.openai.com/v1",
    "api_key_env": "YEQU_OPENAI_API_KEY",
    "default_model": "gpt-...",
    "enabled": false
  }
]
```

配置解析规则：

- `name` 是用户和 API 看到的 provider id。
- `kind` 决定 provider adapter。
- `api_key_env` 指向真实密钥环境变量，不把 key 写进 JSON。
- `default_model` 是该 provider 的默认模型。
- `enabled=false` 的 provider 不参与普通 Agent 调用，但可在 diagnostics 中显示为 disabled。
- 配置错误应导致 provider probe 失败或启动诊断失败，不要静默忽略。

兼容策略：

- 保留现有 `YEQU_DEEPSEEK_API_KEY`、`YEQU_DEEPSEEK_BASE_URL`、`YEQU_DEEPSEEK_MODEL` 作为构建默认 DeepSeek provider 的便捷配置。
- 不把兼容逻辑扩散到 Agent Runtime；只放在 ProviderConfigStore。

## 5. Provider 能力模型

新增 `ProviderCapabilities`：

| 字段 | 说明 |
|---|---|
| `supports_chat` | 是否支持普通 chat completion。 |
| `supports_tools` | 是否支持 tool/function calling。 |
| `supports_streaming` | 是否支持 streaming。 |
| `supports_json_mode` | 是否支持 JSON mode。 |
| `supports_vision` | 是否支持图片输入。 |
| `supports_audio` | 是否支持音频输入。 |
| `supports_file_input` | 是否支持文件输入。 |
| `max_context_tokens` | 已知最大上下文，未知可为空。 |
| `max_output_tokens` | 已知最大输出，未知可为空。 |
| `tool_name_policy` | `openai_strict`、`dotted_allowed` 等。 |

Provider capability 来源：

- 静态配置；
- `/models` 返回的模型 metadata；
- 手动 override；
- 探测结果。

第一版允许静态能力 + 连通性探测。不要为了能力探测写危险或昂贵的复杂请求。

## 6. 模型发现

### 6.1 OpenAI-compatible `/models`

对于 `kind=openai_compatible`：

```http
GET {base_url}/models
Authorization: Bearer <api_key>
```

返回后转换为内部 `ProviderModelInfo`：

| 字段 | 说明 |
|---|---|
| `provider_name` | provider id。 |
| `model` | 模型 id。 |
| `display_name` | 显示名，第一版可等于 model。 |
| `owned_by` | 上游返回的 owner，可为空。 |
| `created_at` | 上游返回时间，可为空。 |
| `capabilities` | 模型能力，第一版可来自静态规则。 |
| `probe_status` | `unknown`、`available`、`failed`。 |
| `last_probed_at` | 最近探测时间。 |
| `error_message` | 探测失败原因。 |

### 6.2 自动探测边界

“自动探测”不代表每次用户发消息前都探测。

建议：

- Center 启动后可异步刷新 provider/model catalog。
- Console 打开 provider 下拉框时读取缓存结果。
- 用户可点击 refresh/probe。
- Agent 调用前只做配置存在性校验，不做长探测阻塞。

探测失败不自动切换 provider。

## 7. 连通性探测

新增 probe 类型：

| Probe | 目的 |
|---|---|
| `config` | 配置是否完整，api key env 是否存在。 |
| `models` | `/models` 是否可访问。 |
| `chat` | 指定模型是否能完成最小 chat。 |
| `tools` | 指定模型是否能接受最小 tool schema。 |
| `stream` | 指定模型是否能返回 stream。 |

第一版实现顺序：

1. `config`
2. `models`
3. `chat`
4. `tools`
5. `stream`

探测结果必须结构化返回：

```json
{
  "provider_name": "deepseek",
  "model": "deepseek-chat",
  "probe": "tools",
  "status": "succeeded",
  "latency_ms": 1234,
  "checked_at": "2026-06-29T00:00:00Z",
  "error_code": null,
  "error_message": null
}
```

失败示例：

```json
{
  "provider_name": "openai",
  "model": "gpt-...",
  "probe": "models",
  "status": "failed",
  "latency_ms": 412,
  "error_code": "auth_failed",
  "error_message": "401 Unauthorized"
}
```

## 8. API 待办

新增 Agent Provider API：

```http
GET /agent/providers
GET /agent/providers/{provider_name}
GET /agent/providers/{provider_name}/models
POST /agent/providers/{provider_name}/probe
POST /agent/providers/{provider_name}/models/{model}/probe
```

`GET /agent/providers` 返回：

```json
{
  "default_provider": "deepseek",
  "default_model": "deepseek-chat",
  "providers": [
    {
      "name": "deepseek",
      "kind": "openai_compatible",
      "enabled": true,
      "default_model": "deepseek-chat",
      "capabilities": {
        "supports_chat": true,
        "supports_tools": true,
        "supports_streaming": true
      },
      "last_probe": {
        "status": "succeeded",
        "checked_at": "..."
      }
    }
  ]
}
```

Agent invoke 请求应增加可选 `model`：

```json
{
  "provider_name": "deepseek",
  "model": "deepseek-chat",
  "session_id": "...",
  "prompt": "..."
}
```

如果未传 `model`：

- 使用 provider 的 `default_model`。
- 如果 provider 没有 default model，返回明确错误。
- 不自动选择其他 provider。

## 9. 后端实现待办

### 阶段 1：抽象整理

- 新增 `src/yequ/agent/prompt_builder.py`。
- 把系统提示词构建从 `DeepSeekProvider` 移出。
- 新增 `src/yequ/agent/openai_compatible_provider.py`。
- 将现有 DeepSeekProvider 改成薄包装或配置实例。
- 保持现有 DeepSeek 行为不变。

验收：

- DeepSeek 正常调用。
- prompt context 仍能显示系统提示词。
- Fake provider 测试不受影响。

### 阶段 2：Provider Registry

- 新增 `ProviderConfig` / `ProviderCapabilities` / `ProviderModelInfo` 数据类。
- 新增 `ProviderConfigStore`。
- 新增 `ProviderRegistry`。
- API route 删除 provider 构建硬编码。
- `_resolve_provider()` 改为调用 registry。

验收：

- `deepseek` 从 registry 构建。
- `fake` 仍可用于测试，但不污染生产 provider catalog。
- 未知 provider 返回 404。

### 阶段 3：Provider / Model API

- 新增 `/agent/providers`。
- 新增 `/agent/providers/{provider_name}/models`。
- `InvokeAgentRequest` / `AgentPlanRequest` 增加 `model`。
- Agent turn metadata 记录 provider 和 model。

验收：

- Console 能拿到 provider 列表。
- 后端响应包含默认 provider/model。
- Agent debug metadata 里能看到真实 provider/model。

### 阶段 4：Probe Service

- 新增 `ProviderProbeService`。
- 实现 config probe。
- 实现 OpenAI-compatible `/models` probe。
- 实现最小 chat probe。
- 实现最小 tools probe。
- 实现 stream probe。
- 探测失败返回结构化错误，不抛成 500。

验收：

- 缺 API key 时 probe 返回 `config_missing`。
- API key 错误时 probe 返回 `auth_failed` 或上游错误。
- 正常 DeepSeek 返回模型列表和 chat/tools/stream probe 成功。

### 阶段 5：Console Provider UX

- provider 下拉框改为动态加载。
- 增加 model 下拉框。
- 显示 provider/model probe 状态。
- 增加手动 refresh/probe 按钮。
- 探测失败时显示错误，不隐藏 provider。

验收：

- 前端不再硬编码 `deepseek` / `fake`。
- 用户能看到当前 provider 是否可用。
- 用户能切换模型。

### 阶段 6：第二 Provider 验证

接入至少一个第二 provider 验证架构：

- OpenAI 官方；
- OpenRouter；
- Qwen OpenAI-compatible endpoint；
- 本地 vLLM / Ollama OpenAI-compatible endpoint。

验收：

- 不改 Agent Runtime 即可新增 provider。
- 只改配置即可新增 OpenAI-compatible provider。
- 新 provider 能执行普通 chat 和 tool call。

## 10. 前端行为要求

Console 中 provider 区域应包含：

- Provider 选择器；
- Model 选择器；
- probe status；
- refresh/probe button；
- disabled/error 状态；
- 当前 provider/model 写入 prompt context diagnostics。

如果 provider probe failed：

- 允许用户查看错误。
- 不自动切换 provider。
- 发送消息时如果仍选择该 provider，应由后端返回明确错误。

## 11. 流式重试可靠性修复

状态：已确认存在，纳入 Provider 系统 P0。

当前 `DeepSeekProvider.invoke_stream()` 在流式场景中由 provider 内部执行重试。这个设计存在边界错误：

- provider 无法安全重放已经输出过的 stream delta。只要 `_stream_one_attempt()` 已经 yield 过文本或 tool call 片段，后续异常就不能重新发起一次完整 LLM 请求，否则 Agent 端会把旧 attempt 和新 attempt 的 delta 累加，造成重复输出或顺序错乱。
- `_is_retryable_error()` 只根据错误消息文本判断，`"timeout"` 或 `"rate"` 会被视作可重试；如果上游 API 快速返回 `"Request timed out."` 这类错误响应，它会被当作可重试超时处理，造成多轮空白等待。
- `agent_stream.py` 目前只做 `assistant_text += content`，没有 provider attempt、是否已输出、是否中途断流等状态，因此无法在 Agent 层修正 provider 重试造成的重复内容。

目标设计：

1. 流式 retry 只能发生在“本次 attempt 尚未输出任何 delta/tool_call 片段”之前。
2. 一旦 stream 已经开始输出，后续异常必须作为明确的 `provider_stream_interrupted` 传播给 Agent Runtime 和前端，不允许静默重试。
3. 非流式调用和流式调用使用分离的 retry policy。流式 retry 次数应更保守，并暴露为可配置项。
4. retry 判断不能只靠字符串匹配，应区分 HTTP 状态码、网络异常、上游结构化错误和真正的 read timeout。
5. retry 过程必须产生可见事件，例如 `agent.provider.retry_scheduled` / `agent.provider.retry_exhausted`，避免用户看到长时间无输出。
6. Agent Runtime 需要记录 provider attempt 状态：`attempt_started`、`emitted_delta`、`emitted_tool_call`、`interrupted_after_output`。
7. 错误不做 fallback，不切换 provider，不伪造成 Agent 自然回复；前端显示为运行错误或 INFO 事件。

验收测试：

- provider 在第一个 delta 前失败：允许按流式 retry policy 重试，最终没有重复文本。
- provider 在第一个 delta 后失败：不重试，返回 `provider_stream_interrupted`，前端显示中断状态。
- 上游快速返回 `"Request timed out."`：不会造成长时间静默等待，retry 事件可见。
- 多次 retry exhausted：Agent run 失败原因结构化，prompt context 和 timeline 能看到 provider、model、attempt、error_code。
- `assistant_text` 不会因为 provider retry 而重复累加。

实施位置：

- `src/yequ/agent/deepseek_provider.py`：拆分 stream retry policy，增加 attempt 输出状态。
- `src/yequ/agent/openai_compatible_provider.py`：Provider 系统重构后承接统一实现。
- `src/yequ/agent/agent_stream.py`：消费 provider retry/interrupted 事件，并将其转成 SSE/timeline 可见事件。
- `tests/`：新增 fake streaming provider 的前置失败、中途失败、快速 timeout 测试。

## 12. 测试策略

避免大面积全量测试，使用聚焦测试。

后端单测：

- config parse；
- provider registry unknown provider；
- default DeepSeek config compatibility；
- OpenAI-compatible tool name sanitize / resolve；
- `/agent/providers` 返回结构；
- probe service 使用 fake HTTP transport 或 monkeypatch。

前端轻量测试：

- provider API response 能渲染下拉框；
- probe failed 状态显示；
- invoke 请求携带 provider/model。

手动验收：

- DeepSeek provider 原有调用不变。
- 第二 provider 可配置后出现在 Console。
- 模型列表可刷新。
- 无效 key 能在 Console 中看到明确错误。

## 13. 与后续能力的关系

### Tool RAG

Provider 系统不替代 Tool RAG。Provider 系统解决“用哪个模型、模型是否可用、模型支持什么”；Tool RAG 解决“给模型哪些工具上下文”。

### 多模态 Artifact

Provider capability 中必须预留 vision/audio/file 能力，这样后续 Artifact 多模态输入可以根据 provider/model 能力选择是否允许发送图片、音频或文件引用。

### Center Execution Runtime v2

Center Execution Runtime v2 和 Agent Runtime 应只依赖 `AgentProvider` 接口和 provider/model metadata，不直接关心 DeepSeek/OpenAI/Qwen 等具体实现。Provider 系统不参与 Operation 调度决策；它只声明模型能力、连通性和调用行为。

## 14. 非目标

第一版不做：

- 多租户 provider 权限；
- 用户级 API key；
- provider 自动负载均衡；
- provider 失败自动切换；
- 成本计费；
- 大规模模型排行榜；
- 对所有模型能力做复杂 benchmark。

## 15. 近期执行顺序

1. 抽出 `PromptBuilder`。
2. 抽出 `OpenAICompatibleProvider`。
3. 将 DeepSeek 迁移为 OpenAI-compatible 配置。
4. 修复流式 retry 边界：已输出后不重试，中断显式传播。
5. 新增 ProviderRegistry。
6. 新增 `/agent/providers`。
7. 新增 `/agent/providers/{provider}/models`。
8. 新增 ProbeService。
9. 前端 provider/model 动态选择。
10. 接入第二个 OpenAI-compatible provider 验证。
