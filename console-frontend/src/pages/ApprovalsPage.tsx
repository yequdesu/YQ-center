import { useQuery } from "@tanstack/react-query";
import { listApprovals } from "@/api/admin";
import { Page, Panel } from "./DashboardPage";
import { StatusBadge } from "@/components/StatusBadge";

export function ApprovalsPage() {
  const approvals = useQuery({ queryKey: ["approvals"], queryFn: () => listApprovals({ limit: 100 }), refetchInterval: 10_000 });
  return (
    <Page title="Approvals">
      <Panel title="Requests">
        <div className="space-y-2">
          {(approvals.data ?? []).map((approval) => (
            <div key={approval.approval_id} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-white/60 p-3 text-[13px]">
              <div className="flex items-center gap-2">
                <span className="font-mono">{approval.approval_id}</span>
                <StatusBadge status={approval.status} />
              </div>
              <div className="mt-1 text-[var(--text-muted)]">{approval.function_name} on {approval.target_node_id}</div>
            </div>
          ))}
        </div>
      </Panel>
    </Page>
  );
}
