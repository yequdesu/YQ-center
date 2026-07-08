import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  createSession,
  getSessionRuntimeState,
  type AgentRunProjection,
  type AgentRuntimePlan,
} from "@/api/agent";
import {
  getSession,
  getMaintenanceRun,
  listMaintenanceRunArtifacts,
  approvePlan,
  runPlan,
  listSessions,
  renameSession,
  deleteSession,
  getApproval,
  getOperation,
  cancelOperation,
  denyApproval,
  approveAndRunApproval,
} from "@/api/admin";
import {
  useAgentChat,
  type ArtifactPresentationBlock,
  type AssistantTextBlock,
  type ChatBlock,
  type RunStatusBlock,
  type SystemEventBlock,
  type OperationCardBlock,
  type PromptContextData,
  type ToolCallState,
  type ToolGroupBlock,
  type UserBlock,
  type YcrTokenSummary,
  type YcrTraceItem,
} from "@/hooks/useAgentChat";
import type { AgentSessionSummary, MaintenanceArtifactDetail } from "@/api/types";
import { StatusBadge } from "@/components/StatusBadge";
import { JsonView } from "@/components/JsonView";
import { ArtifactList, artifactsFromResult } from "@/components/ArtifactCards";
import { Button } from "@/components/Button";
import { EmptyState } from "@/components/EmptyState";
import {
  Send,
  User,
  Bot,
  Wrench,
  Loader2,
  AlertTriangle,
  CheckCircle,
  XCircle,
  FileText,
  ChevronDown,
  ChevronRight,
  Plus,
  Trash2,
  Edit3,
  Check,
  Search,
  Info,
  RefreshCw,
  Database,
  Download,
  Gauge,
  Upload,
} from "lucide-react";

const SESSION_STORAGE_KEY = "yequ_agent_session_id";
const OPERATION_CONTEXT_STORAGE_PREFIX = "yequ_agent_operation_context:";
const DEFAULT_AGENT_MAX_STEPS = 40;
const AGENT_MAX_STEP_OPTIONS = [40, 60, 80, 100] as const;

interface OperationContextChip {
  operationId: string;
  kind: string;
  status: string;
  title?: string;
}

interface OperationStatusUpdate {
  operationId: string;
  status: string;
  previousStatus?: string;
  title?: string;
  kind?: string;
  refType?: string;
  refId?: string;
  message?: string;
  progressPct?: number | null;
  progressMessage?: string | null;
  errorCode?: string | null;
  errorMessage?: string | null;
}

