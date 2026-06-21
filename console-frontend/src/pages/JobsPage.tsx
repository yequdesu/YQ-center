import { useQuery } from "@tanstack/react-query";
import { listJobs } from "@/api/admin";
import { Page, Panel } from "./DashboardPage";
import { StatusBadge } from "@/components/StatusBadge";

export function JobsPage() {
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: () => listJobs({ limit: 100 }), refetchInterval: 5000 });
  return (
    <Page title="Jobs">
      <Panel title="Recent Jobs">
        <div className="space-y-2">
          {(jobs.data ?? []).map((job) => (
            <div key={job.job_id} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-white/60 p-3 text-[13px]">
              <div className="flex items-center gap-2">
                <span className="font-mono">{job.job_id}</span>
                <StatusBadge status={job.status} />
              </div>
              <div className="mt-1 text-[var(--text-muted)]">{job.function_name} on {job.node_id}</div>
            </div>
          ))}
        </div>
      </Panel>
    </Page>
  );
}
