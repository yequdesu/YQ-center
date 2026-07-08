import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Save } from "lucide-react";
import { call } from "../bridge/client";
import { Button } from "../components/Button";
import { TextField } from "../components/TextField";
import { GlassPanel } from "../components/GlassPanel";
import { LoadingSkeleton } from "../components/LoadingSkeleton";
import { useToast } from "../components/Toast";
import { pageTransition } from "../components/motion";

interface SettingsFields {
  runtimeMode: string;
  guiStartAtLogin: boolean;
  closeToTray: boolean;
  heartbeatInterval: string;
  signalInterval: string;
  pollInterval: string;
  maxConcurrentJobs: string;
  reconnectInit: string;
  reconnectMax: string;
  reconnectReady: string;
  reconnectProbe: string;
  cacheTtl: string;
  allowWrite: boolean;
  allowedServices: string;
  logLevel: string;
}

const defaults: SettingsFields = {
  runtimeMode: "hybrid",
  guiStartAtLogin: false,
  closeToTray: true,
  heartbeatInterval: "10",
  signalInterval: "5",
  pollInterval: "3",
  maxConcurrentJobs: "4",
  reconnectInit: "2",
  reconnectMax: "60",
  reconnectReady: "5",
  reconnectProbe: "2",
  cacheTtl: "24",
  allowWrite: true,
  allowedServices: "Spooler\nwuauserv\nWinDefend\nEventLog",
  logLevel: "INFO",
};

function buildYaml(f: SettingsFields): string {
  const svcs = f.allowedServices.split("\n").map(s => s.trim()).filter(Boolean);
  return [
    "runtime:",
    `  mode: "${f.runtimeMode}"`,
    `  gui_start_at_login: ${f.guiStartAtLogin ? "true" : "false"}`,
    `  close_to_tray: ${f.closeToTray ? "true" : "false"}`,
    `  service_startup: "automatic"`,
    "",
    "daemon:",
    `  heartbeat_interval_sec: ${f.heartbeatInterval}`,
    `  signal_report_interval_sec: ${f.signalInterval}`,
    `  job_poll_interval_sec: ${f.pollInterval}`,
    `  max_concurrent_jobs: ${f.maxConcurrentJobs}`,
    `  reconnect_initial_delay_sec: ${f.reconnectInit}`,
    `  reconnect_max_delay_sec: ${f.reconnectMax}`,
    `  reconnect_ready_delay_sec: ${f.reconnectReady}`,
    `  reconnect_probe_interval_sec: ${f.reconnectProbe}`,
    "",
    "cache:",
    `  job_result_ttl_hours: ${f.cacheTtl}`,
    "",
    "safety:",
    `  allow_write_actions: ${f.allowWrite ? "true" : "false"}`,
    "  allowed_services:",
    ...svcs.map(s => `    - "${s}"`),
  ].join("\n");
}

