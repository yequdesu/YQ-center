interface GlassPanelProps {
  children: React.ReactNode;
  className?: string;
  strong?: boolean;
  title?: string;
  variant?: string;
  style?: React.CSSProperties;
}

export function GlassPanel({ children, className = "", strong, title, variant, style }: GlassPanelProps) {
  return (
    <div
      className={`${strong ? "glass" : "bg-[var(--surface-solid)] border border-[var(--border)]"} rounded-md p-5 ${className}`}
      style={{
        boxShadow: strong ? "0 8px 24px rgba(15,23,42,0.08)" : "0 1px 2px rgba(15,23,42,0.06)",
        ...style,
      }}
    >
      {title && <h3 className="text-sm font-semibold text-[var(--text)] mb-3">{title}</h3>}
      {children}
    </div>
  );
}
