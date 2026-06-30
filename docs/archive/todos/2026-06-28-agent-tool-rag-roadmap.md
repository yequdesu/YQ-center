# Agent Tool RAG 与元工具路线图

状态：未来子计划
范围：从属于 `2026-06-30-center-execution-runtime-v2.md`

## 1. 与 Center Execution Runtime v2 的关系

本文不再是一条独立架构线，而是 Center Execution Runtime v2 中“能力发现、上下文压缩与 prompt 治理”的子集。

Tool RAG 不能引入第二套 capability registry、第二套路由模型或独立执行合同。它必须读取 Center Execution Runtime 的事实模型：

- `CapabilityDefinition`：语义身份、示例、schema、effect、risk、artifact 合同和 tags；
- `CapabilitySource`：具体 Node / runtime 实现；
- `RuntimeInstance`：平台、权限、文件系统、显示器、摄像头和其他执行约束；
- Artifact / blob 元数据：媒体和大输出能力；
- policy、approval、resource lock、Invocation、Job、Operation、Timeline：实际执行路径与等待/恢复路径。

第一版可以使用确定性的数据库搜索。向量搜索和 embeddings 是 runtime 模型正确之后的优化，不是第一步。

## 2. 目的

已验收的多 Node 基线让 Agent 上下文变得结构化且可调试，但它仍然过度依赖把原始 Node capability 暴露给模型。

这只适合作为 Win Node + Linux Node 的短期基线。随着项目加入以下能力，它不能成为长期架构：

- 截图；
- 摄像头；
- 文件传输；
- artifact / media / blob 操作；
- 多台 Linux / Windows / macOS 设备；
- 浏览器或应用自动化；
- 设备特定 runtime 约束；
- 更多高风险写操作或破坏性能力。

本路线图描述下一步架构中的一个部分：停止把每个原始 Node 工具注入 prompt，将 capability 发现收敛到 Center 级元工具，并由 Center Execution Runtime v2 提供数据。

## 3. 设计目标

LLM 默认只看到一组小而稳定的工具面：

- `node.list`
- `node.status`
- `capability.search`
- `capability.describe`
- `capability.invoke`

真实 Node capability 保留在 Center registry 中。Agent 通过 Center 发现、描述、校验和调用它们。

## 4. 为什么不能注入所有工具

随着项目增长，原始工具注入会失败：

- prompt context 随设备和插件数量线性增长；
- 多 Node 上同名或近似能力变得模糊；
- 平台特定命名泄漏到用户推理；
- tool schema 占用过多 token；
- 离线、degraded、runtime 受限能力会混淆模型；
- 每新增一个 Node 都会让 prompt 更不稳定。

Tool RAG 负责发现；Center policy 仍负责执行授权；YQP 仍负责设备通信。

## 5. 元工具设计

### `node.list`

返回可见节点和概要状态：

- `node_id`
- platform / runtime 摘要；
- online / degraded / offline 状态；
- capability 数量；
- 最近 heartbeat；
- 可选 tags。

### `node.status`

返回单个节点的详细状态：

- liveness；
- runtime instances；
- signals；
- 最近失败；
- resource / permission 约束；
- 是否可调度及原因。

### `capability.search`

搜索 Center capability registry。

输入：

- 自然语言 query；
- 可选 node 过滤；
- 可选 platform 过滤；
- 可选 effect / risk 过滤；
- 可选 artifact / media / runtime 过滤。

搜索来源：

- capability name；
- description；
- examples；
- input / output schemas；
- node metadata；
- runtime metadata；
- permission requirements；
- 最近成功/失败信号；
- artifact / media 支持信息。

输出必须紧凑。默认返回 candidate id、name、node sources 和短描述，不返回完整 schema。

### `capability.describe`

返回一个候选 capability 的详细 schema 和执行约束：

- canonical capability id；
- registered name；
- source nodes；
- input schema；
- output schema；
- effect / risk；
- permission / runtime requirements；
- approval 行为；
- examples。

### `capability.invoke`

通过现有 Center 路径调用选定 capability：

```text
capability.invoke
  -> policy / approval / resolver
  -> Invocation
  -> Job
  -> YQP
  -> Node
```

它不能绕过 Center 现有 application service、approval、resource lock、job state machine 或 timeline。

## 6. Capability 身份模型

Tool RAG 应使用稳定 capability 身份，而不是原始展示名。

目标模型：

```json
{
  "capability_id": "cap_system_info_v1",
  "canonical_name": "system.info",
  "registered_name": "linux.system.info",
  "aliases": ["linux.system.info"],
  "source_nodes": ["linux-node-01"],
  "platform_constraints": ["linux"],
  "effect": "read",
  "risk": "safe"
}
```

规则：

- `capability_id` 是执行身份。
- `canonical_name` 是语义身份。
- `registered_name` 是 Node 当前上报的名字。
- aliases 必须显式且可见。
- 执行时不允许静默 alias fallback。

## 7. 与 Artifact / Media / Blob 层的关系

Tool RAG 应把 artifact / media / blob 能力作为一等 capability 索引，而不是把它们写成特殊 prompt 文本。

示例：

- 截图；
- 摄像头帧采集；
- 文件上传/下载；
- 图片 artifact 分析；
- 命令输出 artifact；
- 跨设备文件传输。

搜索结果应标明 capability 是否产生或消费 artifact。

## 8. 与 MCP 的关系

MCP 可以作为 Center 上层 adapter：

```text
External MCP Client
  -> Center MCP Adapter
  -> capability.search / capability.invoke
  -> Center policy/job/YQP
  -> Node
```

MCP 不应替代 YQP，因为 YQP 负责 Node 生命周期、Job 恢复、heartbeat、reconciliation 和设备执行状态。

## 9. 分阶段计划

### 阶段 A：不使用 embedding 的 registry 搜索

先做确定性搜索：

- name substring；
- description substring；
- tags；
- platform / effect / risk filters。

这足以验证元工具流程。

### 阶段 B：增加 embedding index

确定性搜索稳定后再加向量搜索。

索引内容：

- capability name；
- description；
- examples；
- schema summaries；
- platform / runtime metadata。

### 阶段 C：替换原始工具注入

Provider 可见工具只保留元工具。

原始 Node capability 不再注入主 prompt。

### 阶段 D：基于元工具规划

Agent 可以按以下模式规划：

```text
search -> describe -> ask approval if needed -> invoke -> observe -> continue
```

## 10. 验收标准

1. 注册大量 capability 后，prompt 仍只包含稳定元工具集合。
2. Agent 能通过 `capability.search` 发现 Linux 和 Windows capability。
3. Agent 能通过 `capability.describe` 查看 schema。
4. Agent 只能通过 `capability.invoke` 调用真实 capability。
5. Center policy、approval、invocation、job、timeline、YQP 仍在执行路径内。
6. 不引入静默 alias 替换。
7. 搜索和调试结果足够可见，能够解释为什么选中某个 capability。

## 11. 非目标

- 不在当前 Agent runtime 基线稳定前实现。
- 第一版不使用 embeddings。
- 不绕过 Center policy。
- 不让 Node 变成 Agent 直接执行用的 MCP server。
