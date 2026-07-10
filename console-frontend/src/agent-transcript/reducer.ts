import type { AgentSessionMessage, SseEvent } from "@/api/types";
import type {
  ArtifactPresentationBlock,
  AssistantTextBlock,
  ChatBlock,
  OperationCardBlock,
  PersistedTranscriptInput,
  PromptContextData,
  SystemEventBlock,
  ToolCallState,
  ToolGroupBlock,
  TranscriptState,
  UserBlock,
  YcrTokenSummary,
  YcrTraceItem,
} from "./types";

export function emptyTranscript(): TranscriptState {
  return {
    blocks: [],
    promptContext: null,
    ycrTrace: [],
    ycrTokenSummary: emptyYcrTokenSummary(),
  };
}

export function appendOptimisticUserPrompt(
  state: TranscriptState,
  prompt: string,
  eventId: string,
  createdAt: string,
): TranscriptState {
  const content = prompt.trim();
  if (!content) return state;
  return appendBlockOnce(state, {
    type: "user",
    id: `user:optimistic:${eventId}`,
    content,
    created_at: createdAt,
    optimistic: true,
  } as UserBlock);
}

export function reduceSseEvent(state: TranscriptState, event: SseEvent): TranscriptState {
  const data = event.data as Record<string, unknown>;
  const createdAt = event.timestamp || nowISO();

  switch (event.event_type) {
    case "agent.prompt_context":
      return { ...state, promptContext: promptContextFromData(data) };

    case "agent.context_block.loaded": {
      const blocks = Array.isArray(data.context_blocks) ? data.context_blocks.length : 0;
      return appendSystemEvent(
        state,
        event,
        blocks > 0 ? `Loaded ${blocks} context block(s)` : "Loaded context",
      );
    }

    case "agent.prompt.received": {
      if (data.internal === true) return state;
      const content = String(data.prompt ?? "");
      if (!content) return state;
      const replaced = replaceOptimisticUserPrompt(
        state,
        content,
        `user:${event.event_id}`,
        createdAt,
      );
      if (replaced) return replaced;
      return appendBlockOnce(state, {
        type: "user",
        id: `user:${event.event_id}`,
        content,
        created_at: createdAt,
      } as UserBlock);
    }

    case "agent.provider.started":
      return upsertRunStatus(state, "Thinking", createdAt);

    case "agent.ycr.context":
      return appendYcrContextTrace(state, event, data, createdAt);

    case "agent.tool_observation.stored":
      return appendYcrToolStorageTrace(state, event, data, createdAt);

    case "agent.ycr.projection":
      return appendYcrProjectionTrace(state, event, data, createdAt);

    case "agent.ycr.error":
      return appendYcrErrorTrace(state, event, data, createdAt);

    case "agent.output.delta":
      return appendAssistantDelta(state, String(data.content ?? ""), createdAt, event.event_id);

    case "agent.tool_call.created":
      return addToolCall(state, data, createdAt, event.event_id);

    case "agent.tool_call.arguments":
      return patchToolCall(state, data, (tool) => ({
        ...tool,
        input: asRecord(data.input),
        targetNodeId: optionalString(data.target_node_id) ?? tool.targetNodeId,
      }));

    case "agent.invocation.created":
      return patchToolCall(state, data, (tool) => ({
        ...tool,
        invocationId: String(data.invocation_id ?? ""),
        targetNodeId: optionalString(data.target_node_id) ?? tool.targetNodeId,
      }));

    case "agent.job.queued":
      return patchToolCall(state, data, (tool) => ({
        ...tool,
        jobId: String(data.job_id ?? ""),
        status: "running",
        targetNodeId: optionalString(data.target_node_id) ?? tool.targetNodeId,
      }));

    case "agent.job.running":
      return patchToolCall(state, data, (tool) => ({
        ...tool,
        status: "running",
        targetNodeId: optionalString(data.target_node_id) ?? tool.targetNodeId,
      }));

    case "agent.job.finished":
      return patchToolCall(state, data, (tool) => ({
        ...tool,
        targetNodeId: optionalString(data.target_node_id) ?? tool.targetNodeId,
      }));

    case "agent.tool_call.completed": {
      const next = patchToolCall(state, data, (tool) => ({
        ...tool,
        status: "succeeded",
        result: asRecord(data.result),
        targetNodeId: optionalString(data.target_node_id) ?? tool.targetNodeId,
      }));
      let updated = next;
      if (String(data.name ?? "") === "capability.search") {
        updated = appendRegistrySearchTrace(updated, event, data, createdAt);
      }
      if (hasArtifacts(data.result)) {
        updated = appendArtifactPresentation(updated, data, createdAt);
      }
      return updated;
    }

    case "agent.tool_call.waiting_approval":
      return patchToolCall(state, data, (tool) => ({
        ...tool,
        status: "waiting_approval",
        approvalId: String(data.approval_id ?? ""),
        errorMessage: undefined,
        targetNodeId: optionalString(data.target_node_id) ?? tool.targetNodeId,
      }));

    case "agent.tool_call.waiting_operation":
      return patchToolCall(state, data, (tool) => ({
        ...tool,
        status: "waiting_operation",
        operationId: optionalString(data.operation_id) ?? tool.operationId,
        waitHandle: asRecord(data.wait_handle),
        result: asRecord(data.result),
        targetNodeId: optionalString(data.target_node_id) ?? tool.targetNodeId,
      }));

    case "agent.tool_call.failed":
      return patchToolCall(state, data, (tool) => ({
        ...tool,
        status: "failed",
        errorCode: String(data.error_code ?? ""),
        errorMessage: String(data.message ?? "Tool failed"),
        targetNodeId: optionalString(data.target_node_id) ?? tool.targetNodeId,
      }));

    case "agent.completed":
      return appendSystemEvent(finishStreaming(state), event, data.status
        ? `Completed (${String(data.status)})`
        : "Completed");

    case "agent.failed":
    case "agent.provider.failed":
      return appendSystemEvent(
        finishStreaming(state),
        event,
        `Failed: ${String(data.message ?? "Unknown error")}`,
      );

    case "agent.approval.required":
      if (data.approval_id && data.call_id) return state;
      return appendSystemEvent(
        state,
        event,
        `Approval required: ${String(data.message ?? "")}`,
      );

    case "agent.operation.created":
      return upsertOperationCard(state, data, createdAt);

    case "agent.operation.waiting":
      return upsertOperationCard(state, data, createdAt);

    case "agent.operation.completed":
      return upsertOperationCard(state, data, createdAt);

    case "agent.run.waiting":
      if (String(data.reason ?? "") === "waiting_operation") {
        return finishStreaming(removeRunStatus(state));
      }
      return state;

    case "stream.close":
      return finishStreaming(removeRunStatus(state));

    default:
      return state;
  }
}

