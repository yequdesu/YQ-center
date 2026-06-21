import { useQuery } from "@tanstack/react-query";
import { listApprovals, listJobs, listNodes, listTimeline } from "@/api/admin";

export function DashboardPage() {
  const nodes = useQuery({ queryKey: ["nodes"], queryFn: listNodes });
  const jobs = useQuery({ queryKey: ["jobs", "dashboard"], queryFn: () => listJobs({ limit: 100 }) });
  const approvals = useQuery({ queryKey: ["approvals", "dashboard"], queryFn: () => listApprovals({ status: "pending", limit: 50 }) });
  const timeline = useQuery({ queryKey: ["timeline", "dashboard"], queryFn: () => listTimeline({ limit: 8 }) });

  const online = (nodes.data ?? []).filter((node) => node.status === "online").length;
  const runningJobs = (jobs.data ?? []).filter((job) => job.status === "running").length;
  const queuedJobs = (jobs.data ?? []).filter((job) => job.status === "queued").length;

  return (
    <Page title="Dashboard">
      <div className="grid grid-cols-4 gap-4">
        <Metric label="Online Nodes" value={online} />
        <Metric label="Total Nodes" value={(nodes.data ?? []).length} />
        <Metric label="Queued Jobs" value={queuedJobs} />
        <Metric label="Running Jobs" value={runningJobs} />
      </div>
      <div className="mt-4 grid grid-cols-2 gap-4">
        <Panel title="Pending Approvals">
          <div className="text-[32px] font-semibold text-[var(--text)]">
            {(approvals.data ?? []).length}
          </div>
        </Panel>
        <Panel title="Recent Timeline">
          <div className="space-y-2">
            {(timeline.data ?? []).map((event) => (
              <div key={event.global_seq} className="text-[12px] text-[var(--text-muted)]">
                #{event.global_seq} {event.event_type}
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </Page>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <Panel title={label}>
      <div className="text-[30px] font-semibold text-[var(--text)]">{value}</div>
    </Panel>
  );
}

export function Page({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="p-6">
      <h1 className="text-[22px] font-semibold text-[var(--text)]">{title}</h1>
      <div className="mt-5">{children}</div>
    </div>
  );
}

export function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="glass-panel rounded-[var(--radius-md)] p-4">
      <h2 className="mb-3 text-[13px] font-semibold text-[var(--text-muted)]">{title}</h2>
      {children}
    </section>
  );
}
