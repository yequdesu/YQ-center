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
