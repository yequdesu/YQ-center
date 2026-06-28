import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listArtifacts } from "@/api/admin";
import { ArtifactList } from "@/components/ArtifactCards";
import { Button } from "@/components/Button";
import { EmptyState } from "@/components/EmptyState";
import { Loader2, PackageSearch, Search } from "lucide-react";

export function ArtifactsPage() {
  const [nodeId, setNodeId] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [jobId, setJobId] = useState("");
  const [artifactType, setArtifactType] = useState("");
  const [filters, setFilters] = useState({
    nodeId: "",
    sessionId: "",
    jobId: "",
    artifactType: "",
  });

  const artifactsQuery = useQuery({
    queryKey: ["artifacts", filters],
    queryFn: () =>
      listArtifacts({
        nodeId: filters.nodeId || undefined,
        sessionId: filters.sessionId || undefined,
        jobId: filters.jobId || undefined,
        artifactType: filters.artifactType || undefined,
        limit: 100,
      }),
  });

  const artifacts = artifactsQuery.data?.artifacts ?? [];

  return (
    <div className="mx-auto max-w-[1120px] p-6">
      <div className="mb-4 flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--accent-muted)] text-[var(--accent)]">
          <PackageSearch size={18} />
        </div>
        <div>
          <h1 className="text-[18px] font-semibold text-[var(--text)]">Artifacts</h1>
          <p className="text-[12px] text-[var(--text-muted)]">
            Files, screenshots, reports, and exports produced by Nodes.
          </p>
        </div>
      </div>

      <div className="mb-4 grid grid-cols-1 gap-2 md:grid-cols-5">
        <FilterInput label="Node" value={nodeId} onChange={setNodeId} placeholder="winClient" />
        <FilterInput
          label="Session"
          value={sessionId}
          onChange={setSessionId}
          placeholder="session id"
        />
        <FilterInput label="Job" value={jobId} onChange={setJobId} placeholder="job id" />
        <FilterInput
          label="Type"
          value={artifactType}
          onChange={setArtifactType}
          placeholder="file"
        />
        <div className="flex items-end">
          <Button
            onClick={() => setFilters({ nodeId, sessionId, jobId, artifactType })}
            className="w-full"
          >
            <Search size={14} />
            Search
          </Button>
        </div>
      </div>

      {artifactsQuery.isLoading ? (
        <div className="flex items-center gap-2 text-[13px] text-[var(--text-muted)]">
          <Loader2 size={14} className="animate-spin" />
          Loading artifacts
        </div>
      ) : artifactsQuery.isError ? (
        <EmptyState
          icon={<PackageSearch size={22} />}
          title="Failed to load artifacts"
          description={
            artifactsQuery.error instanceof Error
              ? artifactsQuery.error.message
              : "Could not fetch artifact data."
          }
        />
      ) : artifacts.length === 0 ? (
        <EmptyState
          icon={<PackageSearch size={22} />}
          title="No artifacts"
          description="Run an artifact-producing capability such as windows.screen.capture or windows.system.report_artifact."
        />
      ) : (
        <ArtifactList artifacts={artifacts} />
      )}
    </div>
  );
}

function FilterInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--text-muted)]">
        {label}
      </span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="h-9 w-full rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-solid)] px-2 text-[13px] text-[var(--text)] outline-none focus:border-[var(--accent)]"
      />
    </label>
  );
}
