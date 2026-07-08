interface BadgeProps {
  variant?: "success" | "warning" | "danger" | "info" | "muted" | "default" | "error";
  children?: React.ReactNode;
  label?: string;
  text?: string;
  className?: string;
  style?: React.CSSProperties;
}

export function Badge({ variant = "muted", children, label, text, className = "", style }: BadgeProps) {
  const actualVariant = (variant === "default" || variant === "error") ? "muted" : variant;
  const displayText = label || text;
  const variants: Record<string, string> = {
    success: "bg-[var(--success-bg)] text-[var(--success)]",
    warning: "bg-[var(--warning-bg)] text-[var(--warning)]",
    danger: "bg-[var(--danger-bg)] text-[var(--danger)]",
    info: "bg-[var(--info-bg)] text-[var(--info)]",
    muted: "bg-[var(--bg-subtle)] text-[var(--text-muted)]",
  };

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-xs ${variants[actualVariant]} ${className}`}
      style={style}
    >
      {displayText || children}
    </span>
  );
}