export function AgentChatPage() {
  const [sessionId, setSessionId] = useState<string>(() => {
    return sessionStorage.getItem(SESSION_STORAGE_KEY) ?? "";
  });
  const [prompt, setPrompt] = useState("");
  const [executionMode, setExecutionMode] = useState("auto");
  const [providerName, setProviderName] = useState("deepseek");
  const [maxSteps, setMaxSteps] = useState<number>(DEFAULT_AGENT_MAX_STEPS);
  const [autoPlan, setAutoPlan] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [editingSessId, setEditingSessId] = useState<string | null>(null);
  const [editLabel, setEditLabel] = useState("");
  const [sessionSearch, setSessionSearch] = useState("");
  const chatEndRef = useRef<HTMLDivElement>(null);
  const creatingSessionRef = useRef(false);
  const reconciledApprovalIdsRef = useRef(new Set<string>());
  const terminalOperationRefreshRef = useRef(new Set<string>());
  const terminalPlanRefreshRef = useRef(new Set<string>());
  const [isCreatingSession, setIsCreatingSession] = useState(false);
  const [approvalBusyId, setApprovalBusyId] = useState<string | null>(null);
  const [approvalActionError, setApprovalActionError] = useState<string | null>(null);
  const [dismissedApprovalIds, setDismissedApprovalIds] = useState<Set<string>>(() => new Set());
  const [continuedOperationIds, setContinuedOperationIds] = useState<Set<string>>(() => new Set());
  const [operationContext, setOperationContext] = useState<OperationContextChip | null>(() =>
    readStoredOperationContext(sessionId),
  );
  const queryClient = useQueryClient();
  const refreshSessionHistory = useCallback(() => {
    if (!sessionId) return;
    queryClient.invalidateQueries({ queryKey: ["agent-session", sessionId] });
    queryClient.invalidateQueries({ queryKey: ["agent-runtime-state", sessionId] });
    queryClient.invalidateQueries({ queryKey: ["agent-sessions"] });
  }, [queryClient, sessionId]);

  const sessionsQuery = useQuery({
    queryKey: ["agent-sessions"],
    queryFn: listSessions,
    refetchInterval: 30_000,
  });

  const sessionQuery = useQuery({
    queryKey: ["agent-session", sessionId],
    queryFn: async () => {
      if (!sessionId) return null;
      return getSession(sessionId);
    },
    enabled: !!sessionId,
    retry: false,
    staleTime: 60_000,
  });

  const {
    blocks,
    isStreaming,
    promptContext,
    ycrTrace,
    ycrTokenSummary,
    planSteps,
    sendInvoke,
    sendPlan,
    resumeLastRun,
    cancel,
    detach,
    clearBlocks,
    loadPersistedSession,
    patchToolCall,
    patchOperation,
  } = useAgentChat({ sessionId, onConversationSettled: refreshSessionHistory });

  const runtimeStateQuery = useQuery({
    queryKey: ["agent-runtime-state", sessionId],
    queryFn: async () => {
      if (!sessionId) return null;
      return getSessionRuntimeState(sessionId);
    },
    enabled: !!sessionId,
    retry: false,
    refetchInterval: isStreaming ? 2_000 : 15_000,
  });

  useEffect(() => {
    const plan = runtimeStateQuery.data?.plan;
    if (!plan || !isAgentPlanTerminal(plan.status)) return;
    const refreshKey = `${plan.plan_id}:${plan.status}`;
    if (terminalPlanRefreshRef.current.has(refreshKey)) return;
    terminalPlanRefreshRef.current.add(refreshKey);
    refreshSessionHistory();
  }, [
    runtimeStateQuery.data?.plan?.plan_id,
    runtimeStateQuery.data?.plan?.status,
    refreshSessionHistory,
  ]);

  // Session selection is idempotent: never create sessions implicitly.
  // If a stored/active session disappears, select an existing session if one
  // exists; otherwise leave the chat in an explicit empty-session state.
  useEffect(() => {
    if (!sessionsQuery.isSuccess) return;
    const sessions = sessionsQuery.data ?? [];
    if (!sessionId) {
      const firstSession = sessions[0]?.session_id;
      if (firstSession) {
        sessionStorage.setItem(SESSION_STORAGE_KEY, firstSession);
        setSessionId(firstSession);
      }
      return;
    }
    if (sessions.some((s) => s.session_id === sessionId)) return;

    queryClient.removeQueries({ queryKey: ["agent-session", sessionId] });
    clearBlocks();
    const nextSession = sessions[0]?.session_id ?? "";
    if (nextSession) {
      sessionStorage.setItem(SESSION_STORAGE_KEY, nextSession);
    } else {
      sessionStorage.removeItem(SESSION_STORAGE_KEY);
    }
    setSessionId(nextSession);
  }, [clearBlocks, queryClient, sessionId, sessionsQuery.data, sessionsQuery.isSuccess]);

  useEffect(() => {
    setOperationContext(readStoredOperationContext(sessionId));
  }, [sessionId]);

  // Load persisted messages into blocks when session data arrives
  useEffect(() => {
    if (sessionQuery.data && sessionId) {
      reconciledApprovalIdsRef.current.clear();
      setDismissedApprovalIds(new Set());
      setContinuedOperationIds(new Set());
      loadPersistedSession(sessionQuery.data);
    }
  }, [loadPersistedSession, sessionId, sessionQuery.data]);

  // Auto-scroll to bottom
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [blocks]);

  const switchSession = (newId: string) => {
    detach();
    clearBlocks();
    sessionStorage.setItem(SESSION_STORAGE_KEY, newId);
    setSessionId(newId);
  };

  const createNewSession = async () => {
    if (creatingSessionRef.current) return;
    creatingSessionRef.current = true;
    setIsCreatingSession(true);
    detach();
    clearBlocks();
    try {
      const s = await createSession({});
      const now = new Date().toISOString();
      const summary: AgentSessionSummary = {
        session_id: s.session_id,
        actor_id: "agent",
        status: "active",
        execution_mode: s.execution_mode,
        started_at: now,
        closed_at: null,
        label: s.session_id.slice(0, 8),
        updated_at: now,
        last_message_preview: "",
        message_count: 0,
        running: false,
      };
      queryClient.setQueryData<AgentSessionSummary[]>(["agent-sessions"], (old) => [
        summary,
        ...(old ?? []).filter((session) => session.session_id !== s.session_id),
      ]);
      sessionStorage.setItem(SESSION_STORAGE_KEY, s.session_id);
      setSessionId(s.session_id);
      queryClient.invalidateQueries({ queryKey: ["agent-sessions"] });
    } finally {
      creatingSessionRef.current = false;
      setIsCreatingSession(false);
    }
  };

  const handleRenameStart = (id: string, currentLabel: string) => {
    setEditingSessId(id);
    setEditLabel(currentLabel);
  };

  const handleRenameSubmit = async (id: string) => {
    if (editLabel.trim()) {
      await renameSession(id, editLabel.trim());
      queryClient.invalidateQueries({ queryKey: ["agent-sessions"] });
    }
    setEditingSessId(null);
    setEditLabel("");
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this session and all its messages?")) return;
    const wasActive = id === sessionId;
    const nextSession = (sessionsQuery.data ?? []).find((s) => s.session_id !== id)?.session_id ?? "";
    await deleteSession(id);
    writeStoredOperationContext(id, null);
    queryClient.setQueryData<AgentSessionSummary[]>(["agent-sessions"], (old) =>
      (old ?? []).filter((session) => session.session_id !== id),
    );
    queryClient.removeQueries({ queryKey: ["agent-session", id] });
    queryClient.invalidateQueries({ queryKey: ["agent-sessions"] });
    if (wasActive) {
      cancel();
      clearBlocks();
      if (nextSession) {
        sessionStorage.setItem(SESSION_STORAGE_KEY, nextSession);
      } else {
        sessionStorage.removeItem(SESSION_STORAGE_KEY);
      }
      setSessionId(nextSession);
    }
  };

  const handleSend = () => {
    const trimmedPrompt = prompt.trim();
    if (!sessionId || isStreaming) return;
    if (operationContext) {
      const operationPrompt = trimmedPrompt || "Continue from the latest operation status.";
      const visibleMessage = operationContextVisibleMessage(operationContext, operationPrompt);
      sendInvoke(operationPrompt, "", providerName, executionMode, {
        visiblePrompt: visibleMessage,
        maxSteps,
        contextRefs: [
          {
            type: "operation",
            operation_id: operationContext.operationId,
            mode: "observation",
          },
        ],
      });
      setContinuedOperationIds((prev) => {
        const next = new Set(prev);
        next.add(operationContext.operationId);
        return next;
      });
      setOperationContext(null);
      writeStoredOperationContext(sessionId, null);
    } else if (trimmedPrompt && autoPlan) {
      sendPlan(prompt.trim(), "", providerName);
    } else if (trimmedPrompt) {
      sendInvoke(prompt.trim(), "", providerName, executionMode, { maxSteps });
    } else {
      return;
    }
    setPrompt("");
  };

  const pendingApprovals = useMemo(
    () =>
      blocks.flatMap((block) =>
        block.type === "tool_group"
          ? block.tool_calls.filter(
              (tool) =>
                tool.status === "waiting_approval" &&
                Boolean(tool.approvalId) &&
                !dismissedApprovalIds.has(String(tool.approvalId)),
            )
          : [],
      ),
    [blocks, dismissedApprovalIds],
  );

  const dismissApproval = useCallback((approvalId: string) => {
    setDismissedApprovalIds((prev) => {
      if (prev.has(approvalId)) return prev;
      const next = new Set(prev);
      next.add(approvalId);
      return next;
    });
  }, []);

  const restoreApproval = useCallback((approvalId: string) => {
    setDismissedApprovalIds((prev) => {
      if (!prev.has(approvalId)) return prev;
      const next = new Set(prev);
      next.delete(approvalId);
      return next;
    });
  }, []);

  const syncProcessedApproval = useCallback(
    async (approvalId: string): Promise<boolean> => {
      const approval = await getApproval(approvalId);
      if (approval.status === "pending") return false;

      if (approval.status === "denied") {
        dismissApproval(approvalId);
        patchToolCall({
          approvalId,
          status: "denied",
          errorCode: null,
          errorMessage: "Denied by user.",
        });
        return true;
      }

      if (approval.status === "expired") {
        dismissApproval(approvalId);
        patchToolCall({
          approvalId,
          status: "failed",
          errorCode: "approval_expired",
          errorMessage: "Approval expired.",
        });
        return true;
      }

      if (approval.status === "consumed") {
        dismissApproval(approvalId);
        const jobId = approval.consumed_invocation?.jobs?.[0]?.job_id ?? approval.invocation?.jobs?.[0]?.job_id;
        patchToolCall({
          approvalId,
          status: "running",
          jobId,
          errorCode: null,
          errorMessage: "Approved action is running; Center will report the result.",
        });
        return true;
      }

      if (approval.status === "approved") {
        dismissApproval(approvalId);
        patchToolCall({
          approvalId,
          status: "running",
          errorCode: "approval_already_approved",
          errorMessage: "Approval is approved; waiting for execution to start.",
        });
        return true;
      }

      return false;
    },
    [dismissApproval, patchToolCall],
  );

  useEffect(() => {
    if (!sessionId || blocks.length === 0) return;

    const waitingTools = blocks.flatMap((block) =>
      block.type === "tool_group"
        ? block.tool_calls.filter(
            (tool) =>
              tool.status === "waiting_approval" &&
              Boolean(tool.approvalId) &&
              !dismissedApprovalIds.has(String(tool.approvalId)) &&
              !reconciledApprovalIdsRef.current.has(String(tool.approvalId)),
          )
        : [],
    );

    if (waitingTools.length === 0) return;

    for (const tool of waitingTools) {
      const approvalId = String(tool.approvalId);
      reconciledApprovalIdsRef.current.add(approvalId);
      void syncProcessedApproval(approvalId).catch((error) => {
        const message = error instanceof Error ? error.message : String(error);
        if (message.includes("not found") || message.includes("404")) {
          dismissApproval(approvalId);
          patchToolCall({
            approvalId,
            status: "failed",
            errorCode: "approval_not_found",
            errorMessage: "Approval no longer exists.",
          });
        }
      });
    }
  }, [blocks, dismissedApprovalIds, dismissApproval, patchToolCall, sessionId, syncProcessedApproval]);

  const handleToolApprovalDecision = useCallback(
    async (toolCall: ToolCallState, decision: "approve" | "deny") => {
      const approvalId = toolCall.approvalId;
      if (!approvalId || approvalBusyId) return;

      setApprovalBusyId(approvalId);
      setApprovalActionError(null);
      dismissApproval(approvalId);
      try {
        if (await syncProcessedApproval(approvalId)) {
          return;
        }

        if (decision === "deny") {
          await denyApproval(approvalId, "Denied from Agent chat");
          dismissApproval(approvalId);
          patchToolCall({
            approvalId,
            status: "denied",
            errorMessage: "Denied by user.",
          });
        } else {
          const result = await approveAndRunApproval(approvalId, "Approved from Agent chat");
          patchToolCall({
            approvalId,
            status: result.operation_id ? "waiting_operation" : "running",
            invocationId: result.invocation_id,
            jobId: result.job_id,
            operationId: result.operation_id ?? undefined,
            waitHandle: result.wait_handle ?? undefined,
            errorCode: null,
            errorMessage: result.operation_id
              ? "Approved action is running as a Center operation."
              : "Approved action is running; Center will refresh the session.",
          });
        }
        queryClient.invalidateQueries({ queryKey: ["agent-sessions"] });
        queryClient.invalidateQueries({ queryKey: ["approvals"] });
        queryClient.invalidateQueries({ queryKey: ["jobs"] });
        refreshSessionHistory();
      } catch (error) {
        const message = error instanceof Error ? error.message : "Approval action failed.";
        if (message.includes("consumed") || message.includes("expected pending")) {
          try {
            if (await syncProcessedApproval(approvalId)) {
              return;
            }
          } catch {
            // Fall through to the visible error below.
          }
        }
        setApprovalActionError(message);
        restoreApproval(approvalId);
        patchToolCall({
          approvalId,
          status: "waiting_approval",
          errorCode: "approval_action_failed",
          errorMessage: message,
        });
      } finally {
        setApprovalBusyId(null);
      }
    },
    [
      approvalBusyId,
      dismissApproval,
      patchToolCall,
      restoreApproval,
      queryClient,
      refreshSessionHistory,
      syncProcessedApproval,
    ],
  );

  const handleApproveAndRun = useCallback(
    (planId: string, onRunStarted: (runId: string) => void) => {
      approvePlan(planId).then(() => {
        runPlan(planId).then((run) => {
          setActiveRunId(run.run_id);
          onRunStarted(run.run_id);
        });
      });
    },
    [],
  );
  const handleResumeOperation = useCallback(
    (operationId: string) => {
      const nextContext = {
        operationId,
        kind: "operation",
        status: "selected",
      };
      setOperationContext(nextContext);
      writeStoredOperationContext(sessionId, nextContext);
    },
    [sessionId],
  );
  const handleOperationStatusChange = useCallback(
    (update: OperationStatusUpdate) => {
      patchOperation(update);
      const terminal = isOperationTerminal(update.status);
      const wasAlreadyTerminal = update.previousStatus
        ? isOperationTerminal(update.previousStatus)
        : false;
      patchToolCall({
        operationId: update.operationId,
        status: terminal ? operationStatusToToolStatus(update.status) : "waiting_operation",
        errorCode: update.errorCode,
        errorMessage: update.errorMessage,
      });
      if (!terminal) return;
      if (wasAlreadyTerminal) return;
      if (!terminalOperationRefreshRef.current.has(update.operationId)) {
        terminalOperationRefreshRef.current.add(update.operationId);
        for (const delayMs of [500, 3500, 12_000, 30_000]) {
          window.setTimeout(refreshSessionHistory, delayMs);
        }
      }
      if (continuedOperationIds.has(update.operationId)) return;
      if (operationContext?.operationId === update.operationId) return;
    },
    [
      continuedOperationIds,
      operationContext?.operationId,
      patchOperation,
      patchToolCall,
      refreshSessionHistory,
    ],
  );
  const handleResumeLastRun = useCallback(() => {
    resumeLastRun(providerName, executionMode, maxSteps);
  }, [executionMode, maxSteps, providerName, resumeLastRun]);

  // Filter sessions by search
  const sessions = sessionsQuery.data ?? [];
  const filteredSessions = sessionSearch
    ? sessions.filter(
        (s) =>
          s.label.toLowerCase().includes(sessionSearch.toLowerCase()) ||
          s.session_id.toLowerCase().includes(sessionSearch.toLowerCase()),
      )
    : sessions;

  // Sort by updated_at desc (or started_at as fallback)
  const sortedSessions = [...filteredSessions].sort((a, b) => {
    const aTime = a.updated_at ?? a.started_at ?? "";
    const bTime = b.updated_at ?? b.started_at ?? "";
    return bTime.localeCompare(aTime);
  });
  const operationBlocks = useMemo(
    () => blocks.filter((block): block is OperationCardBlock => block.type === "operation_card"),
    [blocks],
  );
  const conversationBlocks = useMemo(
    () => blocks.filter((block) => block.type !== "operation_card"),
    [blocks],
  );
  return (
    <div className="flex h-[calc(100vh-var(--topbar-height))]">
      {/* Session Sidebar */}
      <aside className="flex w-[240px] flex-shrink-0 flex-col border-r border-[var(--border)] bg-[var(--surface-muted)]">
        <div className="flex items-center justify-between border-b border-[var(--border)] px-3 py-2">
          <span className="text-[12px] font-semibold text-[var(--text)]">Sessions</span>
          <button
            onClick={createNewSession}
            disabled={isCreatingSession}
            className="rounded-[var(--radius-sm)] p-1 text-[var(--text-muted)] hover:bg-[var(--bg-subtle)] hover:text-[var(--text)]"
            title="New session"
          >
            {isCreatingSession ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
          </button>
        </div>

        {/* Search */}
        <div className="border-b border-[var(--border)] px-2 py-1.5">
          <div className="flex items-center gap-1 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-solid)] px-2 py-1">
            <Search size={12} className="text-[var(--text-muted)]" />
            <input
              value={sessionSearch}
              onChange={(e) => setSessionSearch(e.target.value)}
              placeholder="Filter..."
              className="flex-1 bg-transparent text-[11px] text-[var(--text)] outline-none placeholder:text-[var(--text-subtle)]"
            />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {sortedSessions.map((s) => {
            const lastMsgPreview = s.last_message_preview ?? "";
            const messageCount = s.message_count ?? 0;
            const running = s.running ?? false;
            const updatedAt = s.updated_at ?? s.started_at ?? "";
            const isActive = s.session_id === sessionId;

            return (
              <div
                key={s.session_id}
                onClick={() => switchSession(s.session_id)}
                className={`group cursor-pointer border-b border-[var(--border)] px-3 py-2.5 hover:bg-[var(--bg-subtle)] ${
                  isActive ? "bg-[var(--accent-muted)]" : ""
                }`}
              >
                {editingSessId === s.session_id ? (
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      handleRenameSubmit(s.session_id);
                    }}
                    className="flex items-center gap-1"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <input
                      value={editLabel}
                      onChange={(e) => setEditLabel(e.target.value)}
                      className="flex-1 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-solid)] px-1.5 py-0.5 text-[12px] text-[var(--text)] outline-none"
                      autoFocus
                      onBlur={() => handleRenameSubmit(s.session_id)}
                    />
                    <button type="submit" className="p-0.5 text-[var(--success)]">
                      <Check size={12} />
                    </button>
                  </form>
                ) : (
                  <div className="space-y-1">
                    {/* Top row: label + status indicator */}
                    <div className="flex items-center gap-1.5">
                      <span className="flex-1 truncate text-[12px] font-medium text-[var(--text)]">
                        {s.label}
                      </span>
                      {running && (
                        <Loader2 size={10} className="animate-spin text-[var(--info)]" />
                      )}
                      {s.status === "error" && (
                        <XCircle size={10} className="text-[var(--danger)]" />
                      )}
                      <div className="flex opacity-0 group-hover:opacity-100">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleRenameStart(s.session_id, s.label);
                          }}
                          className="p-0.5 text-[var(--text-muted)] hover:text-[var(--text)]"
                          title="Rename"
                        >
                          <Edit3 size={10} />
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDelete(s.session_id);
                          }}
                          className="p-0.5 text-[var(--text-muted)] hover:text-[var(--danger)]"
                          title="Delete"
                        >
                          <Trash2 size={10} />
                        </button>
                      </div>
                    </div>
                    {/* Last message preview */}
                    {lastMsgPreview && (
                      <p className="truncate text-[11px] leading-[14px] text-[var(--text-subtle)]">
                        {lastMsgPreview}
                      </p>
                    )}
                    {/* Meta row: message count + time */}
                    <div className="flex items-center gap-2 text-[10px] text-[var(--text-muted)]">
                      {messageCount > 0 && <span>{messageCount} msgs</span>}
                      {updatedAt && (
                        <span>{formatRelativeTime(updatedAt)}</span>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </aside>

      {/* Conversation area */}
      <div className="flex flex-1 flex-col">
        <div className="flex min-h-0 flex-1">
          <div className="min-w-0 flex-1 overflow-y-auto p-6">
            {conversationBlocks.length === 0 ? (
              <EmptyState
                icon={<Bot size={36} />}
                title={sessionId ? "YeQu Agent" : "No Session"}
                description={
                  sessionId
                    ? "Input a prompt to start. The Agent will reason about your request and execute tools accordingly."
                    : "Create a session with the plus button to start a conversation."
                }
              />
            ) : (
              <div className="mx-auto max-w-5xl space-y-3">
                {conversationBlocks.map((block) => (
                  <ChatTimelineBlock
                    key={block.id}
                    block={block}
                    ycrTrace={ycrTrace}
                    onApproveAndRun={handleApproveAndRun}
                    onResumeOperation={handleResumeOperation}
                    onOperationStatusChange={handleOperationStatusChange}
                  />
                ))}
                {activeRunId && (
                  <RunProgressCard
                    runId={activeRunId}
                    onComplete={() => setActiveRunId(null)}
                  />
                )}
                <div ref={chatEndRef} />
              </div>
            )}
          </div>
          <ActivityPanel
            operations={operationBlocks}
            agentRun={runtimeStateQuery.data?.run ?? null}
            agentPlan={runtimeStateQuery.data?.plan ?? null}
            promptContext={promptContext}
            ycrTrace={ycrTrace}
            ycrTokenSummary={ycrTokenSummary}
            onResumeOperation={handleResumeOperation}
            onOperationStatusChange={handleOperationStatusChange}
          />
        </div>

        {/* Prompt Composer */}
        <div className="border-t border-[var(--border)] bg-[var(--surface-solid)] p-4">
          <div className="mx-auto max-w-3xl space-y-2">
            {pendingApprovals.length > 0 && (
              <ApprovalQueueBar
                toolCall={pendingApprovals[0]}
                index={1}
                total={pendingApprovals.length}
                busy={approvalBusyId === pendingApprovals[0].approvalId}
                disabled={Boolean(approvalBusyId)}
                error={approvalActionError}
                onApprove={() => handleToolApprovalDecision(pendingApprovals[0], "approve")}
                onDeny={() => handleToolApprovalDecision(pendingApprovals[0], "deny")}
              />
            )}
            <div className="flex items-center gap-2">
              <select
                value={executionMode}
                onChange={(e) => setExecutionMode(e.target.value)}
                className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-solid)] px-2.5 py-1.5 text-[12px] text-[var(--text)] outline-none"
              >
                <option value="auto">auto</option>
                <option value="readonly">readonly</option>
                <option value="assist">assist</option>
              </select>
              <select
                value={providerName}
                onChange={(e) => setProviderName(e.target.value)}
                className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-solid)] px-2.5 py-1.5 text-[12px] text-[var(--text)] outline-none"
              >
                <option value="deepseek">deepseek</option>
                <option value="fake">fake</option>
              </select>

              <label className="flex cursor-pointer items-center gap-1.5 text-[12px] text-[var(--text-muted)]">
                <input
                  type="checkbox"
                  checked={autoPlan}
                  onChange={(e) => setAutoPlan(e.target.checked)}
                  className="h-3.5 w-3.5 rounded"
                />
                Plan
              </label>

              <label className="flex items-center gap-1.5 text-[12px] text-[var(--text-muted)]">
                <span>Steps</span>
                <select
                  value={maxSteps}
                  onChange={(e) => setMaxSteps(Number(e.target.value))}
                  className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-solid)] px-2 py-1.5 text-[12px] text-[var(--text)] outline-none"
                  title="Maximum ReAct steps for this turn"
                >
                  {AGENT_MAX_STEP_OPTIONS.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>

              <span className="flex-1" />

              {isStreaming && (
                <Button variant="ghost" size="sm" onClick={cancel}>
                  Cancel
                </Button>
              )}
              {!isStreaming && sessionId && (
                <Button variant="ghost" size="sm" onClick={handleResumeLastRun}>
                  <RefreshCw size={13} />
                  <span className="ml-1">Resume</span>
                </Button>
              )}
            </div>

            <div className="flex items-end gap-2">
              <PromptComposerInput
                value={prompt}
                onChange={setPrompt}
                operationContext={operationContext}
                onRemoveOperationContext={() => {
                  setOperationContext(null);
                  writeStoredOperationContext(sessionId, null);
                }}
                onSubmit={handleSend}
                placeholder={!sessionId ? "Create a session to start..." : operationContext ? "Add an instruction for this operation context..." : autoPlan ? "Describe maintenance task..." : "Ask the agent..."}
                disabled={!sessionId || isStreaming}
              />
              <Button
                variant="primary"
                size="md"
                onClick={handleSend}
                disabled={!sessionId || (!prompt.trim() && !operationContext) || isStreaming}
              >
                {isStreaming ? (
                  <Loader2 size={16} className="animate-spin" />
                ) : (
                  <Send size={16} />
                )}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function PromptComposerInput({
  value,
  onChange,
  operationContext,
  onRemoveOperationContext,
  onSubmit,
  placeholder,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  operationContext: OperationContextChip | null;
  onRemoveOperationContext: () => void;
  onSubmit: () => void;
  placeholder: string;
  disabled: boolean;
}) {
  const editorRef = useRef<HTMLDivElement | null>(null);
  const contextId = operationContext?.operationId ?? "";

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const renderedContextId = editor.dataset.operationContextId ?? "";
    const renderedText = readPromptComposerText(editor);
    if (renderedContextId === contextId && renderedText === value) return;
    renderPromptComposerContent(editor, value, operationContext);
    if (document.activeElement === editor) {
      placeCaretAtEnd(editor);
    }
  }, [contextId, operationContext, value]);

  const handleInput = () => {
    const editor = editorRef.current;
    if (!editor) return;
    const hasChip = Boolean(editor.querySelector("[data-operation-chip='true']"));
    if (operationContext && !hasChip) {
      onRemoveOperationContext();
    }
    onChange(readPromptComposerText(editor));
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSubmit();
      return;
    }
    if (event.key === "Backspace" && operationContext) {
      const editor = editorRef.current;
      if (editor && !readPromptComposerText(editor).trim()) {
        event.preventDefault();
        onRemoveOperationContext();
      }
    }
  };

  const handlePaste = (event: React.ClipboardEvent<HTMLDivElement>) => {
    event.preventDefault();
    const text = event.clipboardData.getData("text/plain");
    document.execCommand("insertText", false, text);
  };

  const empty = !value.trim() && !operationContext;

  return (
    <div className="relative flex-1">
      {empty && (
        <span className="pointer-events-none absolute left-3 top-2 text-[14px] text-[var(--text-subtle)]">
          {placeholder}
        </span>
      )}
      <div
        ref={editorRef}
        role="textbox"
        aria-label="Agent prompt"
        aria-multiline="true"
        aria-disabled={disabled}
        contentEditable={!disabled}
        suppressContentEditableWarning
        onInput={handleInput}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
        className={`min-h-[42px] max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-solid)] px-3 py-2 text-[14px] text-[var(--text)] outline-none ${
          disabled ? "cursor-not-allowed opacity-60" : "focus:border-[var(--accent)]"
        }`}
      />
    </div>
  );
}

