import { useEffect, useRef, useState } from "react";

interface LogViewerProps {
  lines: string[];
  loading?: boolean;
  maxHeight?: string;
}

export function LogViewer({ lines, loading, maxHeight = "400px" }: LogViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  useEffect(() => {
    if (autoScroll && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [lines, autoScroll]);

  const handleScroll = () => {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    setAutoScroll(scrollHeight - scrollTop - clientHeight < 40);
  };

  return (
    <div
      ref={containerRef}
      className="bg-[var(--surface-muted)] rounded-sm border border-[var(--border)] overflow-auto p-3"
      style={{ maxHeight }}
      onScroll={handleScroll}
    >
      {loading ? (
        <div className="text-xs text-[var(--text-subtle)] animate-pulse">Loading...</div>
      ) : lines.length === 0 ? (
        <div className="text-xs text-[var(--text-subtle)]">No log entries</div>
      ) : (
        lines.map((line, i) => {
          let color = "text-[var(--text)]";
          if (line.includes("[ERROR]")) color = "text-[var(--danger)]";
          else if (line.includes("[WARN]")) color = "text-[var(--warning)]";
          else if (line.includes("[DEBUG]")) color = "text-[var(--text-subtle)]";

          return (
            <div key={i} className={`text-xs leading-[18px] font-mono whitespace-pre-wrap ${color}`}>
              {line}
            </div>
          );
        })
      )}
    </div>
  );
}
