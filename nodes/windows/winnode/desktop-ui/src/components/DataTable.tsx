interface DataTableColumn<T> {
  key: string;
  header?: string;
  label?: string;
  width?: string;
  render?: (value: unknown, row: T) => React.ReactNode;
}

interface DataTableProps<T> {
  columns: DataTableColumn<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  emptyText?: string;
}

export function DataTable<T extends Record<string, unknown>>({
  columns,
  rows,
  rowKey,
  onRowClick,
  emptyText = "No data",
}: DataTableProps<T>) {
  if (rows.length === 0) {
    return (
      <div className="text-center py-12 text-sm text-[var(--text-subtle)]">
        {emptyText}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[var(--border)]">
            {columns.map((col) => (
              <th
                key={col.key}
                className="text-left py-2.5 px-3 text-xs font-semibold text-[var(--text-muted)] uppercase tracking-wider"
                style={{ width: col.width }}
              >
                {col.header || col.label || col.key}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              className={`border-b border-[var(--border)] transition-colors ${onRowClick ? "cursor-pointer hover:bg-[var(--bg-subtle)]" : ""}`}
              onClick={() => onRowClick?.(row)}
            >
              {columns.map((col) => (
                <td key={col.key} className="py-2.5 px-3 text-[var(--text)] leading-5">
                  {col.render
                    ? col.render(row[col.key], row)
                    : String(row[col.key] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