// ── Timeline Block Renderer ──

function ChatTimelineBlock({
  block,
  ycrTrace,
  onApproveAndRun,
  onResumeOperation,
  onOperationStatusChange,
}: {
  block: ChatBlock;
  ycrTrace?: YcrTraceItem[];
  onApproveAndRun?: (planId: string, onRunStarted: (runId: string) => void) => void;
  onResumeOperation?: (operationId: string) => void;
  onOperationStatusChange?: (update: OperationStatusUpdate) => void;
}) {
  const inlineYcr = ycrTrace ? ycrTraceForBlock(block, ycrTrace) : [];
  switch (block.type) {
    case "user":
      return <UserBubble block={block} />;
    case "assistant_text":
      return (
        <WithYcrInline trace={inlineYcr}>
          <AssistantTextBubble block={block} />
        </WithYcrInline>
      );
    case "tool_group":
      return (
        <WithYcrInline trace={inlineYcr}>
          <ToolGroupBubble block={block} />
        </WithYcrInline>
      );
    case "artifact_presentation":
      return <ArtifactPresentationBubble block={block} />;
    case "operation_card":
      return (
        <OperationCard
          block={block}
          onResume={onResumeOperation}
          onStatusChange={onOperationStatusChange}
        />
      );
    case "system_event":
      return <SystemEventBubble block={block} />;
    case "run_status":
      return <RunStatusBubble block={block} />;
    default:
      return null;
  }
}

function WithYcrInline({
  trace,
  children,
}: {
  trace: YcrTraceItem[];
  children: ReactNode;
}) {
  if (trace.length === 0) return <>{children}</>;
  const upload = trace.reduce((sum, item) => sum + (item.uploadEstimatedTokens ?? 0), 0);
  const download = trace.reduce((sum, item) => sum + (item.downloadEstimatedTokens ?? 0), 0);
  const saved = trace.reduce((sum, item) => {
    if (item.kind !== "provider_projection") return sum;
    const raw = item.rawEstimatedTokens ?? 0;
    const projected = item.projectedEstimatedTokens ?? 0;
    return sum + Math.max(0, raw - projected);
  }, 0);
  if (upload + download + saved <= 0) return <>{children}</>;
  return (
    <div className="space-y-1">
      {children}
      <div className="ml-10 flex flex-wrap gap-1.5 text-[10px] text-[var(--text-subtle)]">
        {upload > 0 && <YcrInlineMetric icon={<Upload size={10} />} label="up" value={upload} />}
        {download > 0 && (
          <YcrInlineMetric icon={<Download size={10} />} label="down" value={download} />
        )}
        {saved > 0 && <YcrInlineMetric icon={<Gauge size={10} />} label="saved" value={saved} />}
      </div>
    </div>
  );
}

