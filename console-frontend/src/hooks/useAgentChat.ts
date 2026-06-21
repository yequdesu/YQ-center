import { useCallback, useRef, useState } from "react";
import { createEventStream } from "@/api/stream";
import type { AgentSessionMessage, SseEvent } from "@/api/types";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  toolCalls: ToolCallState[];
  planSteps?: PlanStepState[];
  planId?: string;
  runId?: string;
  approvalRequired?: boolean;
  timestamp: string;
  isStreaming?: boolean;
}

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
}

export function useAgentChat({ sessionId, onPlanCreated }: UseAgentChatOptions) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const abortRef = useRef<(() => void) | null>(null);

  const clearMessages = useCallback(() => {
    setMessages([]);
  }, []);

  const loadPersistedMessages = useCallback((persisted: AgentSessionMessage[]) => {
    setMessages(persisted.map(messageFromPersisted));
  }, []);

  const addMessage = useCallback((msg: ChatMessage) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  const updateCurrentAssistant = useCallback((updater: (msg: ChatMessage) => ChatMessage) => {
    setMessages((prev) => {
      const idx = [...prev].reverse().findIndex((m) => m.role === "assistant");
      if (idx === -1) return prev;
      const realIdx = prev.length - 1 - idx;
      const updated = [...prev];
      updated[realIdx] = updater(updated[realIdx]);
      return updated;
    });
  }, []);

  const sendInvoke = useCallback(
    (prompt: string, targetNodeId: string, providerName: string, executionMode: string) => {
      abortRef.current?.();

      addMessage({
        id: crypto.randomUUID(),
        role: "user",
        content: prompt,
        toolCalls: [],
        timestamp: new Date().toISOString(),
      });

      addMessage({
        id: crypto.randomUUID(),
        role: "assistant",
        content: "",
        toolCalls: [],
        timestamp: new Date().toISOString(),
        isStreaming: true,
      });

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
          onEvent: (event) => handleInvokeEvent(event, updateCurrentAssistant),
          onError: (error) => {
            setIsStreaming(false);
            updateCurrentAssistant((m) => ({
              ...m,
              content: m.content || `Error: ${error.message}`,
              isStreaming: false,
            }));
          },
          onClose: () => {
            setIsStreaming(false);
            updateCurrentAssistant((m) => ({ ...m, isStreaming: false }));
          },
        },
      );

      abortRef.current = () => stream.abort();
    },
    [addMessage, sessionId, updateCurrentAssistant],
  );

  const sendPlan = useCallback(
    (prompt: string, targetNodeId: string, providerName: string) => {
      abortRef.current?.();

      addMessage({
        id: crypto.randomUUID(),
        role: "user",
        content: prompt,
        toolCalls: [],
        timestamp: new Date().toISOString(),
      });

      addMessage({
        id: crypto.randomUUID(),
        role: "assistant",
        content: "",
        toolCalls: [],
        timestamp: new Date().toISOString(),
        isStreaming: true,
      });

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
          onEvent: (event) => handlePlanEvent(event, updateCurrentAssistant, onPlanCreated),
          onError: (error) => {
            setIsStreaming(false);
            updateCurrentAssistant((m) => ({
              ...m,
              content: m.content || `Error: ${error.message}`,
              isStreaming: false,
            }));
          },
          onClose: () => {
            setIsStreaming(false);
            updateCurrentAssistant((m) => ({ ...m, isStreaming: false }));
          },
        },
      );

      abortRef.current = () => stream.abort();
    },
    [addMessage, onPlanCreated, sessionId, updateCurrentAssistant],
  );

  const cancel = useCallback(() => {
    abortRef.current?.();
    abortRef.current = null;
    setIsStreaming(false);
  }, []);

  return {
    messages,
    isStreaming,
    sendInvoke,
    sendPlan,
    cancel,
    clearMessages,
    loadPersistedMessages,
  };
}

