import { useCallback, useRef, useState } from "react";
import { createEventStream } from "@/api/stream";
import type { AgentSessionMessage, SseEvent } from "@/api/types";

// ── Chat Block types ──

export interface UserBlock {
  type: "user";
  id: string;
  content: string;
  created_at: string;
}

export interface AssistantTextBlock {
  type: "assistant_text";
  id: string;
  content: string;
  streaming: boolean;
  created_at: string;
}

export interface ToolGroupBlock {
  type: "tool_group";
  id: string;
  tool_calls: ToolCallState[];
  created_at: string;
}

export interface SystemEventBlock {
  type: "system_event";
  id: string;
  label: string;
  created_at: string;
}

export type ChatBlock =
  | UserBlock
  | AssistantTextBlock
  | ToolGroupBlock
  | SystemEventBlock;

// ── Tool Call State ──

export interface ToolCallState {
  callId: string;
  name: string;
  input: Record<string, unknown>;
  status: "pending" | "running" | "succeeded" | "failed" | "waiting_approval" | "denied";
  targetNodeId?: string;
  invocationId?: string;
  jobId?: string;
  approvalId?: string;
  result?: Record<string, unknown>;
  errorCode?: string;
  errorMessage?: string;
}

export interface ToolCallPatch {
  callId?: string;
  approvalId?: string;
  status?: ToolCallState["status"];
  targetNodeId?: string;
  invocationId?: string;
  jobId?: string;
  result?: Record<string, unknown>;
  errorCode?: string | null;
  errorMessage?: string | null;
}

export interface PlanStepState {
  seq: number;
  kind: string;
  functionName: string;
  requiresApproval: boolean;
}

interface UseAgentChatOptions {
  sessionId: string;
  onPlanCreated?: (planId: string) => void;
  onConversationSettled?: () => void;
}

interface SendInvokeOptions {
  visible?: boolean;
  suppressUserMessage?: boolean;
}

// ── Helpers ──

function nowISO(): string {
  return new Date().toISOString();
}

function genId(): string {
  return crypto.randomUUID();
}

// ── Hook ──

