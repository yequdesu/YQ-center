import type { BridgeResponse } from "./types";

declare global {
  interface Window {
    pywebview?: {
      api: {
        get_config: () => Promise<BridgeResponse>;
        save_config: (text: string) => Promise<BridgeResponse>;
        validate_config: (text?: string) => Promise<BridgeResponse>;
        get_local_status: () => Promise<BridgeResponse>;
        get_runtime_status: () => Promise<BridgeResponse>;
        get_service_status: () => Promise<BridgeResponse>;
        get_gui_autostart: () => Promise<BridgeResponse>;
        set_gui_autostart: (enabled: boolean) => Promise<BridgeResponse>;
        install_service: (accountMode?: string) => Promise<BridgeResponse>;
        install_user_worker: () => Promise<BridgeResponse>;
        start_user_worker: () => Promise<BridgeResponse>;
        stop_user_worker: () => Promise<BridgeResponse>;
        uninstall_service: () => Promise<BridgeResponse>;
        start_service: () => Promise<BridgeResponse>;
        stop_service: () => Promise<BridgeResponse>;
        restart_service: () => Promise<BridgeResponse>;
        start_daemon: () => Promise<BridgeResponse>;
        stop_daemon: () => Promise<BridgeResponse>;
        get_capabilities: () => Promise<BridgeResponse>;
        get_recent_jobs: (limit?: number) => Promise<BridgeResponse>;
        retry_unreported_jobs: () => Promise<BridgeResponse>;
        flush_reported_cache: () => Promise<BridgeResponse>;
        get_logs: (limit?: number, level?: string, query?: string) => Promise<BridgeResponse>;
        export_diagnostics: () => Promise<BridgeResponse>;
        open_path: (path: string) => Promise<BridgeResponse>;
        test_center_health: () => Promise<BridgeResponse>;
      };
    };
  }
}

function getApi() {
  if (window.pywebview) {
    return window.pywebview.api;
  }
  return null;
}

function mock<T>(data: T): Promise<BridgeResponse<T>> {
  return Promise.resolve({ ok: true, data, error: null });
}

function mockError(code: string, message: string): Promise<BridgeResponse> {
  return Promise.resolve({ ok: false, data: null, error: { code, message } });
}

const API_DELAY = 50;

