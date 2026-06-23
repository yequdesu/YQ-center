interface LoadingSkeletonProps {
  lines?: number;
}

export function LoadingSkeleton({ lines = 3 }: LoadingSkeletonProps) {
  return (
    <div className="space-y-3">
      {Array.from({ length: lines }).map((_, index) => (
        <div
          key={index}
          className="h-16 animate-pulse rounded-[var(--radius-md)] border border-[var(--border)] bg-white/55 shadow-sm backdrop-blur-xl"
        >
          <div className="flex h-full items-center gap-3 px-4">
            <div className="h-8 w-8 rounded-[var(--radius-sm)] bg-[var(--accent-muted)]" />
            <div className="min-w-0 flex-1 space-y-2">
              <div className="h-3 w-1/3 rounded-full bg-[var(--border)]" />
              <div className="h-2 w-2/3 rounded-full bg-[var(--border)]" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