function handleInvokeEvent(
  event: SseEvent,
  update: (updater: (msg: ChatMessage) => ChatMessage) => void,
) {
  const data = event.data as Record<string, unknown>;

  switch (event.event_type) {
    case "agent.provider.started":
      update((message) => ({
        ...message,
        content: message.content || "Thinking...",
      }));
      break;

    case "agent.tool_call.created": {
      const callId = String(data.call_id ?? "");
      const toolCall: ToolCallState = {
        callId,
        name: String(data.name ?? ""),
        input: asRecord(data.input),
        status: "pending",
      };
      update((message) => ({
        ...message,
        content: message.content === "Thinking..." ? "" : message.content,
        toolCalls: [...message.toolCalls.filter((t) => t.callId !== callId), toolCall],
      }));
      break;
    }

    case "agent.tool_call.arguments":
      patchTool(data, update, (tool) => ({ ...tool, input: asRecord(data.input) }));
      break;

    case "agent.invocation.created":
      patchTool(data, update, (tool) => ({
        ...tool,
        invocationId: String(data.invocation_id ?? ""),
      }));
      break;

    case "agent.job.queued":
      patchTool(data, update, (tool) => ({
        ...tool,
        jobId: String(data.job_id ?? ""),
        status: "running",
      }));
      break;

    case "agent.job.running":
      patchTool(data, update, (tool) => ({ ...tool, status: "running" }));
      break;

    case "agent.tool_call.completed":
      patchTool(data, update, (tool) => ({
        ...tool,
        status: "succeeded",
        result: asRecord(data.result),
      }));
      break;

    case "agent.tool_call.waiting_approval":
      patchTool(data, update, (tool) => ({
        ...tool,
        status: "waiting_approval",
        approvalId: String(data.approval_id ?? ""),
        errorMessage: String(data.message ?? "Approval required"),
      }));
      break;

    case "agent.tool_call.failed":
      patchTool(data, update, (tool) => ({
        ...tool,
        status: "failed",
        errorCode: String(data.error_code ?? ""),
        errorMessage: String(data.message ?? "Tool failed"),
      }));
      break;

    case "agent.output.delta":
      update((message) => ({
        ...message,
        content: appendContent(message.content, String(data.content ?? "")),
      }));
      break;

    case "agent.fallback_synthesis":
      update((message) => ({
        ...message,
        content: appendContent(message.content, String(data.message ?? "")),
      }));
      break;

    case "agent.completed":
      update((message) => ({
        ...message,
        content: message.content || String(data.message ?? ""),
        isStreaming: false,
      }));
      break;

    case "agent.failed":
    case "agent.provider.failed":
      update((message) => ({
        ...message,
        content: message.content || `Failed: ${String(data.message ?? "Unknown error")}`,
        isStreaming: false,
      }));
      break;
  }
}

function handlePlanEvent(
  event: SseEvent,
  update: (updater: (msg: ChatMessage) => ChatMessage) => void,
  onPlanCreated?: (planId: string) => void,
) {
  const data = event.data as Record<string, unknown>;

  switch (event.event_type) {
    case "agent.planning.summary":
      update((message) => ({
        ...message,
        content: String(data.message ?? "Planning..."),
      }));
      break;

    case "agent.plan.step.created": {
      const step: PlanStepState = {
        seq: Number(data.seq ?? 0),
        kind: String(data.kind ?? ""),
        functionName: String(data.function_name ?? ""),
        requiresApproval: Boolean(data.requires_approval),
      };
      update((message) => ({
        ...message,
        planSteps: [...(message.planSteps ?? []), step],
      }));
      break;
    }

    case "agent.plan.created": {
      const planId = String(data.plan_id ?? "");
      update((message) => ({
        ...message,
        planId,
        content: `Plan created: ${String(data.goal ?? "")}`,
      }));
      if (planId && onPlanCreated) onPlanCreated(planId);
      break;
    }

    case "agent.approval.required":
      update((message) => ({
        ...message,
        approvalRequired: true,
        content: appendContent(message.content, "This plan requires approval."),
      }));
      break;

    case "agent.completed":
      update((message) => ({ ...message, isStreaming: false }));
      break;

    case "agent.failed":
      update((message) => ({
        ...message,
        content: message.content || `Failed: ${String(data.message ?? "Unknown error")}`,
        isStreaming: false,
      }));
      break;
  }
}

function patchTool(
  data: Record<string, unknown>,
  update: (updater: (msg: ChatMessage) => ChatMessage) => void,
  patch: (tool: ToolCallState) => ToolCallState,
) {
  const callId = String(data.call_id ?? "");
  update((message) => ({
    ...message,
    toolCalls: message.toolCalls.map((tool) =>
      tool.callId === callId ? patch(tool) : tool,
    ),
  }));
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function appendContent(current: string, next: string): string {
  if (!next) return current;
  if (!current || current === "Thinking...") return next;
  return `${current}\n\n${next}`;
}

function messageFromPersisted(message: AgentSessionMessage): ChatMessage {
  return {
    id: message.message_id,
    role: message.role,
    content: message.content ?? "",
    toolCalls: toolCallsFromPersisted(message.tool_calls),
    timestamp: message.created_at ?? new Date().toISOString(),
  };
}

function toolCallsFromPersisted(raw: Record<string, unknown>[]): ToolCallState[] {
  return raw.map((item) => ({
    callId: String(item.call_id ?? ""),
    name: String(item.name ?? ""),
    input: asRecord(item.input),
    status: "pending",
  }));
}
