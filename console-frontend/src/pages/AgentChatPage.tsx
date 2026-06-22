import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { createSession } from "@/api/agent";
import {
  getSession,
  getMaintenanceRun,
  listMaintenanceRunArtifacts,
  approvePlan,
  runPlan,
  listSessions,
  renameSession,
  deleteSession,
  listNodes,
  getApproval,
  getJob,
  denyApproval,
  approveAndRunApproval,
} from "@/api/admin";
import { useAgentChat, type ToolCallState, type ChatBlock, type UserBlock, type AssistantTextBlock, type ToolGroupBlock, type SystemEventBlock } from "@/hooks/useAgentChat";
import type { AgentSessionSummary, JobSummary, MaintenanceArtifactDetail } from "@/api/types";
import { StatusBadge } from "@/components/StatusBadge";
import { JsonView } from "@/components/JsonView";
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
} from "lucide-react";

const SESSION_STORAGE_KEY = "yequ_agent_session_id";

export function AgentChatPage() {
  const [sessionId, setSessionId] = useState<string>(() => {
    return sessionStorage.getItem(SESSION_STORAGE_KEY) ?? "";
  });
  const [prompt, setPrompt] = useState("");
  const [targetNodeId, setTargetNodeId] = useState("winClient");
  const [executionMode, setExecutionMode] = useState("auto");
  const [providerName, setProviderName] = useState("deepseek");
  const [autoPlan, setAutoPlan] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [editingSessId, setEditingSessId] = useState<string | null>(null);
  const [editLabel, setEditLabel] = useState("");
  const [sessionSearch, setSessionSearch] = useState("");
  const chatEndRef = useRef<HTMLDivElement>(null);
  const creatingSessionRef = useRef(false);
  const reconciledApprovalIdsRef = useRef(new Set<string>());
  const [isCreatingSession, setIsCreatingSession] = useState(false);
  const [approvalBusyId, setApprovalBusyId] = useState<string | null>(null);
  const [approvalActionError, setApprovalActionError] = useState<string | null>(null);
  const [autoContinuing, setAutoContinuing] = useState(false);
  const [dismissedApprovalIds, setDismissedApprovalIds] = useState<Set<string>>(() => new Set());
  const queryClient = useQueryClient();
  const approvalRunPromisesRef = useRef(new Map<string, Promise<ApprovalRunOutcome>>());
  const refreshSessionHistory = useCallback(() => {
    if (!sessionId) return;
    queryClient.invalidateQueries({ queryKey: ["agent-session", sessionId] });
    queryClient.invalidateQueries({ queryKey: ["agent-sessions"] });
  }, [queryClient, sessionId]);

  const sessionsQuery = useQuery({
    queryKey: ["agent-sessions"],
    queryFn: listSessions,
    refetchInterval: 30_000,
  });

  const nodesQuery = useQuery({
    queryKey: ["nodes"],
    queryFn: listNodes,
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
    sendInvoke,
    sendPlan,
    cancel,
    clearBlocks,
    loadPersistedMessages,
    patchToolCall,
  } = useAgentChat({ sessionId, onConversationSettled: refreshSessionHistory });

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

  // Load persisted messages into blocks when session data arrives
  useEffect(() => {
    if (sessionQuery.data?.messages && sessionId) {
      reconciledApprovalIdsRef.current.clear();
      setDismissedApprovalIds(new Set());
      loadPersistedMessages(sessionQuery.data.messages);
    }
  }, [loadPersistedMessages, sessionId, sessionQuery.data?.messages]);

  useEffect(() => {
    const nodes = nodesQuery.data ?? [];
    if (nodes.length > 0 && !nodes.some((node) => node.node_id === targetNodeId)) {
      setTargetNodeId(nodes[0].node_id);
    }
  }, [nodesQuery.data, targetNodeId]);

  // Auto-scroll to bottom
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [blocks]);

  const switchSession = (newId: string) => {
    if (isStreaming) {
      if (!confirm("A stream is in progress. Switching sessions will cancel it. Continue?")) {
        return;
      }
    }
    cancel();
    clearBlocks();
    sessionStorage.setItem(SESSION_STORAGE_KEY, newId);
    setSessionId(newId);
  };

  const createNewSession = async () => {
    if (creatingSessionRef.current) return;
    if (isStreaming) {
      if (!confirm("A stream is in progress. Creating a new session will cancel it. Continue?")) {
        return;
      }
    }
    creatingSessionRef.current = true;
    setIsCreatingSession(true);
    cancel();
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
    if (!sessionId || !prompt.trim() || isStreaming) return;
    if (autoPlan) {
      sendPlan(prompt.trim(), targetNodeId, providerName);
    } else {
      sendInvoke(prompt.trim(), targetNodeId, providerName, executionMode);
    }
    setPrompt("");
  };

  const waitForJobTerminal = useCallback(
    async (jobId: string, approvalId: string, toolName: string): Promise<ApprovalRunOutcome> => {
      const terminalStatuses = new Set(["succeeded", "failed", "timeout", "cancelled"]);
      for (let attempt = 0; attempt < 70; attempt += 1) {
        const job = await getJob(jobId);
        patchToolCall(jobToToolPatch(job, approvalId));
        if (terminalStatuses.has(job.status)) return jobToApprovalOutcome(job, approvalId, toolName);
        await sleep(1200);
      }
      patchToolCall({
        approvalId,
        status: "failed",
        errorCode: "job_poll_timeout",
        errorMessage: "Timed out while waiting for the approved job to finish.",
      });
      return {
        approvalId,
        toolName,
        jobId,
        status: "failed",
        errorCode: "job_poll_timeout",
        errorMessage: "Timed out while waiting for the approved job to finish.",
      };
    },
    [patchToolCall],
  );

  const scheduleAutoContinue = useCallback(async () => {
    if (!sessionId || autoContinuing) return;
    setAutoContinuing(true);
    try {
      const pendingRuns = Array.from(approvalRunPromisesRef.current.values());
      approvalRunPromisesRef.current.clear();
      const outcomes: ApprovalRunOutcome[] = [];
      if (pendingRuns.length > 0) {
        const settled = await Promise.allSettled(pendingRuns);
        for (const item of settled) {
          if (item.status === "fulfilled") {
            outcomes.push(item.value);
          } else {
            outcomes.push({
              approvalId: "unknown",
              toolName: "unknown",
              status: "failed",
              errorCode: "approval_result_unavailable",
              errorMessage: item.reason instanceof Error ? item.reason.message : String(item.reason),
            });
          }
        }
      }
      sendInvoke(buildApprovalContinuationPrompt(outcomes), targetNodeId, providerName, executionMode, {
        visible: false,
        suppressUserMessage: true,
      });
    } finally {
      setAutoContinuing(false);
    }
  }, [autoContinuing, executionMode, providerName, sendInvoke, sessionId, targetNodeId]);

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

  const syncProcessedApproval = useCallback(
    async (approvalId: string, toolName: string): Promise<ApprovalRunOutcome | null> => {
      const approval = await getApproval(approvalId);
      if (approval.status === "pending") return null;

      if (approval.status === "denied") {
        dismissApproval(approvalId);
        patchToolCall({
          approvalId,
          status: "denied",
          errorCode: null,
          errorMessage: "Denied by user.",
        });
        return {
          approvalId,
          toolName,
          status: "denied",
          errorMessage: "Denied by user.",
        };
      }

      if (approval.status === "expired") {
        dismissApproval(approvalId);
        patchToolCall({
          approvalId,
          status: "failed",
          errorCode: "approval_expired",
          errorMessage: "Approval expired.",
        });
        return {
          approvalId,
          toolName,
          status: "failed",
          errorCode: "approval_expired",
          errorMessage: "Approval expired.",
        };
      }

      if (approval.status === "consumed") {
        dismissApproval(approvalId);
        const jobId = approval.consumed_invocation?.jobs?.[0]?.job_id ?? approval.invocation?.jobs?.[0]?.job_id;
        if (!jobId) {
          patchToolCall({
            approvalId,
            status: "running",
            errorCode: null,
            errorMessage: null,
          });
          return {
            approvalId,
            toolName,
            status: "consumed",
            errorMessage: "Approval was already consumed; waiting for linked job data.",
          };
        }
        const job = await getJob(jobId);
        patchToolCall(jobToToolPatch(job, approvalId));
        if (["succeeded", "failed", "timeout", "cancelled"].includes(job.status)) {
          return jobToApprovalOutcome(job, approvalId, toolName);
        }
        const runPromise = waitForJobTerminal(job.job_id, approvalId, toolName);
        approvalRunPromisesRef.current.set(approvalId, runPromise);
        return runPromise;
      }

      if (approval.status === "approved") {
        patchToolCall({
          approvalId,
          status: "waiting_approval",
          errorCode: "approval_already_approved",
          errorMessage: "Approval is already approved but not consumed yet.",
        });
        return null;
      }

      return null;
    },
    [dismissApproval, patchToolCall, waitForJobTerminal],
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
      void syncProcessedApproval(approvalId, tool.name).catch((error) => {
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

      const isLastApproval = pendingApprovals.length <= 1;
      setApprovalBusyId(approvalId);
      setApprovalActionError(null);
      try {
        const processed = await syncProcessedApproval(approvalId, toolCall.name);
        if (processed) {
          approvalRunPromisesRef.current.set(approvalId, Promise.resolve(processed));
          if (isLastApproval) {
            void scheduleAutoContinue();
          }
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
            status: "running",
            invocationId: result.invocation_id,
            jobId: result.job_id,
            errorCode: null,
            errorMessage: null,
          });
          const runPromise = waitForJobTerminal(result.job_id, approvalId, toolCall.name);
          approvalRunPromisesRef.current.set(approvalId, runPromise);
        }
        queryClient.invalidateQueries({ queryKey: ["agent-sessions"] });
        queryClient.invalidateQueries({ queryKey: ["approvals"] });
        queryClient.invalidateQueries({ queryKey: ["jobs"] });
        if (isLastApproval) {
          void scheduleAutoContinue();
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : "Approval action failed.";
        if (message.includes("consumed") || message.includes("expected pending")) {
          try {
            const processed = await syncProcessedApproval(approvalId, toolCall.name);
            if (processed) {
              approvalRunPromisesRef.current.set(approvalId, Promise.resolve(processed));
              if (isLastApproval) {
                void scheduleAutoContinue();
              }
              return;
            }
          } catch {
            // Fall through to the visible error below.
          }
        }
        setApprovalActionError(message);
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
      pendingApprovals.length,
      queryClient,
      scheduleAutoContinue,
      syncProcessedApproval,
      waitForJobTerminal,
    ],
  );

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

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
        <div className="flex-1 overflow-y-auto p-6">
          {blocks.length === 0 ? (
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
            <div className="mx-auto max-w-3xl space-y-3">
              {blocks.map((block) => (
                <ChatTimelineBlock
                  key={block.id}
                  block={block}
                  onApproveAndRun={handleApproveAndRun}
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
            {autoContinuing && (
              <div className="flex items-center gap-2 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-muted)] px-3 py-2 text-[12px] text-[var(--text-muted)]">
                <Loader2 size={14} className="animate-spin text-[var(--accent)]" />
                Waiting for approved jobs to finish, then continuing automatically...
              </div>
            )}
            <div className="flex items-center gap-2">
              <select
                value={targetNodeId}
                onChange={(e) => setTargetNodeId(e.target.value)}
                className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-solid)] px-2.5 py-1.5 text-[12px] text-[var(--text)] outline-none"
              >
                {(nodesQuery.data ?? []).map((node) => (
                  <option key={node.node_id} value={node.node_id}>
                    {node.node_id}
                  </option>
                ))}
              </select>
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

              <span className="flex-1" />

              {isStreaming && (
                <Button variant="ghost" size="sm" onClick={cancel}>
                  Cancel
                </Button>
              )}
            </div>

            <div className="flex items-end gap-2">
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={!sessionId ? "Create a session to start..." : autoPlan ? "Describe maintenance task..." : "Ask the agent..."}
                rows={2}
                className="flex-1 resize-none rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-solid)] px-3 py-2 text-[14px] text-[var(--text)] outline-none placeholder:text-[var(--text-subtle)]"
                disabled={!sessionId || isStreaming}
              />
              <Button
                variant="primary"
                size="md"
                onClick={handleSend}
                disabled={!sessionId || !prompt.trim() || isStreaming}
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

// ── Timeline Block Renderer ──

function ChatTimelineBlock({
  block,
  onApproveAndRun,
}: {
  block: ChatBlock;
  onApproveAndRun?: (planId: string, onRunStarted: (runId: string) => void) => void;
}) {
  switch (block.type) {
    case "user":
      return <UserBubble block={block} />;
    case "assistant_text":
      return <AssistantTextBubble block={block} />;
    case "tool_group":
      return <ToolGroupBubble block={block} />;
    case "system_event":
      return <SystemEventBubble block={block} />;
    default:
      return null;
  }
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
        <MarkdownMessage content={block.content || "Thinking..."} isStreaming={block.streaming} />
      </div>
    </div>
  );
}

// ── Tool Group Bubble ──

function ToolGroupBubble({ block }: { block: ToolGroupBlock }) {
  const succeeded = block.tool_calls.filter((t) => t.status === "succeeded").length;
  const failed = block.tool_calls.filter((t) => t.status === "failed" || t.status === "denied").length;
  const running = block.tool_calls.filter(
    (t) => t.status === "running" || t.status === "pending",
  ).length;
  const waiting = block.tool_calls.filter((t) => t.status === "waiting_approval").length;

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

// ── Markdown Message ──

function MarkdownMessage({ content, isStreaming }: { content: string; isStreaming?: boolean }) {
  if (!content) {
    return (
      <span className="flex items-center gap-1 text-[var(--text-muted)]">
        <Loader2 size={14} className="animate-spin" />
        Thinking...
      </span>
    );
  }
  return (
    <div className="markdown-body text-[14px] leading-[22px] text-[var(--text)]">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
      {isStreaming && (
        <span className="ml-0.5 inline-block h-4 w-1 translate-y-0.5 animate-pulse bg-[var(--accent)]" />
      )}
    </div>
  );
}

// ── Tool Call Card ──

function ToolCallCard({ toolCall }: { toolCall: ToolCallState }) {
  const [expanded, setExpanded] = useState(false);

  const hasDetails = Boolean(
    toolCall.result ||
      toolCall.errorMessage ||
      toolCall.invocationId ||
      toolCall.jobId ||
      Object.keys(toolCall.input).length > 0 ||
      toolCall.status === "waiting_approval",
  );
  const statusIcon = {
    pending: <Loader2 size={14} className="animate-spin" />,
    running: <Loader2 size={14} className="animate-spin text-[var(--info)]" />,
    succeeded: <CheckCircle size={14} className="text-[var(--success)]" />,
    failed: <XCircle size={14} className="text-[var(--danger)]" />,
    waiting_approval: <AlertTriangle size={14} className="text-[var(--warning)]" />,
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
          {toolCall.jobId && (
            <p className="text-[11px] font-mono text-[var(--text-subtle)]">job: {toolCall.jobId}</p>
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
          {toolCall.result && (
            <div>
              <p className="mb-1 text-[11px] font-medium text-[var(--text-muted)]">Result</p>
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

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

interface ApprovalRunOutcome {
  approvalId: string;
  toolName: string;
  status: string;
  invocationId?: string;
  jobId?: string;
  result?: Record<string, unknown>;
  errorCode?: string | null;
  errorMessage?: string | null;
}

function jobToToolPatch(
  job: JobSummary,
  approvalId: string,
): {
  approvalId: string;
  status: ToolCallState["status"];
  invocationId: string;
  jobId: string;
  result?: Record<string, unknown>;
  errorCode?: string | null;
  errorMessage?: string | null;
} {
  if (job.status === "succeeded") {
    return {
      approvalId,
      status: "succeeded",
      invocationId: job.invocation_id,
      jobId: job.job_id,
      result: job.output ?? undefined,
      errorCode: null,
      errorMessage: null,
    };
  }
  if (job.status === "failed" || job.status === "timeout" || job.status === "cancelled") {
    return {
      approvalId,
      status: "failed",
      invocationId: job.invocation_id,
      jobId: job.job_id,
      errorCode: job.error_code ?? job.status,
      errorMessage: job.error_message ?? `Job ${job.status}`,
    };
  }
  return {
    approvalId,
    status: "running",
    invocationId: job.invocation_id,
    jobId: job.job_id,
    errorCode: null,
    errorMessage: null,
  };
}

function jobToApprovalOutcome(job: JobSummary, approvalId: string, toolName: string): ApprovalRunOutcome {
  return {
    approvalId,
    toolName,
    status: job.status,
    invocationId: job.invocation_id,
    jobId: job.job_id,
    result: job.output ?? undefined,
    errorCode: job.error_code,
    errorMessage: job.error_message,
  };
}

function buildApprovalContinuationPrompt(outcomes: ApprovalRunOutcome[]): string {
  const payload = JSON.stringify(outcomes, null, 2);
  return [
    "以下是刚才用户在 Agent Console 审批条中处理过的审批结果，已经由系统执行或拒绝，不需要再次请求同一个写操作。",
    "",
    "请遵守：",
    "1. 不要重复调用这些 approval_id 对应的写操作，除非用户明确要求重试。",
    "2. 如果 status 是 succeeded，直接基于 result 给出结论；必要时只能调用只读工具复核状态。",
    "3. 如果 status 是 failed/cancelled/timeout/denied，解释失败原因并给出下一步。",
    "",
    "approval_results:",
    payload,
  ].join("\n");
}
