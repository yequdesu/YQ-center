import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { listNodes } from "@/api/admin";
import { Page, Panel } from "./DashboardPage";
import { StatusBadge } from "@/components/StatusBadge";
import { LoadingSkeleton } from "@/components/LoadingSkeleton";
import { EmptyState } from "@/components/EmptyState";
import { Network } from "lucide-react";

export function NodesPage() {
  const nodes = useQuery({ queryKey: ["nodes"], queryFn: listNodes, refetchInterval: 10_000 });

  if (nodes.isPending) return <Page title="Nodes"><LoadingSkeleton lines={3} /></Page>;
  if (nodes.isError) return <Page title="Nodes"><EmptyState icon={<Network size={36} />} title="Failed to load" description={nodes.error?.message} /></Page>;
  if (!nodes.data?.length) return <Page title="Nodes"><EmptyState icon={<Network size={36} />} title="No nodes" description="Provision a node to get started." /></Page>;

  return (
    <Page title="Nodes">
      <Panel title="Registered Nodes">
        <div className="space-y-2">
          {nodes.data!.map((node) => (
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
