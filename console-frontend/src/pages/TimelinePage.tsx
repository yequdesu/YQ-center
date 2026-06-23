import { useQuery } from "@tanstack/react-query";
import { listTimeline } from "@/api/admin";
import { Page, Panel } from "./DashboardPage";
import { LoadingSkeleton } from "@/components/LoadingSkeleton";
import { EmptyState } from "@/components/EmptyState";
import { History } from "lucide-react";

export function TimelinePage() {
  const timeline = useQuery({ queryKey: ["timeline"], queryFn: () => listTimeline({ limit: 100 }), refetchInterval: 5000 });

  if (timeline.isPending) return <Page title="Timeline"><LoadingSkeleton lines={5} /></Page>;
  if (timeline.isError) return <Page title="Timeline"><EmptyState icon={<History size={36} />} title="Failed to load" description={timeline.error?.message} /></Page>;
  if (!timeline.data?.length) return <Page title="Timeline"><EmptyState icon={<History size={36} />} title="No events" description="Timeline events will appear as actions are performed." /></Page>;

  return (
    <Page title="Timeline">
      <Panel title="Events">
        <div className="space-y-2">
          {timeline.data!.map((event) => (
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
