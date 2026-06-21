import type { ReactNode } from "react";

export function EmptyState({
  icon,
  title,
  description,
}: {
  icon: ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div className="mx-auto mt-20 max-w-md rounded-[var(--radius-lg)] border border-[var(--border)] bg-white/55 p-10 text-center shadow-sm backdrop-blur-xl">
      <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-[var(--radius-md)] bg-[var(--accent-muted)] text-[var(--accent)]">
        {icon}
      </div>
      <h2 className="mt-4 text-[18px] font-semibold text-[var(--text)]">{title}</h2>
      <p className="mt-2 text-[13px] leading-6 text-[var(--text-muted)]">{description}</p>
    </div>
  );
}