export function useAgentChat({
  sessionId,
  onPlanCreated,
  onConversationSettled,
}: UseAgentChatOptions) {
  const [blocks, setBlocks] = useState<ChatBlock[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const abortRef = useRef<(() => void) | null>(null);

  const clearBlocks = useCallback(() => {
    setBlocks([]);
  }, []);

  const loadPersistedMessages = useCallback(
    (persisted: AgentSessionMessage[]) => {
      setBlocks(blocksFromPersisted(persisted));
    },
    [],
  );

  // ── Block manipulation helpers ──

  const addBlock = useCallback((block: ChatBlock) => {
    setBlocks((prev) => [...prev, block]);
  }, []);

  /** Update the last block if it matches `predicate`, otherwise create a new block. */
  const upsertLastBlock = useCallback(
    <T extends ChatBlock>(
      predicate: (b: ChatBlock) => b is T,
      create: () => T,
      update: (prev: T) => T,
    ) => {
      setBlocks((prev) => {
        if (prev.length === 0) {
          return [create()];
        }
        const last = prev[prev.length - 1];
        if (predicate(last)) {
          const updated = [...prev];
          updated[updated.length - 1] = update(last);
          return updated;
        }
        return [...prev, create()];
      });
    },
    [],
  );

  const patchToolCall = useCallback((patch: ToolCallPatch) => {
    setBlocks((prev) =>
      prev.map((block) => {
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
            ...(patch.errorMessage !== undefined ? { errorMessage: patch.errorMessage ?? undefined } : {}),
          };
        });
        return changed ? { ...block, tool_calls: toolCalls } : block;
      }),
    );
  }, []);

  // ── SSE event handlers ──

  const addAssistantThinkingBlock = useCallback(() => {
    addBlock({
      type: "assistant_text",
      id: genId(),
      content: "",
      streaming: true,
      created_at: nowISO(),
    } as AssistantTextBlock);
  }, [addBlock]);

  const finishAssistantStreaming = useCallback(() => {
    setBlocks((prev) =>
      prev
        .filter(
          (block) =>
            block.type !== "assistant_text" ||
            block.content.trim() ||
            !block.streaming,
        )
        .map((block) =>
          block.type === "assistant_text"
            ? ({ ...block, streaming: false } as AssistantTextBlock)
            : block,
        ),
    );
  }, []);

  const removeTrailingEmptyThinkingBlock = useCallback(() => {
    setBlocks((prev) => {
      const last = prev[prev.length - 1];
      if (
        last?.type === "assistant_text" &&
        last.streaming &&
        !last.content.trim()
      ) {
        return prev.slice(0, -1);
      }
      return prev;
    });
  }, []);

  const handleInvokeEvent = useCallback(
    (event: SseEvent) => {
      const data = event.data as Record<string, unknown>;

      switch (event.event_type) {
        case "agent.provider.started": {
          // Optionally start an assistant_text block — but we don't create one yet.
          // We wait for the first real delta or tool_call to determine what comes first.
          upsertLastBlock(
            (b): b is AssistantTextBlock =>
              b.type === "assistant_text" && b.streaming && !b.content.trim(),
            () => ({
              type: "assistant_text",
              id: genId(),
              content: "",
              streaming: true,
              created_at: nowISO(),
            }),
            (prev) => prev,
          );
          break;
        }

        case "agent.tool_call.created": {
          removeTrailingEmptyThinkingBlock();
          const callId = String(data.call_id ?? "");
          const toolCall: ToolCallState = {
            callId,
            name: String(data.name ?? ""),
            input: asRecord(data.input),
            status: "pending",
            targetNodeId: optionalString(data.target_node_id),
          };

          upsertLastBlock(
            (b): b is ToolGroupBlock => b.type === "tool_group",
            () => ({
              type: "tool_group",
              id: genId(),
              tool_calls: [toolCall],
              created_at: nowISO(),
            }),
            (prev) => {
              // Avoid duplicate call_ids
              const exists = prev.tool_calls.some((t) => t.callId === callId);
              if (exists) {
                return {
                  ...prev,
                  tool_calls: prev.tool_calls.map((t) =>
                    t.callId === callId ? toolCall : t,
                  ),
                };
              }
              return {
                ...prev,
                tool_calls: [...prev.tool_calls, toolCall],
              };
            },
          );
          break;
        }

        case "agent.tool_call.arguments": {
          patchToolInLastGroup(data, (tool) => ({
            ...tool,
            input: asRecord(data.input),
            targetNodeId: optionalString(data.target_node_id) ?? tool.targetNodeId,
          }));
          break;
        }

        case "agent.invocation.created": {
          patchToolInLastGroup(data, (tool) => ({
            ...tool,
            invocationId: String(data.invocation_id ?? ""),
            targetNodeId: optionalString(data.target_node_id) ?? tool.targetNodeId,
          }));
          break;
        }

        case "agent.job.queued": {
          patchToolInLastGroup(data, (tool) => ({
            ...tool,
            jobId: String(data.job_id ?? ""),
            status: "running" as const,
            targetNodeId: optionalString(data.target_node_id) ?? tool.targetNodeId,
          }));
          break;
        }

        case "agent.job.running": {
          patchToolInLastGroup(data, (tool) => ({
            ...tool,
            status: "running" as const,
            targetNodeId: optionalString(data.target_node_id) ?? tool.targetNodeId,
          }));
          break;
        }

        case "agent.tool_call.completed": {
          patchToolInLastGroup(data, (tool) => ({
            ...tool,
            status: "succeeded" as const,
            result: asRecord(data.result),
            targetNodeId: optionalString(data.target_node_id) ?? tool.targetNodeId,
          }));
          break;
        }

        case "agent.tool_call.waiting_approval": {
          patchToolInLastGroup(data, (tool) => ({
            ...tool,
            status: "waiting_approval" as const,
            approvalId: String(data.approval_id ?? ""),
            errorMessage: String(data.message ?? "Approval required"),
            targetNodeId: optionalString(data.target_node_id) ?? tool.targetNodeId,
          }));
          break;
        }

        case "agent.tool_call.failed": {
          patchToolInLastGroup(data, (tool) => ({
            ...tool,
            status: "failed" as const,
            errorCode: String(data.error_code ?? ""),
            errorMessage: String(data.message ?? "Tool failed"),
            targetNodeId: optionalString(data.target_node_id) ?? tool.targetNodeId,
          }));
          break;
        }

        case "agent.output.delta": {
          const deltaContent = String(data.content ?? "");
          upsertLastBlock(
            (b): b is AssistantTextBlock => b.type === "assistant_text",
            () => ({
              type: "assistant_text",
              id: genId(),
              content: deltaContent,
              streaming: true,
              created_at: nowISO(),
            }),
            (prev) => ({
              ...prev,
              content: prev.content + deltaContent,
              streaming: true,
            }),
          );
          break;
        }

        case "agent.completed": {
          finishAssistantStreaming();
          addBlock({
            type: "system_event",
            id: genId(),
            label: data.status
              ? `Completed (${String(data.status)})`
              : "Completed",
            created_at: nowISO(),
          } as SystemEventBlock);
          setIsStreaming(false);
          break;
        }

        case "agent.failed":
        case "agent.provider.failed": {
          const errorMsg = String(data.message ?? "Unknown error");
          finishAssistantStreaming();
          addBlock({
            type: "system_event",
            id: genId(),
            label: `Failed: ${errorMsg}`,
            created_at: nowISO(),
          } as SystemEventBlock);
          setIsStreaming(false);
          break;
        }

        case "agent.approval.required": {
          // Tool-level approvals are rendered by agent.tool_call.waiting_approval.
          // Rendering both creates a confusing duplicate/flash in the chat.
          if (data.approval_id && data.call_id) {
            break;
          }
          addBlock({
            type: "system_event",
            id: genId(),
            label: `Approval required: ${String(data.message ?? "")}`,
            created_at: nowISO(),
          } as SystemEventBlock);
          break;
        }
      }
    },
    [addBlock, finishAssistantStreaming, removeTrailingEmptyThinkingBlock, upsertLastBlock],
  );

  /** Patch a tool call by call_id in the newest matching tool_group block. */
  function patchToolInLastGroup(
    data: Record<string, unknown>,
    patch: (tool: ToolCallState) => ToolCallState,
  ) {
    const callId = String(data.call_id ?? "");
    setBlocks((prev) => {
      const updated = [...prev];
      for (let i = updated.length - 1; i >= 0; i--) {
        const block = updated[i];
        if (block.type === "tool_group") {
          const hasTool = (block as ToolGroupBlock).tool_calls.some((tool) => tool.callId === callId);
          if (!hasTool) continue;
          updated[i] = {
            ...block,
            tool_calls: (block as ToolGroupBlock).tool_calls.map((tool) =>
              tool.callId === callId ? patch(tool) : tool,
            ),
          } as ToolGroupBlock;
          return updated;
        }
      }
      return prev;
    });
  }

  // ── Send / Cancel ──

  const sendInvoke = useCallback(
    (
      prompt: string,
      targetNodeId: string,
      providerName: string,
      executionMode: string,
      options: SendInvokeOptions = {},
    ) => {
      abortRef.current?.();

      if (options.visible !== false) {
        addBlock({
          type: "user",
          id: genId(),
          content: prompt,
          created_at: nowISO(),
        } as UserBlock);
      }
      addAssistantThinkingBlock();

      setIsStreaming(true);
      const stream = createEventStream(
        "/agent/invoke/stream",
        {
          session_id: sessionId,
          provider_name: providerName,
          prompt,
          target_node_id: targetNodeId || undefined,
          execution_mode: executionMode,
          suppress_user_message: options.suppressUserMessage ?? (options.visible === false),
        },
        {
          onEvent: (event) => handleInvokeEvent(event),
          onError: (error) => {
            setIsStreaming(false);
            finishAssistantStreaming();
            addBlock({
              type: "system_event",
              id: genId(),
              label: `Error: ${error.message}`,
              created_at: nowISO(),
            } as SystemEventBlock);
            onConversationSettled?.();
          },
          onClose: () => {
            setIsStreaming(false);
            finishAssistantStreaming();
            onConversationSettled?.();
          },
        },
      );

      abortRef.current = () => stream.abort();
    },
    [addAssistantThinkingBlock, addBlock, finishAssistantStreaming, handleInvokeEvent, onConversationSettled, sessionId],
  );

  const sendPlan = useCallback(
    (prompt: string, targetNodeId: string, providerName: string) => {
      abortRef.current?.();

      addBlock({
        type: "user",
        id: genId(),
        content: prompt,
        created_at: nowISO(),
      } as UserBlock);
      addAssistantThinkingBlock();

      setIsStreaming(true);
      const stream = createEventStream(
        "/agent/plan/stream",
        {
          session_id: sessionId,
          provider_name: providerName,
          prompt,
          target_node_id: targetNodeId || undefined,
        },
        {
          onEvent: (event) => handlePlanEvent(event),
          onError: (error) => {
            setIsStreaming(false);
            finishAssistantStreaming();
            addBlock({
              type: "system_event",
              id: genId(),
              label: `Error: ${error.message}`,
              created_at: nowISO(),
            } as SystemEventBlock);
            onConversationSettled?.();
          },
          onClose: () => {
            setIsStreaming(false);
            finishAssistantStreaming();
            onConversationSettled?.();
          },
        },
      );

      abortRef.current = () => stream.abort();
    },
    [addAssistantThinkingBlock, addBlock, finishAssistantStreaming, onConversationSettled, sessionId],
  );

  const handlePlanEvent = useCallback(
    (event: SseEvent) => {
      const data = event.data as Record<string, unknown>;

      switch (event.event_type) {
        case "agent.planning.summary": {
          upsertLastBlock(
            (b): b is AssistantTextBlock => b.type === "assistant_text",
            () =>
              ({
                type: "assistant_text",
                id: genId(),
                content: String(data.message ?? "Planning..."),
                streaming: true,
                created_at: nowISO(),
              }) as AssistantTextBlock,
            (prev) => prev,
          );
          break;
        }

        case "agent.plan.created": {
          const planId = String(data.plan_id ?? "");
          upsertLastBlock(
            (b): b is AssistantTextBlock => b.type === "assistant_text",
            () =>
              ({
                type: "assistant_text",
                id: genId(),
                content: `Plan created: ${String(data.goal ?? "")}`,
                streaming: false,
                created_at: nowISO(),
              }) as AssistantTextBlock,
            (prev) => ({ ...prev, streaming: false }),
          );
          if (planId && onPlanCreated) onPlanCreated(planId);
          break;
        }

        case "agent.approval.required": {
          addBlock({
            type: "system_event",
            id: genId(),
            label: "This plan requires approval.",
            created_at: nowISO(),
          } as SystemEventBlock);
          break;
        }

        case "agent.completed": {
          finishAssistantStreaming();
          setIsStreaming(false);
          addBlock({
            type: "system_event",
            id: genId(),
            label: "Plan completed",
            created_at: nowISO(),
          } as SystemEventBlock);
          break;
        }

        case "agent.failed": {
          finishAssistantStreaming();
          setIsStreaming(false);
          addBlock({
            type: "system_event",
            id: genId(),
            label: `Plan failed: ${String(data.message ?? "Unknown error")}`,
            created_at: nowISO(),
          } as SystemEventBlock);
          break;
        }
      }
    },
    [addBlock, finishAssistantStreaming, upsertLastBlock, onPlanCreated],
  );

  const cancel = useCallback(() => {
    abortRef.current?.();
    abortRef.current = null;
    setIsStreaming(false);
    finishAssistantStreaming();
  }, [finishAssistantStreaming]);

  return {
    blocks,
    isStreaming,
    sendInvoke,
    sendPlan,
    cancel,
    clearBlocks,
    loadPersistedMessages,
    patchToolCall,
  };
}

