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
  status: "pending" | "running" | "succeeded" | "failed" | "waiting_approval";
  invocationId?: string;
  jobId?: string;
  approvalId?: string;
  result?: Record<string, unknown>;
  errorCode?: string;
  errorMessage?: string;
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

  /** Update a specific block by id. */
  const updateBlock = useCallback(
    (blockId: string, updater: (block: ChatBlock) => ChatBlock) => {
      setBlocks((prev) =>
        prev.map((b) => (b.id === blockId ? updater(b) : b)),
      );
    },
    [],
  );

  /** Find the most recent tool_group block id. Returns null if none. */
  const findLastToolGroupId = useCallback(
    (blocksSnapshot: ChatBlock[]): string | null => {
      for (let i = blocksSnapshot.length - 1; i >= 0; i--) {
        if (blocksSnapshot[i].type === "tool_group") {
          return blocksSnapshot[i].id;
        }
      }
      return null;
    },
    [],
  );

  // ── SSE event handlers ──

  const handleInvokeEvent = useCallback(
    (event: SseEvent) => {
      const data = event.data as Record<string, unknown>;

      switch (event.event_type) {
        case "agent.provider.started": {
          // Optionally start an assistant_text block — but we don't create one yet.
          // We wait for the first real delta or tool_call to determine what comes first.
          break;
        }

        case "agent.tool_call.created": {
          const callId = String(data.call_id ?? "");
          const toolCall: ToolCallState = {
            callId,
            name: String(data.name ?? ""),
            input: asRecord(data.input),
            status: "pending",
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
          }));
          break;
        }

        case "agent.invocation.created": {
          patchToolInLastGroup(data, (tool) => ({
            ...tool,
            invocationId: String(data.invocation_id ?? ""),
          }));
          break;
        }

        case "agent.job.queued": {
          patchToolInLastGroup(data, (tool) => ({
            ...tool,
            jobId: String(data.job_id ?? ""),
            status: "running" as const,
          }));
          break;
        }

        case "agent.job.running": {
          patchToolInLastGroup(data, (tool) => ({
            ...tool,
            status: "running" as const,
          }));
          break;
        }

        case "agent.tool_call.completed": {
          patchToolInLastGroup(data, (tool) => ({
            ...tool,
            status: "succeeded" as const,
            result: asRecord(data.result),
          }));
          break;
        }

        case "agent.tool_call.waiting_approval": {
          patchToolInLastGroup(data, (tool) => ({
            ...tool,
            status: "waiting_approval" as const,
            approvalId: String(data.approval_id ?? ""),
            errorMessage: String(data.message ?? "Approval required"),
          }));
          break;
        }

        case "agent.tool_call.failed": {
          patchToolInLastGroup(data, (tool) => ({
            ...tool,
            status: "failed" as const,
            errorCode: String(data.error_code ?? ""),
            errorMessage: String(data.message ?? "Tool failed"),
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

        case "agent.fallback_synthesis": {
          const msg = String(data.message ?? "");
          upsertLastBlock(
            (b): b is AssistantTextBlock => b.type === "assistant_text",
            () => ({
              type: "assistant_text",
              id: genId(),
              content: msg,
              streaming: false,
              created_at: nowISO(),
            }),
            (prev) => ({
              ...prev,
              content: prev.content
                ? `${prev.content}\n\n${msg}`
                : msg,
              streaming: false,
            }),
          );
          break;
        }

        case "agent.completed": {
          // Mark last assistant_text as no longer streaming
          setBlocks((prev) => {
            const updated = [...prev];
            for (let i = updated.length - 1; i >= 0; i--) {
              if (updated[i].type === "assistant_text") {
                updated[i] = {
                  ...updated[i],
                  streaming: false,
                } as AssistantTextBlock;
                break;
              }
            }
            // Add subtle system event instead of big bubble
            return [
              ...updated,
              {
                type: "system_event",
                id: genId(),
                label: data.status
                  ? `Completed (${String(data.status)})`
                  : "Completed",
                created_at: nowISO(),
              } as SystemEventBlock,
            ];
          });
          setIsStreaming(false);
          break;
        }

        case "agent.failed":
        case "agent.provider.failed": {
          const errorMsg = String(data.message ?? "Unknown error");
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
    [addBlock, upsertLastBlock],
  );

  /** Patch a tool call inside the most recent tool_group block. */
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
    ) => {
      abortRef.current?.();

      addBlock({
        type: "user",
        id: genId(),
        content: prompt,
        created_at: nowISO(),
      } as UserBlock);

      setIsStreaming(true);
      const stream = createEventStream(
        "/agent/invoke/stream",
        {
          session_id: sessionId,
          provider_name: providerName,
          prompt,
          target_node_id: targetNodeId || undefined,
          execution_mode: executionMode,
        },
        {
          onEvent: (event) => handleInvokeEvent(event),
          onError: (error) => {
            setIsStreaming(false);
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
            setBlocks((prev) => {
              const updated = [...prev];
              for (let i = updated.length - 1; i >= 0; i--) {
                if (updated[i].type === "assistant_text") {
                  updated[i] = {
                    ...updated[i],
                    streaming: false,
                  } as AssistantTextBlock;
                  break;
                }
              }
              return updated;
            });
            onConversationSettled?.();
          },
        },
      );

      abortRef.current = () => stream.abort();
    },
    [addBlock, handleInvokeEvent, onConversationSettled, sessionId],
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

      setIsStreaming(true);
      const stream = createEventStream(
        "/agent/plan/stream",
        {
          session_id: sessionId,
          provider_name: providerName,
          prompt,
          target_node_id: targetNodeId,
        },
        {
          onEvent: (event) => handlePlanEvent(event),
          onError: (error) => {
            setIsStreaming(false);
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
            onConversationSettled?.();
          },
        },
      );

      abortRef.current = () => stream.abort();
    },
    [addBlock, onConversationSettled, sessionId],
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
    [addBlock, upsertLastBlock, onPlanCreated],
  );

  const cancel = useCallback(() => {
    abortRef.current?.();
    abortRef.current = null;
    setIsStreaming(false);
  }, []);

  return {
    blocks,
    isStreaming,
    sendInvoke,
    sendPlan,
    cancel,
    clearBlocks,
    loadPersistedMessages,
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
      const hasToolCalls =
        message.tool_calls && message.tool_calls.length > 0;
      const hasContent = Boolean(message.content);

      // Tool calls come first in timeline (LLM decided to call tools before synthesizing)
      if (hasToolCalls) {
        blocks.push({
          type: "tool_group",
          id: `${message.message_id}_tg`,
          tool_calls: toolCallsFromPersisted(message.tool_calls),
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
    value === "waiting_approval"
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