function YcrInlineMetric({
  icon,
  label,
  value,
}: {
  icon: ReactNode;
  label: string;
  value: number;
}) {
  return (
    <span className="inline-flex items-center gap-1 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-muted)] px-1.5 py-0.5 font-mono">
      {icon}
      {label}:{formatTokenCount(value)}
    </span>
  );
}

function ycrTraceForBlock(block: ChatBlock, trace: YcrTraceItem[]): YcrTraceItem[] {
  if (block.type !== "assistant_text" && block.type !== "tool_group") return [];
  const blockTime = Date.parse(block.created_at);
  if (!Number.isFinite(blockTime)) return [];
  const windowMs = block.type === "assistant_text" ? 8000 : 12000;
  return trace.filter((item) => {
    const itemTime = Date.parse(item.created_at);
    if (!Number.isFinite(itemTime)) return false;
    return Math.abs(itemTime - blockTime) <= windowMs;
  });
}

// ── User Bubble ──

function ApprovalQueueBar({
  toolCall,
  index,
  total,
  busy,
  disabled,
  error,
  onApprove,
  onDeny,
}: {
  toolCall: ToolCallState;
  index: number;
  total: number;
  busy: boolean;
  disabled: boolean;
  error: string | null;
  onApprove: () => void;
  onDeny: () => void;
}) {
  return (
    <div className="overflow-hidden rounded-[var(--radius-md)] border border-[var(--warning-muted)] bg-[var(--surface-solid)] shadow-sm">
      <div className="flex items-center gap-3 bg-[var(--warning-muted)]/20 px-3 py-2">
        <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--warning-muted)]/40 text-[var(--warning)]">
          {busy ? <Loader2 size={16} className="animate-spin" /> : <AlertTriangle size={16} />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[12px] font-semibold text-[var(--text)]">
              Approval {index}/{total}
            </span>
            <span className="truncate font-mono text-[12px] text-[var(--text-muted)]">
              {toolCall.name}
            </span>
            {toolCall.targetNodeId && (
              <span className="max-w-[120px] truncate rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-solid)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-subtle)]">
                @ {toolCall.targetNodeId}
              </span>
            )}
          </div>
          <p className="mt-0.5 truncate text-[11px] text-[var(--text-subtle)]">
            {toolCall.approvalId} · choose one, then the next approval will appear automatically
          </p>
          {error && (
            <p className="mt-1 text-[11px] font-medium text-[var(--danger)]">
              {error}
            </p>
          )}
        </div>
        <div className="flex flex-shrink-0 items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onDeny} disabled={disabled}>
            <XCircle size={13} />
            <span className="ml-1">No</span>
          </Button>
          <Button variant="primary" size="sm" onClick={onApprove} disabled={disabled}>
            <CheckCircle size={13} />
            <span className="ml-1">Yes, run</span>
          </Button>
        </div>
      </div>
    </div>
  );
}

function UserBubble({ block }: { block: UserBlock }) {
  return (
    <div className="flex justify-end gap-3">
      <div className="max-w-[75%] rounded-[var(--radius-md)] bg-[var(--accent-muted)] px-4 py-2.5">
        <p className="whitespace-pre-wrap text-[14px] leading-[22px] text-[var(--text)]">
          {block.content}
        </p>
      </div>
      <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--accent-muted)] text-[var(--accent)]">
        <User size={14} />
      </div>
    </div>
  );
}

// ── Assistant Text Bubble ──

function AssistantTextBubble({ block }: { block: AssistantTextBlock }) {
  return (
    <div className="flex gap-3">
      <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--bg-subtle)] text-[var(--text-muted)]">
        <Bot size={14} />
      </div>
      <div className="max-w-[75%] rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface-solid)] px-4 py-2.5">
        <MarkdownMessage content={block.content} isStreaming={block.streaming} />
      </div>
    </div>
  );
}

function ArtifactPresentationBubble({ block }: { block: ArtifactPresentationBlock }) {
  return (
    <div className="flex gap-3">
      <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--bg-subtle)] text-[var(--text-muted)]">
        <Bot size={14} />
      </div>
      <div className="w-full max-w-[calc(100%-2.5rem)]">
        <ArtifactList artifacts={block.artifacts} mode="presentation" />
      </div>
    </div>
  );
}

function ActivityPanel({
  operations,
  agentRun,
  agentPlan,
  promptContext,
  ycrTrace,
  ycrTokenSummary,
  onResumeOperation,
  onOperationStatusChange,
}: {
  operations: OperationCardBlock[];
  agentRun: AgentRunProjection | null;
  agentPlan: AgentRuntimePlan | null;
  promptContext: PromptContextData | null;
  ycrTrace: YcrTraceItem[];
  ycrTokenSummary: YcrTokenSummary;
  onResumeOperation: (operationId: string) => void;
  onOperationStatusChange?: (update: OperationStatusUpdate) => void;
}) {
  const latestOperations = [...operations].sort((a, b) =>
    (b.created_at ?? "").localeCompare(a.created_at ?? ""),
  );

  return (
    <aside className="hidden w-[360px] flex-shrink-0 overflow-y-auto border-l border-[var(--border)] bg-[var(--surface-muted)] p-3 xl:block">
      <div className="space-y-3">
        <section>
          <div className="mb-2 flex items-center gap-2">
            <RefreshCw size={14} className="text-[var(--text-muted)]" />
            <h2 className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">
              Operations
            </h2>
            <span className="flex-1" />
            <span className="text-[11px] text-[var(--text-subtle)]">{latestOperations.length}</span>
          </div>
          {latestOperations.length === 0 ? (
            <div className="rounded-[var(--radius-sm)] border border-dashed border-[var(--border)] bg-[var(--surface-solid)] p-3 text-[12px] text-[var(--text-subtle)]">
              No waitable operation in this session.
            </div>
          ) : (
            <div className="space-y-2">
              {latestOperations.map((operation) => (
                <OperationCard
                  key={operation.id}
                  block={operation}
                  onResume={onResumeOperation}
                  onStatusChange={onOperationStatusChange}
                  surface="panel"
                />
              ))}
            </div>
          )}
        </section>

        <AgentRunStatePanel run={agentRun} />

        <AgentPlanPanel plan={agentPlan} />

        <YcrActivityPanel
          trace={ycrTrace}
          tokenSummary={ycrTokenSummary}
        />

        {promptContext && <PromptContextPanel promptContext={promptContext} />}
      </div>
    </aside>
  );
}