export async function call<T>(method: string, ...args: unknown[]): Promise<BridgeResponse<T>> {
  const api = getApi();
  if (api) {
    try {
      const fn = (api as Record<string, Function>)[method];
      const result = await fn(...args);
      return result as BridgeResponse<T>;
    } catch (err) {
      return { ok: false, data: null, error: { code: "CALL_ERROR", message: String(err) } };
    }
  }
  await new Promise((r) => setTimeout(r, API_DELAY));
  const store = getMockStore();
  const mocks: Record<string, (args: unknown[]) => Promise<BridgeResponse<unknown>>> = {
    get_config: async () => mock({ raw: store.configText, path: store.configPath }),
    save_config: async ([text]: unknown[]) => {
      store.configText = text as string;
      return mock({ path: store.configPath });
    },
    validate_config: async () => mock({ valid: true }),
    get_local_status: async () =>
      mock({
        configured: true,
        config_path: store.configPath,
        node_id: "winClient",
        service: store.serviceStatus,
        daemon_running: true,
        desktop_daemon_running: false,
        runtime: getRuntimeStatus(store),
        center_connected: true,
        center_reachable: true,
        daemon_status: "running",
        daemon_host: "service",
      }),
    get_runtime_status: async () => mock(getRuntimeStatus(store)),
    get_gui_autostart: async () => mock({ enabled: store.guiAutostart }),
    set_gui_autostart: async ([enabled]: unknown[]) => {
      store.guiAutostart = Boolean(enabled);
      return mock({ enabled: store.guiAutostart });
    },
    get_service_status: async () => mock(store.serviceStatus),
    install_service: async ([accountMode]: unknown[]) => {
      store.serviceStatus.installed = true;
      store.serviceStatus.service_account = accountMode === "CurrentUser" ? "DESKTOP-8SQBU8K\\YeQuDesu" : "LocalSystem";
      store.serviceStatus.user_worker = {
        installed: accountMode === "Hybrid",
        task_state: accountMode === "Hybrid" ? "Ready" : "NotInstalled",
        running: accountMode === "Hybrid",
        reachable: accountMode === "Hybrid",
        user: accountMode === "Hybrid" ? "YeQuDesu" : null,
        pid: accountMode === "Hybrid" ? 12345 : null,
        url: "http://127.0.0.1:9817/health",
      };
      return mock(null);
    },
    install_user_worker: async () => {
      store.serviceStatus.user_worker = {
        installed: true,
        task_state: "Ready",
        running: true,
        reachable: true,
        user: "YeQuDesu",
        pid: 12345,
        url: "http://127.0.0.1:9817/health",
      };
      return mock(null);
    },
    start_user_worker: async () => {
      if (store.serviceStatus.user_worker) {
        store.serviceStatus.user_worker.task_state = "Running";
        store.serviceStatus.user_worker.running = true;
        store.serviceStatus.user_worker.reachable = true;
      }
      return mock(null);
    },
    stop_user_worker: async () => {
      if (store.serviceStatus.user_worker) {
        store.serviceStatus.user_worker.task_state = "Ready";
        store.serviceStatus.user_worker.running = false;
        store.serviceStatus.user_worker.reachable = false;
      }
      return mock(null);
    },
    uninstall_service: async () => {
      store.serviceStatus.installed = false;
      store.serviceStatus.running = false;
      return mock(null);
    },
    start_service: async () => {
      store.serviceStatus.running = true;
      return mock(null);
    },
    stop_service: async () => {
      store.serviceStatus.running = false;
      return mock(null);
    },
    restart_service: async () => mock(null),
    start_daemon: async () => mock({
      daemon_running: true,
      center_connected: true,
      center_reachable: true,
      daemon_status: "running",
      last_heartbeat: new Date().toISOString(),
    }),
    stop_daemon: async () => mock({
      daemon_running: false,
      center_connected: false,
      center_reachable: true,
      daemon_status: "stopped",
      last_heartbeat: new Date().toISOString(),
    }),
    get_capabilities: async () => mock(store.capabilities),
    get_recent_jobs: async () => mock({ jobs: store.jobs, unreported_count: 0 }),
    retry_unreported_jobs: async () => mock({ sent: 0, failed: 0 }),
    flush_reported_cache: async () => mock({ flushed: 0 }),
    get_logs: async () => mock({ lines: store.logs, total: store.logs.length, shown: store.logs.length }),
    export_diagnostics: async () => mock({ path: "data/diagnostics/yequ-win-client-diagnostics-test.zip" }),
    open_path: async () => mock(null),
    test_center_health: async () => mock({ reachable: true, status_code: 200 }),
  };
  const fn = mocks[method];
  if (fn) return fn(args) as Promise<BridgeResponse<T>>;
  return mockError("UNKNOWN_METHOD", `Method ${method} not available`) as Promise<BridgeResponse<T>>;
}

function getMockStore() {
  if (!(window as unknown as Record<string, unknown>).__mockStore) {
    const store = createMockStore();
    (window as unknown as Record<string, unknown>).__mockStore = store;
  }
  return (window as unknown as Record<string, unknown>).__mockStore as ReturnType<typeof createMockStore>;
}

