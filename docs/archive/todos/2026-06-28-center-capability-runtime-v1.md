# Center Capability Runtime v1 待办

状态：已部分验收，后续主线已被取代  
取代文档：`2026-06-30-center-execution-runtime-v2.md`

说明：

本文保留为 capability identity、Center meta tools、Artifact 基线、raw-tool prompt 压缩等已完成工作的历史基线。2026-06-30 之后，新的主线是 Center Execution Runtime v2。后续涉及 Execution Admission、Operation Bus、OperationEvent/Waiter、workflow handlers、Agent wait/resume、future MQ 边界的工作，不再继续扩展本文。

状态：活跃的破坏性重建计划
范围：Win/Linux 多 Node 基线验收后的下一轮主要阶段
负责人：Center / Agent / Console / Node runtimes

## 实现检查点

更新时间：2026-06-29

当前切片已实现：

- Phase 0 baseline schema/models were added:
  - `CapabilityDefinition`;
  - `CapabilitySource`;
  - `Artifact`;
  - `ArtifactBlob`;
  - `AgentRun`;
  - `AgentRunStep`.
- A new Alembic migration creates the runtime tables from a clean database.
- `node.register_capabilities` now writes both:
  - the old flat `Capability` table for short transition safety;
  - the new Definition/Source registry as the next runtime source of truth.
- Linux-style platform prefixes such as `linux.system.info` are stored as
  source `registered_name` and definition aliases while the semantic
  `canonical_name` becomes `system.info`.
- Phase 1 deterministic meta-tool services were added:
  - `node.list`;
  - `node.status`;
  - `capability.search`;
  - `capability.describe`.
- Artifact presentation meta-tool services were added:
  - `artifact.list`;
  - `artifact.get`;
  - `artifact.present`.
- Admin verification APIs were added under `/admin/meta/*`.
- Agent-visible meta tools are now available and execute inside Center without
  creating Node Jobs.
- SSE tool execution now treats successful Center-internal meta tools as
  completed tool calls even when no Job is created.
- Phase 2 `capability.invoke` was added:
  - accepts `source_id` or an unambiguous `capability_ref`;
  - rejects ambiguous canonical names and asks for `source_id` or `node_id`;
  - resolves through `CapabilitySource` and then enters the existing
    policy/approval/Invocation/Job/YQP execution path;
  - does not call Nodes directly and does not add silent alias fallback.
- Non-streaming Agent execution now waits for `created` Jobs as well as
  `running` Jobs, so meta-tool delegated invocations are not misclassified.
- Agent tool preflight now treats Center meta tools as local Center tools
  instead of resolving them as Node capabilities.
- Phase 4 production default was started:
  - production Agent tool lists now expose only Center meta tools by default;
  - raw Node capabilities stay in the registry and must be reached through
    `capability.search`, `capability.describe`, and `capability.invoke`;
  - media/artifact display is reached through `artifact.list`, `artifact.get`,
    and `artifact.present`;
  - test mode still keeps legacy/default raw tools for isolated compatibility
    tests during the transition.
- Phase 3 Artifact baseline was started:
  - Center now has local artifact/blob storage behind `Artifact` and
    `ArtifactBlob`;
  - admin APIs can upload, list, inspect, and download artifacts;
  - Nodes can upload artifacts through YQP `artifact.upload`, and Center
    returns `artifact.accepted` with the stored artifact metadata;
  - storage root is configurable through `YEQU_ARTIFACT_STORAGE_DIR`;
  - upload size is bounded by `YEQU_ARTIFACT_MAX_UPLOAD_BYTES`.
- Phase 3 normal-use integration is now at a stable validation point:
  - artifact creation validates Center schema limits before hitting database
    constraints, so invalid Node uploads fail as protocol errors instead of
    surfacing as database 500s;
  - capability descriptions infer `artifact_outputs` from an `artifacts`
    result schema when the Node manifest does not declare the artifact contract
    explicitly;
  - Agent prompt context now tells the model how to treat Center artifact
    references without inventing binary/media contents;
  - Console has an Artifact Browser route for listing, filtering, previewing,
    opening, and downloading Center artifacts;
  - Agent tool-call cards render artifact previews/links inline when a tool
    result contains `artifacts`.