export function transcriptFromPersisted(input: PersistedTranscriptInput): TranscriptState {
  const turnEvents = (input.turns ?? [])
    .flatMap((turn) => turn.events ?? [])
    .sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? "") || a.seq - b.seq);

  if (turnEvents.length > 0) {
    return turnEvents.reduce(
      (state, event) =>
        reduceSseEvent(state, {
          event_id: event.event_id,
          event_type: event.event_type,
          session_id: event.session_id,
          trace_id: event.trace_id,
          timestamp: event.created_at ?? nowISO(),
          data: event.data,
        }),
      emptyTranscript(),
    );
  }

  return {
    blocks: blocksFromMessages(input.messages),
    promptContext: null,
    ycrTrace: [],
    ycrTokenSummary: emptyYcrTokenSummary(),
  };
}

function appendAssistantDelta(
  state: TranscriptState,
  content: string,
  createdAt: string,
  eventId: string,
): TranscriptState {
  if (!content) return state;
  const blocks = removeRunStatus(state).blocks;
  const last = blocks[blocks.length - 1];
  if (last?.type === "assistant_text" && last.streaming) {
    return {
      ...state,
      blocks: [
        ...blocks.slice(0, -1),
        { ...last, content: last.content + content, streaming: true },
      ],
    };
  }
  return {
    ...state,
    blocks: [
      ...blocks,
      {
        type: "assistant_text",
        id: `assistant:${eventId}`,
        content,
        streaming: true,
        created_at: createdAt,
      } as AssistantTextBlock,
    ],
  };
}

