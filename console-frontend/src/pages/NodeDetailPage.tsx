import { useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { getNode, listCapabilities } from "@/api/admin";
import { Page, Panel } from "./DashboardPage";
import { JsonView } from "@/components/JsonView";
import { StatusBadge } from "@/components/StatusBadge";

export function NodeDetailPage() {
  const { nodeId } = useParams({ from: "/nodes/$nodeId" });
  const node = useQuery({ queryKey: ["node", nodeId], queryFn: () => getNode(nodeId) });
  const caps = useQuery({ queryKey: ["capabilities", nodeId], queryFn: () => listCapabilities(nodeId) });
  return (
    <Page title={`Node ${nodeId}`}>
      <div className="grid grid-cols-2 gap-4">
        <Panel title="Node">
          {node.data && (
            <div className="space-y-2 text-[13px]">
              <div className="flex items-center gap-2"><StatusBadge status={node.data.status} /> {node.data.node_name}</div>
              <JsonView data={node.data} />
            </div>
          )}
        </Panel>
        <Panel title="Capabilities">
          <div className="space-y-2">
            {(caps.data ?? []).map((cap) => (
              <div key={`${cap.plugin_id}:${cap.name}`} className="rounded-[var(--radius-sm)] bg-white/60 p-2 text-[12px]">
                <span className="font-mono">{cap.name}</span>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </Page>
  );
}
