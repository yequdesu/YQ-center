interface StatusDotProps {
  status: "online" | "offline" | "warning" | "unknown";
  label?: string;
  className?: string;
  active?: boolean;
  variant?: string;
  size?: string;
}

export function StatusDot({ status, label, className = "", active, variant, size }: StatusDotProps) {
  if (active !== undefined) {
    status = active ? "online" : "offline";
  }
  if (variant === "error") status = "offline";
  if (variant === "success") status = "online";
  if (variant === "warning") status = "warning";
  const colors: Record<string, string> = {
    online: "bg-[var(--success)]",
    offline: "bg-[var(--text-subtle)]",
    warning: "bg-[var(--warning)]",
    unknown: "bg-[var(--text-subtle)]",
  };

  return (
    <div className={`inline-flex items-center gap-2 ${className}`}>
      <span className={`relative flex h-2.5 w-2.5`}>
        {status === "online" && (
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[var(--success)] opacity-40" />
        )}
        <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${colors[status]}`} />
      </span>
      {label && <span className="text-sm text-[var(--text-muted)]">{label}</span>}
    </div>
  );
}
