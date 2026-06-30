import { useCallback, useEffect, useRef, useState } from "react";
import { createEventStream } from "@/api/stream";
import {
  appendOptimisticUserPrompt,
  applyToolPatch,
  emptyTranscript,
  reduceSseEvent,
  transcriptFromPersisted,
} from "@/agent-transcript/reducer";
import type {
  ChatBlock,
  PlanStepState,
  PromptContextData,
  ToolCallPatch,
  ToolCallState,
  UserBlock,
  AssistantTextBlock,
  ToolGroupBlock,
  SystemEventBlock,
  RunStatusBlock,
  ArtifactPresentationBlock,
  OperationCardBlock,
} from "@/agent-transcript/types";
import type { AgentSessionDetail, SseEvent } from "@/api/types";

export type {
  ChatBlock,
  PlanStepState,
  PromptContextData,
  ToolCallPatch,
  ToolCallState,
  UserBlock,
  AssistantTextBlock,
  ToolGroupBlock,
  SystemEventBlock,
  RunStatusBlock,
  ArtifactPresentationBlock,
  OperationCardBlock,
};

interface UseAgentChatOptions {
  sessionId: string;
  onPlanCreated?: (planId: string) => void;
  onConversationSettled?: () => void;
}

interface SendInvokeOptions {
  visible?: boolean;
  suppressUserMessage?: boolean;
}

