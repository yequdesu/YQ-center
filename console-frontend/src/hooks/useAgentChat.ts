import { useCallback, useRef, useState } from "react";
import { createEventStream } from "@/api/stream";
import type { SseEvent } from "@/api/types";

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
  status: "pending" | "running" | "succeeded" | "failed";
  invocationId?: string;
  jobId?: string;
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

  const addMessage = useCallback((msg: ChatMessage) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  const updateLastAssistant = useCallback(
    (updater: (msg: ChatMessage) => ChatMessage) => {
      setMessages((prev) => {
        const idx = [...prev].reverse().findIndex((m) => m.role === "assistant" || m.role === "tool");
        if (idx === -1) return prev;
        const realIdx = prev.length - 1 - idx;
        const updated = [...prev];
        updated[realIdx] = updater(updated[realIdx]);
        return updated;
      });
    },
    [],
  );

  const sendInvoke = useCallback(
    (prompt: string, targetNodeId: string, providerName: string, executionMode: string) => {
      // Abort any existing stream
      abortRef.current?.();

      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: prompt,
        toolCalls: [],
        timestamp: new Date().toISOString(),
      };
      addMessage(userMsg);

      const assistantMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: "",
        toolCalls: [],
        timestamp: new Date().toISOString(),
        isStreaming: true,
      };
      addMessage(assistantMsg);
      setIsStreaming(true);

      const stream = createEventStream(
        "/agent/invoke/stream",
        {
          session_id: sessionId,
          provider_name: providerName,
          prompt,
          target_node_id: targetNodeId,
          execution_mode: executionMode,
        },
        {
          onEvent: (event: SseEvent) => {
            handleInvokeEvent(event, updateLastAssistant);
          },
          onError: (err) => {
            setIsStreaming(false);
            updateLastAssistant((m) => ({
              ...m,
              content: m.content || `Error: ${err.message}`,
              isStreaming: false,
            }));
          },
          onClose: () => {
            setIsStreaming(false);
            updateLastAssistant((m) => ({ ...m, isStreaming: false }));
          },
        },
      );

      abortRef.current = () => stream.abort();
    },
    [sessionId, addMessage, updateLastAssistant],
  );

  const sendPlan = useCallback(
    (prompt: string, targetNodeId: string, providerName: string) => {
      abortRef.current?.();

      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: prompt,
        toolCalls: [],
        timestamp: new Date().toISOString(),
      };
      addMessage(userMsg);

      const assistantMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: "",
        toolCalls: [],
        timestamp: new Date().toISOString(),
        isStreaming: true,
      };
      addMessage(assistantMsg);
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
          onEvent: (event: SseEvent) => {
            handlePlanEvent(event, updateLastAssistant, onPlanCreated);
          },
          onError: (err) => {
            setIsStreaming(false);
            updateLastAssistant((m) => ({
              ...m,
              content: `Error: ${err.message}`,
              isStreaming: false,
            }));
          },
          onClose: () => {
            setIsStreaming(false);
            updateLastAssistant((m) => ({ ...m, isStreaming: false }));
          },
        },
      );

      abortRef.current = () => stream.abort();
    },
    [sessionId, addMessage, updateLastAssistant, onPlanCreated],
  );

  const cancel = useCallback(() => {
    abortRef.current?.();
    setIsStreaming(false);
  }, []);

  return {
    messages,
    isStreaming,
    sendInvoke,
    sendPlan,
    cancel,
  };
}

