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
