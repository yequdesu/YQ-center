import { useQuery } from "@tanstack/react-query";
import { listInvocations } from "@/api/admin";
import { Page, Panel } from "./DashboardPage";
import { StatusBadge } from "@/components/StatusBadge";

export function InvocationsPage() {
  const invocations = useQuery({ queryKey: ["invocations"], queryFn: () => listInvocations({ limit: 100 }), refetchInterval: 5000 });
  return (
    <Page title="Invocations">
      <Panel title="Recent Invocations">
        <div className="space-y-2">
          {(invocations.data ?? []).map((inv) => (
            <div key={inv.invocation_id} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-white/60 p-3 text-[13px]">
              <div className="flex items-center gap-2">
                <span className="font-mono">{inv.invocation_id}</span>
                <StatusBadge status={inv.status} />
              </div>
              <div className="mt-1 text-[var(--text-muted)]">{inv.function_name}</div>
            </div>
          ))}
        </div>
      </Panel>
    </Page>
  );
}
