import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { listApprovals, getApproval, approveApproval, denyApproval, approveAndRunApproval } from "@/api/admin";
import { Page, Panel } from "./DashboardPage";
import { StatusBadge } from "@/components/StatusBadge";
import { JsonView } from "@/components/JsonView";
import { Button } from "@/components/Button";
import {
  CheckCircle,
  XCircle,
  Loader2,
  Play,
  ChevronDown,
  ChevronRight,
} from "lucide-react";

export function ApprovalsPage() {
  const queryClient = useQueryClient();
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [actingId, setActingId] = useState<string | null>(null);

  const approvals = useQuery({
    queryKey: ["approvals"],
    queryFn: () => listApprovals({ limit: 100 }),
    refetchInterval: 10_000,
  });

  const detailQuery = useQuery({
    queryKey: ["approval-detail", expandedId],
    queryFn: () => (expandedId ? getApproval(expandedId) : null),
    enabled: !!expandedId,
  });

  const refreshAll = () => {
    queryClient.invalidateQueries({ queryKey: ["approvals"] });
    queryClient.invalidateQueries({ queryKey: ["approval-detail"] });
    queryClient.invalidateQueries({ queryKey: ["timeline"] });
  };

  const handleApprove = async (id: string) => {
    if (!confirm("Approve this request?")) return;
    setActingId(id);
    try {
      await approveApproval(id);
      refreshAll();
    } finally {
      setActingId(null);
    }
  };

  const handleDeny = async (id: string) => {
    if (!confirm("Deny this request?")) return;
    setActingId(id);
    try {
      await denyApproval(id);
      refreshAll();
    } finally {
      setActingId(null);
    }
  };

  const handleApproveAndRun = async (id: string) => {
    if (!confirm("Approve and immediately execute this operation?")) return;
    setActingId(id);
    try {
      await approveAndRunApproval(id);
      refreshAll();
    } finally {
      setActingId(null);
    }
  };

  return (
    <Page title="Approvals">
      <Panel title="Requests">
        <div className="space-y-2">
          {(approvals.data ?? []).map((approval) => {
            const isExpanded = expandedId === approval.approval_id;
            const isActing = actingId === approval.approval_id;
            const detail = isExpanded ? detailQuery.data : null;

            return (
              <div
                key={approval.approval_id}
                className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-white/60 text-[13px]"
              >
                {/* Header — clickable row */}
                <button
                  type="button"
                  onClick={() =>
                    setExpandedId(isExpanded ? null : approval.approval_id)
                  }
                  className="flex w-full items-center gap-2 p-3 text-left hover:bg-[var(--bg-subtle)]"
                >
                  {isExpanded ? (
                    <ChevronDown size={14} className="text-[var(--text-muted)]" />
                  ) : (
                    <ChevronRight size={14} className="text-[var(--text-muted)]" />
                  )}
                  <span className="font-mono text-[12px]">{approval.approval_id}</span>
                  <StatusBadge status={approval.status} />
                  <span className="flex-1" />
                  <span className="text-[var(--text-muted)] text-[12px]">
                    {approval.function_name} on {approval.target_node_id}
                  </span>
                </button>

                {/* Detail panel */}
                {isExpanded && detailQuery.isPending && (
                  <div className="border-t border-[var(--border)] px-3 py-2 text-[var(--text-muted)]">
                    <Loader2 size={14} className="animate-spin inline" /> Loading...
                  </div>
                )}
                {isExpanded && detail && (
                  <div className="border-t border-[var(--border)] space-y-2 p-3">
                    <div className="grid grid-cols-2 gap-2 text-[12px]">
                      <div>
                        <span className="text-[var(--text-muted)]">Function: </span>
                        <span className="font-mono">{detail.function_name}</span>
                      </div>
                      <div>
                        <span className="text-[var(--text-muted)]">Target Node: </span>
                        <span className="font-mono">{detail.target_node_id}</span>
                      </div>
                      <div>
                        <span className="text-[var(--text-muted)]">Risk: </span>
                        <StatusBadge status={detail.risk} />
                      </div>
                      <div>
                        <span className="text-[var(--text-muted)]">Effect: </span>
                        <StatusBadge status={detail.effect} />
                      </div>
                      <div>
                        <span className="text-[var(--text-muted)]">Created: </span>
                        {detail.created_at ? new Date(detail.created_at).toLocaleString() : "-"}
                      </div>
                      <div>
                        <span className="text-[var(--text-muted)]">Expires: </span>
                        {detail.expires_at ? new Date(detail.expires_at).toLocaleString() : "-"}
                      </div>
                    </div>

                    {detail.resource_keys && detail.resource_keys.length > 0 && (
                      <div>
                        <p className="text-[11px] font-medium text-[var(--text-muted)]">Resource Keys</p>
                        <div className="flex gap-1 mt-0.5">
                          {detail.resource_keys.map((k: string) => (
                            <span key={k} className="rounded-[var(--radius-sm)] bg-[var(--bg-subtle)] px-1.5 py-0.5 font-mono text-[11px]">
                              {k}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Linked invocation */}
                    {detail.invocation && (
                      <div className="rounded-[var(--radius-sm)] border border-[var(--border)] p-2">
                        <p className="text-[11px] font-medium text-[var(--text-muted)]">Linked Invocation</p>
                        <p className="font-mono text-[11px]">{detail.invocation.invocation_id}</p>
                        <StatusBadge status={detail.invocation.status} />
                      </div>
                    )}

                    {/* Linked consumed invocation */}
                    {detail.consumed_invocation && (
                      <div className="rounded-[var(--radius-sm)] border border-[var(--border)] p-2">
                        <p className="text-[11px] font-medium text-[var(--text-muted)]">Execution Invocation</p>
                        <p className="font-mono text-[11px]">{detail.consumed_invocation.invocation_id}</p>
                        <StatusBadge status={detail.consumed_invocation.status} />
                        {detail.consumed_invocation.jobs && (
                          <div className="mt-1 space-y-0.5">
                            {(detail.consumed_invocation.jobs as Array<{ job_id: string; status: string }>).map((j) => (
                              <div key={j.job_id} className="flex items-center gap-1 text-[11px]">
                                <span className="font-mono text-[var(--text-subtle)]">{j.job_id}</span>
                                <StatusBadge status={j.status} />
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Actions */}
                    {detail.status === "pending" && (
                      <div className="flex gap-2 pt-1">
                        <Button
                          variant="primary"
                          size="sm"
                          onClick={() => handleApproveAndRun(detail.approval_id)}
                          disabled={!!actingId}
                        >
                          {isActing ? (
                            <Loader2 size={12} className="animate-spin" />
                          ) : (
                            <Play size={12} />
                          )}
                          <span className="ml-1">Approve &amp; Execute</span>
                        </Button>
                        <Button
                          variant="primary"
                          size="sm"
                          onClick={() => handleApprove(detail.approval_id)}
                          disabled={!!actingId}
                        >
                          <CheckCircle size={12} />
                          <span className="ml-1">Approve Only</span>
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleDeny(detail.approval_id)}
                          disabled={!!actingId}
                        >
                          <XCircle size={12} />
                          <span className="ml-1">Deny</span>
                        </Button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </Panel>
    </Page>
  );
}
