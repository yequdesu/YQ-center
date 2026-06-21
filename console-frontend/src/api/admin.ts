import { api } from "./client";
import type {
  ApprovalDetail,
  AgentSessionDetail,
  AgentSessionSummary,
  CapabilitySummary,
  InvocationDetail,
  JobSummary,
  MaintenanceArtifactDetail,
  MaintenancePlanDetail,
  MaintenanceRunDetail,
  NodeDetail,
  NodeSummary,
  TimelineSummary,
} from "./types";

// ── Nodes ──

export function listNodes() {
  return api.get<NodeSummary[]>("/admin/nodes");
}

export function getNode(nodeId: string) {
  return api.get<NodeDetail>(`/admin/nodes/${encodeURIComponent(nodeId)}`);
}

// ── Capabilities ──

export function listCapabilities(nodeId?: string) {
  return api.get<CapabilitySummary[]>("/admin/capabilities", {
    node_id: nodeId,
  });
}

// ── Jobs ──

export function listJobs(params?: {
  nodeId?: string;
  status?: string;
  invocationId?: string;
  limit?: number;
}) {
  return api.get<JobSummary[]>("/admin/jobs", {
    node_id: params?.nodeId,
    status: params?.status,
    invocation_id: params?.invocationId,
    limit: params?.limit ?? 50,
  });
}

export function getJob(jobId: string) {
  return api.get<JobSummary>(`/admin/jobs/${encodeURIComponent(jobId)}`);
}

// ── Invocations ──

export function listInvocations(params?: {
  status?: string;
  actorId?: string;
  sessionId?: string;
  limit?: number;
}) {
  return api.get<InvocationDetail[]>("/admin/invocations", {
    status: params?.status,
    actor_id: params?.actorId,
    session_id: params?.sessionId,
    limit: params?.limit ?? 50,
  });
}

export function getInvocation(invocationId: string) {
  return api.get<InvocationDetail>(
    `/admin/invocations/${encodeURIComponent(invocationId)}`,
  );
}

export interface CreateInvocationBody {
  function_name: string;
  target_node_id: string;
  input: Record<string, unknown>;
  actor_type?: string;
  actor_id?: string;
  session_id?: string;
  execution_mode?: string;
  timeout_sec?: number;
  lease_sec?: number;
  max_depth?: number;
  max_steps?: number;
  max_total_duration_sec?: number;
  approval_id?: string;
  dry_run?: boolean;
}

export function createInvocation(body: CreateInvocationBody) {
  return api.post<
    InvocationDetail & {
      job_id: string;
      job_status: string;
      approval_id: string;
      dry_run: boolean;
      allowed: boolean;
      decision: string;
    }
  >("/admin/invocations", body);
}

// ── Sessions ──

export function getSession(sessionId: string) {
  return api.get<AgentSessionDetail>(`/admin/sessions/${encodeURIComponent(sessionId)}`);
}

export function listSessions() {
  return api.get<AgentSessionSummary[]>("/admin/sessions");
}

export function listSessionMessages(sessionId: string) {
  return api.get<AgentSessionDetail["messages"]>(
    `/admin/sessions/${encodeURIComponent(sessionId)}/messages`,
  );
}

export function renameSession(sessionId: string, label: string) {
  return api.patch<{ session_id: string; label: string }>(
    `/admin/sessions/${encodeURIComponent(sessionId)}`,
    { label },
  );
}

export function deleteSession(sessionId: string) {
  return api.delete(`/admin/sessions/${encodeURIComponent(sessionId)}`);
}

// ── Approvals ──

export function listApprovals(params?: { status?: string; limit?: number }) {
  return api.get<ApprovalDetail[]>("/admin/approvals", {
    status: params?.status,
    limit: params?.limit ?? 50,
  });
}

export function getApproval(approvalId: string) {
  return api.get<ApprovalDetail>(
    `/admin/approvals/${encodeURIComponent(approvalId)}`,
  );
}

export function approveApproval(approvalId: string, reason?: string) {
  return api.post<ApprovalDetail>(
    `/admin/approvals/${encodeURIComponent(approvalId)}/approve`,
    { reason },
  );
}

export function denyApproval(approvalId: string, reason?: string) {
  return api.post<ApprovalDetail>(
    `/admin/approvals/${encodeURIComponent(approvalId)}/deny`,
    { reason },
  );
}

// ── Timeline ──

export function listTimeline(params?: {
  nodeId?: string;
  jobId?: string;
  invocationId?: string;
  sessionId?: string;
  eventType?: string;
  approvalId?: string;
  limit?: number;
  createdAfter?: string;
  createdBefore?: string;
}) {
  return api.get<TimelineSummary[]>("/admin/timeline", {
    node_id: params?.nodeId,
    job_id: params?.jobId,
    invocation_id: params?.invocationId,
    session_id: params?.sessionId,
    event_type: params?.eventType,
    approval_id: params?.approvalId,
    limit: params?.limit ?? 50,
    created_after: params?.createdAfter,
    created_before: params?.createdBefore,
  });
}

// ── Maintenance Plans ──

export function listMaintenancePlans(params?: { status?: string; limit?: number }) {
  return api.get<MaintenancePlanDetail[]>("/admin/maintenance/plans", {
    status: params?.status,
    limit: params?.limit ?? 50,
  });
}

export function getMaintenancePlan(planId: string) {
  return api.get<MaintenancePlanDetail>(
    `/admin/maintenance/plans/${encodeURIComponent(planId)}`,
  );
}

export interface CreatePlanBody {
  goal: string;
  target_node_id: string;
  steps: Record<string, unknown>[];
  actor_id?: string;
  session_id?: string;
  risk?: string;
  max_total_duration_sec?: number;
  rollback_strategy?: string;
  execution_mode?: string;
}

export function createPlan(body: CreatePlanBody) {
  return api.post<MaintenancePlanDetail & { step_count: number; steps: Record<string, unknown>[] }>(
    "/admin/maintenance/plans",
    body,
  );
}

export function approvePlan(planId: string, approvalId?: string) {
  return api.post<MaintenancePlanDetail>(
    `/admin/maintenance/plans/${encodeURIComponent(planId)}/approve`,
    approvalId ? { approval_id: approvalId } : undefined,
  );
}

export function runPlan(planId: string, params?: { approval_id?: string; dry_run?: boolean }) {
  const queryParams: Record<string, string> = {};
  if (params?.approval_id) queryParams.approval_id = params.approval_id;
  if (params?.dry_run) queryParams.dry_run = "true";

  const qs = Object.entries(queryParams)
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
    .join("&");

  return api.post<MaintenanceRunDetail>(
    `/admin/maintenance/plans/${encodeURIComponent(planId)}/run${qs ? `?${qs}` : ""}`,
  );
}

// ── Maintenance Runs ──

export function getMaintenanceRun(runId: string) {
  return api.get<MaintenanceRunDetail>(
    `/admin/maintenance/runs/${encodeURIComponent(runId)}`,
  );
}

export function listMaintenanceRunArtifacts(
  runId: string,
  params?: { kind?: string; stepId?: string },
) {
  return api.get<{ run_id: string; artifacts: MaintenanceArtifactDetail[] }>(
    `/admin/maintenance/runs/${encodeURIComponent(runId)}/artifacts`,
    {
      kind: params?.kind,
      step_id: params?.stepId,
    },
  );
}
