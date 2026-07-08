import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import { RefreshCw, Server } from "lucide-react";
import { call } from "../bridge/client";
import { Button } from "../components/Button";
import { StatusDot } from "../components/StatusDot";
import { GlassPanel } from "../components/GlassPanel";
import { Badge } from "../components/Badge";
import { DataTable } from "../components/DataTable";
import { LoadingSkeleton } from "../components/LoadingSkeleton";
import { pageTransition } from "../components/motion";
import type { JobResult, ServiceStatus, LocalStatus } from "../bridge/types";

export default function DashboardPage() {
  const [status, setStatus] = useState<LocalStatus | null>(null);
  const [jobs, setJobs] = useState<JobResult[]>([]);
  const [unreported, setUnreported] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [statusRes, jobsRes] = await Promise.all([
        call<LocalStatus>("get_local_status"),
        call<{ jobs: JobResult[]; unreported_count: number }>("get_recent_jobs", 10),
      ]);

      if (statusRes.ok && statusRes.data) {
        setStatus(statusRes.data);
      } else {
        setError(statusRes.error?.message || "Failed to fetch status");
      }

      if (jobsRes.ok && jobsRes.data) {
        setJobs(jobsRes.data.jobs || []);
        setUnreported(jobsRes.data.unreported_count || 0);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "An unexpected error occurred");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const svc: ServiceStatus | undefined = status?.service;

  return (
    <motion.div
      variants={pageTransition}
      initial="initial"
      animate="animate"
      exit="exit"
      className="flex flex-col gap-5 h-full overflow-auto"
    >
      <div className="flex items-center justify-between">
        <h1 className="text-[22px] font-semibold text-[var(--text)]">Dashboard</h1>
        <Button variant="secondary" onClick={fetchData} loading={loading}>
          <RefreshCw size={14} /> Refresh
        </Button>
      </div>

      {error && (
        <div className="flex flex-col items-center gap-3 py-10 text-center">
          <span className="text-sm text-[var(--danger)]">Failed to load dashboard data</span>
          <span className="text-xs text-[var(--text-muted)]">{error}</span>
          <Button variant="secondary" onClick={fetchData}>Retry</Button>
        </div>
      )}

      {!error && (
        <>
          {/* Status Strip */}
          <div className="grid grid-cols-4 gap-3">
            <GlassPanel className="flex flex-col gap-1">
              <span className="text-xs text-[var(--text-muted)] uppercase tracking-wider">Runtime</span>
              <div className="flex items-center gap-2">
                <StatusDot status={status?.runtime?.active_mode === "conflict" ? "offline" : status?.daemon_running ? "online" : "offline"} />
                <span className="text-sm font-semibold text-[var(--text)]">
                  {status?.runtime?.label || (status?.daemon_running ? "Running" : "Stopped")}
                </span>
              </div>
            </GlassPanel>
            <GlassPanel className="flex flex-col gap-1">
              <span className="text-xs text-[var(--text-muted)] uppercase tracking-wider">Node</span>
              <div className="flex items-center gap-2">
                <StatusDot status={status?.configured ? "online" : "offline"} />
                <span className="text-sm font-semibold text-[var(--text)]">
                  {status?.configured ? "Configured" : "Not Configured"}
                </span>
              </div>
            </GlassPanel>
            <GlassPanel className="flex flex-col gap-1">
              <span className="text-xs text-[var(--text-muted)] uppercase tracking-wider">Service</span>
              <div className="flex items-center gap-2">
                <StatusDot status={svc?.installed ? "online" : "offline"} />
                <span className="text-sm font-semibold text-[var(--text)]">
                  {svc?.installed ? (svc?.running ? "Running" : "Stopped") : "Not Installed"}
                </span>
              </div>
            </GlassPanel>
            <GlassPanel className="flex flex-col gap-1">
              <span className="text-xs text-[var(--text-muted)] uppercase tracking-wider">Pending</span>
              <div className="flex items-center gap-2">
                <Badge variant={unreported > 0 ? "warning" : "success"}>
                  {unreported}
                </Badge>
                <span className="text-xs text-[var(--text-muted)]">unreported</span>
              </div>
            </GlassPanel>
          </div>

          {/* Connection + Service panels */}
          <div className="grid grid-cols-2 gap-4">
            {loading && !status ? (
              <>
                <GlassPanel><LoadingSkeleton /></GlassPanel>
                <GlassPanel><LoadingSkeleton /></GlassPanel>
              </>
            ) : (
              <>
                <GlassPanel title="Connection">
                  <div className="flex flex-col gap-3">
                    <div className="flex justify-between items-center py-2 border-b border-[var(--border)]">
                      <span className="text-sm text-[var(--text-muted)]">Node ID</span>
                      <span className="text-sm font-mono text-[var(--text)]">{status?.node_id || "--"}</span>
                    </div>
                    <div className="flex justify-between items-center py-2 border-b border-[var(--border)]">
                      <span className="text-sm text-[var(--text-muted)]">Config Path</span>
                      <span className="text-sm font-mono text-[var(--text)]">{status?.config_path || "--"}</span>
                    </div>
                    <div className="flex justify-between items-center py-2 border-b border-[var(--border)]">
                      <span className="text-sm text-[var(--text-muted)]">Status</span>
                      <StatusDot
                        status={status?.center_connected ? "online" : "offline"}
                        label={status?.center_connected ? "Connected" : "Disconnected"}
                      />
                    </div>
                    <div className="flex justify-between items-center py-2 border-b border-[var(--border)]">
                      <span className="text-sm text-[var(--text-muted)]">Runtime</span>
                      <Badge variant={status?.runtime?.conflict ? "danger" : "info"}>
                        {status?.runtime?.label || "--"}
                      </Badge>
                    </div>
                    <div className="flex justify-between items-center py-2 border-b border-[var(--border)]">
                      <span className="text-sm text-[var(--text-muted)]">Daemon</span>
                      <StatusDot
                        status={status?.daemon_running ? "online" : "offline"}
                        label={status?.daemon_status || "unknown"}
                      />
                    </div>
                    <div className="flex justify-between items-center py-2 border-b border-[var(--border)]">
                      <span className="text-sm text-[var(--text-muted)]">Last Heartbeat</span>
                      <span className="text-sm font-mono text-[var(--text)]">{status?.last_heartbeat || "--"}</span>
                    </div>
                    <div className="flex justify-between items-center py-2 border-b border-[var(--border)]">
                      <span className="text-sm text-[var(--text-muted)]">Capabilities</span>
                      <span className="text-sm font-mono text-[var(--text)]">
                        {status?.last_capability_register
                          ? `${status.capability_count || "?"} registered`
                          : "--"}
                      </span>
                    </div>
                    {status?.last_error && (
                      <div className="rounded-sm border border-[var(--danger)]/20 bg-[var(--danger-bg)] p-2 text-xs text-[var(--danger)]">
                        {status.last_error}
                      </div>
                    )}
                  </div>
                </GlassPanel>

                <GlassPanel title="Service">
                  {svc?.installed ? (
                    <div className="flex flex-col gap-3">
                      <div className="flex justify-between items-center py-2 border-b border-[var(--border)]">
                        <span className="text-sm text-[var(--text-muted)]">Status</span>
                        <Badge variant={svc.running ? "success" : "warning"}>
                          {svc.running ? "Running" : "Stopped"}
                        </Badge>
                      </div>
                      <div className="flex justify-between items-center py-2 border-b border-[var(--border)]">
                        <span className="text-sm text-[var(--text-muted)]">Startup Type</span>
                        <span className="text-sm text-[var(--text)]">{svc.startup_type}</span>
                      </div>
                      <div className="flex justify-between items-center py-2 border-b border-[var(--border)]">
                        <span className="text-sm text-[var(--text-muted)]">Display Name</span>
                        <span className="text-sm text-[var(--text)]">{svc.display_name}</span>
                      </div>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center gap-2 py-6 text-center">
                      <Server size={24} className="text-[var(--text-subtle)]" />
                      <span className="text-sm text-[var(--text-muted)]">Service not installed</span>
                    </div>
                  )}
                </GlassPanel>
              </>
            )}
          </div>

          {/* Recent Jobs */}
          <GlassPanel title="Recent Jobs" className="flex-1 min-h-0 flex flex-col">
            <div className="flex-1 min-h-0 overflow-auto">
              {loading && jobs.length === 0 ? (
                <LoadingSkeleton />
              ) : jobs.length > 0 ? (
                <DataTable
                  columns={[
                    { key: "job_id", header: "Job ID" },
                    { key: "function_name", header: "Function" },
                    {
                      key: "status",
                      header: "Status",
                      render: (v) => (
                        <Badge variant={v === "succeeded" ? "success" : v === "failed" ? "danger" : "info"}>
                          {String(v)}
                        </Badge>
                      ),
                    },
                    { key: "created_at", header: "Created" },
                  ]}
                  rows={jobs as unknown as Record<string, unknown>[]}
                  rowKey={(r) => r.job_id as string}
                />
              ) : (
                <div className="flex flex-col items-center gap-2 py-10 text-center">
                  <span className="text-sm text-[var(--text-muted)]">No recent jobs</span>
                </div>
              )}
            </div>
          </GlassPanel>
        </>
      )}
    </motion.div>
  );
}
