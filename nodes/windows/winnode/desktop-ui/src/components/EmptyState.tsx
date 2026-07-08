interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  subtitle?: string;
  action?: React.ReactNode;
}

export function EmptyState({ icon, title, description, subtitle, action }: EmptyStateProps) {
  const desc = description || subtitle;
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      {icon && <div className="mb-4 text-[var(--text-subtle)]">{icon}</div>}
      <h3 className="text-sm font-semibold text-[var(--text-muted)] mb-1">{title}</h3>
      {desc && <p className="text-xs text-[var(--text-subtle)] mb-4 max-w-xs">{desc}</p>}
      {action}
    </div>
  );
}