- Agent can now actively present existing media artifacts through
  `artifact.present`; Console renders these as independent chat artifact blocks
  instead of burying the media inside a tool-call result.
- Frontend user prompts are rendered optimistically and reconciled with the
  server `agent.prompt.received` event, preventing duplicate user bubbles.
- `node.register_capabilities` now treats a registration as the reporting
  Node's complete capability snapshot. Active sources/capabilities from old
  plugin IDs or old name prefixes are deactivated when the Node reports a new
  snapshot.
- Startup now includes a cleanup path for orphaned DB sessions, and the
  DeepSeek provider retry cadence has been changed to fixed 5x5s intervals.
- Provider expansion is now tracked separately in
  `docs/todos/2026-06-29-agent-provider-system.md`; DeepSeek should become one
  OpenAI-compatible provider configuration rather than an API route special
  case.

当前切片尚未实现：

- cross-node file transfer;
- automatic artifact lifecycle/TTL cleanup;
- durable Agent graph/resume integration beyond schema.
- provider/model registry, probing, and dynamic Console selection.

## 阶段目标

Turn YeQu Center from a Node tool relay into a capability runtime.

The validated Win/Linux baseline proves that Center can connect multiple
devices, route Agent tool calls, preserve transcript state, and expose prompt
diagnostics. The next phase should be larger than adding a few more tools.

This phase builds the layer that lets the project scale to:

- screenshots;
- camera capture;
- file transfer;
- command output artifacts;
- multimodal inputs and outputs;
- multiple Windows/Linux/macOS nodes;
- node/runtime permission differences;
- Center-level search, routing, policy, and audit.

The target architecture:

```text
Agent / CLI / Console / future MCP adapter
  -> Center Meta Tools and APIs
  -> Capability Registry v2
  -> Capability Runtime
  -> Policy / Approval / Resource Locks
  -> Artifact / Media / Blob Layer
  -> Invocation / Job / Timeline
  -> YQP
  -> Nodes
```

This phase should produce a visible user-level win:

- the Agent no longer needs to see every raw Node capability in its prompt;
- the user asks for an outcome, not a specific Node tool name;
- Center can discover, describe, route, execute, and audit capabilities;
- screenshots, files, and other large outputs become first-class artifacts;
- multi-node tasks become practical instead of prompt-heavy.

## 为什么下一阶段应做这件事

The project is now stable enough with one Win Node and one Linux Node. The next
risk is not basic connectivity; it is capability growth.

If each new Node capability is injected directly into Agent context:

- prompt size grows linearly with devices and plugins;
- platform-specific names leak into reasoning;
- similar capabilities across nodes become ambiguous;
- schemas for camera/files/screenshots consume too much context;
- large outputs do not fit naturally in chat messages;
- cross-device tasks require ad hoc glue.

The solution is not only Tool RAG. Tool RAG is one component inside a broader
Center runtime:

```text
Capability identity + discovery + execution + artifacts + runtime constraints.
```

## 不可妥协的设计规则

- Center remains the control plane.
- Nodes do not become directly callable by the Agent.
- YQP remains the Node protocol.
- Policy, approval, resource locks, Invocation, Job, and Timeline stay in the
  execution path.
- No silent alias fallback.
- No hardcoded Agent natural-language replies pretending to be model output.
- Errors propagate visibly.
- Runtime/platform/permission truth comes from Center state, not tool-name
  prefixes.
- Preserving old development data and API shapes is not a goal in this rapid
  iteration phase; clean architecture is more important.
- Destructive migrations are allowed.
- Old development data may be dropped.
- The old `Capability.name`-as-identity model must not be preserved as the new
  runtime truth.
