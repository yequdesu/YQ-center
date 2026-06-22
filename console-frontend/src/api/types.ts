// ── Shared enum types matching Center ──

export type NodeStatus = "provisioned" | "online" | "degraded" | "offline" | "rejoining";

export type JobStatus =
  | "created"
  | "queued"
  | "claimed"
  | "running"
  | "cancelling"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "timeout";

export type InvocationStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "timeout"
  | "cancelled"
  | "partial"
  | "waiting_approval"
  | "rejected";

export type ApprovalStatus = "pending" | "approved" | "denied" | "expired" | "consumed";

export type MaintenancePlanStatus =
  | "draft"
  | "approved"
  | "running"
  | "succeeded"
  | "failed"
  | "waiting_approval"
  | "partially_succeeded"
  | "rollback_recommended"
  | "cancelled";

export type MaintenanceRunStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "partially_succeeded"
  | "rollback_recommended"
  | "cancelled";

export type MaintenanceStepStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped";

export type ArtifactKind =
  | "check_result"
  | "before"
  | "after"
  | "verify_result"
  | "log"
  | "error"
  | "rollback_hint";

export type ExecutionMode = "auto" | "assist" | "readonly" | "manual";
export type RiskLevel = "safe" | "maintenance" | "destructive" | "catastrophic";
export type StepKind = "check" | "repair" | "verify" | "write" | "rollback";
export type StepCondition =
  | "always"
  | "if_previous_unhealthy"
  | "after_repair"
  | "if_previous_failed"
  | "manual";

// ── API response types ──

export interface NodeSummary {
  node_id: string;
  node_name: string;
  role: string;
  locality: string;
  status: NodeStatus;
  daemon_version: string | null;
  platform_os: string | null;
  platform_arch: string | null;
  last_seen_at: string | null;
  last_heartbeat_at: string | null;
}

export interface NodeDetail extends NodeSummary {
  token_hash: string;
  heartbeat_interval_sec: number | null;
  job_delivery_mode: string | null;
  created_at: string | null;
}

export interface CapabilitySummary {
  plugin_id: string;
  plugin_version: string;
  capability_type: string;
  name: string;
  status: string;
  risk: string | null;
  effect: string | null;
  timeout_sec: number | null;
  idempotency: string | null;
  scope: string | null;
  ttl_sec: number | null;
  is_active: boolean;
  input_schema?: Record<string, unknown> | null;
  output_schema?: Record<string, unknown> | null;
}

export interface JobSummary {
  job_id: string;
  invocation_id: string;
  node_id: string;
  function_name: string;
  status: JobStatus;
  timeout_sec: number;
  lease_sec: number;
  claimed_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  error_message: string | null;
  error_details: Record<string, unknown> | null;
  cancel_reason: string | null;
  attempt: number;
  output: Record<string, unknown> | null;
}

export interface InvocationDetail {
  invocation_id: string;
  actor_type: string;
  actor_id: string;
  session_id: string | null;
  function_name: string;
  status: InvocationStatus;
  execution_mode: string;
  target_node_id: string | null;
  call_path: string[];
  max_depth: number | null;
  max_steps: number | null;
  max_total_duration_sec: number | null;
  started_at: string | null;
  finished_at: string | null;
  result: Record<string, unknown> | null;
  error_code: string | null;
  error_message: string | null;
  jobs: JobSummary[];
}

export interface TimelineSummary {
  global_seq: number;
  event_type: string;
  actor_type: string | null;
  actor_id: string | null;
  session_id: string | null;
  invocation_id: string | null;
  job_id: string | null;
  node_id: string | null;
  timestamp: string | null;
  data: Record<string, unknown> | null;
}

export interface ApprovalDetail {
  approval_id: string;
  status: ApprovalStatus;
  actor_id: string;
  session_id: string | null;
  function_name: string;
  target_node_id: string;
  input_hash: string;
  risk: string;
  effect: string;
  resource_keys: string[];
  expires_at: string;
  created_at: string;
  consumed_at: string | null;
  approved_by: string | null;
  denied_by: string | null;
  decision_reason: string | null;
  consumed_invocation_id: string | null;
  invocation?: {
    invocation_id: string;
    status: string;
    function_name: string;
    started_at: string | null;
    finished_at: string | null;
    jobs: { job_id: string; status: string }[];
  };
  consumed_invocation?: {
    invocation_id: string;
    status: string;
    function_name: string;
    started_at: string | null;
    finished_at: string | null;
    jobs: { job_id: string; status: string }[];
  };
}