function createMockStore() {
  return {
    configPath: "config.local.yaml",
    configText: `center:
  base_url: "https://gtw.yequdesu.top"
  yqp_path: "/yqp/"
  timeout_sec: 30

node:
  node_id: "winClient"
  node_name: "Windows Client"
  token: "winc-token-4a7f3c9e1b2d8f6c"
  role:
    - "compute"
  locality: "lan"

daemon:
  heartbeat_interval_sec: 10
  signal_report_interval_sec: 5
  job_poll_interval_sec: 3
  max_concurrent_jobs: 4

paths:
  data_dir: "./data"
  log_dir: "./logs"

runtime:
  mode: "hybrid"
  gui_start_at_login: false
  close_to_tray: true
  service_startup: "automatic"

safety:
  allow_write_actions: true
  allowed_services:
    - "Spooler"
    - "wuauserv"
    - "WinDefend"
    - "EventLog"

cache:
  job_result_ttl_hours: 24
`,
    guiAutostart: false,
    serviceStatus: {
      installed: false,
      running: false,
      startup_type: "N/A",
      display_name: "YeQu Windows Client",
      service_account: "",
      user_worker: {
        installed: false,
        task_state: "NotInstalled",
        running: false,
        reachable: false,
      },
    },
    capabilities: {
      plugin_id: "windows.capabilities",
      plugin_version: "0.6.0",
      status: "loaded",
      functions: [
        { name: "windows.artifact.download_file", risk: "maintenance", effect: "write", timeout_sec: 120, description: "Download a Center artifact to Windows" },
        { name: "windows.everything.find", risk: "safe", effect: "read", timeout_sec: 20, description: "Search files through Everything" },
        { name: "windows.exec.run", risk: "safe", effect: "read", timeout_sec: 30, description: "Run a controlled Windows command string" },
        { name: "windows.file.upload_artifact", risk: "safe", effect: "read", timeout_sec: 120, description: "Upload a Windows file as a Center artifact" },
        { name: "windows.screen.capture", risk: "safe", effect: "read", timeout_sec: 15, description: "Capture screen artifact" },
        { name: "windows.transfer.croc.receive", risk: "maintenance", effect: "external", timeout_sec: 3600, description: "Receive a yq-croc transfer" },
        { name: "windows.transfer.croc.reconcile", risk: "safe", effect: "read", timeout_sec: 20, description: "Inspect yq-croc transfer ledger" },
        { name: "windows.transfer.croc.send", risk: "maintenance", effect: "external", timeout_sec: 3600, description: "Send a yq-croc transfer" },
        { name: "windows.transfer.croc.status", risk: "safe", effect: "read", timeout_sec: 10, description: "Check yq-croc runtime status" },
        { name: "windows.transfer.local.stat", risk: "safe", effect: "read", timeout_sec: 30, description: "Stat a local transfer file" },
      ],
      signals: [
        { name: "windows.cpu.usage", ttl_sec: 15, description: "CPU usage percentage" },
        { name: "windows.memory.usage", ttl_sec: 15, description: "Memory usage percentage" },
        { name: "windows.disk.usage", ttl_sec: 30, description: "Disk usage percentage" },
      ],
    },
    jobs: [
      { job_id: "job_001", invocation_id: null, function_name: "windows.exec.run", status: "succeeded", result_json: null, error_code: null, error_message: null, created_at: "2026-06-21T12:00:00Z", reported_at: "2026-06-21T12:00:01Z" },
      { job_id: "job_002", invocation_id: null, function_name: "windows.transfer.croc.status", status: "succeeded", result_json: null, error_code: null, error_message: null, created_at: "2026-06-21T12:01:00Z", reported_at: "2026-06-21T12:01:01Z" },
      { job_id: "job_003", invocation_id: null, function_name: "windows.everything.find", status: "succeeded", result_json: null, error_code: null, error_message: null, created_at: "2026-06-21T12:02:00Z", reported_at: "2026-06-21T12:02:01Z" },
    ],
    logs: [
      "2026-06-21T12:00:00 [INFO] yequ-win-client: daemon started",
      "2026-06-21T12:00:01 [INFO] yequ-win-client: loaded config: config.local.yaml",
      "2026-06-21T12:00:01 [INFO] yequ-win-client: sending node.hello",
      "2026-06-21T12:00:02 [INFO] yequ-win-client: node.accepted - heartbeat=10s signal=5s job_poll=3s",
      "2026-06-21T12:00:02 [INFO] yequ-win-client: registering capabilities - 1 plugin",
      "2026-06-21T12:00:02 [INFO] yequ-win-client: registry.accepted",
      "2026-06-21T12:00:05 [DEBUG] yequ-win-client.daemon: heartbeat sent - uptime=5s running_jobs=0",
      "2026-06-21T12:00:10 [DEBUG] yequ-win-client.daemon: heartbeat sent - uptime=10s running_jobs=0",
      "2026-06-21T12:00:15 [DEBUG] yequ-win-client.daemon: heartbeat sent - uptime=15s running_jobs=0",
    ],
  };
}

function getRuntimeStatus(store: ReturnType<typeof createMockStore>) {
  const workerRunning = Boolean(store.serviceStatus.user_worker?.running);
  const serviceRunning = Boolean(store.serviceStatus.running);
  const serviceInstalled = Boolean(store.serviceStatus.installed);
  const activeMode = serviceRunning && workerRunning
    ? "hybrid"
    : serviceRunning
      ? "service"
      : serviceInstalled
        ? "stopped"
        : "uninstalled";
  return {
    configured_mode: "hybrid",
    active_mode: activeMode,
    label: activeMode === "hybrid" ? "Hybrid" : activeMode === "service" ? "Service" : activeMode === "stopped" ? "Stopped" : "Not Installed",
    message: activeMode === "hybrid"
      ? "Service is online and User Worker is available."
      : activeMode === "service"
        ? "Windows Service is the active daemon host."
        : activeMode === "stopped"
          ? "Windows Service is installed but not running."
          : "Install the Windows Service or switch runtime.mode to desktop/dev.",
    conflict: false,
    service_installed: serviceInstalled,
    service_running: serviceRunning,
    desktop_daemon_running: false,
    gui_start_at_login: store.guiAutostart,
    close_to_tray: true,
    service_startup: "automatic",
  };
}
