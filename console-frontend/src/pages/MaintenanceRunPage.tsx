import { useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { getMaintenanceRun, listMaintenanceRunArtifacts } from "@/api/admin";
import { Page, Panel } from "./DashboardPage";
import { JsonView } from "@/components/JsonView";

export function MaintenanceRunPage() {
  const { runId } = useParams({ from: "/maintenance/runs/$runId" });
  const run = useQuery({ queryKey: ["maintenance-run", runId], queryFn: () => getMaintenanceRun(runId), refetchInterval: 3000 });
  const artifacts = useQuery({ queryKey: ["maintenance-artifacts", runId], queryFn: () => listMaintenanceRunArtifacts(runId) });
  return (
    <Page title={`Run ${runId}`}>
      <div className="grid grid-cols-2 gap-4">
        <Panel title="Run Detail"><JsonView data={run.data ?? {}} /></Panel>
        <Panel title="Artifacts"><JsonView data={artifacts.data ?? { artifacts: [] }} /></Panel>
      </div>
    </Page>
  );
}