export interface MaintenancePlanDetail {
  plan_id: string;
  goal: string;
  actor_id: string;
  target_node_id: string;
  risk: string;
  status: MaintenancePlanStatus;
  approval_id: string | null;
  resource_keys: string[] | null;
  max_total_duration_sec: number | null;
  rollback_strategy: string | null;
  created_at: string;
  approved_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  steps?: MaintenanceStepDetail[];
}

export interface MaintenanceStepDetail {
  step_id: string;
  plan_id: string;
  seq: number;
  function_name: string;
  input_data: Record<string, unknown> | null;
  depends_on: string[] | null;
  continue_on_failure: boolean;
  timeout_sec: number;
  resource_keys: string[] | null;
  kind: StepKind;
  condition: StepCondition;
  requires_approval: boolean;
  skip_reason: string | null;
  risk: string;
  rollback_hint: Record<string, unknown> | null;
  status: MaintenanceStepStatus;
  job_id: string | null;
  invocation_id: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface MaintenanceRunDetail {
  run_id: string;
  plan_id: string;
  status: MaintenanceRunStatus;
  rollback_recommended: boolean;
  started_at: string;
  finished_at: string | null;
  current_step_id: string | null;
  summary: RunSummary | null;
  artifact_summary?: {
    total: number;
    by_kind: Record<string, number>;
  };
  steps?: MaintenanceStepDetail[];
  rollback_hints?: RollbackHintSummary[];
}

export interface RunSummary {
  total_steps: number;
  succeeded: number;
  failed: number;
  skipped: number;
}

export interface RollbackHintSummary {
  artifact_id: string;
  step_id: string | null;
  name: string;
  data: Record<string, unknown> | null;
}

export interface MaintenanceArtifactDetail {
  artifact_id: string;
  run_id: string;
  step_id: string | null;
  invocation_id: string | null;
  job_id: string | null;
  kind: ArtifactKind;
  name: string;
  content_type: string;
  summary: Record<string, unknown> | null;
  data: Record<string, unknown> | null;
  created_at: string;
}

export interface AgentSessionMessage {
  message_id: string;
  session_id: string;
  role: "user" | "assistant" | "tool" | "system";
  content: string | null;
  tool_call_id: string | null;
  tool_calls: Record<string, unknown>[];
  created_at: string | null;
}

export interface AgentSessionDetail {
  session_id: string;
  actor_type: string;
  actor_id: string;
  status: string;
  execution_mode: string;
  started_at: string | null;
  closed_at: string | null;
  close_reason: string | null;
  metadata: Record<string, unknown>;
  label: string;
  messages: AgentSessionMessage[];
}

export interface AgentSessionSummary {
  session_id: string;
  actor_id: string;
  status: string;
  execution_mode: string;
  started_at: string | null;
  closed_at: string | null;
  label: string;
  updated_at?: string | null;
  last_message_preview?: string | null;
  message_count?: number;
  running?: boolean;
}

// ── SSE Event types ──

export type SseEventType =
  | "stream.open"
  | "stream.heartbeat"
  | "stream.close"
  | "agent.session.resolved"
  | "agent.prompt.received"
  | "agent.provider.started"
  | "agent.provider.delta"
  | "agent.planning.summary"
  | "agent.tool_call.created"
  | "agent.tool_call.arguments"
  | "agent.invocation.created"
  | "agent.job.queued"
  | "agent.job.running"
  | "agent.job.finished"
  | "agent.tool_call.completed"
  | "agent.tool_call.failed"
  | "agent.tool_call.waiting_approval"
  | "agent.output.delta"
  | "agent.plan.step.created"
  | "agent.plan.created"
  | "agent.approval.required"
  | "agent.loop.started"
  | "agent.loop.iteration"
  | "agent.observing"
  | "agent.synthesizing"
  | "agent.fallback_synthesis"
  | "agent.provider.failed"
  | "agent.completed"
  | "agent.failed";

export interface SseEvent {
  event_id: string;
  event_type: SseEventType;
  session_id: string;
  trace_id: string;
  timestamp: string;
  data: Record<string, unknown>;
}

// ── API client config ──

export interface ApiClientConfig {
  baseUrl: string;
  token: string;
  onUnauthorized: () => void;
}
