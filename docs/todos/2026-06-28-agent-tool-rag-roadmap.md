# Agent Tool RAG And Meta Tool Roadmap

Status: future sub-plan
Scope: subordinate to `2026-06-28-center-capability-runtime-v1.md`

## Relationship To Capability Runtime v1

This document is no longer an independent architecture track. It is the
retrieval and prompt-compression subset of Center Capability Runtime v1.

Tool RAG must not introduce a second capability registry, a second routing
model, or a separate execution contract. It must read from the rebuilt Center
runtime model:

- `CapabilityDefinition` for semantic identity, examples, schemas, effects,
  risks, artifact contracts, and tags;
- `CapabilitySource` for concrete Node/runtime implementations;
- `RuntimeInstance` for platform, permission, filesystem, display, camera, and
  other execution constraints;
- Artifact/blob metadata for media and large-output affordances;
- policy, approval, resource locks, Invocation, Job, and Timeline for actual
  execution.

The first implementation can use deterministic database search. Vector search
or embeddings are an optimization after the runtime model is correct.

## Purpose

The validated multi-node baseline made Agent context structured and debuggable,
but it still relies too much on raw Node capabilities reaching the model.

That is acceptable only as a short-lived baseline for Win Node + Linux Node. It
must not become the future architecture when the project adds:

- screenshots;
- camera access;
- filesystem transfer;
- artifact/media/blob operations;
- multiple Linux/Windows/macOS devices;
- browser or app automation;
- device-specific runtime constraints;
- more risky write/destructive capabilities.

This roadmap describes one part of the next architecture step: stop injecting
every raw node tool into the prompt and move capability discovery behind
Center-level meta tools backed by Capability Runtime v1.

## Design Goal

The LLM should see a small stable tool surface:

- `node.list`
- `node.status`
- `capability.search`
- `capability.describe`
- `capability.invoke`

Real node capabilities remain in Center's registry. The Agent discovers,
describes, validates, and invokes them through Center.

## Why Not Inject All Tools

Raw tool injection fails as the project grows:

- prompt context becomes too large;
- same-name capabilities across nodes become ambiguous;
- platform-specific names leak into user reasoning;
- tool schemas consume too many tokens;
- offline/degraded/runtime-limited capabilities confuse the model;
- every new node makes prompts less stable.

Tool RAG should solve discovery. Center policy should still solve execution
authorization. YQP should still solve device communication.

## Proposed Meta Tools

### `node.list`

Returns visible nodes and summary status:

- `node_id`
- platform/runtime summary;
- online/degraded/offline status;
- capability count;
- last heartbeat;
- optional tags.

### `node.status`

Returns detailed state for one node:

- liveness;
- runtime instances;
- signals;
- recent failures;
- resource/permission constraints;
- schedulability reason.

### `capability.search`

Searches the Center capability registry.

Inputs:

- natural-language query;
- optional node filters;
- optional platform filters;
- optional effect/risk filters;
- optional artifact/media/runtime filters.

Search sources:

- capability names;
- descriptions;
- examples;
- input/output schemas;
- node metadata;
- runtime metadata;
- permission requirements;
- recent success/failure signals;
- artifact/media support.

Output should be compact. It should return candidate IDs, names, node sources,
and short descriptions, not full schemas by default.

### `capability.describe`

Returns detailed schema and execution constraints for one capability candidate:

- canonical capability ID;
- registered name;
- source nodes;
- input schema;
- output schema;
- effect/risk;
- permission/runtime requirements;
- approval behavior;
- examples.

### `capability.invoke`

Invokes a selected capability through the existing Center path:

```text
capability.invoke
  -> policy / approval / resolver
  -> Invocation
  -> Job
  -> YQP
  -> Node
```

This must not bypass Center's existing application service, approval, resource
lock, job state machine, or timeline.

## Capability Identity Model

Tool RAG should use stable capability identity instead of raw display names.

Target model:

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

Rules:

- `capability_id` is execution identity.
- `canonical_name` is semantic identity.
- `registered_name` is what a Node currently reports.
- aliases are explicit and visible.
- no silent alias fallback during execution.

## Relationship To Artifact / Media / Blob Layer

Tool RAG should index artifact/media/blob capabilities as first-class
capabilities, not as special prompt text.

Examples:

- screenshot capture;
- camera frame capture;
- file upload/download;
- image analysis artifact;
- command output artifact;
- cross-device file transfer.

The search result should indicate whether a capability produces or consumes
artifacts.

## Relationship To MCP

MCP can be added as an adapter above Center:

```text
External MCP Client
  -> Center MCP Adapter
  -> capability.search / capability.invoke
  -> Center policy/job/YQP
  -> Node
```

MCP should not replace YQP because YQP owns node lifecycle, job recovery,
heartbeat, reconciliation, and device execution state.

## Phased Plan

### Phase A: Registry Search Without Embeddings

Start with deterministic search:

- name substring;
- description substring;
- tags;
- platform/effect/risk filters.

This is enough to validate the meta-tool flow.

### Phase B: Add Embedding Index

Add vector search only after deterministic search works.

Index:

- capability name;
- description;
- examples;
- schema summaries;
- platform/runtime metadata.

### Phase C: Replace Raw Tool Injection

Provider-visible tools become only meta tools.

Raw node capabilities are no longer injected into the main prompt.

### Phase D: Add Planning Over Meta Tools

The Agent can plan:

```text
search -> describe -> ask approval if needed -> invoke -> observe -> continue
```

## Acceptance Criteria

1. With many registered capabilities, the prompt still contains only the stable
   meta tool set.
2. The Agent can discover Linux and Windows capabilities through
   `capability.search`.
3. The Agent can inspect schemas through `capability.describe`.
4. The Agent invokes real capabilities only through `capability.invoke`.
5. Center policy, approval, invocation, job, timeline, and YQP remain in the
   execution path.
6. No silent alias substitution is introduced.
7. Search/debug results are visible enough to understand why a capability was
   selected.

## Non-Goals

- Do not implement this before the current Agent runtime baseline is stable.
- Do not use embeddings as the first version.
- Do not bypass Center policy.
- Do not let Nodes become direct MCP servers for Agent execution.
