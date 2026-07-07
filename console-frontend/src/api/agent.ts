import { api } from "./client";

export interface CreateSessionBody {
  actor_id?: string;
  execution_mode?: string;
  max_depth?: number;
  max_steps?: number;
  max_total_duration_sec?: number;
}

export interface CreateSessionResponse {
  session_id: string;
  execution_mode: string;
  max_depth: number;
  max_steps: number;
  max_total_duration_sec: number;
}

export function createSession(body: CreateSessionBody = {}) {
  return api.post<CreateSessionResponse>("/agent/sessions", body);
}

export interface AgentOperationNotification {
  notification_id: string;
  session_id: string;
  operation_id: string;
  event_id: string | null;
  operation_status: string;
  status: "pending" | "processing" | "reported" | "failed";
  attempts: number;
  last_error: string | null;
  report_turn_id: string | null;
  created_at: string | null;
  updated_at: string | null;
  claimed_at: string | null;
  reported_at: string | null;
  operation: {
    operation_id: string;
    kind: string;
    status: string;
    ref_type: string;
    ref_id: string;
    title: string | null;
    progress_pct: number | null;
    progress_message: string | null;
    error_code: string | null;
    error_message: string | null;
  };
}

export interface AgentRuntimePlanStep {
  step_id: string;
  step_index: number;
  kind: string;
  title: string;
  status: string;
  operation_id: string | null;
  tool_call_id: string | null;
  completed_at: string | null;
  metadata: Record<string, unknown>;
}

export interface AgentRuntimePlan {
  plan_id: string;
  session_id: string;
  run_id: string;
  turn_id: string | null;
  status: string;
  objective: string;
  provider_name: string;
  execution_mode: string;
  target_node_id: string | null;
  completed_at: string | null;
  metadata: Record<string, unknown>;
  steps: AgentRuntimePlanStep[];
}

export function getSessionAgentPlan(sessionId: string) {
  return api.get<{ plan: AgentRuntimePlan | null }>(
    `/agent/sessions/${encodeURIComponent(sessionId)}/plan`,
  );
}

export function claimOperationNotification(sessionId: string) {
  return api.post<{ notification: AgentOperationNotification | null }>(
    `/agent/sessions/${sessionId}/operation-notifications/claim`,
    {},
  );
}

export function markOperationNotificationReported(notificationId: string, turnId?: string | null) {
  return api.post<{ notification: AgentOperationNotification }>(
    `/agent/operation-notifications/${notificationId}/reported`,
    { turn_id: turnId ?? null },
  );
}

export function markOperationNotificationFailed(notificationId: string, error: string) {
  return api.post<{ notification: AgentOperationNotification }>(
    `/agent/operation-notifications/${notificationId}/failed`,
    { error },
  );
}
