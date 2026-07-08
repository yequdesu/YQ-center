interface LoadingSkeletonProps {
  rows?: number;
  lines?: number;
  height?: string;
  className?: string;
}

export function LoadingSkeleton({ rows = 4, lines, height, className = "" }: LoadingSkeletonProps) {
  const count = lines || rows;
  return (
    <div className={`space-y-3 ${className}`}>
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="h-5 bg-[var(--bg-subtle)] rounded-xs animate-pulse"
          style={{ width: `${60 + Math.random() * 40}%` }}
        />
      ))}
    </div>
  );
}
