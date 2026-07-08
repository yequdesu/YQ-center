import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { Search } from "lucide-react";
import { call } from "../bridge/client";
import type { CapabilityFunction, CapabilitySignal, PluginManifest } from "../bridge/types";
import { GlassPanel } from "../components/GlassPanel";
import { DataTable } from "../components/DataTable";
import { Badge } from "../components/Badge";
import { Tabs } from "../components/Tabs";
import { LoadingSkeleton } from "../components/LoadingSkeleton";
import { EmptyState } from "../components/EmptyState";
import { pageTransition } from "../components/motion";

export default function CapabilitiesPage() {
  const [tab, setTab] = useState("functions");
  const [search, setSearch] = useState("");
  const [riskFilter, setRiskFilter] = useState("All");
  const [loading, setLoading] = useState(true);
  const [functions, setFunctions] = useState<CapabilityFunction[]>([]);
  const [signals, setSignals] = useState<CapabilitySignal[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const res = await call<PluginManifest>("get_capabilities");
        if (res.ok && res.data) {
          if (!cancelled) {
            setFunctions(res.data.functions || []);
            setSignals(res.data.signals || []);
          }
        } else if (!cancelled) {
          setError(res.error?.message || "Failed to load");
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const filteredFunctions = useMemo(() =>
    functions.filter((f) => {
      if (search && !f.name.toLowerCase().includes(search.toLowerCase())) return false;
      if (riskFilter !== "All" && f.risk !== riskFilter) return false;
      return true;
    }), [functions, search, riskFilter]
  );

  const filteredSignals = useMemo(() =>
    signals.filter((s) => !search || s.name.toLowerCase().includes(search.toLowerCase())),
    [signals, search]
  );

  if (loading) return (
    <motion.div variants={pageTransition} initial="initial" animate="animate" className="p-5">
      <h1 className="text-[22px] font-semibold text-[var(--text)] mb-5">Capabilities</h1>
      <GlassPanel><LoadingSkeleton /></GlassPanel>
    </motion.div>
  );

  return (
    <motion.div variants={pageTransition} initial="initial" animate="animate" className="flex flex-col gap-5 h-full overflow-auto p-5">
      <h1 className="text-[22px] font-semibold text-[var(--text)]">Capabilities</h1>

      {error && (
        <div className="bg-[var(--danger-bg)] text-[var(--danger)] p-3 rounded-sm text-sm">{error}</div>
      )}

      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 max-w-[300px]">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--text-subtle)]" />
          <input
            type="text"
            placeholder="Search..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-8 pr-3 py-2 text-sm rounded-sm border border-[var(--border)] bg-[var(--surface-solid)] text-[var(--text)] placeholder:text-[var(--text-subtle)] outline-none focus:border-[var(--accent)]"
          />
        </div>
        {tab === "functions" && (
          <select value={riskFilter} onChange={(e) => setRiskFilter(e.target.value)}
            className="px-3 py-2 text-sm rounded-sm border border-[var(--border)] bg-[var(--surface-solid)] text-[var(--text)] outline-none focus:border-[var(--accent)]">
            <option value="All">All Risks</option>
            <option value="safe">Safe</option>
            <option value="maintenance">Maintenance</option>
          </select>
        )}
      </div>

      <Tabs tabs={[
        { key: "functions", label: `Functions (${filteredFunctions.length})` },
        { key: "signals", label: `Signals (${filteredSignals.length})` },
      ]} active={tab} onChange={setTab} />

      <GlassPanel className="flex-1 min-h-0">
        {tab === "functions" ? (
          filteredFunctions.length === 0 ? (
            <EmptyState title="No functions found" />
          ) : (
            <DataTable
              columns={[
                { key: "name", header: "Name" },
                {
                  key: "risk", header: "Risk",
                  render: (v) => <Badge variant={v === "safe" ? "success" : "warning"}>{String(v)}</Badge>,
                },
                { key: "effect", header: "Effect" },
                { key: "timeout_sec", header: "Timeout (s)" },
              ]}
              rows={filteredFunctions as unknown as Record<string, unknown>[]}
              rowKey={(r) => r.name as string}
            />
          )
        ) : (
          filteredSignals.length === 0 ? (
            <EmptyState title="No signals found" />
          ) : (
            <DataTable
              columns={[
                { key: "name", header: "Name" },
                { key: "ttl_sec", header: "TTL (s)" },
              ]}
              rows={filteredSignals as unknown as Record<string, unknown>[]}
              rowKey={(r) => r.name as string}
            />
          )
        )}
      </GlassPanel>
    </motion.div>
  );
}