// ── Persisted message → Block reconstruction ──

function blocksFromPersisted(persisted: AgentSessionMessage[]): ChatBlock[] {
  const blocks: ChatBlock[] = [];

  for (const message of persisted) {
    if (message.role === "system") continue;

    if (message.role === "user") {
      blocks.push({
        type: "user",
        id: message.message_id,
        content: message.content ?? "",
        created_at: message.created_at ?? nowISO(),
      } as UserBlock);
      continue;
    }

    if (message.role === "tool") {
      // Find the tool_group containing this tool_call_id and update status
      const callId = message.tool_call_id;
      if (!callId) continue;

      const parsed = parseToolMessageContent(message.content);
      for (let i = blocks.length - 1; i >= 0; i--) {
        const block = blocks[i];
        if (block.type === "tool_group") {
          const tg = block as ToolGroupBlock;
          const toolIdx = tg.tool_calls.findIndex((t) => t.callId === callId);
          if (toolIdx !== -1) {
            const updated = [...tg.tool_calls];
            updated[toolIdx] = {
              ...updated[toolIdx],
              status: parseToolStatus(parsed.status),
              result: asRecord(parsed.result),
              errorMessage: parsed.error
                ? String(parsed.error)
                : updated[toolIdx].errorMessage,
              approvalId: parsed.approval_id
                ? String(parsed.approval_id)
                : updated[toolIdx].approvalId,
              targetNodeId: optionalString(parsed.target_node_id)
                ?? updated[toolIdx].targetNodeId,
            };
            blocks[i] = { ...tg, tool_calls: updated } as ToolGroupBlock;
          }
          break;
        }
      }
      continue;
    }

    // Assistant message
    if (message.role === "assistant") {
      const visibleToolCalls = message.tool_calls || [];
      const hasToolCalls = visibleToolCalls.length > 0;
      const hasContent = Boolean(message.content);

      // Tool calls come first in timeline (LLM decided to call tools before synthesizing)
      if (hasToolCalls) {
        blocks.push({
          type: "tool_group",
          id: `${message.message_id}_tg`,
          tool_calls: toolCallsFromPersisted(visibleToolCalls),
          created_at: message.created_at ?? nowISO(),
        } as ToolGroupBlock);
      }

      // Content comes after tool calls
      if (hasContent) {
        blocks.push({
          type: "assistant_text",
          id: hasToolCalls ? `${message.message_id}_txt` : message.message_id,
          content: message.content ?? "",
          streaming: false,
          created_at: message.created_at ?? nowISO(),
        } as AssistantTextBlock);
      }
    }
  }

  return blocks;
}

// ── Utility functions ──

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function parseToolMessageContent(content: string | null): Record<string, unknown> {
  if (!content) return {};
  try {
    const parsed: unknown = JSON.parse(content);
    return asRecord(parsed);
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

function toolCallsFromPersisted(
  raw: Record<string, unknown>[],
): ToolCallState[] {
  return raw.map((item) => ({
    callId: String(item.call_id ?? ""),
    name: String(item.name ?? ""),
    input: asRecord(item.input),
    status: parseToolStatus(item.status),
    targetNodeId: optionalString(item.target_node_id),
    invocationId: item.invocation_id
      ? String(item.invocation_id)
      : undefined,
    jobId: item.job_id ? String(item.job_id) : undefined,
    result: asRecord(item.result),
    errorCode: item.error_code ? String(item.error_code) : undefined,
    errorMessage: item.error_message
      ? String(item.error_message)
      : undefined,
  }));
}

function optionalString(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  return value.trim() ? value : undefined;
}
