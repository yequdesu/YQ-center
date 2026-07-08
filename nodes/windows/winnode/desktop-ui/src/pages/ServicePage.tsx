import { useEffect, useState, useCallback } from "react";
import { motion } from "framer-motion";
import { Play, Square, RotateCcw, Download, Trash2, ShieldAlert } from "lucide-react";
import { call } from "../bridge/client";
import type { RuntimeStatus, ServiceStatus } from "../bridge/types";
import { Button } from "../components/Button";
import { GlassPanel } from "../components/GlassPanel";
import { Badge } from "../components/Badge";
import { StatusDot } from "../components/StatusDot";
import { LoadingSkeleton } from "../components/LoadingSkeleton";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useToast } from "../components/Toast";
import { pageTransition } from "../components/motion";

type ServiceAccountMode = "Hybrid" | "LocalSystem" | "CurrentUser";

export default function ServicePage() {
  const [status, setStatus] = useState<ServiceStatus | null>(null);
  const [daemon, setDaemon] = useState<{
    daemon_running: boolean;
    center_connected: boolean;
    center_reachable: boolean;
    daemon_status: string;
    last_heartbeat?: string | null;
    last_capability_register?: string | null;
    capability_count?: string | null;
    reconnect_attempts?: string;
    last_error?: string;
  } | null>(null);
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState<string | null>(null);
  const [confirmUninstall, setConfirmUninstall] = useState(false);
  const [installDialogOpen, setInstallDialogOpen] = useState(false);
  const [installMode, setInstallMode] = useState<ServiceAccountMode>("Hybrid");
  const [error, setError] = useState<string | null>(null);
  const { toast } = useToast();

  const fetchStatus = useCallback(async () => {
    try {
      const [serviceRes, daemonRes, runtimeRes] = await Promise.all([
        call<ServiceStatus>("get_service_status"),
        call<{
          daemon_running: boolean;
          center_connected: boolean;
          center_reachable: boolean;
          daemon_status: string;
          last_heartbeat?: string | null;
          last_capability_register?: string | null;
          capability_count?: string | null;
          reconnect_attempts?: string;
          last_error?: string;
        }>("get_daemon_status"),
        call<RuntimeStatus>("get_runtime_status"),
      ]);
      if (serviceRes.ok && serviceRes.data) {
        setStatus(serviceRes.data as ServiceStatus);
        setError(null);
      } else {
        setError(serviceRes.error?.message || "Failed to fetch service status");
      }
      if (daemonRes.ok && daemonRes.data) {
        setDaemon(daemonRes.data);
      }
      if (runtimeRes.ok && runtimeRes.data) {
        setRuntime(runtimeRes.data);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  const doAction = async (method: string, label: string, ...args: unknown[]) => {
    setAction(label);
    try {
      const res = await call(method, ...args);
      if (res.ok) {
        toast(`${label} completed`, "success");
        window.dispatchEvent(new Event("yequ-status-refresh"));
        await fetchStatus();
      } else {
        toast(res.error?.message || `${label} failed`, "error");
      }
    } catch {
      toast(`${label} failed`, "error");
    } finally {
      setAction(null);
    }
  };

  const handleUninstall = async () => {
    setConfirmUninstall(false);
    await doAction("uninstall_service", "Uninstall");
  };

  const handleInstall = async () => {
    setInstallDialogOpen(false);
    await doAction("install_service", "Install", installMode);
  };

  if (loading) {
    return (
      <motion.div variants={pageTransition} initial="initial" animate="animate" className="p-5">
        <h1 className="text-[22px] font-semibold text-[var(--text)] mb-5">Service</h1>
        <GlassPanel><LoadingSkeleton /></GlassPanel>
      </motion.div>
    );
  }

  const serviceAccount = status?.service_account || "";
  const desktopDaemonAllowed = runtime?.configured_mode === "desktop" || runtime?.configured_mode === "dev";
  const desktopDaemonBlocked = !desktopDaemonAllowed || Boolean(status?.running);
  const showDaemonDiagnostics = Boolean(
    daemon?.daemon_running || daemon?.daemon_status === "error"
  );
  const developerDaemonHint = !desktopDaemonAllowed
    ? `Current runtime.mode is ${runtime?.configured_mode || "hybrid"}. Install the Windows Service for normal background operation, or switch Settings > Runtime Mode to desktop/dev for GUI-bound development.`
    : status?.running
      ? "Stop the Windows Service before starting Developer Local Daemon."
      : "";
  const isLocalSystem = serviceAccount.toLowerCase() === "localsystem";
  const worker = status?.user_worker;
  const hybridActive = Boolean(status?.installed && status.running && isLocalSystem && worker?.running);
  const systemOnly = Boolean(status?.installed && isLocalSystem && !worker?.running);
  const userServiceMode = Boolean(status?.installed && serviceAccount && !isLocalSystem);
  const runtimeMode = !status?.installed
    ? {
        label: "Not Installed",
        variant: "warning" as const,
        text: "Install the Windows service to keep the node online in the background.",
      }
    : hybridActive
    ? {
        label: "Hybrid Active",
        variant: "success" as const,
        text: "LocalSystem handles privileged maintenance; User Worker handles desktop, profile, mapped-drive, and user app state tools.",
      }
    : systemOnly
    ? {
        label: "System Service Only",
        variant: "warning" as const,
        text: "Privileged maintenance works, but user-profile tools need the User Worker.",
      }
    : userServiceMode
    ? {
        label: "User Service",
        variant: "info" as const,
        text: "The daemon runs as a user account. It can see user resources but may have less privilege for maintenance.",
      }
    : {
        label: "Partial",
        variant: "warning" as const,
        text: "The service is installed, but the runtime mode is incomplete.",
      };

  return (
    <motion.div variants={pageTransition} initial="initial" animate="animate" className="flex flex-col gap-5 h-full overflow-auto p-5">
      <h1 className="text-[22px] font-semibold text-[var(--text)]">Service</h1>

      {error && (
        <div className="bg-[var(--danger-bg)] text-[var(--danger)] p-3 rounded-sm text-sm">{error}</div>
      )}

      <GlassPanel strong title="Runtime Mode">
        <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={runtime ? (runtime.conflict ? "danger" : "info") : runtimeMode.variant}>
                {runtime?.label || runtimeMode.label}
              </Badge>
              {status?.running && <Badge variant="muted">Service Running</Badge>}
              {worker?.running && <Badge variant="muted">User Worker Running</Badge>}
            </div>
            <p className="max-w-3xl text-sm leading-6 text-[var(--text-muted)]">
              {runtime?.message || runtimeMode.text}
            </p>
          </div>
          <div className="grid min-w-[220px] gap-2 text-xs text-[var(--text-muted)]">
            <div className="flex justify-between gap-4">
              <span>Service</span>
              <span className="font-mono text-[var(--text)]">{serviceAccount || "--"}</span>
            </div>
            <div className="flex justify-between gap-4">
              <span>Configured</span>
              <span className="font-mono text-[var(--text)]">{runtime?.configured_mode || "--"}</span>
            </div>
            <div className="flex justify-between gap-4">
              <span>User Worker</span>
              <span className="font-mono text-[var(--text)]">
                {worker?.running ? worker.user || "running" : worker?.installed ? worker.task_state : "not installed"}
              </span>
            </div>
          </div>
        </div>
      </GlassPanel>

      {/* Status */}
      <GlassPanel title="Developer Local Daemon">
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between py-2 border-b border-[var(--border)]">
              <span className="text-sm text-[var(--text-muted)]">Desktop Host</span>
            <div className="flex items-center gap-2">
              <StatusDot status={daemon?.daemon_running ? "online" : "offline"} />
              <Badge variant={daemon?.daemon_running ? "success" : "warning"}>
                {daemon?.daemon_status || "unknown"}
              </Badge>
            </div>
          </div>
          <div className="flex items-center justify-between py-2 border-b border-[var(--border)]">
            <span className="text-sm text-[var(--text-muted)]">Center</span>
            <Badge variant={daemon?.center_connected ? "success" : daemon?.center_reachable ? "info" : "warning"}>
              {daemon?.center_connected ? "Connected" : daemon?.center_reachable ? "Reachable" : "Unreachable"}
            </Badge>
          </div>
          <div className="flex items-center justify-between py-2 border-b border-[var(--border)]">
            <span className="text-sm text-[var(--text-muted)]">Last Heartbeat</span>
            <span className="text-sm font-mono text-[var(--text)]">{daemon?.last_heartbeat || "--"}</span>
          </div>
          <div className="flex items-center justify-between py-2 border-b border-[var(--border)]">
            <span className="text-sm text-[var(--text-muted)]">Capabilities</span>
            <span className="text-sm font-mono text-[var(--text)]">
              {daemon?.last_capability_register
                ? `${daemon.capability_count || "?"} @ ${daemon.last_capability_register}`
                : "--"}
            </span>
          </div>
          {showDaemonDiagnostics && daemon?.reconnect_attempts && daemon.reconnect_attempts !== "0" && (
            <div className="flex items-center justify-between py-2 border-b border-[var(--border)]">
              <span className="text-sm text-[var(--text-muted)]">Reconnect Attempts</span>
              <Badge variant="warning">{daemon.reconnect_attempts}</Badge>
            </div>
          )}
          {showDaemonDiagnostics && daemon?.last_error && (
            <div className="rounded-sm border border-[var(--danger)]/20 bg-[var(--danger-bg)] p-3 text-xs text-[var(--danger)]">
              {daemon.last_error}
            </div>
          )}
          <div className="flex flex-wrap gap-2 pt-1">
            {!daemon?.daemon_running ? (
              <Button variant="primary" onClick={() => doAction("start_daemon", "Start Developer Daemon")} loading={action === "Start Developer Daemon"} disabled={desktopDaemonBlocked}>
                <Play size={14} /> Start Developer Daemon
              </Button>
            ) : (
              <Button variant="secondary" onClick={() => doAction("stop_daemon", "Stop Developer Daemon")} loading={action === "Stop Developer Daemon"}>
                <Square size={14} /> Stop Developer Daemon
              </Button>
            )}
          </div>
          {desktopDaemonBlocked && !daemon?.daemon_running && (
            <div className="rounded-sm border border-[var(--warning)]/25 bg-[var(--warning-bg)] p-3 text-xs text-[var(--warning)]">
              {developerDaemonHint}
            </div>
          )}
        </div>
      </GlassPanel>

      <GlassPanel title="Service Status">
        {status?.installed ? (
          <div className="flex flex-col gap-3">
            <div className="flex items-center justify-between py-2 border-b border-[var(--border)]">
              <span className="text-sm text-[var(--text-muted)]">Status</span>
              <div className="flex items-center gap-2">
                <StatusDot status={status.running ? "online" : "offline"} />
                <Badge variant={status.running ? "success" : "warning"}>
                  {status.running ? "Running" : "Stopped"}
                </Badge>
              </div>
            </div>
            <div className="flex items-center justify-between py-2 border-b border-[var(--border)]">
              <span className="text-sm text-[var(--text-muted)]">Startup Type</span>
              <span className="text-sm text-[var(--text)]">{status.startup_type}</span>
            </div>
            <div className="flex items-center justify-between gap-4 py-2 border-b border-[var(--border)]">
              <span className="text-sm text-[var(--text-muted)]">Service Account</span>
              <span className="text-sm font-mono text-[var(--text)] text-right break-all">
                {status.service_account || "--"}
              </span>
            </div>
            <div className="flex items-center justify-between py-2 border-b border-[var(--border)]">
              <span className="text-sm text-[var(--text-muted)]">Display Name</span>
              <span className="text-sm text-[var(--text)]">{status.display_name}</span>
            </div>
            <div className="flex items-center justify-between gap-4 py-2 border-b border-[var(--border)]">
              <span className="text-sm text-[var(--text-muted)]">User Worker</span>
              <div className="flex items-center gap-2">
                <StatusDot status={status.user_worker?.running ? "online" : "offline"} />
                <Badge variant={status.user_worker?.running ? "success" : "warning"}>
                  {status.user_worker?.running
                    ? "Running"
                    : status.user_worker?.installed
                    ? status.user_worker.task_state
                    : "Not Installed"}
                </Badge>
                {status.user_worker?.user && (
                  <span className="text-xs font-mono text-[var(--text-muted)]">{status.user_worker.user}</span>
                )}
              </div>
            </div>
            {systemOnly && (
              <div className="rounded-sm border border-[var(--warning)]/25 bg-[var(--warning-bg)] p-3 text-xs text-[var(--warning)]">
                This is System Service only. Install the User Worker to complete Hybrid mode and expose user profile paths, mapped drives, and per-user app state.
              </div>
            )}
            {hybridActive && (
              <div className="rounded-sm border border-[var(--success)]/20 bg-[var(--success-bg)] p-3 text-xs text-[var(--success)]">
                Hybrid mode is active: LocalSystem handles privileged maintenance, and the User Worker handles user-context tools.
              </div>
            )}
          </div>
        ) : (
          <div className="flex flex-col items-center gap-3 py-8">
            <ShieldAlert size={28} className="text-[var(--text-subtle)]" />
            <span className="text-sm text-[var(--text-muted)]">Service not installed</span>
            <span className="text-xs text-[var(--text-subtle)]">Click "Install Service" below to set up the Windows service.</span>
          </div>
        )}
      </GlassPanel>

      {/* Actions */}
      <GlassPanel title="Actions">
        <div className="flex flex-wrap gap-2">
          {!status?.installed ? (
            <Button variant="primary" onClick={() => setInstallDialogOpen(true)} loading={action === "Install"}>
              <Download size={14} /> Install Service
            </Button>
          ) : (
            <>
              {!status.running ? (
                <Button variant="primary" onClick={() => doAction("start_service", "Start")} loading={action === "Start"}>
                  <Play size={14} /> Start
                </Button>
              ) : (
                <>
                  <Button variant="secondary" onClick={() => doAction("stop_service", "Stop")} loading={action === "Stop"}>
                    <Square size={14} /> Stop
                  </Button>
                  <Button variant="secondary" onClick={() => doAction("restart_service", "Restart")} loading={action === "Restart"}>
                    <RotateCcw size={14} /> Restart
                  </Button>
                </>
              )}
              <Button variant="danger" onClick={() => setConfirmUninstall(true)} loading={action === "Uninstall"}>
                <Trash2 size={14} /> Uninstall Service
              </Button>
              {status.service_account === "LocalSystem" && !status.user_worker?.installed && (
                <Button
                  variant="secondary"
                  onClick={() => doAction("install_user_worker", "Install User Worker")}
                  loading={action === "Install User Worker"}
                >
                  <Download size={14} /> Install User Worker
                </Button>
              )}
              {status.service_account === "LocalSystem" && status.user_worker?.installed && !status.user_worker?.running && (
                <Button
                  variant="secondary"
                  onClick={() => doAction("start_user_worker", "Start User Worker")}
                  loading={action === "Start User Worker"}
                >
                  <Play size={14} /> Start User Worker
                </Button>
              )}
              {status.service_account === "LocalSystem" && status.user_worker?.running && (
                <Button
                  variant="secondary"
                  onClick={() => doAction("stop_user_worker", "Stop User Worker")}
                  loading={action === "Stop User Worker"}
                >
                  <Square size={14} /> Stop User Worker
                </Button>
              )}
            </>
          )}
        </div>
      </GlassPanel>

      <ConfirmDialog
        open={confirmUninstall}
        title="Uninstall Service"
        message="Are you sure you want to uninstall the YeQuWinClient service?"
        confirmLabel="Uninstall"
        variant="danger"
        onConfirm={handleUninstall}
        onCancel={() => setConfirmUninstall(false)}
      />

      <AnimateInstallDialog
        open={installDialogOpen}
        mode={installMode}
        busy={action === "Install"}
        onModeChange={setInstallMode}
        onConfirm={handleInstall}
        onCancel={() => setInstallDialogOpen(false)}
      />
    </motion.div>
  );
}

function AnimateInstallDialog({
  open,
  mode,
  busy,
  onModeChange,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  mode: ServiceAccountMode;
  busy: boolean;
  onModeChange: (mode: ServiceAccountMode) => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="absolute inset-0 bg-black/20 backdrop-blur-sm"
        onClick={onCancel}
      />
      <motion.div
        initial={{ opacity: 0, scale: 0.96, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96, y: 8 }}
        transition={{ type: "spring", stiffness: 500, damping: 32 }}
        className="relative glass rounded-lg p-6 max-w-xl w-full mx-4"
      >
        <h3 className="text-base font-semibold text-[var(--text)] mb-2">Install Windows Service</h3>
        <p className="text-sm text-[var(--text-muted)] mb-5">
          Choose the account that should run the daemon. This controls which local files, drives, and user-scoped app state tools can see.
        </p>

        <div className="grid gap-3 mb-5">
          <AccountOption
            selected={mode === "Hybrid"}
            title="Hybrid (Recommended)"
            subtitle="Installs the LocalSystem service for privileged maintenance and a per-user worker for profile folders, mapped drives, and user app state."
            onClick={() => onModeChange("Hybrid")}
          />
          <AccountOption
            selected={mode === "LocalSystem"}
            title="LocalSystem"
            subtitle="Best for always-on privileged maintenance. User profile paths, mapped drives, and per-user app state may be unavailable."
            onClick={() => onModeChange("LocalSystem")}
          />
          <AccountOption
            selected={mode === "CurrentUser"}
            title="Current Windows User"
            subtitle="Best when tools need your profile folders and user-owned files. UAC will open and Windows will ask for your account password."
            onClick={() => onModeChange("CurrentUser")}
          />
        </div>

        {mode === "CurrentUser" && (
          <div className="rounded-sm border border-[var(--warning)]/25 bg-[var(--warning-bg)] p-3 text-xs text-[var(--warning)] mb-5">
            Windows may require this account to have the "Log on as a service" right. If service start fails with a logon error, grant that right and reinstall.
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" onClick={onConfirm} loading={busy}>
            Install
          </Button>
        </div>
      </motion.div>
    </div>
  );
}

function AccountOption({
  selected,
  title,
  subtitle,
  onClick,
}: {
  selected: boolean;
  title: string;
  subtitle: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full rounded-lg border p-4 text-left transition-all ${
        selected
          ? "border-[var(--accent)] bg-[var(--bg-subtle)] shadow-sm"
          : "border-[var(--border)] bg-[var(--surface-solid)] hover:bg-[var(--bg-subtle)]"
      }`}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="font-medium text-sm text-[var(--text)]">{title}</div>
        <div className={`h-3 w-3 rounded-full ${selected ? "bg-[var(--accent)]" : "bg-[var(--border)]"}`} />
      </div>
      <div className="mt-1 text-xs leading-5 text-[var(--text-muted)]">{subtitle}</div>
    </button>
  );
}
