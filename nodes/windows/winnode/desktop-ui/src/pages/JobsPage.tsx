import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import { RefreshCw, RotateCcw, Trash2 } from "lucide-react";
import { call } from "../bridge/client";
import type { JobResult } from "../bridge/types";
import { Button } from "../components/Button";
import { GlassPanel } from "../components/GlassPanel";
import { DataTable } from "../components/DataTable";
import { Badge } from "../components/Badge";
import { LoadingSkeleton } from "../components/LoadingSkeleton";
import { EmptyState } from "../components/EmptyState";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useToast } from "../components/Toast";
import { pageTransition } from "../components/motion";

export default function JobsPage() {
  const [jobs, setJobs] = useState<JobResult[]>([]);
  const [unreportedCount, setUnreportedCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [flushing, setFlushing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const { toast } = useToast();

  const fetchJobs = useCallback(async () => {
    setError(null);
    try {
      const res = await call<{ jobs: JobResult[]; unreported_count: number }>("get_recent_jobs", 50);
      if (res.ok && res.data) {
        setJobs(res.data.jobs || []);
        setUnreportedCount(res.data.unreported_count || 0);
      } else {
        setError(res.error?.message || "Failed to fetch jobs");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch jobs");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchJobs();
  }, [fetchJobs]);

  const handleRefresh = async () => {
    setRefreshing(true);
    await fetchJobs();
  };

  const handleRetry = async () => {
    setRetrying(true);
    try {
      const res = await call<{ sent: number; failed: number }>("retry_unreported_jobs");
      if (res.ok) {
        toast(`Retry completed: ${res.data?.sent || 0} sent, ${res.data?.failed || 0} failed`, "success");
        await fetchJobs();
      } else {
        toast(res.error?.message || "Retry failed", "error");
      }
    } catch {
      toast("Failed to retry", "error");
    } finally {
      setRetrying(false);
    }
  };

  const handleFlush = async () => {
    setConfirmOpen(false);
    setFlushing(true);
    try {
      const res = await call<{ flushed: number }>("flush_reported_cache");
      if (res.ok) {
        toast(`Reported cache flushed: ${res.data?.flushed || 0}`, "success");
        await fetchJobs();
      } else {
        toast(res.error?.message || "Flush failed", "error");
      }
    } catch {
      toast("Failed to flush", "error");
    } finally {
      setFlushing(false);
    }
  };

  const statusVariant = (v: string) =>
    v === "succeeded" ? "success" : v === "failed" ? "danger" : v === "running" ? "info" : "muted" as
      "success" | "warning" | "danger" | "info" | "muted";

  if (loading) {
    return (
      <motion.div variants={pageTransition} initial="initial" animate="animate" className="p-5">
        <LoadingSkeleton />
      </motion.div>
    );
  }

  return (
    <motion.div variants={pageTransition} initial="initial" animate="animate" className="flex flex-col gap-5 h-full overflow-auto p-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-[22px] font-semibold text-[var(--text)]">Jobs</h1>
          {unreportedCount > 0 && (
            <Badge variant="warning">{unreportedCount} unreported</Badge>
          )}
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={handleRefresh} loading={refreshing}>
            <RefreshCw size={14} /> Refresh
          </Button>
          <Button variant="secondary" onClick={handleRetry} loading={retrying} disabled={unreportedCount === 0}>
            <RotateCcw size={14} /> Retry Unreported
          </Button>
          <Button variant="secondary" onClick={() => setConfirmOpen(true)} loading={flushing}>
            <Trash2 size={14} /> Flush Reported
          </Button>
        </div>
      </div>

      {error && (
        <div className="bg-[var(--danger-bg)] text-[var(--danger)] p-3 rounded-sm text-sm">{error}</div>
      )}

      <GlassPanel className="flex-1 min-h-0">
        {jobs.length === 0 ? (
          <EmptyState title="No jobs found" description="Completed and in-progress jobs will appear here" />
        ) : (
          <DataTable
            columns={[
              { key: "job_id", header: "Job ID" },
              { key: "function_name", header: "Function" },
              {
                key: "status",
                header: "Status",
                render: (v) => <Badge variant={statusVariant(String(v))}>{String(v)}</Badge>,
              },
              { key: "created_at", header: "Created" },
              { key: "error_code", header: "Error Code" },
              {
                key: "error_message",
                header: "Error Message",
                render: (v) => (
                  <span className="block max-w-[360px] truncate text-xs text-[var(--text-muted)]" title={String(v || "")}>
                    {String(v || "")}
                  </span>
                ),
              },
            ]}
            rows={jobs as unknown as Record<string, unknown>[]}
            rowKey={(r) => r.job_id as string}
          />
        )}
      </GlassPanel>

      <ConfirmDialog
        open={confirmOpen}
        title="Flush Reported Cache"
        message="Are you sure you want to flush the reported job cache? This action cannot be undone."
        confirmLabel="Flush"
        onConfirm={handleFlush}
        onCancel={() => setConfirmOpen(false)}
      />
    </motion.div>
  );
}
