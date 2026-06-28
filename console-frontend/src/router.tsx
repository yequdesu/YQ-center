import { createRouter, createRootRoute, createRoute } from "@tanstack/react-router";
import { AppShell } from "@/layout/AppShell";
import { DashboardPage } from "@/pages/DashboardPage";
import { AgentChatPage } from "@/pages/AgentChatPage";
import { NodesPage } from "@/pages/NodesPage";
import { NodeDetailPage } from "@/pages/NodeDetailPage";
import { ApprovalsPage } from "@/pages/ApprovalsPage";
import { MaintenancePlansPage } from "@/pages/MaintenancePlansPage";
import { MaintenanceRunPage } from "@/pages/MaintenanceRunPage";
import { InvocationsPage } from "@/pages/InvocationsPage";
import { JobsPage } from "@/pages/JobsPage";
import { TimelinePage } from "@/pages/TimelinePage";
import { ArtifactsPage } from "@/pages/ArtifactsPage";

const rootRoute = createRootRoute({
  component: () => <AppShell />,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: DashboardPage,
});

const chatRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/chat",
  component: AgentChatPage,
});

const nodesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/nodes",
  component: NodesPage,
});

const nodeDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/nodes/$nodeId",
  component: NodeDetailPage,
});

const approvalsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/approvals",
  component: ApprovalsPage,
});

const maintenanceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/maintenance",
  component: MaintenancePlansPage,
});

const maintenanceRunRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/maintenance/runs/$runId",
  component: MaintenanceRunPage,
});

const invocationsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/invocations",
  component: InvocationsPage,
});

const jobsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/jobs",
  component: JobsPage,
});

const artifactsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/artifacts",
  component: ArtifactsPage,
});

const timelineRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/timeline",
  component: TimelinePage,
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  chatRoute,
  nodesRoute,
  nodeDetailRoute,
  approvalsRoute,
  maintenanceRoute,
  maintenanceRunRoute,
  invocationsRoute,
  jobsRoute,
  artifactsRoute,
  timelineRoute,
]);

export const router = createRouter({ routeTree, basepath: "/console" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