function AgentRunStatePanel({ run }: { run: AgentRunProjection | null }) {
  const taskState = run?.task_state ?? null;
  const objectiveText = runtimeObjectiveText(taskState?.objective, run?.user_message);
  const pendingOperations = countArray(taskState?.pending_operations);
  const pendingApprovals = countArray(taskState?.pending_approvals);
  const artifacts = countArray(taskState?.artifacts);
  const workingSet = runtimeWorkingSetCount(taskState?.working_set);
  const facts = countArray(taskState?.facts);
  const blockers = countArray(taskState?.blockers);
  const completionStatus =
    typeof taskState?.completion === "object" && taskState.completion !== null
      ? String((taskState.completion as Record<string, unknown>).status ?? "")
      : "";

  return (
    <section>
      <div className="mb-2 flex items-center gap-2">
        <Bot size={14} className="text-[var(--text-muted)]" />
        <h2 className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">
          Runtime
        </h2>
        <span className="flex-1" />
        {run && <StatusBadge status={run.status} />}
      </div>
      {!run ? (
        <div className="rounded-[var(--radius-sm)] border border-dashed border-[var(--border)] bg-[var(--surface-solid)] p-3 text-[12px] text-[var(--text-subtle)]">
          No AgentRun state has been recorded in this session.
        </div>
      ) : (
        <div className="space-y-2 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-solid)] p-3 text-[12px]">
          <div className="min-w-0">
            <div className="truncate font-medium text-[var(--text)]">
              {objectiveText}
            </div>
            <div className="mt-1 flex flex-wrap gap-1.5 text-[10px] text-[var(--text-subtle)]">
              <span className="rounded-[var(--radius-sm)] bg-[var(--surface-muted)] px-1.5 py-0.5 font-mono">
                {run.run_id}
              </span>
              <span className="rounded-[var(--radius-sm)] bg-[var(--surface-muted)] px-1.5 py-0.5">
                {run.provider_name}
              </span>
              {run.target_node_id && (
                <span className="rounded-[var(--radius-sm)] bg-[var(--surface-muted)] px-1.5 py-0.5">
                  @{run.target_node_id}
                </span>
              )}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <RuntimeMetric label="events" value={run.events.length} />
            <RuntimeMetric label="steps" value={run.steps.length} />
            <RuntimeMetric label="facts" value={facts} />
            <RuntimeMetric label="blockers" value={blockers} />
            <RuntimeMetric label="operations" value={pendingOperations} />
            <RuntimeMetric label="approvals" value={pendingApprovals} />
            <RuntimeMetric label="artifacts" value={artifacts} />
            <RuntimeMetric label="working set" value={workingSet} />
          </div>
          {completionStatus && (
            <div className="rounded-[var(--radius-sm)] bg-[var(--surface-muted)] px-2 py-1 text-[11px] text-[var(--text-muted)]">
              completion: {completionStatus}
            </div>
          )}
          {run.error_message && (
            <div className="rounded-[var(--radius-sm)] border border-red-200 bg-red-50 px-2 py-1 text-[11px] text-red-700">
              {run.error_code ? `${run.error_code}: ` : ""}
              {run.error_message}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function RuntimeMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-muted)] px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-[0.06em] text-[var(--text-subtle)]">
        {label}
      </div>
      <div className="mt-0.5 font-mono text-[12px] font-semibold text-[var(--text)]">
        {value.toLocaleString()}
      </div>
    </div>
  );
}

function countArray(value: unknown): number {
  return Array.isArray(value) ? value.length : 0;
}

function runtimeObjectiveText(objective: unknown, fallback?: string): string {
  if (typeof objective === "string" && objective.trim()) {
    return objective;
  }
  if (objective && typeof objective === "object" && !Array.isArray(objective)) {
    const text = (objective as Record<string, unknown>).text;
    if (typeof text === "string" && text.trim()) {
      return text;
    }
  }
  return fallback?.trim() || "Agent task";
}

function runtimeWorkingSetCount(value: unknown): number {
  if (Array.isArray(value)) {
    return value.length;
  }
  if (!value || typeof value !== "object") {
    return 0;
  }
  return Object.values(value as Record<string, unknown>).reduce<number>((total, item) => {
    return total + (Array.isArray(item) ? item.length : 0);
  }, 0);
}

function AgentPlanPanel({ plan }: { plan: AgentRuntimePlan | null }) {
  const latestSteps = plan?.steps ?? [];
  return (
    <section>
      <div className="mb-2 flex items-center gap-2">
        <FileText size={14} className="text-[var(--text-muted)]" />
        <h2 className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">
          Plan
        </h2>
        <span className="flex-1" />
        {plan && <StatusBadge status={plan.status} />}
      </div>
      {!plan ? (
        <div className="rounded-[var(--radius-sm)] border border-dashed border-[var(--border)] bg-[var(--surface-solid)] p-3 text-[12px] text-[var(--text-subtle)]">
          No Agent plan has been created in this session.
        </div>
      ) : (
        <div className="space-y-2 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-solid)] p-3">
          <div>
            <div className="line-clamp-2 text-[12px] font-medium text-[var(--text)]">
              {plan.objective}
            </div>
            <div className="mt-1 flex flex-wrap gap-1.5 text-[10px] text-[var(--text-subtle)]">
              <span className="rounded-[var(--radius-sm)] bg-[var(--surface-muted)] px-1.5 py-0.5 font-mono">
                {plan.plan_id}
              </span>
              <span className="rounded-[var(--radius-sm)] bg-[var(--surface-muted)] px-1.5 py-0.5">
                {plan.provider_name}
              </span>
              {plan.target_node_id && (
                <span className="rounded-[var(--radius-sm)] bg-[var(--surface-muted)] px-1.5 py-0.5">
                  @{plan.target_node_id}
                </span>
              )}
            </div>
          </div>
          {latestSteps.length > 0 && (
            <div className="space-y-1">
              {latestSteps.map((step) => (
                <div
                  key={step.step_id}
                  className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-muted)] p-2"
                >
                  <div className="flex items-start gap-2">
                    <span className="mt-0.5 font-mono text-[10px] text-[var(--text-subtle)]">
                      {step.step_index}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[12px] text-[var(--text)]">{step.title}</div>
                      <div className="mt-1 flex flex-wrap gap-1.5 text-[10px] text-[var(--text-subtle)]">
                        <span>{step.kind}</span>
                        {step.operation_id && <span>op {step.operation_id}</span>}
                        {step.tool_call_id && <span>tool {step.tool_call_id}</span>}
                      </div>
                    </div>
                    <StatusBadge status={step.status} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function YcrActivityPanel({
  trace,
  tokenSummary,
}: {
  trace: YcrTraceItem[];
  tokenSummary: YcrTokenSummary;
}) {
  const latest = [...trace].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 8);
  const hasActual = tokenSummary.uploadActualTokens > 0 || tokenSummary.downloadActualTokens > 0;
  return (
    <section>
      <div className="mb-2 flex items-center gap-2">
        <Database size={14} className="text-[var(--text-muted)]" />
        <h2 className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">
          YCR
        </h2>
        <span className="flex-1" />
        <span className="text-[11px] text-[var(--text-subtle)]">
          {tokenSummary.projectionCount} projections
        </span>
      </div>

      <div className="space-y-2 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-solid)] p-3">
        <div className="grid grid-cols-2 gap-2">
          <YcrMetric
            icon={<Upload size={12} />}
            label="Upload est"
            value={formatTokenCount(tokenSummary.uploadEstimatedTokens)}
          />
          <YcrMetric
            icon={<Download size={12} />}
            label="Download est"
            value={formatTokenCount(tokenSummary.downloadEstimatedTokens)}
          />
          <YcrMetric
            icon={<Gauge size={12} />}
            label="Saved est"
            value={formatTokenCount(tokenSummary.toolSavedEstimatedTokens)}
          />
          <YcrMetric
            icon={<RefreshCw size={12} />}
            label={hasActual ? "Actual" : "Provider calls"}
            value={
              hasActual
                ? `${formatTokenCount(tokenSummary.uploadActualTokens)} / ${formatTokenCount(
                    tokenSummary.downloadActualTokens,
                  )}`
                : String(tokenSummary.providerCallCount)
            }
          />
        </div>

        {latest.length === 0 ? (
          <div className="rounded-[var(--radius-sm)] border border-dashed border-[var(--border)] bg-[var(--surface-muted)] p-2.5 text-[12px] text-[var(--text-subtle)]">
            No YCR telemetry in this session yet.
          </div>
        ) : (
          <div className="space-y-1.5">
            {latest.map((item) => (
              <YcrTraceRow key={item.id} item={item} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function YcrMetric({
  icon,
  label,
  value,
}: {
  icon: ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-muted)] p-2">
      <div className="mb-1 flex items-center gap-1 text-[10px] uppercase tracking-[0.06em] text-[var(--text-subtle)]">
        {icon}
        <span>{label}</span>
      </div>
      <div className="font-mono text-[13px] font-semibold text-[var(--text)]">{value}</div>
    </div>
  );
}

function YcrTraceRow({ item }: { item: YcrTraceItem }) {
  const step = item.step ? `step ${item.step}` : "step ?";
  const title =
    item.kind === "tool_storage"
      ? item.toolName ?? "tool result"
      : item.kind === "provider_projection"
        ? item.toolName ?? "provider projection"
        : item.kind === "registry_search"
          ? "registry search"
          : item.kind === "error"
            ? "YCR error"
            : `${item.providerName ?? "provider"} ${item.kind === "provider_input" ? "input" : "output"}`;
  return (
    <details className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-muted)] p-2 text-[12px]">
      <summary className="cursor-pointer list-none">
        <div className="flex items-center gap-2">
          {item.kind === "tool_storage" ||
          item.kind === "provider_projection" ||
          item.kind === "registry_search" ? (
            <Database size={12} className="text-[var(--accent)]" />
          ) : item.kind === "error" ? (
            <XCircle size={12} className="text-[var(--danger)]" />
          ) : item.kind === "provider_input" ? (
            <Upload size={12} className="text-[var(--info)]" />
          ) : (
            <Download size={12} className="text-[var(--success)]" />
          )}
          <span className="min-w-0 flex-1 truncate font-medium text-[var(--text)]">{title}</span>
          <span className="font-mono text-[10px] text-[var(--text-subtle)]">{step}</span>
        </div>
        <div className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-[11px] text-[var(--text-subtle)]">
          {item.uploadEstimatedTokens !== undefined && (
            <span>up {formatTokenCount(item.uploadEstimatedTokens)}</span>
          )}
          {item.downloadEstimatedTokens !== undefined && (
            <span>down {formatTokenCount(item.downloadEstimatedTokens)}</span>
          )}
          {item.rawEstimatedTokens !== undefined && (
            <span>raw {formatTokenCount(item.rawEstimatedTokens)}</span>
          )}
          {item.projectedEstimatedTokens !== undefined && (
            <span>proj {formatTokenCount(item.projectedEstimatedTokens)}</span>
          )}
          {item.refCount ? <span>{item.refCount} refs</span> : null}
          {item.omittedCount ? <span>{item.omittedCount} omitted</span> : null}
          {item.snapshotStatus && <span>snapshot {item.snapshotStatus}</span>}
          {item.capabilityCandidateCount !== undefined && (
            <span>{formatTokenCount(item.capabilityCandidateCount)} candidates</span>
          )}
          {item.sessionStateCounts && Object.keys(item.sessionStateCounts).length > 0 && (
            <span>state {formatStateCounts(item.sessionStateCounts)}</span>
          )}
        </div>
      </summary>
      <div className="mt-2 space-y-1.5">
        {item.summary && (
          <p className="text-[11px] leading-[16px] text-[var(--text-muted)]">{item.summary}</p>
        )}
        {item.projectionPolicy && (
          <p className="font-mono text-[10px] text-[var(--text-subtle)]">
            policy: {item.projectionPolicy}
          </p>
        )}
        {item.rawSizeBytes !== undefined && item.projectedSizeBytes !== undefined && (
          <p className="font-mono text-[10px] text-[var(--text-subtle)]">
            bytes: {formatBytes(item.rawSizeBytes)} {"->"} {formatBytes(item.projectedSizeBytes)}
          </p>
        )}
        {item.kind === "registry_search" && (
          <RegistrySearchSummary item={item} />
        )}
        {item.data && <JsonView data={item.data} />}
      </div>
    </details>
  );
}

function RegistrySearchSummary({ item }: { item: YcrTraceItem }) {
  const retrieval = item.retrieval ?? {};
  const index = asPanelRecord(retrieval.index);
  const cache = asPanelRecord(retrieval.cache);
  const matches = item.registryMatches ?? [];
  const strategy = String(retrieval.strategy ?? "registry");
  const indexStatus = String(index.status ?? "ready");
  const cacheStatus = typeof cache.status === "string" ? cache.status : "";
  const retryAfter =
    typeof index.retry_after_seconds === "number" ? index.retry_after_seconds : undefined;
  return (
    <div className="space-y-1.5">
      <div className="grid grid-cols-2 gap-1.5 font-mono text-[10px] text-[var(--text-subtle)]">
        <span>strategy: {strategy}</span>
        <span>index: {indexStatus}</span>
        {cacheStatus && <span>cache: {cacheStatus}</span>}
        {retryAfter !== undefined && <span>retry: {retryAfter}s</span>}
      </div>
      {indexStatus === "not_ready" && (
        <div className="rounded-[var(--radius-sm)] border border-[var(--warning)]/40 bg-[var(--warning)]/10 px-2 py-1 text-[11px] text-[var(--warning)]">
          Capability index is not ready. Waiting for YCR background indexing before semantic tool search can return matches.
        </div>
      )}
      {matches.length > 0 && (
        <div className="space-y-1">
          {matches.slice(0, 5).map((match, index) => (
            <div
              key={`${String(match.canonical_name ?? "capability")}:${index}`}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-subtle)] px-2 py-1"
            >
              <div className="truncate font-mono text-[11px] text-[var(--text)]">
                {String(match.canonical_name ?? "unknown")}
              </div>
              <div className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5 text-[10px] text-[var(--text-subtle)]">
                <span>scope {String(match.scope ?? "-")}</span>
                <span>plane {String(match.plane ?? "-")}</span>
                <span>surface {String(match.invocation_surface ?? "-")}</span>
                <span>dispatch {String(match.dispatch_kind ?? "-")}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function PromptContextPanel({ promptContext }: { promptContext: PromptContextData }) {
  return (
    <details className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-solid)] p-3 text-[12px]">
      <summary className="cursor-pointer font-medium text-[var(--text-muted)]">
        Context — {promptContext.provider_name} / {promptContext.execution_mode}
        {promptContext.routing_mode && ` / ${promptContext.routing_mode}`}
        {promptContext.target_node_id && ` @ ${promptContext.target_node_id}`}
        <span className="ml-2 text-[var(--text-subtle)]">
          ({promptContext.available_functions.length} tools)
        </span>
      </summary>
      <div className="mt-2 space-y-2">
        <div>
          <span className="font-semibold text-[var(--text-subtle)]">
            Capability Context
          </span>
          <JsonView data={promptContext.capability_context ?? {}} />
        </div>
        <div>
          <span className="font-semibold text-[var(--text-subtle)]">
            Tool Count By Node
          </span>
          <JsonView data={promptContext.tool_count_by_node ?? {}} />
        </div>
        <div>
          <span className="font-semibold text-[var(--text-subtle)]">System Prompt</span>
          <pre className="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-subtle)] p-2 text-[11px] leading-relaxed text-[var(--text)]">
            {promptContext.system_prompt}
          </pre>
        </div>
        <div>
          <span className="font-semibold text-[var(--text-subtle)]">
            Tools ({promptContext.available_functions.length})
          </span>
          <div className="mt-1 max-h-64 overflow-y-auto space-y-0.5">
            {promptContext.available_functions.map((f) => (
              <div key={f.name} className="grid gap-0.5">
                <span className="font-mono text-[var(--text)]">{f.name}</span>
                <span className="text-[var(--text-subtle)]">{f.description}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </details>
  );
}

function OperationCard({
  block,
  onResume,
  onStatusChange,
  surface = "timeline",
}: {
  block: OperationCardBlock;
  onResume?: (operationId: string) => void;
  onStatusChange?: (update: OperationStatusUpdate) => void;
  surface?: "timeline" | "panel";
}) {
  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const lastStatusNotificationRef = useRef("");
  const queryClient = useQueryClient();
  const operationQuery = useQuery({
    queryKey: ["operation", block.operationId],
    queryFn: () => getOperation(block.operationId),
    refetchInterval: (queryResult) => {
      const status = queryResult.state.data?.operation.status ?? block.status;
      return isOperationTerminal(status) ? false : 2000;
    },
  });

  const operation = operationQuery.data?.operation;
  const artifacts = operationQuery.data?.artifacts ?? [];
  const status = operation?.status ?? block.status;
  const title = operation?.title ?? block.title ?? `${block.kind} operation`;
  const refType = operation?.ref_type ?? block.refType;
  const refId = operation?.ref_id ?? block.refId;
  const errorMessage = operation?.error_message ?? block.errorMessage;
  const canCancel = Boolean(operation?.cancel_supported ?? block.waitHandle?.cancel_supported);
  const terminal = isOperationTerminal(status);
  const progressPct = operation?.progress_pct ?? block.progressPct ?? null;
  const progressMessage = operation?.progress_message ?? block.progressMessage ?? null;
  const message = progressMessage ?? operationStatusMessage(status, block.message);
  const transferSummary = getTransferSummary(operationQuery.data?.transfer);
  const compact = surface === "panel";

  useEffect(() => {
    if (!operation || !onStatusChange) return;
    const nextStatus = operation.status ?? block.status;
    const nextTitle = operation.title ?? block.title;
    const nextRefType = operation.ref_type ?? block.refType;
    const nextRefId = operation.ref_id ?? block.refId;
    const nextProgressPct = operation.progress_pct ?? block.progressPct ?? null;
    const nextProgressMessage = operation.progress_message ?? block.progressMessage ?? null;
    const nextErrorCode = operation.error_code ?? block.errorCode ?? null;
    const nextErrorMessage = operation.error_message ?? block.errorMessage ?? null;
    const hasChange =
      nextStatus !== block.status ||
      nextTitle !== block.title ||
      nextRefType !== block.refType ||
      nextRefId !== block.refId ||
      nextProgressPct !== (block.progressPct ?? null) ||
      nextProgressMessage !== (block.progressMessage ?? null) ||
      nextErrorCode !== (block.errorCode ?? null) ||
      nextErrorMessage !== (block.errorMessage ?? null);
    if (!hasChange) return;
    const notifyKey = JSON.stringify([
      block.operationId,
      nextStatus,
      nextTitle,
      nextRefType,
      nextRefId,
      nextProgressPct,
      nextProgressMessage,
      nextErrorCode,
      nextErrorMessage,
    ]);
    if (lastStatusNotificationRef.current === notifyKey) return;
    lastStatusNotificationRef.current = notifyKey;
    onStatusChange({
      operationId: block.operationId,
      status: nextStatus,
      previousStatus: block.status,
      title: nextTitle,
      kind: operation.kind ?? block.kind,
      refType: nextRefType,
      refId: nextRefId,
      progressPct: nextProgressPct,
      progressMessage: nextProgressMessage,
      errorCode: nextErrorCode,
      errorMessage: nextErrorMessage,
    });
  }, [
    block.errorCode,
    block.errorMessage,
    block.kind,
    block.operationId,
    block.progressMessage,
    block.progressPct,
    block.refId,
    block.refType,
    block.status,
    block.title,
    onStatusChange,
    operation,
  ]);

  const handleCancel = async () => {
    setCancelBusy(true);
    setCancelError(null);
    try {
      await cancelOperation(block.operationId, "cancelled_from_agent_chat");
      queryClient.invalidateQueries({ queryKey: ["operation", block.operationId] });
    } catch (error) {
      setCancelError(error instanceof Error ? error.message : String(error));
    } finally {
      setCancelBusy(false);
    }
  };

  return (
    <div className={compact ? "" : "flex gap-3"}>
      {!compact && (
        <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--bg-subtle)] text-[var(--text-muted)]">
          {terminal ? <CheckCircle size={14} /> : <RefreshCw size={14} className="animate-spin" />}
        </div>
      )}
      <div className={`${compact ? "w-full" : "w-full max-w-[75%]"} rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface-solid)] p-3 shadow-sm`}>
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-semibold text-[var(--text)]">{title}</span>
          <span className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-muted)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-subtle)]">
            {block.kind}
          </span>
          <span className="flex-1" />
          <StatusBadge status={status} />
        </div>
        <div className="mt-2 grid gap-1 text-[11px] text-[var(--text-subtle)]">
          <p className="font-mono">operation: {block.operationId}</p>
          {refType && refId && <p className="font-mono">{refType}: {refId}</p>}
          <p>{message}</p>
        </div>
        <OperationProgressBar progressPct={progressPct} terminal={terminal} />
        {transferSummary && <TransferOperationSummary summary={transferSummary} />}
        {errorMessage && (
          <p className="mt-2 rounded-[var(--radius-sm)] border border-[var(--danger-muted)] bg-[var(--danger-muted)]/20 p-2 text-[12px] text-[var(--danger)]">
            {errorMessage}
          </p>
        )}
        {artifacts.length > 0 && (
          <div className="mt-3">
            <ArtifactList artifacts={artifacts} />
          </div>
        )}
        {cancelError && <p className="mt-2 text-[12px] text-[var(--danger)]">{cancelError}</p>}
        <div className="mt-3 flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() =>
              queryClient.invalidateQueries({ queryKey: ["operation", block.operationId] })
            }
            disabled={operationQuery.isFetching}
          >
            <RefreshCw size={13} />
            <span className="ml-1">Refresh</span>
          </Button>
          {canCancel && !terminal && (
            <Button variant="ghost" size="sm" onClick={handleCancel} disabled={cancelBusy}>
              {cancelBusy ? <Loader2 size={13} className="animate-spin" /> : <XCircle size={13} />}
              <span className="ml-1">Cancel</span>
            </Button>
          )}
          {terminal && onResume && (
            <Button variant="primary" size="sm" onClick={() => onResume(block.operationId)}>
              <Bot size={13} />
              <span className="ml-1">Append</span>
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

interface TransferSummaryView {
  sourceNodeId: string | null;
  sourcePath: string | null;
  sourceStatus: string | null;
  targetNodeId: string | null;
  targetPath: string | null;
  targetOutputDir: string | null;
  targetStatus: string | null;
  sizeBytes: number | null;
  phase: string | null;
  bytesTransferred: number | null;
  rateBytesPerSec: number | null;
  etaSec: number | null;
  lastProgressAt: string | null;
  progressSource: string | null;
  sha256Match: boolean | null;
  sizeMatch: boolean | null;
}

function TransferOperationSummary({ summary }: { summary: TransferSummaryView }) {
  return (
    <div className="mt-3 grid gap-1.5 text-[11px] text-[var(--text-subtle)]">
      <TransferSummaryRow
        label="From"
        nodeId={summary.sourceNodeId}
        path={summary.sourcePath}
        status={summary.sourceStatus}
      />
      <TransferSummaryRow
        label="To"
        nodeId={summary.targetNodeId}
        path={summary.targetPath ?? summary.targetOutputDir}
        status={summary.targetStatus}
      />
      {summary.phase && (
        <div className="flex">
          <span className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-muted)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-subtle)]">
            {formatPhase(summary.phase)}
          </span>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {summary.bytesTransferred !== null && (
          <span>
            {formatBytes(summary.bytesTransferred)}
            {summary.sizeBytes !== null ? ` / ${formatBytes(summary.sizeBytes)}` : ""}
          </span>
        )}
        {summary.bytesTransferred === null && summary.sizeBytes !== null && (
          <span>{formatBytes(summary.sizeBytes)}</span>
        )}
        {summary.rateBytesPerSec !== null && (
          <span>{formatBytes(summary.rateBytesPerSec)}/s</span>
        )}
        {summary.etaSec !== null && <span>ETA {formatDuration(summary.etaSec)}</span>}
        {summary.sizeMatch !== null && (
          <span>size {summary.sizeMatch ? "verified" : "mismatch"}</span>
        )}
        {summary.sha256Match !== null && (
          <span>sha256 {summary.sha256Match ? "verified" : "mismatch"}</span>
        )}
      </div>
      {(summary.lastProgressAt || summary.progressSource) && (
        <div className="flex flex-wrap items-center gap-2 text-[10px] text-[var(--text-muted)]">
          {summary.lastProgressAt && <span>updated {formatTimestamp(summary.lastProgressAt)}</span>}
          {summary.progressSource && <span>{summary.progressSource}</span>}
        </div>
      )}
    </div>
  );
}

function TransferSummaryRow({
  label,
  nodeId,
  path,
  status,
}: {
  label: string;
  nodeId: string | null;
  path: string | null;
  status: string | null;
}) {
  return (
    <div className="grid grid-cols-[34px_minmax(0,1fr)] items-start gap-2">
      <span className="text-[var(--text-muted)]">{label}</span>
      <span className="min-w-0">
        {nodeId && <span className="font-mono text-[var(--text)]">{nodeId}</span>}
        {path && (
          <span className="ml-1 break-all font-mono text-[var(--text-subtle)]">
            {fileName(path)}
          </span>
        )}
        {status && <StatusBadge status={status} />}
      </span>
    </div>
  );
}

function OperationProgressBar({
  progressPct,
  terminal,
}: {
  progressPct: number | null;
  terminal: boolean;
}) {
  if (progressPct === null && terminal) return null;
  const safePct =
    progressPct === null ? null : Math.max(0, Math.min(100, Math.round(progressPct)));
  return (
    <div className="mt-3">
      <div className="h-1.5 overflow-hidden rounded-full bg-[var(--bg-subtle)]">
        {safePct === null ? (
          <div className="h-full w-1/3 animate-pulse rounded-full bg-[var(--accent)]/60" />
        ) : (
          <div
            className="h-full rounded-full bg-[var(--accent)] transition-[width] duration-300"
            style={{ width: `${safePct}%` }}
          />
        )}
      </div>
      {safePct !== null && (
        <div className="mt-1 text-right font-mono text-[10px] text-[var(--text-subtle)]">
          {safePct}%
        </div>
      )}
    </div>
  );
}

function getTransferSummary(transfer: Record<string, unknown> | undefined): TransferSummaryView | null {
  const summary = recordValue(transfer?.summary);
  if (!summary) return null;
  const source = recordValue(summary.source);
  const target = recordValue(summary.target);
  const verification = recordValue(summary.verification);
  const progress = recordValue(summary.progress);
  if (!source && !target) return null;
  return {
    sourceNodeId: stringValue(source?.node_id),
    sourcePath: stringValue(source?.path),
    sourceStatus: stringValue(source?.status),
    targetNodeId: stringValue(target?.node_id),
    targetPath: stringValue(target?.path),
    targetOutputDir: stringValue(target?.output_dir),
    targetStatus: stringValue(target?.status),
    sizeBytes: numberValue(source?.size_bytes ?? target?.size_bytes),
    phase: stringValue(progress?.phase),
    bytesTransferred: numberValue(progress?.bytes_transferred),
    rateBytesPerSec: numberValue(progress?.rate_bytes_per_sec),
    etaSec: numberValue(progress?.eta_sec),
    lastProgressAt: stringValue(progress?.last_progress_at),
    progressSource: stringValue(
      recordValue(progress?.source)?.progress_source ?? recordValue(progress?.target)?.progress_source,
    ),
    sha256Match: booleanValue(verification?.sha256_match),
    sizeMatch: booleanValue(verification?.size_match),
  };
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function booleanValue(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function fileName(path: string) {
  return path.replace(/\\/g, "/").split("/").filter(Boolean).pop() ?? path;
}

function formatBytes(value: number) {
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function formatTokenCount(value: number) {
  if (!Number.isFinite(value) || value <= 0) return "0";
  return Math.round(value).toLocaleString("en-US");
}

function formatStateCounts(value: Record<string, unknown>) {
  return Object.entries(value)
    .filter(([, count]) => typeof count === "number" && count > 0)
    .map(([key, count]) => `${key}:${formatTokenCount(count as number)}`)
    .join(" ");
}

function asPanelRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function formatPhase(value: string) {
  return value.replace(/_/g, " ");
}

function formatDuration(seconds: number) {
  const safe = Math.max(0, Math.round(seconds));
  if (safe < 60) return `${safe}s`;
  const minutes = Math.floor(safe / 60);
  const rest = safe % 60;
  if (minutes < 60) return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const minuteRest = minutes % 60;
  return minuteRest ? `${hours}h ${minuteRest}m` : `${hours}h`;
}

function formatTimestamp(value: string) {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value;
  return new Date(timestamp).toLocaleTimeString();
}

function readPromptComposerText(editor: HTMLElement) {
  const clone = editor.cloneNode(true) as HTMLElement;
  clone.querySelectorAll("[data-operation-chip='true']").forEach((node) => node.remove());
  const text = (clone.textContent ?? "").replace(/\u00a0/g, " ");
  return text.startsWith(" ") ? text.slice(1) : text;
}

function renderPromptComposerContent(
  editor: HTMLElement,
  value: string,
  operationContext: OperationContextChip | null,
) {
  editor.replaceChildren();
  editor.dataset.operationContextId = operationContext?.operationId ?? "";

  if (operationContext) {
    const chip = document.createElement("span");
    chip.dataset.operationChip = "true";
    chip.contentEditable = "false";
    chip.title = "Operation context. Press Backspace on an empty prompt to remove it.";
    chip.className = [
      "mr-1 inline-flex max-w-full select-none items-center rounded-[var(--radius-sm)]",
      "border border-[var(--border)] bg-[var(--surface-muted)] px-1.5 py-0.5",
      "align-baseline font-mono text-[12px] text-[var(--text)]",
    ].join(" ");
    chip.textContent = `Operation ${operationContext.operationId} (${operationContext.status})`;
    editor.appendChild(chip);
    editor.appendChild(document.createTextNode(" "));
  }

  if (value) {
    editor.appendChild(document.createTextNode(value));
  }
}

function placeCaretAtEnd(element: HTMLElement) {
  const range = document.createRange();
  range.selectNodeContents(element);
  range.collapse(false);
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
}

function operationContextVisibleMessage(context: OperationContextChip, userMessage: string) {
  const prefix = `[Operation ${context.operationId}]`;
  return userMessage ? `${prefix} ${userMessage}` : `${prefix} Continue from latest status.`;
}

function operationContextStorageKey(sessionId: string) {
  return `${OPERATION_CONTEXT_STORAGE_PREFIX}${sessionId}`;
}

function readStoredOperationContext(sessionId: string): OperationContextChip | null {
  if (!sessionId) return null;
  try {
    const raw = sessionStorage.getItem(operationContextStorageKey(sessionId));
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<OperationContextChip>;
    if (typeof value.operationId !== "string" || !value.operationId.trim()) return null;
    return {
      operationId: value.operationId,
      kind: typeof value.kind === "string" && value.kind ? value.kind : "operation",
      status: typeof value.status === "string" && value.status ? value.status : "selected",
      title: typeof value.title === "string" && value.title ? value.title : undefined,
    };
  } catch {
    sessionStorage.removeItem(operationContextStorageKey(sessionId));
    return null;
  }
}

function writeStoredOperationContext(
  sessionId: string,
  context: OperationContextChip | null,
) {
  if (!sessionId) return;
  const key = operationContextStorageKey(sessionId);
  if (!context) {
    sessionStorage.removeItem(key);
    return;
  }
  sessionStorage.setItem(key, JSON.stringify(context));
}

function isOperationTerminal(status: string) {
  return ["succeeded", "failed", "cancelled", "timeout"].includes(status);
}

function isAgentPlanTerminal(status: string) {
  return ["succeeded", "failed", "cancelled"].includes(status);
}

function operationStatusToToolStatus(status: string): ToolCallState["status"] {
  return status === "succeeded" ? "succeeded" : "failed";
}

function operationStatusMessage(status: string, fallback?: string) {
  switch (status) {
    case "succeeded":
      return "Operation completed.";
    case "failed":
      return "Operation failed.";
    case "cancelled":
      return "Operation cancelled.";
    case "timeout":
      return "Operation timed out.";
    case "queued":
      return "Operation is queued in Center runtime.";
    case "running":
      return "Operation is running in Center runtime.";
    default:
      return fallback || "Operation is tracked by Center runtime.";
  }
}

// ── Tool Group Bubble ──

function ToolGroupBubble({ block }: { block: ToolGroupBlock }) {
  const succeeded = block.tool_calls.filter((t) => t.status === "succeeded").length;
  const failed = block.tool_calls.filter((t) => t.status === "failed" || t.status === "denied").length;
  const running = block.tool_calls.filter(
    (t) => t.status === "running" || t.status === "pending",
  ).length;
  const waiting = block.tool_calls.filter(
    (t) => t.status === "waiting_approval" || t.status === "waiting_operation",
  ).length;

  return (
    <div className="flex gap-3">
      <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--bg-subtle)] text-[var(--text-muted)]">
        <Wrench size={14} />
      </div>
      <div className="w-full max-w-[75%] rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface-muted)] p-2.5 shadow-sm">
        <div className="mb-2 flex items-center gap-2 px-1">
          <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">
            Tool calls
          </span>
          <span className="text-[12px] text-[var(--text-subtle)]">{block.tool_calls.length}</span>
          <span className="flex-1" />
          {running > 0 && <span className="text-[11px] text-[var(--info)]">{running} running</span>}
          {waiting > 0 && <span className="text-[11px] text-[var(--warning)]">{waiting} waiting</span>}
          {succeeded > 0 && (
            <span className="text-[11px] text-[var(--success)]">{succeeded} succeeded</span>
          )}
          {failed > 0 && <span className="text-[11px] text-[var(--danger)]">{failed} failed</span>}
        </div>
        <div className="space-y-1.5">
          {block.tool_calls.map((tc) => (
            <ToolCallCard key={tc.callId} toolCall={tc} />
          ))}
        </div>
      </div>
    </div>
  );
}

// ── System Event Bubble (subtle) ──

function SystemEventBubble({ block }: { block: SystemEventBlock }) {
  return (
    <div className="flex items-center justify-center gap-2 py-1">
      <Info size={12} className="text-[var(--text-subtle)]" />
      <span className="text-[11px] text-[var(--text-muted)]">{block.label}</span>
    </div>
  );
}

function RunStatusBubble({ block }: { block: RunStatusBlock }) {
  return (
    <div className="flex items-center justify-center gap-2 py-1">
      <Loader2 size={12} className="animate-spin text-[var(--info)]" />
      <span className="text-[11px] text-[var(--text-muted)]">{block.label}</span>
    </div>
  );
}

// ── Markdown Message ──

function MarkdownMessage({ content, isStreaming }: { content: string; isStreaming?: boolean }) {
  if (!content) {
    if (!isStreaming) return null;
    return (
      <ThinkingIndicator />
    );
  }
  return (
    <div className="markdown-body chat-markdown text-[14px] leading-[22px] text-[var(--text)]">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
      {isStreaming && (
        <span className="ml-1 inline-block h-4 w-[2px] translate-y-0.5 animate-pulse rounded-full bg-[var(--accent)]/70 shadow-[0_0_10px_rgba(47,127,143,0.35)]" />
      )}
    </div>
  );
}

function ThinkingIndicator() {
  return (
    <div className="flex items-center gap-2.5 py-0.5 text-[13px] text-[var(--text-muted)]">
      <span className="relative flex h-5 w-5 items-center justify-center">
        <span className="absolute h-5 w-5 animate-ping rounded-full bg-[var(--accent-muted)]" />
        <span className="relative h-2.5 w-2.5 rounded-full bg-[var(--accent)]/75 shadow-[0_0_16px_rgba(47,127,143,0.32)]" />
      </span>
      <span className="font-medium">Thinking</span>
      <span className="flex items-center gap-1">
        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--text-subtle)] [animation-delay:-0.24s]" />
        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--text-subtle)] [animation-delay:-0.12s]" />
        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--text-subtle)]" />
      </span>
    </div>
  );
}

// ── Tool Call Card ──

function ToolCallCard({ toolCall }: { toolCall: ToolCallState }) {
  const [expanded, setExpanded] = useState(false);
  const artifacts = artifactsFromResult(toolCall.result);
  const showInlineArtifacts = toolCall.name !== "artifact.present" && artifacts.length > 0;

  const hasDetails = Boolean(
    toolCall.result ||
      toolCall.errorMessage ||
      toolCall.invocationId ||
      toolCall.jobId ||
      toolCall.targetNodeId ||
      toolCall.operationId ||
      toolCall.ycrStorage ||
      toolCall.ycrProjection ||
      Object.keys(toolCall.input).length > 0 ||
      toolCall.status === "waiting_approval" ||
      toolCall.status === "waiting_operation",
  );
  const statusIcon = {
    pending: <Loader2 size={14} className="animate-spin" />,
    running: <Loader2 size={14} className="animate-spin text-[var(--info)]" />,
    succeeded: <CheckCircle size={14} className="text-[var(--success)]" />,
    failed: <XCircle size={14} className="text-[var(--danger)]" />,
    waiting_approval: <AlertTriangle size={14} className="text-[var(--warning)]" />,
    waiting_operation: <RefreshCw size={14} className="text-[var(--info)]" />,
    denied: <XCircle size={14} className="text-[var(--danger)]" />,
  }[toolCall.status];

  return (
    <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-solid)]/80 p-2.5">
      <button
        type="button"
        onClick={() => hasDetails && setExpanded((value) => !value)}
        className="flex w-full items-center gap-2 text-left"
        disabled={!hasDetails}
      >
        {hasDetails ? (
          expanded ? (
            <ChevronDown size={14} className="text-[var(--text-muted)]" />
          ) : (
            <ChevronRight size={14} className="text-[var(--text-muted)]" />
          )
        ) : (
          <span className="w-3.5" />
        )}
        <Wrench size={14} className="text-[var(--text-muted)]" />
        <span className="text-[13px] font-mono font-medium text-[var(--text)]">
          {toolCall.name}
        </span>
        {toolCall.targetNodeId && (
          <span className="max-w-[140px] truncate rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-muted)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-muted)]">
            @ {toolCall.targetNodeId}
          </span>
        )}
        <span className="flex-1" />
        {statusIcon}
        <StatusBadge status={toolCall.status} />
      </button>
      {toolCall.errorMessage && !expanded && (
        <p className="mt-1.5 line-clamp-2 text-[12px] text-[var(--danger)]">{toolCall.errorMessage}</p>
      )}
      {expanded && (
        <div className="mt-2 space-y-2">
          {toolCall.invocationId && (
            <p className="text-[11px] font-mono text-[var(--text-subtle)]">
              invocation: {toolCall.invocationId}
            </p>
          )}
          {toolCall.targetNodeId && (
            <p className="text-[11px] font-mono text-[var(--text-subtle)]">
              node: {toolCall.targetNodeId}
            </p>
          )}
          {toolCall.jobId && (
            <p className="text-[11px] font-mono text-[var(--text-subtle)]">job: {toolCall.jobId}</p>
          )}
          {toolCall.operationId && (
            <p className="text-[11px] font-mono text-[var(--text-subtle)]">
              operation: {toolCall.operationId}
            </p>
          )}
          {toolCall.ycrStorage && (
            <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-muted)] p-2">
              <p className="mb-1 text-[11px] font-medium text-[var(--text-muted)]">YCR Storage</p>
              <div className="space-y-0.5 font-mono text-[10px] text-[var(--text-subtle)]">
                {toolCall.rawRefId && <p>raw ref: {toolCall.rawRefId}</p>}
                {toolCall.rawSizeBytes !== undefined && <p>raw: {formatBytes(toolCall.rawSizeBytes)}</p>}
                {toolCall.shellSizeBytes !== undefined && <p>provider shell: {formatBytes(toolCall.shellSizeBytes)}</p>}
              </div>
            </div>
          )}
          {toolCall.ycrProjection && (
            <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-muted)] p-2">
              <p className="mb-1 text-[11px] font-medium text-[var(--text-muted)]">Provider Projection</p>
              <div className="space-y-0.5 font-mono text-[10px] text-[var(--text-subtle)]">
                {typeof toolCall.ycrProjection.projection_policy === "string" && (
                  <p>policy: {toolCall.ycrProjection.projection_policy}</p>
                )}
                {toolCall.projectedEstimatedTokens !== undefined && (
                  <p>tokens: {formatTokenCount(toolCall.projectedEstimatedTokens)}</p>
                )}
                {toolCall.projectedSizeBytes !== undefined && (
                  <p>bytes: {formatBytes(toolCall.projectedSizeBytes)}</p>
                )}
              </div>
              <div className="mt-1.5">
                <JsonView data={toolCall.ycrProjection} />
              </div>
            </div>
          )}
          {Object.keys(toolCall.input).length > 0 && (
            <div>
              <p className="mb-1 text-[11px] font-medium text-[var(--text-muted)]">Input</p>
              <JsonView data={toolCall.input} />
            </div>
          )}
          {toolCall.errorMessage && (
            <p className="rounded-[var(--radius-sm)] border border-[var(--danger-muted)] bg-[var(--danger-muted)]/20 p-2 text-[12px] text-[var(--danger)]">
              {toolCall.errorMessage}
            </p>
          )}
          {toolCall.status === "waiting_approval" && toolCall.approvalId && (
            <div className="rounded-[var(--radius-sm)] border border-[var(--warning-muted)] bg-[var(--warning-muted)]/10 p-2.5 space-y-2">
              <div className="flex items-center gap-1.5">
                <AlertTriangle size={14} className="text-[var(--warning)]" />
                <span className="text-[12px] font-medium text-[var(--warning)]">
                  Waiting for approval in the action bar below
                </span>
              </div>
              {toolCall.approvalId && (
                <p className="text-[11px] font-mono text-[var(--text-subtle)]">
                  approval: {toolCall.approvalId}
                </p>
              )}
            </div>
          )}
          {toolCall.status === "waiting_operation" && toolCall.operationId && (
            <div className="space-y-2 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-subtle)] p-2.5">
              <div className="flex items-center gap-1.5">
                <RefreshCw size={14} className="text-[var(--info)]" />
                <span className="text-[12px] font-medium text-[var(--text)]">
                  Waiting for Center Operation
                </span>
              </div>
              <p className="text-[11px] font-mono text-[var(--text-subtle)]">
                operation: {toolCall.operationId}
              </p>
            </div>
          )}
          {toolCall.result && (
            <div>
              <p className="mb-1 text-[11px] font-medium text-[var(--text-muted)]">Result</p>
              {showInlineArtifacts && (
                <div className="mb-2">
                  <ArtifactList artifacts={artifacts} />
                </div>
              )}
              <JsonView data={toolCall.result} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Run Progress Card (live-polled, unchanged from original) ──

function RunProgressCard({ runId, onComplete }: { runId: string; onComplete?: () => void }) {
  const [showArtifacts, setShowArtifacts] = useState(false);
  const prevStatusRef = useRef<string | null>(null);

  const runQuery = useQuery({
    queryKey: ["run-progress", runId],
    queryFn: () => getMaintenanceRun(runId),
    refetchInterval: (queryResult) => {
      const data = queryResult.state.data;
      if (data && ["succeeded", "failed", "rollback_recommended", "cancelled"].includes(data.status)) {
        return false;
      }
      return 2000;
    },
  });

  const artifactsQuery = useQuery({
    queryKey: ["run-artifacts", runId],
    queryFn: () =>
      listMaintenanceRunArtifacts(runId).then(
        (r) => r.artifacts as MaintenanceArtifactDetail[],
      ),
    refetchInterval: () => {
      const data = runQuery.data;
      if (data && ["succeeded", "failed", "rollback_recommended", "cancelled"].includes(data.status)) {
        return false;
      }
      return 2000;
    },
    enabled: !!runQuery.data,
  });

  const run = runQuery.data;
  const artifacts = artifactsQuery.data ?? [];

  useEffect(() => {
    if (run && ["succeeded", "failed", "rollback_recommended", "cancelled"].includes(run.status)) {
      if (prevStatusRef.current && prevStatusRef.current !== run.status && onComplete) {
        // Parent handles completion
      }
      prevStatusRef.current = run.status;
    }
  }, [run, onComplete]);

  if (runQuery.isPending) {
    return (
      <div className="mt-3 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-subtle)] p-3">
        <div className="flex items-center gap-2 text-[13px] text-[var(--text-muted)]">
          <Loader2 size={14} className="animate-spin" />
          Loading run {runId}...
        </div>
      </div>
    );
  }

  if (!run) return null;

  const isTerminal = ["succeeded", "failed", "rollback_recommended", "cancelled"].includes(run.status);
  const isRollback = run.status === "rollback_recommended" || run.rollback_recommended;

  const errorArtifacts = artifacts.filter((a) => a.kind === "error");
  const rollbackArtifacts = artifacts.filter((a) => a.kind === "rollback_hint");

  return (
    <div className="mt-3 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-subtle)] p-3">
      <div className="flex items-center gap-2">
        {!isTerminal ? (
          <Loader2 size={14} className="animate-spin text-[var(--info)]" />
        ) : isRollback ? (
          <AlertTriangle size={14} className="text-[var(--warning)]" />
        ) : run.status === "failed" ? (
          <XCircle size={14} className="text-[var(--danger)]" />
        ) : (
          <CheckCircle size={14} className="text-[var(--success)]" />
        )}
        <span className="text-[13px] font-semibold text-[var(--text)]">Run</span>
        <span className="text-[11px] font-mono text-[var(--text-subtle)]">{runId}</span>
        <span className="flex-1" />
        {run.summary && (
          <span className="text-[12px] text-[var(--text-muted)]">
            {run.summary.succeeded}/{run.summary.total_steps} done
          </span>
        )}
        <StatusBadge status={run.status} />
      </div>

      {run.steps && run.steps.length > 0 && (
        <div className="mt-2 space-y-1">
          {run.steps.map((step) => (
            <div key={step.step_id} className="flex items-center gap-2 text-[12px]">
              <span className="w-4 text-right text-[var(--text-subtle)]">{step.seq}.</span>
              <StatusBadge status={step.kind} />
              <span className="font-mono text-[var(--text)]">{step.function_name}</span>
              <span className="flex-1" />
              <StatusBadge status={step.status} />
              {step.skip_reason && (
                <span className="text-[var(--text-subtle)] text-[11px]">({step.skip_reason})</span>
              )}
            </div>
          ))}
        </div>
      )}

      {isRollback && (
        <div className="mt-2 rounded-[var(--radius-sm)] border border-[var(--warning-muted)] bg-[var(--warning-muted)]/20 p-2">
          <p className="text-[12px] font-medium text-[var(--warning)]">
            Rollback recommended. Review rollback hints before proceeding.
          </p>
        </div>
      )}

      {errorArtifacts.length > 0 && (
        <div className="mt-2 space-y-1.5">
          {errorArtifacts.map((a) => (
            <div
              key={a.artifact_id}
              className="rounded-[var(--radius-sm)] border border-[var(--danger-muted)] bg-[var(--danger-muted)]/20 p-2"
            >
              <p className="text-[12px] font-medium text-[var(--danger)]">Error: {a.name}</p>
              {a.data && (
                <div className="mt-1">
                  <JsonView data={a.data} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {rollbackArtifacts.length > 0 && (
        <div className="mt-2 space-y-1.5">
          {rollbackArtifacts.map((a) => (
            <div
              key={a.artifact_id}
              className="rounded-[var(--radius-sm)] border border-[var(--warning-muted)] bg-[var(--warning-muted)]/20 p-2"
            >
              <p className="text-[12px] font-medium text-[var(--warning)]">
                Rollback Hint: {a.name}
              </p>
              {a.data && (
                <div className="mt-1">
                  <JsonView data={a.data} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {artifacts.length > 0 && (
        <div className="mt-2">
          <button
            onClick={() => setShowArtifacts(!showArtifacts)}
            className="flex items-center gap-1 text-[12px] text-[var(--text-muted)] hover:text-[var(--text)]"
          >
            {showArtifacts ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            Artifacts ({artifacts.length})
          </button>
          {showArtifacts && (
            <div className="mt-1.5 space-y-1.5">
              {artifacts.map((a) => (
                <div
                  key={a.artifact_id}
                  className="rounded-[var(--radius-sm)] border border-[var(--border)] p-2"
                >
                  <div className="flex items-center gap-2">
                    <StatusBadge status={a.kind} />
                    <span className="text-[12px] font-mono text-[var(--text)]">{a.name}</span>
                  </div>
                  {a.summary && (
                    <div className="mt-1">
                      <JsonView data={a.summary} />
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Relative time formatter ──

function formatRelativeTime(iso: string): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

