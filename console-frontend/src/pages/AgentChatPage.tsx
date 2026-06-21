import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createSession } from "@/api/agent";
import { getSession, getMaintenanceRun, listMaintenanceRunArtifacts, approvePlan, runPlan, listSessions, renameSession, deleteSession } from "@/api/admin";
import { useAgentChat, type ToolCallState, type PlanStepState } from "@/hooks/useAgentChat";
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
  const chatEndRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  // Session list query
  const sessionsQuery = useQuery({
    queryKey: ["agent-sessions"],
    queryFn: listSessions,
    refetchInterval: 30_000,
  });

  // Ensure session exists
  useQuery({
    queryKey: ["agent-session", sessionId],
    queryFn: async () => {
      if (!sessionId) {
        const s = await createSession({});
        sessionStorage.setItem(SESSION_STORAGE_KEY, s.session_id);
        setSessionId(s.session_id);
        return s;
      }
      try {
        const existing = await getSession(sessionId);
        return existing;
      } catch {
        const s = await createSession({});
        sessionStorage.setItem(SESSION_STORAGE_KEY, s.session_id);
        setSessionId(s.session_id);
        return s;
      }
    },
    staleTime: 60_000,
  });

  const { messages, isStreaming, sendInvoke, sendPlan, cancel } = useAgentChat({ sessionId });

  // Auto-scroll to bottom
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const switchSession = (newId: string) => {
    sessionStorage.setItem(SESSION_STORAGE_KEY, newId);
    setSessionId(newId);
    // Clear messages for the new session
    cancel();
  };

  const createNewSession = async () => {
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

  return (
    <div className="flex h-[calc(100vh-var(--topbar-height))]">
      {/* Session Sidebar */}
      <aside className="flex w-[200px] flex-shrink-0 flex-col border-r border-[var(--border)] bg-[var(--surface-muted)]">
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

        <div className="flex-1 overflow-y-auto">
          {(sessionsQuery.data ?? []).map((s) => (
            <div
              key={s.session_id}
              onClick={() => switchSession(s.session_id)}
              className={`group flex items-center cursor-pointer border-b border-[var(--border)] px-3 py-2 hover:bg-[var(--bg-subtle)] ${
                s.session_id === sessionId ? "bg-[var(--accent-muted)]" : ""
              }`}
            >
              {editingSessId === s.session_id ? (
                <form
                  onSubmit={(e) => { e.preventDefault(); handleRenameSubmit(s.session_id); }}
                  className="flex flex-1 items-center gap-1"
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
                <>
                  <span className="flex-1 truncate text-[12px] text-[var(--text)]">
                    {s.label}
                  </span>
                  <div className="flex opacity-0 group-hover:opacity-100">
                    <button
                      onClick={(e) => { e.stopPropagation(); handleRenameStart(s.session_id, s.label); }}
                      className="p-0.5 text-[var(--text-muted)] hover:text-[var(--text)]"
                      title="Rename"
                    >
                      <Edit3 size={10} />
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); handleDelete(s.session_id); }}
                      className="p-0.5 text-[var(--text-muted)] hover:text-[var(--danger)]"
                      title="Delete"
                    >
                      <Trash2 size={10} />
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      </aside>

      {/* Conversation area */}
      <div className="flex flex-1 flex-col">
        <div className="flex-1 overflow-y-auto p-6">
          {messages.length === 0 ? (
            <EmptyState
              icon={<Bot size={36} />}
              title="YeQu Agent"
              description="Input a prompt to start. The Agent will reason about your request and execute tools accordingly."
            />
          ) : (
            <div className="mx-auto max-w-3xl space-y-4">
              {messages.map((msg) => (
                <ChatBubble
                  key={msg.id}
                  message={msg}
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
                <option value="winClient">winClient</option>
                <option value="debian-home">debian-home</option>
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

      {/* Inspector (tool calls / plan steps) */}
      <InspectorPanel messages={messages} sessionId={sessionId} />
    </div>
  );
}

// ── Chat Bubble ──

function ChatBubble({
  message,
  onApproveAndRun,
}: {
  message: ReturnType<typeof useAgentChat>["messages"][number];
  onApproveAndRun?: (planId: string, onRunStarted: (runId: string) => void) => void;
}) {
  const isUser = message.role === "user";
  const [runId, setRunId] = useState<string | null>(message.runId ?? null);

  return (
    <div className={`flex gap-3 ${isUser ? "justify-end" : ""}`}>
      <div
        className={`flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-[var(--radius-sm)] ${
          isUser
            ? "order-last bg-[var(--accent-muted)] text-[var(--accent)]"
            : "bg-[var(--bg-subtle)] text-[var(--text-muted)]"
        }`}
      >
        {isUser ? <User size={14} /> : <Bot size={14} />}
      </div>

      <div
        className={`max-w-[75%] rounded-[var(--radius-md)] px-4 py-2.5 ${
          isUser
            ? "bg-[var(--accent-muted)]"
            : "border border-[var(--border)] bg-[var(--surface-solid)]"
        }`}
      >
        {message.content && (
          <p className="whitespace-pre-wrap text-[14px] leading-[22px] text-[var(--text)]">
            {message.content}
            {message.isStreaming && (
              <span className="ml-0.5 inline-block h-4 w-1 animate-pulse bg-[var(--accent)]" />
            )}
          </p>
        )}

        {/* Tool calls */}
        {message.toolCalls.map((tc) => (
          <ToolCallCard key={tc.callId} toolCall={tc} />
        ))}

        {/* Plan steps */}
        {message.planSteps && message.planSteps.length > 0 && (
          <PlanStepsPreview
            steps={message.planSteps}
            planId={message.planId}
            approvalRequired={message.approvalRequired}
            onApprove={
              onApproveAndRun && message.planId
                ? () => {
                    onApproveAndRun(message.planId!, (rid) => {
                      setRunId(rid);
                    });
                  }
                : undefined
            }
          />
        )}

        {/* Inline run progress (per-bubble) */}
        {runId && <RunProgressCard runId={runId} />}
      </div>
    </div>
  );
}

// ── Tool Call Card ──

function ToolCallCard({ toolCall }: { toolCall: ToolCallState }) {
  const statusIcon = {
    pending: <Loader2 size={14} className="animate-spin" />,
    running: <Loader2 size={14} className="animate-spin text-[var(--info)]" />,
    succeeded: <CheckCircle size={14} className="text-[var(--success)]" />,
    failed: <XCircle size={14} className="text-[var(--danger)]" />,
  }[toolCall.status];

  return (
    <div className="mt-2 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-subtle)] p-2.5">
      <div className="flex items-center gap-2">
        <Wrench size={14} className="text-[var(--text-muted)]" />
        <span className="text-[13px] font-mono font-medium text-[var(--text)]">
          {toolCall.name}
        </span>
        <span className="flex-1" />
        {statusIcon}
        <StatusBadge status={toolCall.status} />
      </div>
      {toolCall.errorMessage && (
        <p className="mt-1.5 text-[12px] text-[var(--danger)]">{toolCall.errorMessage}</p>
      )}
      {toolCall.result && (
        <div className="mt-1.5">
          <JsonView data={toolCall.result} />
        </div>
      )}
    </div>
  );
}

// ── Plan Steps Preview ──

function PlanStepsPreview({
  steps,
  planId,
  approvalRequired,
  onApprove,
}: {
  steps: PlanStepState[];
  planId?: string;
  approvalRequired?: boolean;
  onApprove?: () => void;
}) {
  return (
    <div className="mt-3 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-subtle)] p-3">
      <div className="flex items-center gap-2">
        <FileText size={14} className="text-[var(--text-muted)]" />
        <span className="text-[13px] font-semibold text-[var(--text)]">Maintenance Plan</span>
        {planId && (
          <span className="text-[11px] font-mono text-[var(--text-subtle)]">{planId}</span>
        )}
      </div>

      <div className="mt-2 space-y-1.5">
        {steps.map((step) => (
          <div key={step.seq} className="flex items-center gap-2 text-[13px]">
            <span className="text-[var(--text-subtle)] w-4 text-right">{step.seq}.</span>
            <StatusBadge status={step.kind} />
            <span className="font-mono text-[var(--text)]">{step.functionName}</span>
            {step.requiresApproval && (
              <AlertTriangle size={12} className="text-[var(--warning)]" />
            )}
          </div>
        ))}
      </div>

      {approvalRequired && planId && onApprove && (
        <div className="mt-3 flex gap-2">
          <Button variant="primary" size="sm" onClick={onApprove}>
            <CheckCircle size={14} />
            <span className="ml-1">Approve & Run</span>
          </Button>
        </div>
      )}
    </div>
  );
}

// ── Run Progress Card (live-polled) ──

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

  // Notify parent when run completes
  useEffect(() => {
    if (run && ["succeeded", "failed", "rollback_recommended", "cancelled"].includes(run.status)) {
      if (prevStatusRef.current && prevStatusRef.current !== run.status && onComplete) {
        // Delay completion so user can see final state
        // (onComplete is handled by the parent; we just track it)
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
      {/* Header */}
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

      {/* Step-by-step status */}
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

      {/* Rollback recommended warning */}
      {isRollback && (
        <div className="mt-2 rounded-[var(--radius-sm)] border border-[var(--warning-muted)] bg-[var(--warning-muted)]/20 p-2">
          <p className="text-[12px] font-medium text-[var(--warning)]">
            Rollback recommended. Review rollback hints before proceeding.
          </p>
        </div>
      )}

      {/* Error artifacts */}
      {errorArtifacts.length > 0 && (
        <div className="mt-2 space-y-1.5">
          {errorArtifacts.map((a) => (
            <div
              key={a.artifact_id}
              className="rounded-[var(--radius-sm)] border border-[var(--danger-muted)] bg-[var(--danger-muted)]/20 p-2"
            >
              <p className="text-[12px] font-medium text-[var(--danger)]">
                Error: {a.name}
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

      {/* Rollback hint artifacts */}
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

      {/* Artifacts summary + expand */}
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

// ── Inspector Panel ──

function InspectorPanel({
  messages,
  sessionId,
}: {
  messages: ReturnType<typeof useAgentChat>["messages"];
  sessionId: string;
}) {
  const lastMsg = [...messages].reverse().find((m) => m.role === "tool" || m.role === "assistant");

  return (
    <aside className="flex w-[360px] flex-shrink-0 flex-col border-l border-[var(--border)] bg-[var(--surface-muted)] p-4">
      <h3 className="text-[13px] font-semibold text-[var(--text)]">Inspector</h3>

      <div className="mt-3 space-y-3">
        <div className="rounded-[var(--radius-sm)] bg-[var(--surface-solid)] p-2.5">
          <p className="text-[11px] font-medium text-[var(--text-muted)]">Session</p>
          <p className="mt-0.5 text-[12px] font-mono text-[var(--text)]">{sessionId}</p>
        </div>

        {lastMsg && lastMsg.toolCalls.length > 0 && (
          <div className="rounded-[var(--radius-sm)] bg-[var(--surface-solid)] p-2.5">
            <p className="text-[11px] font-medium text-[var(--text-muted)]">
              Tool Calls ({lastMsg.toolCalls.length})
            </p>
            <div className="mt-1.5 space-y-1">
              {lastMsg.toolCalls.map((tc) => (
                <div key={tc.callId} className="flex items-center gap-2 text-[12px]">
                  <StatusBadge status={tc.status} />
                  <span className="font-mono text-[var(--text)]">{tc.name}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {lastMsg?.planId && (
          <div className="rounded-[var(--radius-sm)] bg-[var(--surface-solid)] p-2.5">
            <p className="text-[11px] font-medium text-[var(--text-muted)]">Plan</p>
            <p className="mt-0.5 text-[12px] font-mono text-[var(--text)]">{lastMsg.planId}</p>
          </div>
        )}
      </div>
    </aside>
  );
}