- New infrastructure should replace weak foundations directly.

## 阶段 0：破坏性基础重建

### 目标

Rebuild the core capability/runtime/artifact schema before adding more
features.

This phase explicitly permits destructive Alembic migrations and local/remote
database reset during rapid iteration. The project has reached the point where
wrapping the old capability table would create a larger long-term mess than
replacing it.

### 需要淘汰的旧基础

- `Capability.name` as global identity.
- tool-name prefixes as platform/runtime truth.
- Agent-visible raw Node tools as the default execution interface.
- capability registration that stores only one flat implementation record.
- large tool outputs embedded directly into chat/tool result payloads.
- approval pauses without a durable Agent run/resume model.

### 新核心表

Minimum destructive rebuild target:

```text
capability_definitions
capability_sources
runtime_instances
artifacts
artifact_blobs
agent_runs
agent_run_steps
```

Existing `nodes`, `invocations`, `jobs`, `timeline_events`,
`timeline_sequences`, `approvals`, and `resource_locks` can remain if their
contracts still match the new runtime.

### 重建规则

- Do not carry old capability IDs forward.
- Do not attempt automatic production-style data migration.
- Keep old models only temporarily when needed to land an intermediate commit,
  and mark them as legacy immediately.
- Each new model must have a clear owner and relationship to Center runtime.
- Schema must support artifacts and runtime permissions from the beginning.

### 阶段 0 交付物

- Destructive Alembic migration.
- New SQLAlchemy models.
- Registration path that writes new `CapabilityDefinition` and
  `CapabilitySource` records.
- Agent/API reads use new models.
- Old `Capability` model either removed or explicitly isolated as legacy input.
- Focused tests for registration, search, describe, invoke resolution, and
  artifact linkage.

## 工作流 A：Capability Registry v2

### 目标

Replace the flat capability model with semantic definitions and concrete
sources.

当前状态：

- Node capabilities have names such as `system.info` or `linux.system.info`.
- These names are both display names and execution names.
- Routing truth is partially inferred by humans from prefixes.

目标 definition/source 状态：

```json
{
  "capability_id": "cap_01J_system_info",
  "canonical_name": "system.info",
  "display_name": "Linux system info",
  "aliases": ["linux.system.info"],
  "effect": "read",
  "risk": "safe",
  "capability_kind": "function",
  "artifact_inputs": [],
  "artifact_outputs": [],
  "tags": ["system", "diagnostics"],
  "examples": [
    "Check Linux host uptime and memory summary."
  ],
  "sources": [
    {
      "source_id": "capsrc_01J_linux_system_info",
      "registered_name": "linux.system.info",
      "source_node_id": "linux-node-01",
      "runtime_id": "linux-node-01:user",
      "platform_os": "linux",
      "status": "available"
    }
  ],
  "permission_requirements": {
    "privilege": "user",
    "filesystem_scopes": []
  }
}
```

### 必要决策

- `capability_id` is Center's stable identity.
- `canonical_name` is semantic grouping, not a hidden alias.
- `CapabilityDefinition` owns semantic identity, schemas, examples, risk,
  effect, artifact contracts, and tags.
- `CapabilitySource` owns a concrete Node/runtime implementation and the raw
  registered name reported by that Node.
- aliases are explicit metadata and visible in diagnostics.
- execution must resolve to a concrete `CapabilitySource`.
- same semantic capability on multiple nodes must be represented as multiple
  sources under one search result, not as one ambiguous tool.

### 交付物

- Capability identity service.
- Deterministic canonicalization rules.
- Definition/source registration pipeline.
- Destructive migration replacing the old flat capability model.
- Tests for:
  - Win `system.info`;
  - Linux `linux.system.info`;
  - same semantic capability across nodes;
  - explicit alias metadata;
  - no silent alias execution.

## 工作流 B：Runtime 与权限模型

### 目标

Represent the real execution environment where a capability runs.

