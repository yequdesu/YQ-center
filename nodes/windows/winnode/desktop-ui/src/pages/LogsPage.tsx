import { useState, useEffect, useCallback, useRef } from "react";
import { motion } from "framer-motion";
import { RefreshCw, Copy } from "lucide-react";
import { call } from "../bridge/client";
import { Button } from "../components/Button";
import { GlassPanel } from "../components/GlassPanel";
import { LogViewer } from "../components/LogViewer";
import { Badge } from "../components/Badge";
import { useToast } from "../components/Toast";
import { pageTransition } from "../components/motion";

type LogLevel = "" | "INFO" | "WARN" | "ERROR" | "DEBUG";

export default function LogsPage() {
  const [lines, setLines] = useState<string[]>([]);
  const [total, setTotal] = useState(0);
  const [shown, setShown] = useState(0);
  const [level, setLevel] = useState<LogLevel>("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { toast } = useToast();
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchLogs = useCallback(async (lvl: LogLevel, q: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await call<{ lines: string[]; total: number; shown: number }>(
        "get_logs", 500, lvl || undefined, q || undefined
      );
      if (res.ok && res.data) {
        setLines(res.data.lines || []);
        setTotal(res.data.total || 0);
        setShown(res.data.shown || 0);
      } else {
        setError(res.error?.message || "Failed to fetch logs");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch logs");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchLogs("", "");
  }, [fetchLogs]);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => fetchLogs(level, query), 300);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [query, level, fetchLogs]);

  const handleRefresh = () => fetchLogs(level, query);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(lines.join("\n"));
      toast("Copied to clipboard", "success");
    } catch {
      toast("Failed to copy", "error");
    }
  };

  return (
    <motion.div variants={pageTransition} initial="initial" animate="animate" className="flex flex-col gap-4 h-full overflow-auto p-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-[22px] font-semibold text-[var(--text)]">Logs</h1>
          <Badge variant="muted">{shown} shown</Badge>
          <Badge variant="muted">{total} total</Badge>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value as LogLevel)}
            className="px-3 py-2 text-sm rounded-sm border border-[var(--border)] bg-[var(--surface-solid)] text-[var(--text)] outline-none focus:border-[var(--accent)] min-w-[90px]"
          >
            <option value="">ALL</option>
            <option value="INFO">INFO</option>
            <option value="WARN">WARN</option>
            <option value="ERROR">ERROR</option>
            <option value="DEBUG">DEBUG</option>
          </select>
          <input
            type="text"
            placeholder="Search logs..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="px-3 py-2 text-sm rounded-sm border border-[var(--border)] bg-[var(--surface-solid)] text-[var(--text)] placeholder:text-[var(--text-subtle)] outline-none focus:border-[var(--accent)] min-w-[180px]"
          />
          <Button variant="secondary" onClick={handleRefresh}>
            <RefreshCw size={14} /> Refresh
          </Button>
          <Button variant="secondary" onClick={handleCopy} disabled={lines.length === 0}>
            <Copy size={14} /> Copy
          </Button>
        </div>
      </div>

      {error && (
        <div className="bg-[var(--danger-bg)] text-[var(--danger)] p-3 rounded-sm text-sm">{error}</div>
      )}

      <div className="flex-1 min-h-0">
        <LogViewer lines={lines} loading={loading} maxHeight="100%" />
      </div>
    </motion.div>
  );
}