function addToolCall(
  state: TranscriptState,
  data: Record<string, unknown>,
  createdAt: string,
  eventId: string,
): TranscriptState {
  const callId = String(data.call_id ?? "");
  const toolCall: ToolCallState = {
    callId,
    name: String(data.name ?? ""),
    input: asRecord(data.input),
    status: "pending",
    targetNodeId: optionalString(data.target_node_id),
  };
  const blocks = finishStreaming(removeRunStatus(state)).blocks;
  const last = blocks[blocks.length - 1];
  if (last?.type === "tool_group") {
    const exists = last.tool_calls.some((tool) => tool.callId === callId);
    return {
      ...state,
      blocks: [
        ...blocks.slice(0, -1),
        {
          ...last,
          tool_calls: exists
            ? last.tool_calls.map((tool) => (tool.callId === callId ? toolCall : tool))
            : [...last.tool_calls, toolCall],
        },
      ],
    };
  }
  return {
    ...state,
    blocks: [
      ...blocks,
      {
        type: "tool_group",
        id: `tool_group:${eventId}`,
        tool_calls: [toolCall],
        created_at: createdAt,
      } as ToolGroupBlock,
    ],
  };
}

function patchToolCall(
  state: TranscriptState,
  data: Record<string, unknown>,
  patch: (tool: ToolCallState) => ToolCallState,
): TranscriptState {
  const callId = String(data.call_id ?? "");
  const blocks = [...state.blocks];
  for (let i = blocks.length - 1; i >= 0; i -= 1) {
    const block = blocks[i];
    if (block.type !== "tool_group") continue;
    if (!block.tool_calls.some((tool) => tool.callId === callId)) continue;
    blocks[i] = {
      ...block,
      tool_calls: block.tool_calls.map((tool) =>
        tool.callId === callId ? patch(tool) : tool,
      ),
    };
    return { ...state, blocks };
  }
  return state;
}

function finishStreaming(state: TranscriptState): TranscriptState {
  return {
    ...state,
    blocks: state.blocks
      .filter((block) => block.type !== "assistant_text" || block.content.trim())
      .map((block) =>
        block.type === "assistant_text" ? { ...block, streaming: false } : block,
      ),
  };
}

function upsertRunStatus(
  state: TranscriptState,
  label: string,
  createdAt: string,
): TranscriptState {
  const blocks = removeRunStatus(state).blocks;
  return {
    ...state,
    blocks: [
      ...blocks,
      { type: "run_status", id: "run_status:active", label, created_at: createdAt },
    ],
  };
}

function removeRunStatus(state: TranscriptState): TranscriptState {
  return {
    ...state,
    blocks: state.blocks.filter((block) => block.type !== "run_status"),
  };
}

function appendSystemEvent(
  state: TranscriptState,
  event: SseEvent,
  label: string,
): TranscriptState {
  return appendBlockOnce(state, {
    type: "system_event",
    id: `system:${event.event_id}`,
    label,
    created_at: event.timestamp || nowISO(),
  } as SystemEventBlock);
}

