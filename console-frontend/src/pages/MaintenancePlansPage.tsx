import { useQuery } from "@tanstack/react-query";
import { listMaintenancePlans } from "@/api/admin";
import { Page, Panel } from "./DashboardPage";
import { StatusBadge } from "@/components/StatusBadge";

export function MaintenancePlansPage() {
  const plans = useQuery({ queryKey: ["maintenance-plans"], queryFn: () => listMaintenancePlans({ limit: 100 }) });
  return (
    <Page title="Maintenance">
      <Panel title="Plans">
        <div className="space-y-2">
          {(plans.data ?? []).map((plan) => (
            <div key={plan.plan_id} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-white/60 p-3 text-[13px]">
              <div className="flex items-center gap-2">
                <span className="font-mono">{plan.plan_id}</span>
                <StatusBadge status={plan.status} />
              </div>
              <div className="mt-1 text-[var(--text-muted)]">{plan.goal}</div>
            </div>
          ))}
        </div>
      </Panel>
    </Page>
  );
}