This is necessary because Windows and Linux nodes can expose different
permissions, filesystem visibility, desktop access, camera access, and service
contexts.

### 必要模型

Runtime instance fields should be usable by routing and diagnostics:

- `runtime_id`;
- `node_id`;
- `platform_os`;
- `process_user`;
- `privilege`: `user`, `admin`, `root`, `service`;
- `desktop_session`: available/unavailable;
- `camera_access`: available/unavailable/unknown;
- `filesystem_scopes`;
- `network_scopes`;
- `artifact_cache_dir`;
- `capability_count`;
- `health`;
- `last_seen_at`.

### Linux 权限策略

For Linux Node first version:

- daemon should normally run as a regular user;
- capabilities declare required privilege;
- unsupported high-privilege capabilities are not registered or are marked
  unavailable with a clear reason;
- sudo-based escalation can be added later only with explicit allowlists;
- no silent privilege fallback.

### 交付物

- Runtime metadata exposed in capability context and diagnostics.
- Node status view includes runtime constraints.
- Capability registration validates required runtime traits.
- Tests for user/root mismatch and unavailable capability reasons.

## 工作流 C：Center 元工具

### 目标

Expose a small stable tool surface to the Agent.

初始元工具：

- `node.list`
- `node.status`
- `capability.search`
- `capability.describe`
- `capability.invoke`
- `artifact.get`
- `artifact.list`

The Agent should discover and invoke capabilities through Center. It should not
receive raw Node capabilities in the default prompt.

### 工具合同

`node.list`:

- returns node summaries;
- includes status, platform, runtime count, capability counts, last heartbeat.

`node.status`:

- returns detailed node/runtime state;
- includes schedulability and unavailable reasons.

`capability.search`:

- deterministic first;
- search by query, tags, platform, node, effect, risk, artifact input/output;
- returns compact candidates.

`capability.describe`:

- returns schema, examples, runtime constraints, approval behavior, artifact
  behavior.

`capability.invoke`:

- accepts `capability_id` or selected source;
- calls existing Center application services;
- creates Invocation and Job;
- returns structured observation or approval pause.

`artifact.get` / `artifact.list`:

- lets Agent inspect artifact metadata and retrieve text-safe summaries;
- binary payloads are never injected blindly into prompt context.

### 交付物

- Meta tool provider for Agent.
- Debug UI shows search/describe/invoke decisions.
- Tests proving Agent can execute real Node capabilities through meta tools
  without raw Node tool injection.

## 工作流 D：Artifact / Media / Blob 层

### 目标

Make files, screenshots, camera frames, command outputs, and large payloads
first-class Center artifacts.

This is required before expanding Win Node screenshot/camera and cross-node
file transfer in a clean way.

### Artifact 模型

Required fields:

- `artifact_id`;
- `kind`: `file`, `image`, `camera_frame`, `screenshot`, `text`, `json`,
  `binary`, `archive`;
- `media_type`;
- `size_bytes`;
- `sha256`;
- `storage_backend`;
- `storage_key`;
- `producer_node_id`;
- `producer_runtime_id`;
- `source_capability_id`;
- `invocation_id`;
- `job_id`;
- `created_at`;
- `expires_at`;
- `visibility`;
- `metadata`;

### 必要 API

- upload artifact from Node;
- register artifact metadata;
- download artifact;
- preview artifact metadata;
- list artifacts by session/invocation/job/node;
- garbage collect expired artifacts.

### 存储策略

First version can use local filesystem storage on Center. It must be behind an
interface so S3-compatible storage can be added later.

### 交付物

- Artifact service and model.
- YQP artifact reporting/upload contract.
- Console Artifact Browser baseline.
- Tests for upload, metadata, download, TTL cleanup, and access checks.

## 工作流 E：文件传输与跨 Node Artifact

### 目标

Enable practical file movement without making the Agent manually shuttle bytes
through chat.

模式：