function appendArtifactPresentation(
  state: TranscriptState,
  data: Record<string, unknown>,
  createdAt: string,
): TranscriptState {
  const result = asRecord(data.result);
  const artifacts = collectArtifactList(result);
  const callId = String(data.call_id ?? "");
  if (!callId || artifacts.length === 0) return state;
  return appendBlockOnce(state, {
    type: "artifact_presentation",
    id: `artifact_presentation:${callId}`,
    artifacts,
    created_at: createdAt,
  } as ArtifactPresentationBlock);
}

function upsertOperationCard(
  state: TranscriptState,
  data: Record<string, unknown>,
  createdAt: string,
): TranscriptState {
  const operationId = optionalString(data.operation_id);
  if (!operationId) return state;
  const id = `operation:${operationId}`;
  const patch = (existing?: OperationCardBlock): OperationCardBlock => ({
    type: "operation_card",
    id,
    operationId,
    kind: optionalString(data.kind) ?? existing?.kind ?? "operation",
    status: optionalString(data.status) ?? existing?.status ?? "running",
    title: optionalString(data.title) ?? existing?.title,
    refType: optionalString(data.ref_type) ?? existing?.refType,
    refId: optionalString(data.ref_id) ?? existing?.refId,
    waitHandle: Object.keys(asRecord(data.wait_handle)).length
      ? asRecord(data.wait_handle)
      : existing?.waitHandle,
    message: optionalString(data.message) ?? existing?.message,
    progressPct: optionalNumber(data.progress_pct) ?? existing?.progressPct,
    progressMessage: optionalString(data.progress_message) ?? existing?.progressMessage,
    errorCode: optionalString(data.error_code) ?? existing?.errorCode,
    errorMessage: optionalString(data.error_message) ?? existing?.errorMessage,
    created_at: existing?.created_at ?? createdAt,
  });

  const blocks = state.blocks.map((block) =>
    block.type === "operation_card" && block.operationId === operationId
      ? patch(block)
      : block,
  );
  if (blocks.some((block) => block.type === "operation_card" && block.operationId === operationId)) {
    return { ...state, blocks };
  }
  return { ...state, blocks: [...removeRunStatus(state).blocks, patch()] };
}

function replaceOptimisticUserPrompt(
  state: TranscriptState,
  content: string,
  serverId: string,
  createdAt: string,
): TranscriptState | null {
  for (let index = state.blocks.length - 1; index >= 0; index -= 1) {
    const block = state.blocks[index];
    if (block.type !== "user" || !block.optimistic || block.content !== content) {
      continue;
    }
    const blocks = [...state.blocks];
    blocks[index] = {
      ...block,
      id: serverId,
      created_at: createdAt,
      optimistic: false,
    };
    return { ...state, blocks };
  }
  return null;
}

function appendBlockOnce(state: TranscriptState, block: ChatBlock): TranscriptState {
  if (state.blocks.some((existing) => existing.id === block.id)) return state;
  return { ...state, blocks: [...state.blocks, block] };
}

function emptyYcrTokenSummary(): YcrTokenSummary {
  return {
    uploadEstimatedTokens: 0,
    downloadEstimatedTokens: 0,
    uploadActualTokens: 0,
    downloadActualTokens: 0,
    toolRawEstimatedTokens: 0,
    toolProjectedEstimatedTokens: 0,
    toolSavedEstimatedTokens: 0,
    projectionCount: 0,
    providerCallCount: 0,
  };
}

