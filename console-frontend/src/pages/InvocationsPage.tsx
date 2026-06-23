import { useQuery } from "@tanstack/react-query";
import { listInvocations } from "@/api/admin";
import { Page, Panel } from "./DashboardPage";
import { StatusBadge } from "@/components/StatusBadge";
import { LoadingSkeleton } from "@/components/LoadingSkeleton";
import { EmptyState } from "@/components/EmptyState";
import { GitBranch } from "lucide-react";

export function InvocationsPage() {
  const invocations = useQuery({ queryKey: ["invocations"], queryFn: () => listInvocations({ limit: 100 }), refetchInterval: 5000 });

  if (invocations.isPending) return <Page title="Invocations"><LoadingSkeleton lines={4} /></Page>;
  if (invocations.isError) return <Page title="Invocations"><EmptyState icon={<GitBranch size={36} />} title="Failed to load" description={invocations.error?.message} /></Page>;
  if (!invocations.data?.length) return <Page title="Invocations"><EmptyState icon={<GitBranch size={36} />} title="No invocations" description="Use Agent Chat to trigger invocations." /></Page>;

  return (
    <Page title="Invocations">
      <Panel title="Recent Invocations">
        <div className="space-y-2">
          {invocations.data!.map((inv) => (
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