Current baseline keeps small artifacts in Center, but large and cross-node
transfer must not assume every byte crosses the Center HTTP ingress. The
concrete execution plan now lives in
`docs/todos/2026-06-29-artifact-transfer-strategy.md`.

```text
Small artifact:
Node -> Center artifact -> user download / Agent presentation

Large or cross-node file:
Node A -> croc transport -> Node B
Node -> croc transport -> Center-side receiver -> Center artifact
Center artifact -> Center-side sender -> croc transport -> Node
```

初始能力：

- `file.read_artifact`
- `file.write_artifact`
- `file.stat`
- `artifact.transfer`

规则：

- Node filesystem permissions are enforced by Node runtime capability contract.
- Center sees artifact metadata and transfer audit.
- Large binary data never becomes an Agent text message.
- All transfers write Timeline events.
- YQP `artifact.upload` remains the lightweight artifact path; croc-backed
  transfer is the planned large-file data plane.

## 工作流 F：Agent Runtime v2 Graph

### 目标

Move from "loop with helpers" to explicit run graph when meta tools and
artifacts need richer pause/resume behavior.

Required graph nodes:

- build context;
- search capabilities;
- describe capability;
- choose source;
- preflight policy;
- request approval;
- invoke capability;
- wait job;
- observe artifact/result;
- synthesize final;
- fail;
- pause/resume.

This is where Agent approval resume belongs. It should not be bolted onto the
current loop as a one-off.

### 交付物

- `AgentRun` table or equivalent durable checkpoint if `AgentTurnEvent` is no
  longer enough.
- Resume API for approval pauses.
- Reconnect-safe stream projection.
- Tests for approval resume and artifact-producing tool observation.

## 工作流 G：Console Runtime UX

### 目标

Expose the new runtime clearly enough to debug personal infrastructure tasks.

Views:

- Node Explorer;
- Runtime Detail;
- Capability Explorer;
- Capability Search Debug;
- Artifact Browser;
- Agent Run Timeline;
- Prompt/Context Inspector.

Requirements:

- show why a node/capability is available or unavailable;
- show capability source node/runtime;
- show artifact previews;
- show approval and policy decisions;
- show search and describe steps in Agent timeline.

### 近期 UX 待办

- Agent chat should append the user's outgoing message immediately when the
  request is submitted. It must not wait for the first LLM delta, tool event, or
  final response before showing the user's message in the transcript.
- If the request fails before the backend emits `agent.prompt.received`, the
  optimistic user message should remain visible and the failure should be shown
  as an explicit system/error segment.
- Session reload should reconcile the optimistic local message with the durable
  `agent.prompt.received` event once it arrives, without duplicating the user
  message.

## 工作流 H：MCP Adapter 边界

### 目标

Allow external MCP clients to use Center without replacing Center's control
plane.

目标：

```text
External MCP Client
  -> Center MCP Adapter
  -> Center Meta Tools
  -> Policy / Job / YQP
  -> Nodes
```

非目标：

- Do not make each personal device a direct MCP server for the Agent as the
  primary architecture.

## 执行阶段

### 阶段 0：破坏性 schema 与 model 重建

- Create destructive Alembic migration.
- Replace flat capability identity with:
  - `CapabilityDefinition`;
  - `CapabilitySource`;
  - rebuilt `RuntimeInstance` if needed;
  - `Artifact`;
  - `ArtifactBlob`;
  - `AgentRun`;
  - `AgentRunStep`.
- Reset development database as needed.
- Rewrite Node registration to populate definition/source records.
- Remove or isolate old `Capability` semantics.
- Add focused model/registration tests.

### 阶段 1：Registry 与元工具骨架

- Add capability identity service on top of new schema.
- Add deterministic `node.list`, `node.status`, `capability.search`,
  `capability.describe`.
- Agent prompt uses Center meta tools in the new path.
- No embeddings.
- Artifact schema exists even if full storage APIs land in Phase 3.

### 阶段 2：通过元工具执行 Capability Invoke