function appendYcrContextTrace(
  state: TranscriptState,
  event: SseEvent,
  data: Record<string, unknown>,
  createdAt: string,
): TranscriptState {
  const phase = String(data.phase ?? "");
  const tokens = asRecord(data.tokens);
  const ycrState = asRecord(data.state);
  const snapshot = asRecord(ycrState.capability_context_snapshot);
  const sessionStateCounts = asRecord(ycrState.session_state_counts);
  const item: YcrTraceItem = {
    id: `ycr:${event.event_id}`,
    kind: phase === "provider_output" ? "provider_output" : "provider_input",
    label: phase === "provider_output" ? "Provider output" : "Provider input",
    created_at: createdAt,
    step: optionalNumber(data.step),
    providerName: optionalString(data.provider_name),
    model: optionalString(data.model),
    toolCount: optionalNumber(data.tool_count),
    uploadEstimatedTokens: optionalNumber(tokens.upload_estimated),
    downloadEstimatedTokens: optionalNumber(tokens.download_estimated),
    uploadActualTokens: optionalNumber(tokens.upload_actual),
    downloadActualTokens: optionalNumber(tokens.download_actual),
    totalActualTokens: optionalNumber(tokens.total_actual),
    ycrState,
    snapshotStatus: optionalString(snapshot.status),
    capabilityCandidateCount: optionalNumber(ycrState.capability_candidate_count),
    sessionStateCounts,
    data,
  };
  const summary = {
    ...state.ycrTokenSummary,
    uploadEstimatedTokens:
      state.ycrTokenSummary.uploadEstimatedTokens + (item.uploadEstimatedTokens ?? 0),
    downloadEstimatedTokens:
      state.ycrTokenSummary.downloadEstimatedTokens + (item.downloadEstimatedTokens ?? 0),
    uploadActualTokens: state.ycrTokenSummary.uploadActualTokens + (item.uploadActualTokens ?? 0),
    downloadActualTokens:
      state.ycrTokenSummary.downloadActualTokens + (item.downloadActualTokens ?? 0),
    providerCallCount:
      state.ycrTokenSummary.providerCallCount + (item.kind === "provider_input" ? 1 : 0),
  };
  return appendYcrTraceItem(state, item, summary);
}

function appendYcrToolStorageTrace(
  state: TranscriptState,
  event: SseEvent,
  data: Record<string, unknown>,
  createdAt: string,
): TranscriptState {
  const rawTokens = optionalNumber(data.raw_estimated_tokens) ?? 0;
  const rawRef = asRecord(data.raw_ref);
  const callId = optionalString(data.call_id);
  const item: YcrTraceItem = {
    id: `ycr:${event.event_id}`,
    kind: "tool_storage",
    label: "Tool result stored",
    created_at: createdAt,
    step: optionalNumber(data.step),
    toolName: optionalString(data.name),
    callId,
    targetNodeId: optionalString(data.target_node_id),
    projectionPolicy: optionalString(asRecord(data.ycr).projection_policy),
    summary: optionalString(rawRef.summary),
    rawEstimatedTokens: rawTokens,
    rawSizeBytes: optionalNumber(data.raw_size_bytes),
    projectedSizeBytes: optionalNumber(data.shell_size_bytes),
    refCount: rawRef.ref_id ? 1 : undefined,
    data,
  };
  const next = callId
    ? patchToolCall(state, { call_id: callId }, (tool) => ({
        ...tool,
        rawRefId: optionalString(rawRef.ref_id) ?? tool.rawRefId,
        rawSizeBytes: item.rawSizeBytes ?? tool.rawSizeBytes,
        shellSizeBytes: optionalNumber(data.shell_size_bytes) ?? tool.shellSizeBytes,
        ycrStorage: data,
      }))
    : state;
  const summary = {
    ...next.ycrTokenSummary,
    toolRawEstimatedTokens: next.ycrTokenSummary.toolRawEstimatedTokens + rawTokens,
  };
  return appendYcrTraceItem(next, item, summary);
}

