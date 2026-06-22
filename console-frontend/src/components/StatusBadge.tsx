export function StatusBadge({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  const color =
    normalized.includes("success") || normalized === "running" || normalized === "online"
      ? "bg-emerald-50 text-emerald-700"
      : normalized.includes("fail") ||
          normalized.includes("error") ||
          normalized === "offline" ||
          normalized === "denied" ||
          normalized === "cancelled"
        ? "bg-rose-50 text-rose-700"
        : normalized.includes("wait") || normalized.includes("approval") || normalized.includes("pending")
          ? "bg-amber-50 text-amber-700"
          : "bg-slate-100 text-slate-600";
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${color}`}>
      {status}
    </span>
  );
}
