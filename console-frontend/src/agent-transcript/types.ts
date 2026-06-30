import type { AgentSessionMessage, AgentTurnEvent, CenterArtifactDetail } from "@/api/types";

export interface UserBlock {
  type: "user";
  id: string;
  content: string;
  created_at: string;
  optimistic?: boolean;
}

export interface AssistantTextBlock {
  type: "assistant_text";
  id: string;
  content: string;
  streaming: boolean;
  created_at: string;
}

export interface ToolGroupBlock {
  type: "tool_group";
  id: string;
  tool_calls: ToolCallState[];
  created_at: string;
}

export interface SystemEventBlock {
  type: "system_event";
  id: string;
  label: string;
  created_at: string;
}

export interface RunStatusBlock {
  type: "run_status";
  id: string;
  label: string;
  created_at: string;
}

export interface ArtifactPresentationBlock {
  type: "artifact_presentation";
  id: string;
  artifacts: Partial<CenterArtifactDetail>[];
  created_at: string;
}

export interface OperationCardBlock {
  type: "operation_card";
  id: string;
  operationId: string;
  kind: string;
  status: string;
  title?: string;
  refType?: string;
  refId?: string;
  waitHandle?: Record<string, unknown>;
  message?: string;
  errorCode?: string;
  errorMessage?: string;
  created_at: string;
}

export type ChatBlock =
  | UserBlock
  | AssistantTextBlock
  | ToolGroupBlock
  | SystemEventBlock
  | RunStatusBlock
  | ArtifactPresentationBlock
  | OperationCardBlock;

export interface ToolCallState {
  callId: string;
  name: string;
  input: Record<string, unknown>;
  status:
    | "pending"
    | "running"
    | "succeeded"
    | "failed"
    | "waiting_approval"
    | "waiting_operation"
    | "denied";
  targetNodeId?: string;
  invocationId?: string;
  jobId?: string;
  approvalId?: string;
  operationId?: string;
  waitHandle?: Record<string, unknown>;
  result?: Record<string, unknown>;
  errorCode?: string;
  errorMessage?: string;
}

export interface ToolCallPatch {
  callId?: string;
  approvalId?: string;
  status?: ToolCallState["status"];
  targetNodeId?: string;
  invocationId?: string;
  jobId?: string;
  operationId?: string;
  waitHandle?: Record<string, unknown>;
  result?: Record<string, unknown>;
  errorCode?: string | null;
  errorMessage?: string | null;
}

export interface PlanStepState {
  seq: number;
  kind: string;
  functionName: string;
  requiresApproval: boolean;
}

export interface PromptContextData {
  provider_name: string;
  system_prompt: string;
  target_node_id: string | null;
  execution_mode: string;
  routing_mode?: string;
  capability_context?: Record<string, unknown>;
  nodes?: unknown[];
  tool_count_by_node?: Record<string, unknown>;
  available_functions: Array<{
    name: string;
    description: string;
    risk: string;
    effect: string;
    source_nodes?: string[];
  }>;
}

export interface TranscriptState {
  blocks: ChatBlock[];
  promptContext: PromptContextData | null;
}

export interface PersistedTranscriptInput {
  messages: AgentSessionMessage[];
  turns?: Array<{ events?: AgentTurnEvent[] }>;
}
