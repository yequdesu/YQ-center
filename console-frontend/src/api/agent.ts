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

export interface AgentRunStep {
  step_id: string;
  step_index: number;
  step_type: string;
  status: string;
  input_data: unknown;
  output_data: unknown;
  error_code: string | null;
  error_message: string | null;
  metadata: Record<string, unknown>;
  started_at: string | null;
  completed_at: string | null;
}

export interface AgentRunEvent {
  event_id: string;
  seq: number;
  event_type: string;
  created_at: string;
  tool_call_id: string | null;
  operation_id: string | null;
  approval_id: string | null;
  artifact_id: string | null;
  plan_id: string | null;
  plan_step_id: string | null;
  payload: Record<string, unknown>;
}

export interface AgentTaskState {
  objective?: string;
  facts?: unknown[];
  blockers?: unknown[];
  pending_operations?: unknown[];
  pending_approvals?: unknown[];
  artifacts?: unknown[];
  working_set?: unknown[];
  completion?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface AgentRunProjection {
  run_id: string;
  session_id: string;
  trace_id: string;
  provider_name: string;
  status: string;
  execution_mode: string;
  target_node_id: string | null;
  user_message: string;
  final_message: string | null;
  error_code: string | null;
  error_message: string | null;
  metadata: Record<string, unknown>;
  task_state: AgentTaskState;
  started_at: string | null;
  completed_at: string | null;
  steps: AgentRunStep[];
  events: AgentRunEvent[];
}

export interface AgentRuntimeTimingSegment {
  event_type: string;
  category: string;
  elapsed_ms: number;
  recorded_at?: string | null;
  step?: number | string | null;
  first_delta_ms?: number | null;
  tool_call_count?: number | null;
}

export interface AgentRuntimeOperationWaitSegment {
  operation_id: string;
  kind?: string | null;
  status?: string | null;
  terminal_event?: string | null;
  title?: string | null;
  ref_type?: string | null;
  ref_id?: string | null;
  elapsed_ms: number;
  recorded_at?: string | null;
}

export interface AgentRuntimeDbError {
  recorded_at?: string | null;
  phase?: string | null;
  name?: string | null;
  target_node_id?: string | null;
  error_code?: string | null;
  message?: string | null;
}

export interface AgentRuntimeTiming {
  event_count: number;
  segment_count: number;
  category_totals_ms: Record<string, number>;
  top_segments: AgentRuntimeTimingSegment[];
  operation_wait_segments: AgentRuntimeOperationWaitSegment[];
  ycr_build_turn_timing_ms: Record<string, number>;
  tool_call_counts: Record<string, number>;
  db_errors: AgentRuntimeDbError[];
  db_error_count: number;
}

export interface AgentRuntimeStateResponse {
  run: AgentRunProjection | null;
  plan: AgentRuntimePlan | null;
  timing: AgentRuntimeTiming;
}

export function getSessionRuntimeState(sessionId: string) {
  return api.get<AgentRuntimeStateResponse>(
    `/agent/sessions/${encodeURIComponent(sessionId)}/runtime-state`,
  );
}
