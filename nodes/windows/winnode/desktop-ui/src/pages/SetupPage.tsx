import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import { Wifi, Check, Save, AlertCircle } from "lucide-react";
import { call } from "../bridge/client";
import { Button } from "../components/Button";
import { TextField, PasswordField } from "../components/TextField";
import { GlassPanel } from "../components/GlassPanel";
import { useToast } from "../components/Toast";
import { pageTransition } from "../components/motion";

interface Fields {
  centerUrl: string;
  yqpPath: string;
  nodeId: string;
  nodeName: string;
  nodeToken: string;
  allowWrite: boolean;
  allowedServices: string;
}

const empty: Fields = {
  centerUrl: "https://gtw.yequdesu.top",
  yqpPath: "/yqp/",
  nodeId: "winClient",
  nodeName: "Windows Client",
  nodeToken: "",
  allowWrite: true,
  allowedServices: "Spooler\nwuauserv\nWinDefend\nEventLog",
};

function buildConfigYaml(f: Fields): string {
  const svcs = f.allowedServices.split("\n").map((s) => s.trim()).filter(Boolean);
  const svcLines = svcs.length > 0 ? svcs.map((s) => "    - \"" + s + "\"") : ["    - []"];
  const lines = [
    "center:",
    "  base_url: \"" + f.centerUrl + "\"",
    "  yqp_path: \"" + f.yqpPath + "\"",
    "  timeout_sec: 30",
    "",
    "node:",
    "  node_id: \"" + f.nodeId + "\"",
    "  node_name: \"" + f.nodeName + "\"",
    "  token: \"" + f.nodeToken + "\"",
    "  role:",
    "    - \"compute\"",
    "  locality: \"lan\"",
    "",
    "daemon:",
    "  heartbeat_interval_sec: 10",
    "  signal_report_interval_sec: 5",
    "  job_poll_interval_sec: 3",
    "  max_concurrent_jobs: 4",
    "  reconnect_initial_delay_sec: 2",
    "  reconnect_max_delay_sec: 60",
    "  reconnect_ready_delay_sec: 5",
    "  reconnect_probe_interval_sec: 2",
    "",
    "runtime:",
    "  mode: \"hybrid\"",
    "  gui_start_at_login: false",
    "  close_to_tray: true",
    "  service_startup: \"automatic\"",
    "",
    "paths:",
    "  data_dir: \"./data\"",
    "  log_dir: \"./logs\"",
    "",
    "safety:",
    "  allow_write_actions: " + (f.allowWrite ? "true" : "false"),
    "  allowed_services:",
    ...svcLines,
  ];
  return lines.join("\n");
}

