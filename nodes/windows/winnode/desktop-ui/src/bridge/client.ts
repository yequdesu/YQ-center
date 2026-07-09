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

function unavailable<T>(method: string): BridgeResponse<T> {
  return {
    ok: false,
    data: null,
    error: {
      code: "BRIDGE_UNAVAILABLE",
      message: `Desktop bridge is unavailable; cannot call ${method}.`,
    },
  };
}

export async function call<T>(method: string, ...args: unknown[]): Promise<BridgeResponse<T>> {
  const api = window.pywebview?.api as Record<string, ((...args: unknown[]) => Promise<BridgeResponse<T>>) | undefined> | undefined;
  if (!api) return unavailable<T>(method);
  const fn = api[method];
  if (!fn) {
    return {
      ok: false,
      data: null,
      error: {
        code: "UNKNOWN_METHOD",
        message: `Desktop bridge method ${method} is not available.`,
      },
    };
  }
  try {
    return await fn(...args);
  } catch (err) {
    return {
      ok: false,
      data: null,
      error: {
        code: "CALL_ERROR",
        message: err instanceof Error ? err.message : String(err),
      },
    };
  }
}
