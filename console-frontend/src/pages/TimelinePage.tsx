import { useQuery } from "@tanstack/react-query";
import { listTimeline } from "@/api/admin";
import { Page, Panel } from "./DashboardPage";

export function TimelinePage() {
  const timeline = useQuery({ queryKey: ["timeline"], queryFn: () => listTimeline({ limit: 100 }), refetchInterval: 5000 });
  return (
    <Page title="Timeline">
      <Panel title="Events">
        <div className="space-y-2">
          {(timeline.data ?? []).map((event) => (
            <div key={event.global_seq} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-white/60 p-3 text-[12px]">
              <span className="font-mono text-[var(--text-subtle)]">#{event.global_seq}</span>{" "}
              <span className="font-medium">{event.event_type}</span>
              <div className="mt-1 text-[var(--text-muted)]">{event.timestamp}</div>
            </div>
          ))}
        </div>
      </Panel>
    </Page>
  );
}
