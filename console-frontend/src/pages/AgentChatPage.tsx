import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { createSession } from "@/api/agent";
import { getSession, getMaintenanceRun, listMaintenanceRunArtifacts, approvePlan, runPlan, listSessions, renameSession, deleteSession, listNodes, approveApproval, denyApproval, approveAndRunApproval } from "@/api/admin";
import { useAgentChat, type ToolCallState, type ChatBlock, type UserBlock, type AssistantTextBlock, type ToolGroupBlock, type SystemEventBlock } from "@/hooks/useAgentChat";
import type { MaintenanceArtifactDetail } from "@/api/types";
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
  const queryClient = useQueryClient();
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
      if (!sessionId) {
        const s = await createSession({});
        sessionStorage.setItem(SESSION_STORAGE_KEY, s.session_id);
        setSessionId(s.session_id);
        return getSession(s.session_id);
      }
      try {
        const existing = await getSession(sessionId);
        return existing;
      } catch {
        const s = await createSession({});
        sessionStorage.setItem(SESSION_STORAGE_KEY, s.session_id);
        setSessionId(s.session_id);
        return getSession(s.session_id);
      }
    },
    staleTime: 0, // Never use stale cache for session detail
  });

  const {
    blocks,
    isStreaming,
    sendInvoke,
    sendPlan,
    cancel,
    clearBlocks,
    loadPersistedMessages,
  } = useAgentChat({ sessionId, onConversationSettled: refreshSessionHistory });

  // Load persisted messages into blocks when session data arrives
  useEffect(() => {
    if (sessionQuery.data?.messages && sessionId) {
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
    // Force fresh fetch for the new session
    queryClient.invalidateQueries({ queryKey: ["agent-session", newId] });
  };

  const createNewSession = async () => {
    if (isStreaming) {
      if (!confirm("A stream is in progress. Creating a new session will cancel it. Continue?")) {
        return;
      }
    }
    cancel();
    clearBlocks();
    const s = await createSession({});
    sessionStorage.setItem(SESSION_STORAGE_KEY, s.session_id);
    setSessionId(s.session_id);
    queryClient.invalidateQueries({ queryKey: ["agent-sessions"] });
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
    await deleteSession(id);
    queryClient.invalidateQueries({ queryKey: ["agent-sessions"] });
    if (id === sessionId) {
      const s = await createSession({});
      sessionStorage.setItem(SESSION_STORAGE_KEY, s.session_id);
      setSessionId(s.session_id);
    }
  };

  const handleSend = () => {
    if (!prompt.trim() || isStreaming) return;
    if (autoPlan) {
      sendPlan(prompt.trim(), targetNodeId, providerName);
    } else {
      sendInvoke(prompt.trim(), targetNodeId, providerName, executionMode);
    }
    setPrompt("");
  };

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
            className="rounded-[var(--radius-sm)] p-1 text-[var(--text-muted)] hover:bg-[var(--bg-subtle)] hover:text-[var(--text)]"
            title="New session"
          >
            <Plus size={14} />
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
              title="YeQu Agent"
              description="Input a prompt to start. The Agent will reason about your request and execute tools accordingly."
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
                placeholder={autoPlan ? "Describe maintenance task..." : "Ask the agent..."}
                rows={2}
                className="flex-1 resize-none rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-solid)] px-3 py-2 text-[14px] text-[var(--text)] outline-none placeholder:text-[var(--text-subtle)]"
                disabled={isStreaming}
              />
              <Button
                variant="primary"
                size="md"
                onClick={handleSend}
                disabled={!prompt.trim() || isStreaming}
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
  const failed = block.tool_calls.filter((t) => t.status === "failed").length;
  const running = block.tool_calls.filter(
    (t) => t.status === "running" || t.status === "pending",
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
  const [approvalAction, setApprovalAction] = useState<"idle" | "loading" | "done">("idle");
  const queryClient = useQueryClient();

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
  }[toolCall.status];

  const handleApproveAndExecute = async () => {
    if (!toolCall.approvalId) return;
    if (!confirm(`Approve and execute "${toolCall.name}"?`)) return;
    setApprovalAction("loading");
    try {
      await approveAndRunApproval(toolCall.approvalId);
      setApprovalAction("done");
      queryClient.invalidateQueries({ queryKey: ["agent-sessions"] });
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
    } catch {
      setApprovalAction("idle");
    }
  };

  const handleDeny = async () => {
    if (!toolCall.approvalId) return;
    if (!confirm(`Deny "${toolCall.name}"?`)) return;
    setApprovalAction("loading");
    try {
      await denyApproval(toolCall.approvalId);
      setApprovalAction("done");
      queryClient.invalidateQueries({ queryKey: ["agent-sessions"] });
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
    } catch {
      setApprovalAction("idle");
    }
  };

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
          {/* Approval actions */}
          {toolCall.status === "waiting_approval" && toolCall.approvalId && approvalAction !== "done" && (
            <div className="rounded-[var(--radius-sm)] border border-[var(--warning-muted)] bg-[var(--warning-muted)]/10 p-2.5 space-y-2">
              <div className="flex items-center gap-1.5">
                <AlertTriangle size={14} className="text-[var(--warning)]" />
                <span className="text-[12px] font-medium text-[var(--warning)]">
                  This operation requires approval
                </span>
              </div>
              {toolCall.approvalId && (
                <p className="text-[11px] font-mono text-[var(--text-subtle)]">
                  approval: {toolCall.approvalId}
                </p>
              )}
              <div className="flex gap-2">
                <Button
                  variant="primary"
                  size="sm"
                  onClick={handleApproveAndExecute}
                  disabled={approvalAction === "loading"}
                >
                  {approvalAction === "loading" ? (
                    <Loader2 size={12} className="animate-spin" />
                  ) : (
                    <CheckCircle size={12} />
                  )}
                  <span className="ml-1">Approve and Execute</span>
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleDeny}
                  disabled={approvalAction === "loading"}
                >
                  <XCircle size={12} />
                  <span className="ml-1">Deny</span>
                </Button>
              </div>
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