export default function SetupPage() {
  const { toast } = useToast();
  const [fields, setFields] = useState<Fields>(empty);
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState<"idle" | "testing" | "validating" | "saving">("idle");
  const [validation, setValidation] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await call<{ raw: string; path: string }>("get_config");
        if (res.ok && res.data?.raw) {
          const raw = res.data.raw;
          setFields({
            centerUrl: extractVal(raw, "base_url") || empty.centerUrl,
            yqpPath: extractVal(raw, "yqp_path") || empty.yqpPath,
            nodeId: extractVal(raw, "node_id") || empty.nodeId,
            nodeName: extractVal(raw, "node_name") || empty.nodeName,
            nodeToken: extractVal(raw, "token") || empty.nodeToken,
            allowWrite: raw.includes("allow_write_actions: true"),
            allowedServices: empty.allowedServices,
          });
        }
      } catch {
        // use defaults
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const busy = action !== "idle";

  const handleTestConnection = async () => {
    setAction("testing");
    setTestResult(null);
    try {
      const yamlText = buildConfigYaml(fields);
      await call("save_config", yamlText);
      const res = await call<{ reachable: boolean; status_code?: number }>("test_center_health");
      if (res.ok && res.data) {
        if (res.data.reachable) {
          setTestResult("Connected (HTTP " + (res.data.status_code || "?") + ")");
          window.dispatchEvent(new Event("yequ-status-refresh"));
          toast("Center reachable", "success");
        } else {
          setTestResult("Unreachable");
          window.dispatchEvent(new Event("yequ-status-refresh"));
          toast("Center unreachable", "error");
        }
      } else {
        setTestResult("Test failed: " + (res.error?.message || "Unknown"));
        toast("Connection test failed", "error");
      }
    } catch (e) {
      setTestResult(e instanceof Error ? e.message : "Test failed");
      toast("Connection test error", "error");
    } finally {
      setAction("idle");
    }
  };

  const handleValidate = async () => {
    setAction("validating");
    setValidation(null);
    try {
      const yamlText = buildConfigYaml(fields);
      const res = await call<{ valid: boolean }>("validate_config", yamlText);
      if (res.ok && res.data) {
        setValidation("Configuration is valid");
        toast("Config valid", "success");
      } else {
        setValidation(res.error?.message || "Validation failed");
        toast("Config invalid", "error");
      }
    } catch (e) {
      setValidation(e instanceof Error ? e.message : "Validation error");
      toast("Validation error", "error");
    } finally {
      setAction("idle");
    }
  };

  const handleSave = async () => {
    setAction("saving");
    try {
      const yamlText = buildConfigYaml(fields);
      const res = await call("save_config", yamlText);
      if (res.ok) {
        toast("Configuration saved", "success");
      } else {
        toast(res.error?.message || "Save failed", "error");
      }
    } catch (e) {
      toast("Failed to save", "error");
    } finally {
      setAction("idle");
    }
  };

  if (loading) return (
    <motion.div variants={pageTransition} initial="initial" animate="animate" className="p-5 max-w-[640px]">
      <div className="bg-[var(--bg-subtle)] rounded-sm h-8 w-24 mb-4 animate-pulse" />
      <div className="space-y-4">
        {[1, 2, 3].map(i => (
          <GlassPanel key={i}>
            <div className="space-y-3">
              <div className="bg-[var(--bg-subtle)] h-5 w-32 animate-pulse rounded-xs" />
              <div className="bg-[var(--bg-subtle)] h-9 w-full animate-pulse rounded-xs" />
              <div className="bg-[var(--bg-subtle)] h-9 w-full animate-pulse rounded-xs" />
            </div>
          </GlassPanel>
        ))}
      </div>
    </motion.div>
  );

  return (
    <motion.div variants={pageTransition} initial="initial" animate="animate" className="flex flex-col gap-4 p-5 max-w-[640px] h-full overflow-auto">
      <h1 className="text-[22px] font-semibold text-[var(--text)]">Setup</h1>

      {/* Connection */}
      <GlassPanel>
        <h3 className="text-[15px] font-semibold text-[var(--text)] mb-3">Connection</h3>
        <div className="flex flex-col gap-3">
          <TextField label="Center Base URL" value={fields.centerUrl} onChange={(v) => setFields((p) => ({ ...p, centerUrl: v }))} placeholder="https://gtw.yequdesu.top" disabled={busy} />
          <TextField label="YQP Path" value={fields.yqpPath} onChange={(v) => setFields((p) => ({ ...p, yqpPath: v }))} placeholder="/yqp/" disabled={busy} />
          <div className="flex items-center gap-3">
            <Button variant="primary" onClick={handleTestConnection} loading={action === "testing"}>
              <Wifi size={14} /> Test Connection
            </Button>
            {testResult && (
              <span className={"text-sm " + (testResult.startsWith("Connected") ? "text-[var(--success)]" : "text-[var(--danger)]")}>
                {testResult}
              </span>
            )}
          </div>
        </div>
      </GlassPanel>

      {/* Node Identity */}
      <GlassPanel>
        <h3 className="text-[15px] font-semibold text-[var(--text)] mb-3">Node Identity</h3>
        <div className="flex flex-col gap-3">
          <TextField label="Node ID" value={fields.nodeId} onChange={(v) => setFields((p) => ({ ...p, nodeId: v }))} placeholder="winClient" disabled={busy} />
          <TextField label="Node Name" value={fields.nodeName} onChange={(v) => setFields((p) => ({ ...p, nodeName: v }))} placeholder="Windows Client" disabled={busy} />
          <PasswordField label="Node Token" value={fields.nodeToken} onChange={(v) => setFields((p) => ({ ...p, nodeToken: v }))} placeholder="Token" disabled={busy} />
        </div>
      </GlassPanel>

      {/* Safety */}
      <GlassPanel>
        <h3 className="text-[15px] font-semibold text-[var(--text)] mb-3">Safety Policy</h3>
        <div className="flex flex-col gap-3">
          <label className="flex items-center justify-between py-1 cursor-pointer">
            <span className="text-sm text-[var(--text)]">Allow Write Actions</span>
            <input type="checkbox" checked={fields.allowWrite} onChange={(e) => setFields((p) => ({ ...p, allowWrite: e.target.checked }))} disabled={busy}
              className="w-[18px] h-[18px] accent-[var(--accent)] cursor-pointer" />
          </label>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-[var(--text-muted)]">Allowed Services (one per line)</label>
            <textarea value={fields.allowedServices} onChange={(e) => setFields((p) => ({ ...p, allowedServices: e.target.value }))} rows={4} disabled={busy}
              className="px-3 py-2 text-sm rounded-sm border border-[var(--border)] bg-[var(--surface-solid)] text-[var(--text)] placeholder:text-[var(--text-subtle)] outline-none focus:border-[var(--accent)] resize-y" />
          </div>
        </div>
      </GlassPanel>

      {/* Validation */}
      {validation && (
        <div className={"p-3 rounded-sm text-sm " + (validation.toLowerCase().includes("valid") ? "bg-[var(--success-bg)] text-[var(--success)]" : "bg-[var(--danger-bg)] text-[var(--danger)]")}>
          {validation}
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-3">
        <Button variant="secondary" onClick={handleValidate} loading={action === "validating"}>
          <Check size={14} /> Validate Config
        </Button>
        <Button variant="primary" onClick={handleSave} loading={action === "saving"}>
          <Save size={14} /> Save Config
        </Button>
      </div>
    </motion.div>
  );
}

function extractVal(raw: string, key: string): string | null {
  const match = raw.match(new RegExp(key + ":\\s*\"?([^\"\\n\\r]+)\"?", "i"));
  return match ? match[1].trim() : null;
}
