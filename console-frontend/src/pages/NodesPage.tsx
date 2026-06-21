import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { listNodes } from "@/api/admin";
import { Page, Panel } from "./DashboardPage";
import { StatusBadge } from "@/components/StatusBadge";

export function NodesPage() {
  const nodes = useQuery({ queryKey: ["nodes"], queryFn: listNodes, refetchInterval: 10_000 });
  return (
    <Page title="Nodes">
      <Panel title="Registered Nodes">
        <div className="space-y-2">
          {(nodes.data ?? []).map((node) => (
            <Link
              key={node.node_id}
              to="/nodes/$nodeId"
              params={{ nodeId: node.node_id }}
              className="flex items-center gap-3 rounded-[var(--radius-sm)] border border-[var(--border)] bg-white/60 p-3"
            >
              <span className="font-mono text-[13px] text-[var(--text)]">{node.node_id}</span>
              <StatusBadge status={node.status} />
              <span className="text-[12px] text-[var(--text-muted)]">{node.node_name}</span>
            </Link>
          ))}
        </div>
      </Panel>
    </Page>
  );
}
