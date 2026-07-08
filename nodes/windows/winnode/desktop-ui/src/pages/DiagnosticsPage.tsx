import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { FileArchive, FolderOpen, HardDrive, ScrollText } from "lucide-react";
import { call } from "../bridge/client";
import { Button } from "../components/Button";
import { GlassPanel } from "../components/GlassPanel";
import { StatusDot } from "../components/StatusDot";
import { Badge } from "../components/Badge";
import { LoadingSkeleton } from "../components/LoadingSkeleton";
import { useToast } from "../components/Toast";
import { pageTransition } from "../components/motion";
import type { LocalStatus, ServiceStatus } from "../bridge/types";

export default function DiagnosticsPage() {
  const [status, setStatus] = useState<LocalStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [zipPath, setZipPath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { toast } = useToast();

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const res = await call<LocalStatus>("get_local_status");
        if (res.ok && res.data) {
          setStatus(res.data);
        } else {
          setError(res.error?.message || "Failed to load status");
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load status");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const handleGenerate = async () => {
    setGenerating(true);
    setZipPath(null);
    try {
      const res = await call<{ path: string }>("export_diagnostics");
      if (res.ok && res.data) {
        setZipPath(res.data.path);
        toast("Diagnostics package generated", "success");
      } else {
        toast(res.error?.message || "Generation failed", "error");
      }
    } catch {
      toast("Failed to generate diagnostics", "error");
    } finally {
      setGenerating(false);
    }
  };

  const handleOpenFolder = () => {
    if (!zipPath) return;
    call("open_path", zipPath);
  };

  const svc: ServiceStatus | undefined = status?.service;

  if (loading) {
    return (
      <motion.div variants={pageTransition} initial="initial" animate="animate" className="p-5">
        <h1 className="text-[22px] font-semibold text-[var(--text)] mb-5">Diagnostics</h1>
        <div className="grid grid-cols-4 gap-4 mb-5">
          {[1, 2, 3, 4].map((i) => (
            <GlassPanel key={i}><LoadingSkeleton /></GlassPanel>
          ))}
        </div>
      </motion.div>
    );
  }

  return (
    <motion.div variants={pageTransition} initial="initial" animate="animate" className="flex flex-col gap-5 h-full overflow-auto p-5">
      <h1 className="text-[22px] font-semibold text-[var(--text)]">Diagnostics</h1>

      {error && (
        <div className="bg-[var(--danger-bg)] text-[var(--danger)] p-3 rounded-sm text-sm">{error}</div>
      )}

      {status && (
        <div className="grid grid-cols-4 gap-4">
          <GlassPanel>
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">Config</span>
              <StatusDot status={status.configured ? "online" : "offline"} />
            </div>
            <Badge variant={status.configured ? "success" : "warning"}>
              {status.configured ? "Configured" : "Not Configured"}
            </Badge>
            <p className="text-xs text-[var(--text-subtle)] mt-1 break-all">{status.config_path}</p>
          </GlassPanel>

          <GlassPanel>
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">
                <ScrollText size={14} className="inline mr-1" /> Log Files
              </span>
            </div>
            <span className="text-2xl font-semibold text-[var(--text)]">logs/</span>
            <p className="text-xs text-[var(--text-subtle)] mt-1">Rotating log files</p>
          </GlassPanel>

          <GlassPanel>
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">
                <HardDrive size={14} className="inline mr-1" /> Database
              </span>
            </div>
            <span className="text-2xl font-semibold text-[var(--text)]">SQLite</span>
            <p className="text-xs text-[var(--text-subtle)] mt-1">data/node_state.sqlite3</p>
          </GlassPanel>

          <GlassPanel>
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">Service</span>
              <StatusDot status={svc?.installed ? "online" : "offline"} />
            </div>
            <Badge variant={svc?.installed ? "success" : "muted"}>
              {svc?.installed ? (svc?.running ? "Running" : "Stopped") : "Not Installed"}
            </Badge>
            <p className="text-xs text-[var(--text-subtle)] mt-1">{svc?.display_name || "YeQu Windows Client"}</p>
          </GlassPanel>
        </div>
      )}

      <div className="flex flex-col gap-3">
        <Button variant="primary" onClick={handleGenerate} loading={generating}>
          <FileArchive size={16} /> {generating ? "Generating..." : "Generate Diagnostics Package"}
        </Button>

        {zipPath && (
          <GlassPanel>
            <div className="flex items-center gap-3 flex-wrap">
              <span className="text-sm text-[var(--text)]">Saved:</span>
              <code className="text-xs font-mono text-[var(--accent)] break-all flex-1">{zipPath}</code>
              <Button variant="secondary" onClick={handleOpenFolder}>
                <FolderOpen size={14} /> Open Folder
              </Button>
            </div>
          </GlassPanel>
        )}
      </div>
    </motion.div>
  );
}