function appendYcrProjectionTrace(
  state: TranscriptState,
  event: SseEvent,
  data: Record<string, unknown>,
  createdAt: string,
): TranscriptState {
  const estimate = asRecord(data.context_estimate);
  const rawTokens =
    optionalNumber(data.raw_estimated_tokens) ??
    optionalNumber(estimate.raw_estimated_tokens) ??
    0;
  const projectedTokens =
    optionalNumber(data.projected_estimated_tokens) ??
    optionalNumber(estimate.projected_estimated_tokens) ??
    0;
  const rawSizeBytes =
    optionalNumber(data.raw_size_bytes) ?? optionalNumber(estimate.raw_size_bytes);
  const projectedSizeBytes =
    optionalNumber(data.projected_size_bytes) ?? optionalNumber(estimate.projected_size_bytes);
  const refs = Array.isArray(data.refs) ? data.refs : [];
  const callId = optionalString(data.call_id);
  const item: YcrTraceItem = {
    id: `ycr:${event.event_id}`,
    kind: "provider_projection",
    label: "Provider projection",
    created_at: createdAt,
    step: optionalNumber(data.step),
    toolName: optionalString(data.name),
    callId,
    targetNodeId: optionalString(data.target_node_id),
    projectionPolicy: optionalString(data.projection_policy),
    summary: optionalString(data.summary),
    rawEstimatedTokens: rawTokens,
    projectedEstimatedTokens: projectedTokens,
    rawSizeBytes,
    projectedSizeBytes,
    refCount: refs.length || optionalNumber(data.ref_count) || optionalNumber(estimate.ref_count),
    omittedCount: optionalNumber(data.omitted_count) || optionalNumber(estimate.omitted_count),
    data,
  };
  const next = callId
    ? patchToolCall(state, { call_id: callId }, (tool) => ({
        ...tool,
        projectedEstimatedTokens: projectedTokens || tool.projectedEstimatedTokens,
        rawSizeBytes: rawSizeBytes ?? tool.rawSizeBytes,
        projectedSizeBytes: projectedSizeBytes ?? tool.projectedSizeBytes,
        ycrProjection: data,
      }))
    : state;
  const summary = {
    ...next.ycrTokenSummary,
    toolProjectedEstimatedTokens:
      next.ycrTokenSummary.toolProjectedEstimatedTokens + projectedTokens,
    toolSavedEstimatedTokens:
      next.ycrTokenSummary.toolSavedEstimatedTokens + Math.max(0, rawTokens - projectedTokens),
    projectionCount: next.ycrTokenSummary.projectionCount + 1,
  };
  return appendYcrTraceItem(next, item, summary);
}

function appendYcrErrorTrace(
  state: TranscriptState,
  event: SseEvent,
  data: Record<string, unknown>,
  createdAt: string,
): TranscriptState {
  const item: YcrTraceItem = {
    id: `ycr:${event.event_id}`,
    kind: "error",
    label: "YCR error",
    created_at: createdAt,
    step: optionalNumber(data.step),
    summary: optionalString(data.message) ?? optionalString(data.error_code),
    data,
  };
  return appendYcrTraceItem(state, item, state.ycrTokenSummary);
}

function appendRegistrySearchTrace(
  state: TranscriptState,
  event: SseEvent,
  data: Record<string, unknown>,
  createdAt: string,
): TranscriptState {
  const result = asRecord(data.result);
  const capabilities = Array.isArray(result.capabilities)
    ? result.capabilities.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item) && typeof item === "object" && !Array.isArray(item),
      )
    : [];
  const retrieval = asRecord(result.retrieval);
  const index = asRecord(retrieval.index);
  const strategy = optionalString(retrieval.strategy);
  const indexStatus = optionalString(index.status);
  const retryAfter = optionalNumber(index.retry_after_seconds);
  const summary =
    indexStatus === "not_ready"
      ? `Capability index not ready; retry after ${retryAfter ?? 5}s.`
      : `${capabilities.length} registry match(es)${strategy ? ` via ${strategy}` : ""}.`;
  const item: YcrTraceItem = {
    id: `registry:${event.event_id}`,
    kind: "registry_search",
    label: "Registry search",
    created_at: createdAt,
    step: optionalNumber(data.step),
    toolName: "capability.search",
    callId: optionalString(data.call_id),
    summary,
    registryMatches: capabilities,
    retrieval,
    data: result,
  };
  return appendYcrTraceItem(state, item, state.ycrTokenSummary);
}

