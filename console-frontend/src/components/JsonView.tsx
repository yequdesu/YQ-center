export function JsonView({ data }: { data: unknown }) {
  return (
    <pre className="max-h-[260px] overflow-auto rounded-[var(--radius-sm)] bg-slate-950/90 p-3 text-[11px] leading-5 text-slate-100">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}