export default function SettingsPage() {
  const [fields, setFields] = useState<SettingsFields>(defaults);
  const [rawConfig, setRawConfig] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const { toast } = useToast();

  useEffect(() => {
    (async () => {
      try {
        const [res, autostartRes] = await Promise.all([
          call<{ raw: string }>("get_config"),
          call<{ enabled: boolean }>("get_gui_autostart"),
        ]);
        if (res.ok && res.data) {
          const raw = res.data.raw;
          setRawConfig(raw);
          setFields({
            runtimeMode: extract(raw, "mode") || "hybrid",
            guiStartAtLogin: autostartRes.ok && autostartRes.data ? autostartRes.data.enabled : raw.includes("gui_start_at_login: true"),
            closeToTray: !raw.includes("close_to_tray: false"),
            heartbeatInterval: extract(raw, "heartbeat_interval_sec") || "10",
            signalInterval: extract(raw, "signal_report_interval_sec") || "5",
            pollInterval: extract(raw, "job_poll_interval_sec") || "3",
            maxConcurrentJobs: extract(raw, "max_concurrent_jobs") || "4",
            reconnectInit: extract(raw, "reconnect_initial_delay_sec") || "2",
            reconnectMax: extract(raw, "reconnect_max_delay_sec") || "60",
            reconnectReady: extract(raw, "reconnect_ready_delay_sec") || "5",
            reconnectProbe: extract(raw, "reconnect_probe_interval_sec") || "2",
            cacheTtl: extract(raw, "job_result_ttl_hours") || "24",
            allowWrite: raw.includes("allow_write_actions: true"),
            allowedServices: defaults.allowedServices,
            logLevel: extract(raw, "log_level") || "INFO",
          });
        }
      } catch { /* defaults */}
      finally { setLoading(false); }
    })();
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      const yaml = buildYaml(fields);
      const res = await call("save_config", mergeSettingsYaml(rawConfig, yaml));
      if (res.ok) {
        await call("set_gui_autostart", fields.guiStartAtLogin);
        setRawConfig(mergeSettingsYaml(rawConfig, yaml));
        toast("Settings saved. Restart service for changes to take effect.", "success");
      } else {
        toast(res.error?.message || "Save failed", "error");
      }
    } catch {
      toast("Failed to save", "error");
    } finally {
      setSaving(false);
    }
  };

  const set = (key: keyof SettingsFields) => (v: string) => setFields(p => ({ ...p, [key]: v }));

  if (loading) return (
    <motion.div variants={pageTransition} initial="initial" animate="animate" className="p-5 max-w-[640px]">
      <h1 className="text-[22px] font-semibold text-[var(--text)] mb-5">Settings</h1>
      <GlassPanel><LoadingSkeleton /></GlassPanel>
    </motion.div>
  );

  return (
    <motion.div variants={pageTransition} initial="initial" animate="animate" className="flex flex-col gap-5 h-full overflow-auto p-5 max-w-[640px]">
      <div className="flex items-center justify-between">
        <h1 className="text-[22px] font-semibold text-[var(--text)]">Settings</h1>
        <Button variant="primary" onClick={handleSave} loading={saving}>
          <Save size={14} /> Save Settings
        </Button>
      </div>

      <GlassPanel title="Runtime Mode">
        <div className="grid gap-4">
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-[var(--text-muted)]">Mode</span>
            <select value={fields.runtimeMode} onChange={(e) => setFields(p => ({ ...p, runtimeMode: e.target.value }))}
              className="px-3 py-2 text-sm rounded-sm border border-[var(--border)] bg-[var(--surface-solid)] text-[var(--text)] outline-none focus:border-[var(--accent)]">
              <option value="hybrid">Hybrid</option>
              <option value="service">Service</option>
              <option value="desktop">Desktop</option>
              <option value="dev">Dev</option>
            </select>
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="flex items-center justify-between rounded-sm border border-[var(--border)] bg-[var(--surface-solid)] px-3 py-2">
              <span className="text-sm text-[var(--text)]">Start GUI at login</span>
              <input type="checkbox" checked={fields.guiStartAtLogin} onChange={(e) => setFields(p => ({ ...p, guiStartAtLogin: e.target.checked }))}
                className="w-[18px] h-[18px] accent-[var(--accent)] cursor-pointer" />
            </label>
            <label className="flex items-center justify-between rounded-sm border border-[var(--border)] bg-[var(--surface-solid)] px-3 py-2">
              <span className="text-sm text-[var(--text)]">Close to tray</span>
              <input type="checkbox" checked={fields.closeToTray} onChange={(e) => setFields(p => ({ ...p, closeToTray: e.target.checked }))}
                className="w-[18px] h-[18px] accent-[var(--accent)] cursor-pointer" />
            </label>
          </div>
        </div>
      </GlassPanel>

      <GlassPanel title="Daemon Intervals">
        <div className="grid grid-cols-4 gap-3">
          <TextField label="Heartbeat (s)" value={fields.heartbeatInterval} onChange={set("heartbeatInterval")} />
          <TextField label="Signal (s)" value={fields.signalInterval} onChange={set("signalInterval")} />
          <TextField label="Poll (s)" value={fields.pollInterval} onChange={set("pollInterval")} />
          <TextField label="Max Jobs" value={fields.maxConcurrentJobs} onChange={set("maxConcurrentJobs")} />
        </div>
      </GlassPanel>

      <GlassPanel title="Reconnect Policy">
        <div className="grid grid-cols-4 gap-3">
          <TextField label="Initial Delay (s)" value={fields.reconnectInit} onChange={set("reconnectInit")} />
          <TextField label="Max Delay (s)" value={fields.reconnectMax} onChange={set("reconnectMax")} />
          <TextField label="Ready Delay (s)" value={fields.reconnectReady} onChange={set("reconnectReady")} />
          <TextField label="Probe (s)" value={fields.reconnectProbe} onChange={set("reconnectProbe")} />
        </div>
      </GlassPanel>

      <GlassPanel title="Cache">
        <TextField label="Job Result TTL (hours)" value={fields.cacheTtl} onChange={set("cacheTtl")} />
      </GlassPanel>

      <GlassPanel title="Safety">
        <label className="flex items-center justify-between py-1 cursor-pointer mb-3">
          <span className="text-sm text-[var(--text)]">Allow Write Actions</span>
          <input type="checkbox" checked={fields.allowWrite} onChange={(e) => setFields(p => ({ ...p, allowWrite: e.target.checked }))}
            className="w-[18px] h-[18px] accent-[var(--accent)] cursor-pointer" />
        </label>
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-[var(--text-muted)]">Allowed Services (one per line)</label>
          <textarea value={fields.allowedServices} onChange={(e) => set("allowedServices")(e.target.value)} rows={4}
            className="px-3 py-2 text-sm rounded-sm border border-[var(--border)] bg-[var(--surface-solid)] text-[var(--text)] outline-none focus:border-[var(--accent)] resize-y" />
        </div>
      </GlassPanel>

      <GlassPanel title="Logging">
        <select value={fields.logLevel} onChange={(e) => setFields(p => ({ ...p, logLevel: e.target.value }))}
          className="px-3 py-2 text-sm rounded-sm border border-[var(--border)] bg-[var(--surface-solid)] text-[var(--text)] outline-none focus:border-[var(--accent)]">
          <option value="DEBUG">DEBUG</option>
          <option value="INFO">INFO</option>
          <option value="WARN">WARN</option>
          <option value="ERROR">ERROR</option>
        </select>
      </GlassPanel>
    </motion.div>
  );
}

function extract(raw: string, key: string): string | null {
  const m = raw.match(new RegExp(`${key}:\\s*"?([^"\\n\\r]+)"?`, "i"));
  return m ? m[1].trim() : null;
}

function mergeSettingsYaml(raw: string, settingsYaml: string): string {
  const replacementKeys = new Set(["runtime", "daemon", "cache", "safety"]);
  const output: string[] = [];
  let skipping = false;

  for (const line of raw.split(/\r?\n/)) {
    const topLevel = line.match(/^([A-Za-z_][\w-]*):(?:\s.*)?$/);
    if (topLevel) {
      skipping = replacementKeys.has(topLevel[1]);
      if (!skipping) output.push(line);
      continue;
    }
    if (!skipping) output.push(line);
  }

  const preserved = output.join("\n").trimEnd();
  return [preserved, settingsYaml.trim()].filter(Boolean).join("\n\n") + "\n";
}