function appendYcrTraceItem(
  state: TranscriptState,
  item: YcrTraceItem,
  summary: YcrTokenSummary,
): TranscriptState {
  if (state.ycrTrace.some((existing) => existing.id === item.id)) return state;
  return {
    ...state,
    ycrTrace: [...state.ycrTrace, item],
    ycrTokenSummary: summary,
  };
}

function blocksFromMessages(messages: AgentSessionMessage[]): ChatBlock[] {
  const blocks: ChatBlock[] = [];

  for (const message of messages) {
    if (message.role === "system") continue;
    if (message.role === "user") {
      blocks.push({
        type: "user",
        id: message.message_id,
        content: message.content ?? "",
        created_at: message.created_at ?? nowISO(),
      });
      continue;
    }
    if (message.role === "assistant") {
      const hasContent = Boolean(message.content);
      if (hasContent) {
        blocks.push({
          type: "assistant_text",
          id: `${message.message_id}:text`,
          content: message.content ?? "",
          streaming: false,
          created_at: message.created_at ?? nowISO(),
        });
      }
      const visibleToolCalls = message.tool_calls || [];
      if (visibleToolCalls.length > 0) {
        blocks.push({
          type: "tool_group",
          id: `${message.message_id}:tools`,
          tool_calls: visibleToolCalls.map(toolCallFromPersisted),
          created_at: message.created_at ?? nowISO(),
        });
      }
      continue;
    }
    if (message.role === "tool") {
      patchToolMessage(blocks, message);
    }
  }

  return blocks;
}

function patchToolMessage(blocks: ChatBlock[], message: AgentSessionMessage): void {
  const callId = message.tool_call_id;
  if (!callId) return;
  const parsed = parseToolMessageContent(message.content);
  for (let i = blocks.length - 1; i >= 0; i -= 1) {
    const block = blocks[i];
    if (block.type !== "tool_group") continue;
    const idx = block.tool_calls.findIndex((tool) => tool.callId === callId);
    if (idx < 0) continue;
    const updated = [...block.tool_calls];
    updated[idx] = {
      ...updated[idx],
      status: parseToolStatus(parsed.status),
      result: asRecord(parsed.result),
      errorMessage: parsed.error ? String(parsed.error) : updated[idx].errorMessage,
      approvalId: parsed.approval_id ? String(parsed.approval_id) : updated[idx].approvalId,
      targetNodeId: optionalString(parsed.target_node_id) ?? updated[idx].targetNodeId,
      rawRefId:
        optionalString(asRecord(parsed.ycr).raw_ref) ??
        optionalString(parsed.raw_ref_id) ??
        updated[idx].rawRefId,
      ycrStorage: asRecord(parsed.ycr).raw_ref
        ? { shell: parsed, raw_ref: { ref_id: asRecord(parsed.ycr).raw_ref } }
        : updated[idx].ycrStorage,
    };
    blocks[i] = { ...block, tool_calls: updated };
    return;
  }
}

function toolCallFromPersisted(item: Record<string, unknown>): ToolCallState {
  return {
    callId: String(item.call_id ?? ""),
    name: String(item.name ?? ""),
    input: asRecord(item.input),
    status: parseToolStatus(item.status),
    targetNodeId: optionalString(item.target_node_id),
    invocationId: item.invocation_id ? String(item.invocation_id) : undefined,
    jobId: item.job_id ? String(item.job_id) : undefined,
    result: asRecord(item.result),
    errorCode: item.error_code ? String(item.error_code) : undefined,
    errorMessage: item.error_message ? String(item.error_message) : undefined,
    ycrStorage: asRecord(item.ycr_storage),
    ycrProjection: asRecord(item.ycr_projection),
    rawRefId: optionalString(item.raw_ref_id),
    rawSizeBytes: optionalNumber(item.raw_size_bytes),
    shellSizeBytes: optionalNumber(item.shell_size_bytes),
    projectedSizeBytes: optionalNumber(item.projected_size_bytes),
    projectedEstimatedTokens: optionalNumber(item.projected_estimated_tokens),
  };
}

