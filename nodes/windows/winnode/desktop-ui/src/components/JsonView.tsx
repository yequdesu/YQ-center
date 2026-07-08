import { Badge } from "./Badge";

interface JsonViewProps {
  data: unknown;
  className?: string;
}

export function JsonView({ data, className = "" }: JsonViewProps) {
  const formatted = safeStringify(data);

  if (typeof data === "object" && data !== null) {
    return (
      <pre
        className={`text-xs font-mono leading-[18px] bg-[var(--surface-muted)] border border-[var(--border)] rounded-sm p-3 overflow-auto max-h-64 whitespace-pre-wrap ${className}`}
      >
        {formatted}
      </pre>
    );
  }

  return <Badge variant="muted">{formatted}</Badge>;
}

function safeStringify(data: unknown): string {
  try {
    return JSON.stringify(data, null, 2);
  } catch {
    return String(data);
  }
}
