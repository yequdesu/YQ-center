import type { AgentSessionMessage, SseEvent } from "@/api/types";
import type {
  AssistantTextBlock,
  ChatBlock,
  PersistedTranscriptInput,
  PromptContextData,
  SystemEventBlock,
  ToolCallState,
  ToolGroupBlock,
  TranscriptState,
  UserBlock,
} from "./types";

export function emptyTranscript(): TranscriptState {
  return { blocks: [], promptContext: null };
}

export function reduceSseEvent(state: TranscriptState, event: SseEvent): TranscriptState {
  const data = event.data as Record<string, unknown>;
  const createdAt = event.timestamp || nowISO();

  switch (event.event_type) {
    case "agent.prompt_context":
      return { ...state, promptContext: promptContextFromData(data) };

    case "agent.prompt.received": {
      if (data.internal === true) return state;
      const content = String(data.prompt ?? "");
      if (!content) return state;
      return appendBlockOnce(state, {
        type: "user",
        id: `user:${event.event_id}`,
        content,
        created_at: createdAt,
      } as UserBlock);
    }

    case "agent.provider.started":
      return upsertRunStatus(state, "Thinking", createdAt);

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

    case "agent.tool_call.completed":
      return patchToolCall(state, data, (tool) => ({
        ...tool,
        status: "succeeded",
        result: asRecord(data.result),
        targetNodeId: optionalString(data.target_node_id) ?? tool.targetNodeId,
      }));

    case "agent.tool_call.waiting_approval":
      return patchToolCall(state, data, (tool) => ({
        ...tool,
        status: "waiting_approval",
        approvalId: String(data.approval_id ?? ""),
        errorMessage: String(data.message ?? "Approval required"),
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

  return { blocks: blocksFromMessages(input.messages), promptContext: null };
}

export function applyToolPatch(state: TranscriptState, patch: {
  callId?: string;
  approvalId?: string;
  status?: ToolCallState["status"];
  targetNodeId?: string;
  invocationId?: string;
  jobId?: string;
  result?: Record<string, unknown>;
  errorCode?: string | null;
  errorMessage?: string | null;
}): TranscriptState {
  return {
    ...state,
    blocks: state.blocks.map((block) => {
      if (block.type !== "tool_group") return block;
      let changed = false;
      const toolCalls = block.tool_calls.map((tool) => {
        const matchesCallId = patch.callId && tool.callId === patch.callId;
        const matchesApprovalId = patch.approvalId && tool.approvalId === patch.approvalId;
        if (!matchesCallId && !matchesApprovalId) return tool;
        changed = true;
        return {
          ...tool,
          ...(patch.status ? { status: patch.status } : {}),
          ...(patch.targetNodeId !== undefined ? { targetNodeId: patch.targetNodeId } : {}),
          ...(patch.invocationId !== undefined ? { invocationId: patch.invocationId } : {}),
          ...(patch.jobId !== undefined ? { jobId: patch.jobId } : {}),
          ...(patch.result !== undefined ? { result: patch.result } : {}),
          ...(patch.errorCode !== undefined ? { errorCode: patch.errorCode ?? undefined } : {}),
          ...(patch.errorMessage !== undefined
            ? { errorMessage: patch.errorMessage ?? undefined }
            : {}),
        };
      });
      return changed ? { ...block, tool_calls: toolCalls } : block;
    }),
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

function appendBlockOnce(state: TranscriptState, block: ChatBlock): TranscriptState {
  if (state.blocks.some((existing) => existing.id === block.id)) return state;
  return { ...state, blocks: [...state.blocks, block] };
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

function nowISO(): string {
  return new Date().toISOString();
}