function promptContextFromData(data: Record<string, unknown>): PromptContextData {
  return {
    provider_name: String(data.provider_name ?? ""),
    system_prompt: String(data.system_prompt ?? ""),
    target_node_id: optionalString(data.target_node_id) ?? null,
    execution_mode: String(data.execution_mode ?? ""),
    routing_mode: optionalString(data.routing_mode),
    capability_context: asRecord(data.capability_context),
    nodes: Array.isArray(data.nodes) ? data.nodes : [],
    tool_count_by_node: asRecord(data.tool_count_by_node),
    available_functions: Array.isArray(data.available_functions)
      ? (data.available_functions as Array<Record<string, unknown>>).map((f) => ({
          name: String(f.name ?? ""),
          description: String(f.description ?? ""),
          risk: String(f.risk ?? "safe"),
          effect: String(f.effect ?? "read"),
          source_nodes: Array.isArray(f.source_nodes)
            ? f.source_nodes.map(String)
            : [],
        }))
      : [],
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function hasArtifacts(value: unknown): boolean {
  return collectArtifactList(value).length > 0;
}

function collectArtifactList(value: unknown): Array<Record<string, unknown>> {
  const found: Array<Record<string, unknown>> = [];
  collectArtifacts(value, found, 0);
  const seen = new Set<string>();
  return found.filter((artifact) => {
    const key = String(artifact.artifact_id ?? artifact.download_url ?? "");
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function collectArtifacts(value: unknown, found: Array<Record<string, unknown>>, depth: number): void {
  if (depth > 5 || value === null || value === undefined) return;
  if (Array.isArray(value)) {
    for (const item of value) collectArtifacts(item, found, depth + 1);
    return;
  }
  if (typeof value !== "object") return;

  const record = value as Record<string, unknown>;
  if (isArtifactRecord(record)) {
    found.push(record);
  }

  const artifact = record.artifact;
  if (artifact && typeof artifact === "object" && !Array.isArray(artifact)) {
    const artifactRecord = artifact as Record<string, unknown>;
    if (isArtifactRecord(artifactRecord)) found.push(artifactRecord);
  }

  const artifacts = record.artifacts;
  if (Array.isArray(artifacts)) {
    for (const item of artifacts) {
      if (item && typeof item === "object" && !Array.isArray(item) && isArtifactRecord(item as Record<string, unknown>)) {
        found.push(item as Record<string, unknown>);
      } else {
        collectArtifacts(item, found, depth + 1);
      }
    }
  }

  for (const key of ["result", "output", "data"]) {
    collectArtifacts(record[key], found, depth + 1);
  }
}

function isArtifactRecord(record: Record<string, unknown>): boolean {
  return (
    typeof record.artifact_id === "string" &&
    (typeof record.download_url === "string" || typeof record.content_type === "string")
  );
}

function parseToolMessageContent(content: string | null): Record<string, unknown> {
  if (!content) return {};
  try {
    return asRecord(JSON.parse(content));
  } catch {
    return { status: "failed", error: content };
  }
}

function parseToolStatus(value: unknown): ToolCallState["status"] {
  if (
    value === "succeeded" ||
    value === "failed" ||
    value === "waiting_approval" ||
    value === "waiting_operation" ||
    value === "denied"
  ) {
    return value;
  }
  if (value === "running") return "running";
  return "pending";
}

function optionalString(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  return value.trim() ? value : undefined;
}

function optionalNumber(value: unknown): number | undefined {
  if (typeof value !== "number" || !Number.isFinite(value)) return undefined;
  return value;
}

function nowISO(): string {
  return new Date().toISOString();
}