function handleInvokeEvent(
  event: SseEvent,
  update: (updater: (msg: ChatMessage) => ChatMessage) => void,
) {
  const d = event.data as Record<string, unknown>;

  switch (event.event_type) {
    case "agent.provider.delta":
      update((m) => ({
        ...m,
        content: m.content + (d.content as string ?? ""),
      }));
      break;

    case "agent.tool_call.created": {
      const tc: ToolCallState = {
        callId: d.call_id as string ?? "",
        name: d.name as string ?? "",
        input: (d.input as Record<string, unknown>) ?? {},
        status: "pending",
      };
      update((m) => ({
        ...m,
        role: "tool",
        toolCalls: [...m.toolCalls.filter((t) => t.callId !== tc.callId), tc],
      }));
      break;
    }

    case "agent.tool_call.arguments":
      update((m) => {
        const callId = d.call_id as string ?? "";
        return {
          ...m,
          toolCalls: m.toolCalls.map((t) =>
            t.callId === callId
              ? { ...t, input: (d.input as Record<string, unknown>) ?? t.input }
              : t,
          ),
        };
      });
      break;

    case "agent.invocation.created":
      update((m) => {
        const callId = d.call_id as string ?? "";
        return {
          ...m,
          toolCalls: m.toolCalls.map((t) =>
            t.callId === callId
              ? { ...t, invocationId: d.invocation_id as string }
              : t,
          ),
        };
      });
      break;

    case "agent.job.queued":
      update((m) => {
        const callId = d.call_id as string ?? "";
        return {
          ...m,
          toolCalls: m.toolCalls.map((t) =>
            t.callId === callId
              ? { ...t, jobId: d.job_id as string, status: "running" }
              : t,
          ),
        };
      });
      break;

    case "agent.job.running":
      update((m) => {
        const callId = d.call_id as string ?? "";
        return {
          ...m,
          toolCalls: m.toolCalls.map((t) =>
            t.callId === callId ? { ...t, status: "running" } : t,
          ),
        };
      });
      break;

    case "agent.tool_call.completed":
      update((m) => {
        const callId = d.call_id as string ?? "";
        return {
          ...m,
          toolCalls: m.toolCalls.map((t) =>
            t.callId === callId
              ? { ...t, status: "succeeded", result: d.result as Record<string, unknown> }
              : t,
          ),
        };
      });
      break;

    case "agent.tool_call.failed":
      update((m) => {
        const callId = d.call_id as string ?? "";
        return {
          ...m,
          toolCalls: m.toolCalls.map((t) =>
            t.callId === callId
              ? {
                  ...t,
                  status: "failed",
                  errorCode: d.error_code as string,
                  errorMessage: d.message as string,
                }
              : t,
          ),
        };
      });
      break;

    case "agent.output.delta":
      update((m) => ({
        ...m,
        content: m.content + (d.content as string ?? ""),
      }));
      break;

    case "agent.completed":
      update((m) => ({ ...m, isStreaming: false }));
      break;

    case "agent.failed":
      update((m) => ({
        ...m,
        content: m.content || `Failed: ${d.message as string ?? "Unknown error"}`,
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
  const d = event.data as Record<string, unknown>;

  switch (event.event_type) {
    case "agent.planning.summary":
      update((m) => ({
        ...m,
        content: `Planning: ${d.message as string ?? ""}`,
      }));
      break;

    case "agent.plan.step.created": {
      const step: PlanStepState = {
        seq: d.seq as number,
        kind: d.kind as string,
        functionName: d.function_name as string,
        requiresApproval: d.requires_approval as boolean,
      };
      update((m) => ({
        ...m,
        planSteps: [...(m.planSteps ?? []), step],
      }));
      break;
    }

    case "agent.plan.created":
      update((m) => ({
        ...m,
        planId: d.plan_id as string,
        content: `Plan created: ${d.goal as string ?? ""}`,
      }));
      if (d.plan_id && onPlanCreated) {
        onPlanCreated(d.plan_id as string);
      }
      break;

    case "agent.approval.required":
      update((m) => ({
        ...m,
        approvalRequired: true,
        content: m.content + "\n\n⚠️ This plan requires approval.",
      }));
      break;

    case "agent.completed":
      update((m) => ({ ...m, isStreaming: false }));
      break;

    case "agent.failed":
      update((m) => ({
        ...m,
        content: m.content || `Failed: ${d.message as string ?? "Unknown error"}`,
        isStreaming: false,
      }));
      break;
  }
}
