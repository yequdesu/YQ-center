export interface BridgeResponse<T = unknown> {
  ok: boolean;
  data: T | null;
  error: BridgeError | null;
}

export interface BridgeError {
  code: string;
  message: string;
}

export interface ServiceStatus {
  installed: boolean;
  running: boolean;
  startup_type: string;
  display_name: string;
  path_name?: string;
  service_account?: string;
  user_worker?: {
    installed: boolean;
    task_state: string;
    running: boolean;
    reachable: boolean;
    user?: string | null;
    pid?: number | null;
    url?: string;
  };
  error?: string;
}

export interface RuntimeStatus {
  configured_mode: "hybrid" | "service" | "desktop" | "dev" | string;
  active_mode: "hybrid" | "service" | "desktop" | "stopped" | "uninstalled" | "conflict" | string;
  label: string;
  message: string;
  conflict: boolean;
  service_installed: boolean;
  service_running: boolean;
  desktop_daemon_running: boolean;
  gui_start_at_login: boolean;
  close_to_tray: boolean;
  service_startup: string;
}

export interface CapabilityFunction {
  name: string;
  risk: string;
  effect: string;
  timeout_sec: number;
  description?: string;
  schema?: Record<string, unknown>;
}

export interface CapabilitySignal {
  name: string;
  ttl_sec: number;
  description?: string;
}

export interface PluginManifest {
  plugin_id: string;
  plugin_version: string;
  status: string;
  functions: CapabilityFunction[];
  signals: CapabilitySignal[];
}

export interface JobResult {
  job_id: string;
  invocation_id: string | null;
  function_name: string | null;
  status: string;
  result_json: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  reported_at: string | null;
}

export interface LocalStatus {
  configured: boolean;
  config_path: string;
  node_id: string | null;
  service: ServiceStatus;
  runtime?: RuntimeStatus;
  daemon_running: boolean;
  desktop_daemon_running?: boolean;
  center_connected: boolean;
  center_reachable: boolean;
  daemon_status: string;
  daemon_host?: string;
  last_heartbeat?: string | null;
  last_capability_register?: string | null;
  capability_count?: string | null;
  reconnect_attempts?: string;
  last_error?: string;
}

export interface ConfigData {
  raw: string;
  path: string;
}
