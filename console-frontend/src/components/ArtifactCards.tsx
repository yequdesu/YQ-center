import { useEffect, useState } from "react";
import { Download, Eye, FileJson, FileText, Image as ImageIcon } from "lucide-react";
import { getConfig } from "@/api/client";
import type { CenterArtifactDetail } from "@/api/types";

type ArtifactLike = Partial<CenterArtifactDetail>;

export function ArtifactList({ artifacts }: { artifacts: ArtifactLike[] }) {
  if (artifacts.length === 0) return null;
  return (
    <div className="space-y-2">
      {artifacts.map((artifact, index) => (
        <ArtifactCard
          key={String(artifact.artifact_id ?? artifact.download_url ?? index)}
          artifact={artifact}
        />
      ))}
    </div>
  );
}

export function ArtifactCard({ artifact }: { artifact: ArtifactLike }) {
  const contentType = optionalString(artifact.content_type);
  const title =
    optionalString(artifact.title) ||
    optionalString(artifact.artifact_id) ||
    "artifact";
  const isImage = Boolean(contentType?.startsWith("image/"));
  const isJson = contentType === "application/json" || title.endsWith(".json");
  const isText = Boolean(contentType?.startsWith("text/"));
  const Icon = isImage ? ImageIcon : isJson ? FileJson : FileText;

  return (
    <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-muted)]/60 p-2.5">
      <div className="flex items-start gap-2">
        <div className="mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--bg-subtle)] text-[var(--text-muted)]">
          <Icon size={14} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-[13px] font-medium text-[var(--text)]">
              {title}
            </span>
            <span className="rounded-[var(--radius-sm)] border border-[var(--border)] px-1.5 py-0.5 text-[11px] text-[var(--text-muted)]">
              {optionalString(artifact.artifact_type) || "artifact"}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-[var(--text-subtle)]">
            {contentType && <span>{contentType}</span>}
            {typeof artifact.size_bytes === "number" && (
              <span>{formatBytes(artifact.size_bytes)}</span>
            )}
            {optionalString(artifact.node_id) && <span>@ {String(artifact.node_id)}</span>}
            {optionalString(artifact.job_id) && (
              <span className="font-mono">job {String(artifact.job_id)}</span>
            )}
          </div>
          {isImage && <ArtifactImage artifact={artifact} alt={title} />}
          {artifact.summary && typeof artifact.summary === "object" && (
            <div className="mt-2 flex flex-wrap gap-1">
              {Object.entries(artifact.summary as Record<string, unknown>).map(([key, value]) => (
                <span
                  key={key}
                  className="rounded-[var(--radius-sm)] bg-[var(--bg-subtle)] px-1.5 py-0.5 text-[11px] text-[var(--text-muted)]"
                >
                  {key}: {String(value)}
                </span>
              ))}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={() => void downloadArtifact(artifact)}
          className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-[var(--radius-sm)] border border-[var(--border)] text-[var(--text-muted)] hover:bg-[var(--surface-solid)] hover:text-[var(--text)]"
          title="Download artifact"
        >
          <Download size={14} />
        </button>
      </div>
      {(isJson || isText) && (
        <button
          type="button"
          onClick={() => void openArtifact(artifact)}
          className="mt-2 inline-flex items-center gap-1 text-[12px] text-[var(--accent)] hover:underline"
        >
          <Eye size={13} />
          Open
        </button>
      )}
    </div>
  );
}

function ArtifactImage({ artifact, alt }: { artifact: ArtifactLike; alt: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const downloadUrl = optionalString(artifact.download_url);

  useEffect(() => {
    if (!downloadUrl) return undefined;
    let revokedUrl: string | null = null;
    let cancelled = false;
    setError(null);
    fetchArtifactBlob(artifact)
      .then((blob) => {
        if (cancelled) return;
        revokedUrl = URL.createObjectURL(blob);
        setSrc(revokedUrl);
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setSrc(null);
        setError(reason instanceof Error ? reason.message : "Artifact preview failed");
      });
    return () => {
      cancelled = true;
      if (revokedUrl) URL.revokeObjectURL(revokedUrl);
    };
  }, [artifact, downloadUrl]);

  if (!downloadUrl) return null;
  if (error) {
    return <p className="mt-2 text-[11px] text-[var(--danger)]">{error}</p>;
  }
  if (!src) return null;
  return (
    <div className="mt-2 overflow-hidden rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-solid)]">
      <img src={src} alt={alt} className="max-h-[320px] w-full object-contain" />
    </div>
  );
}

export function artifactsFromResult(result: Record<string, unknown> | undefined) {
  if (!result) return [];
  const found: ArtifactLike[] = [];
  collectArtifacts(result, found, 0);
  return dedupeArtifacts(found);
}

function artifactUrl(artifact: ArtifactLike) {
  const downloadUrl = optionalString(artifact.download_url);
  if (!downloadUrl) throw new Error("Artifact has no download_url");
  const cfg = getConfig();
  return new URL(downloadUrl, cfg.baseUrl).toString();
}

async function fetchArtifactBlob(artifact: ArtifactLike) {
  const cfg = getConfig();
  const response = await fetch(artifactUrl(artifact), {
    headers: { Authorization: `Bearer ${cfg.token}` },
  });
  if (!response.ok) {
    throw new Error(`Artifact download failed: ${response.status}`);
  }
  return response.blob();
}

async function downloadArtifact(artifact: ArtifactLike) {
  const blob = await fetchArtifactBlob(artifact);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download =
    optionalString(artifact.title) ||
    optionalString(artifact.artifact_id) ||
    "artifact";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function openArtifact(artifact: ArtifactLike) {
  const blob = await fetchArtifactBlob(artifact);
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank", "noopener,noreferrer");
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function collectArtifacts(value: unknown, found: ArtifactLike[], depth: number) {
  if (depth > 5 || value === null || value === undefined) return;
  if (Array.isArray(value)) {
    for (const item of value) collectArtifacts(item, found, depth + 1);
    return;
  }
  if (typeof value !== "object") return;

  const record: Record<string, unknown> = value as Record<string, unknown>;
  if (isArtifactLike(value)) {
    found.push(value);
  }

  const artifact = record["artifact"];
  if (isArtifactLike(artifact)) {
    found.push(artifact);
  }

  const artifacts = record["artifacts"];
  if (Array.isArray(artifacts)) {
    for (const item of artifacts) {
      if (isArtifactLike(item)) found.push(item);
      else collectArtifacts(item, found, depth + 1);
    }
  }

  for (const key of ["result", "output", "data"]) {
    collectArtifacts(record[key], found, depth + 1);
  }
}

function isArtifactLike(value: unknown): value is ArtifactLike {
  if (value === null || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.artifact_id === "string" &&
    (typeof record.download_url === "string" || typeof record.content_type === "string")
  );
}

function dedupeArtifacts(artifacts: ArtifactLike[]) {
  const seen = new Set<string>();
  const unique: ArtifactLike[] = [];
  for (const artifact of artifacts) {
    const key = String(artifact.artifact_id ?? artifact.download_url ?? "");
    if (!key || seen.has(key)) continue;
    seen.add(key);
    unique.push(artifact);
  }
  return unique;
}

function formatBytes(value: number) {
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}