export function useAgentChat({
  sessionId,
  onPlanCreated,
  onConversationSettled,
}: UseAgentChatOptions) {
  const [transcript, setTranscript] = useState(emptyTranscript);
  const [isStreaming, setIsStreaming] = useState(false);
  const [planSteps, setPlanSteps] = useState<PlanStepState[]>([]);
  const abortRef = useRef<(() => void) | null>(null);
  const currentSessionIdRef = useRef(sessionId);

  useEffect(() => {
    currentSessionIdRef.current = sessionId;
  }, [sessionId]);

  const clearBlocks = useCallback(() => {
    setTranscript(emptyTranscript());
    setPlanSteps([]);
  }, []);

  const loadPersistedSession = useCallback((session: AgentSessionDetail) => {
    setTranscript(
      transcriptFromPersisted({
        messages: session.messages,
        turns: session.turns,
      }),
    );
  }, []);

  const loadPersistedMessages = useCallback(
    (messages: AgentSessionDetail["messages"]) => {
      setTranscript(transcriptFromPersisted({ messages }));
    },
    [],
  );

  const patchToolCall = useCallback((patch: ToolCallPatch) => {
    setTranscript((prev) => applyToolPatch(prev, patch));
  }, []);

  const handleInvokeEvent = useCallback((event: SseEvent) => {
    if (event.session_id !== currentSessionIdRef.current) return;
    setTranscript((prev) => reduceSseEvent(prev, event));
  }, []);

  const handlePlanEvent = useCallback(
    (event: SseEvent) => {
      const data = event.data as Record<string, unknown>;
      if (event.session_id !== currentSessionIdRef.current) return;

      switch (event.event_type) {
        case "agent.plan.step.created":
          setPlanSteps((prev) => [
            ...prev,
            {
              seq: Number(data.seq ?? 0),
              kind: String(data.kind ?? ""),
              functionName: String(data.function_name ?? ""),
              requiresApproval: Boolean(data.requires_approval),
            },
          ]);
          break;

        case "agent.plan.created": {
          const planId = String(data.plan_id ?? "");
          if (planId && onPlanCreated) onPlanCreated(planId);
          setTranscript((prev) => reduceSseEvent(prev, {
            ...event,
            event_type: "agent.completed",
            data: { status: "plan_created", message: String(data.goal ?? "") },
          }));
          break;
        }

        case "agent.planning.summary":
          setTranscript((prev) => reduceSseEvent(prev, {
            ...event,
            event_type: "agent.output.delta",
            data: { content: String(data.message ?? "Planning...") },
          }));
          break;

        default:
          setTranscript((prev) => reduceSseEvent(prev, event));
      }
    },
    [onPlanCreated],
  );

  const startStream = useCallback(
    (
      path: string,
      payload: Record<string, unknown>,
      onEvent: (event: SseEvent) => void,
    ) => {
      abortRef.current?.();
      setIsStreaming(true);

      const stream = createEventStream(path, payload, {
        onEvent,
        onError: (error) => {
          if (String(payload.session_id ?? "") !== currentSessionIdRef.current) return;
          setIsStreaming(false);
          setTranscript((prev) =>
            reduceSseEvent(prev, {
              event_id: crypto.randomUUID(),
              event_type: "agent.failed",
              session_id: sessionId,
              trace_id: "",
              timestamp: new Date().toISOString(),
              data: { message: error.message, error_code: "stream_error" },
            }),
          );
          onConversationSettled?.();
        },
        onClose: () => {
          if (String(payload.session_id ?? "") !== currentSessionIdRef.current) return;
          setIsStreaming(false);
          setTranscript((prev) =>
            reduceSseEvent(prev, {
              event_id: crypto.randomUUID(),
              event_type: "stream.close",
              session_id: sessionId,
              trace_id: "",
              timestamp: new Date().toISOString(),
              data: {},
            }),
          );
          onConversationSettled?.();
        },
      });

      abortRef.current = () => stream.abort();
    },
    [onConversationSettled, sessionId],
  );

  const sendInvoke = useCallback(
    (
      prompt: string,
      targetNodeId: string,
      providerName: string,
      executionMode: string,
      options: SendInvokeOptions = {},
    ) => {
      const suppressUserMessage =
        options.suppressUserMessage ?? (options.visible === false);

      // Add user message and thinking indicator locally before SSE stream
      // so the UI updates instantly instead of waiting for the first SSE event.
      if (!suppressUserMessage) {
        const eventId = crypto.randomUUID();
        const timestamp = new Date().toISOString();
        setTranscript((prev) =>
          appendOptimisticUserPrompt(prev, prompt, eventId, timestamp),
        );
      }
      setTranscript((prev) =>
        reduceSseEvent(prev, {
          event_id: crypto.randomUUID(),
          event_type: "agent.provider.started",
          session_id: sessionId,
          trace_id: "",
          timestamp: new Date().toISOString(),
          data: {},
        }),
      );

      startStream(
        "/agent/invoke/stream",
        {
          session_id: sessionId,
          provider_name: providerName,
          prompt,
          target_node_id: targetNodeId || undefined,
          execution_mode: executionMode,
          suppress_user_message: suppressUserMessage,
        },
        handleInvokeEvent,
      );
    },
    [handleInvokeEvent, sessionId, startStream],
  );

  const sendPlan = useCallback(
    (prompt: string, targetNodeId: string, providerName: string) => {
      // Add user message and thinking indicator locally before SSE stream.
      const eventId = crypto.randomUUID();
      const timestamp = new Date().toISOString();
      setTranscript((prev) =>
        appendOptimisticUserPrompt(prev, prompt, eventId, timestamp),
      );
      setTranscript((prev) =>
        reduceSseEvent(prev, {
          event_id: crypto.randomUUID(),
          event_type: "agent.provider.started",
          session_id: sessionId,
          trace_id: "",
          timestamp: new Date().toISOString(),
          data: {},
        }),
      );

      startStream(
        "/agent/plan/stream",
        {
          session_id: sessionId,
          provider_name: providerName,
          prompt,
          target_node_id: targetNodeId || undefined,
        },
        handlePlanEvent,
      );
    },
    [handlePlanEvent, sessionId, startStream],
  );

  const resumeOperation = useCallback(
    (operationId: string, providerName: string, executionMode: string) => {
      setTranscript((prev) =>
        reduceSseEvent(prev, {
          event_id: crypto.randomUUID(),
          event_type: "agent.provider.started",
          session_id: sessionId,
          trace_id: "",
          timestamp: new Date().toISOString(),
          data: {},
        }),
      );

      startStream(
        "/agent/resume-operation/stream",
        {
          session_id: sessionId,
          provider_name: providerName,
          operation_id: operationId,
          execution_mode: executionMode,
        },
        handleInvokeEvent,
      );
    },
    [handleInvokeEvent, sessionId, startStream],
  );

  const resumeLastRun = useCallback(
    (providerName: string, executionMode: string) => {
      setTranscript((prev) =>
        reduceSseEvent(prev, {
          event_id: crypto.randomUUID(),
          event_type: "agent.provider.started",
          session_id: sessionId,
          trace_id: "",
          timestamp: new Date().toISOString(),
          data: {},
        }),
      );

      startStream(
        "/agent/resume-last-run/stream",
        {
          session_id: sessionId,
          provider_name: providerName,
          execution_mode: executionMode,
        },
        handleInvokeEvent,
      );
    },
    [handleInvokeEvent, sessionId, startStream],
  );

  const cancel = useCallback(() => {
    abortRef.current?.();
    abortRef.current = null;
    setIsStreaming(false);
    setTranscript((prev) =>
      reduceSseEvent(prev, {
        event_id: crypto.randomUUID(),
        event_type: "stream.close",
        session_id: sessionId,
        trace_id: "",
        timestamp: new Date().toISOString(),
        data: {},
      }),
    );
  }, [sessionId]);

  const detach = useCallback(() => {
    abortRef.current = null;
    setIsStreaming(false);
  }, []);

  return {
    blocks: transcript.blocks,
    isStreaming,
    promptContext: transcript.promptContext,
    planSteps,
    sendInvoke,
    sendPlan,
    resumeOperation,
    resumeLastRun,
    cancel,
    detach,
    clearBlocks,
    loadPersistedMessages,
    loadPersistedSession,
    patchToolCall,
  };
}
