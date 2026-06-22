import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { listMaintenancePlans, getMaintenancePlan, approvePlan, runPlan } from "@/api/admin";
import { Page, Panel } from "./DashboardPage";
import { StatusBadge } from "@/components/StatusBadge";
import { JsonView } from "@/components/JsonView";
import { Button } from "@/components/Button";
import {
  CheckCircle,
  Play,
  Loader2,
  ChevronDown,
  ChevronRight,
} from "lucide-react";

export function MaintenancePlansPage() {
  const queryClient = useQueryClient();
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [actingId, setActingId] = useState<string | null>(null);

  const plans = useQuery({
    queryKey: ["maintenance-plans"],
    queryFn: () => listMaintenancePlans({ limit: 100 }),
    refetchInterval: 10_000,
  });

  const detailQuery = useQuery({
    queryKey: ["maintenance-plan", expandedId],
    queryFn: () => (expandedId ? getMaintenancePlan(expandedId) : null),
    enabled: !!expandedId,
  });

  const refreshAll = () => {
    queryClient.invalidateQueries({ queryKey: ["maintenance-plans"] });
    queryClient.invalidateQueries({ queryKey: ["maintenance-plan"] });
    queryClient.invalidateQueries({ queryKey: ["timeline"] });
  };

  const handleApprove = async (planId: string) => {
    if (!confirm("Approve this maintenance plan?")) return;
    setActingId(planId);
    try {
      await approvePlan(planId);
      refreshAll();
    } finally {
      setActingId(null);
    }
  };

  const handleRun = async (planId: string) => {
    if (!confirm("Run this maintenance plan on the target node?")) return;
    setActingId(planId);
    try {
      await runPlan(planId);
      refreshAll();
    } finally {
      setActingId(null);
    }
  };

  return (
    <Page title="Maintenance">
      <Panel title="Plans">
        <div className="space-y-2">
          {(plans.data ?? []).map((plan) => {
            const isExpanded = expandedId === plan.plan_id;
            const isActing = actingId === plan.plan_id;
            const detail = isExpanded ? detailQuery.data : null;

            return (
              <div
                key={plan.plan_id}
                className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-white/60 text-[13px]"
              >
                {/* Header */}
                <button
                  type="button"
                  onClick={() => setExpandedId(isExpanded ? null : plan.plan_id)}
                  className="flex w-full items-center gap-2 p-3 text-left hover:bg-[var(--bg-subtle)]"
                >
                  {isExpanded ? (
                    <ChevronDown size={14} className="text-[var(--text-muted)]" />
                  ) : (
                    <ChevronRight size={14} className="text-[var(--text-muted)]" />
                  )}
                  <span className="font-mono text-[12px]">{plan.plan_id}</span>
                  <StatusBadge status={plan.status} />
                  <span className="flex-1" />
                  <span className="text-[var(--text-muted)] text-[12px] truncate max-w-[300px]">
                    {plan.goal}
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
                        <span className="text-[var(--text-muted)]">Goal: </span>
                        <span>{detail.goal}</span>
                      </div>
                      <div>
                        <span className="text-[var(--text-muted)]">Target: </span>
                        <span className="font-mono">{detail.target_node_id}</span>
                      </div>
                      <div>
                        <span className="text-[var(--text-muted)]">Risk: </span>
                        <StatusBadge status={detail.risk} />
                      </div>
                      <div>
                        <span className="text-[var(--text-muted)]">Created: </span>
                        {detail.created_at ? new Date(detail.created_at).toLocaleString() : "-"}
                      </div>
                    </div>

                    {/* Steps */}
                    {detail.steps && detail.steps.length > 0 && (
                      <div>
                        <p className="text-[11px] font-medium text-[var(--text-muted)] mb-1">
                          Steps ({detail.steps.length})
                        </p>
                        <div className="space-y-1.5">
                          {detail.steps.map((step) => (
                            <div
                              key={step.step_id}
                              className="rounded-[var(--radius-sm)] border border-[var(--border)] p-2"
                            >
                              <div className="flex items-center gap-2 text-[12px]">
                                <span className="w-4 text-right text-[var(--text-subtle)]">
                                  {step.seq}.
                                </span>
                                <StatusBadge status={step.kind} />
                                <span className="font-mono">{step.function_name}</span>
                                <span className="flex-1" />
                                <StatusBadge status={step.status} />
                                {step.requires_approval && (
                                  <span className="text-[10px] text-[var(--warning)]">needs approval</span>
                                )}
                              </div>
                              {step.input_data && Object.keys(step.input_data).length > 0 && (
                                <div className="mt-1">
                                  <JsonView data={step.input_data} />
                                </div>
                              )}
                              {step.error && (
                                <p className="mt-1 text-[11px] text-[var(--danger)]">{step.error}</p>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Actions */}
                    <div className="flex gap-2 pt-1">
                      {detail.status === "draft" || detail.status === "waiting_approval" ? (
                        <Button
                          variant="primary"
                          size="sm"
                          onClick={() => handleApprove(detail.plan_id)}
                          disabled={!!actingId}
                        >
                          {isActing ? (
                            <Loader2 size={12} className="animate-spin" />
                          ) : (
                            <CheckCircle size={12} />
                          )}
                          <span className="ml-1">Approve Plan</span>
                        </Button>
                      ) : null}
                      {detail.status === "approved" && (
                        <Button
                          variant="primary"
                          size="sm"
                          onClick={() => handleRun(detail.plan_id)}
                          disabled={!!actingId}
                        >
                          {isActing ? (
                            <Loader2 size={12} className="animate-spin" />
                          ) : (
                            <Play size={12} />
                          )}
                          <span className="ml-1">Run Plan</span>
                        </Button>
                      )}
                    </div>
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