- Implement `capability.invoke`.
- Route through Center execution boundary. Reuse ToolInvocationApplicationService
  only if it still matches the new definition/source model; otherwise rewrite
  the boundary cleanly.
- Preserve policy/approval/job/timeline.
- Agent can complete simple Win/Linux tasks using only meta tools.

### 阶段 3：Artifact 层基线

- Add artifact model/service/storage.
- Add Node artifact reporting/upload path.
- Add Console artifact listing and download.
- Implement screenshot/file output as artifacts.
- Add Agent-visible artifact presentation meta tools.
- Render presented artifacts as first-class Console chat blocks.

### 阶段 4：从默认 Agent Runtime 移除原始工具注入

- Default Agent prompt contains only meta tools.
- Raw Node tools are not part of normal Agent execution.
- Add tests with many fake capabilities to prove context stays small.
- Current production default already exposes Center meta tools instead of raw
  Node capabilities; remaining work is stress-testing the prompt size and
  moving toward semantic retrieval when capability counts grow.

### 阶段 5：跨 Node 文件传输

- Implement the dual transfer strategy described in
  `2026-06-29-artifact-transfer-strategy.md`.
- Add TransferSession model/service and transfer meta tools.
- Add file read/write capabilities using artifacts.
- Add croc status/send/receive capabilities on WinNode and LinuxNode.
- Validate Linux <-> Windows transfer.

### 阶段 6：Agent Runtime v2 Graph 与 Resume

- Add durable run graph/checkpoint if needed.
- Implement approval resume.
- Add artifact-aware observations.

### 阶段 7：MCP Adapter

- Expose Center meta tools through an MCP adapter.
- Keep Center policy and audit in the path.

## 验收标准

1. With many registered Node capabilities, Agent prompt contains only stable
   Center meta tools by default.
2. Agent can discover Win and Linux capabilities through `capability.search`.
3. Agent can inspect a capability through `capability.describe`.
4. Agent can invoke a real Node capability through `capability.invoke`.
5. Policy, approval, resource locks, Invocation, Job, Timeline, and YQP remain
   in the path.
6. Screenshots or command outputs can be returned as artifacts, not giant chat
   payloads.
7. Center can list artifacts for a session/invocation/job.
7a. Agent can actively present an existing artifact through `artifact.present`.
8. A file can move from one Node to another through Center artifact storage.
9. Runtime permission limitations are visible and do not become silent
   fallback behavior.
10. Prompt/debug UI explains why a capability was selected.
11. The old flat capability identity path is removed or explicitly marked
    legacy and unused by default.

## 测试策略

Backend:

- capability identity canonicalization;
- deterministic search;
- describe schema;
- invoke through existing application service;
- policy denied / approval required;
- artifact upload/download/TTL;
- cross-node transfer;
- raw tool injection disabled by default;
- Agent meta-tool run over Win and Linux fixtures.
- destructive migration produces the new schema from an empty database.

Frontend:

- Node Explorer;
- Capability Explorer;
- Artifact Browser;
- Agent Run Timeline;
- search/describe/invoke debug blocks.

Manual:

- Win screenshot -> Center artifact -> Agent summary;
- Linux file stat/read -> Center artifact;
- Win/Linux capability search with similar semantic capabilities;
- cross-node file transfer through Center;
- approval pause and resume when graph phase lands.

## 与现有文档的关系

- `2026-06-28-agent-multinode-routing.md` is the validated baseline this plan
  builds on.
- `2026-06-28-agent-tool-rag-roadmap.md` becomes the Tool RAG / meta-tool
  subset of this larger runtime plan.

## v1 非目标

- No embedding/vector search in the first implementation slice.
- No direct Agent-to-Node execution.
- No bypass of policy or approvals.
- No hidden capability aliases.
- No production-grade distributed object store in the first storage backend.
- No large external Agent framework migration.
- No production-style backward compatibility migration for old development
  capability rows.
