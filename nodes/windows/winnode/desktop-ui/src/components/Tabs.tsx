interface TabsProps {
  tabs: { key?: string; id?: string; label: string }[];
  active: string;
  onChange: (key: string) => void;
}

export function Tabs({ tabs, active, onChange }: TabsProps) {
  return (
    <div className="flex gap-0 border-b border-[var(--border)] mb-4">
      {tabs.map((tab) => {
        const tabKey = tab.key || tab.id || "";
        return (
        <button
          key={tabKey}
          onClick={() => onChange(tabKey)}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-[1px] ${
            active === tabKey
              ? "border-[var(--accent)] text-[var(--accent)]"
              : "border-transparent text-[var(--text-muted)] hover:text-[var(--text)]"
          }`}
        >
          {tab.label}
        </button>
        );
      })}
    </div>
  );
}
