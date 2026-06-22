import { Link, Outlet } from "@tanstack/react-router";
import { Bot, ClipboardList, Gauge, GitBranch, History, Network, ShieldCheck, Wrench } from "lucide-react";

const navItems = [
  { to: "/", label: "Dashboard", icon: Gauge },
  { to: "/chat", label: "Agent", icon: Bot },
  { to: "/nodes", label: "Nodes", icon: Network },
  { to: "/approvals", label: "Approvals", icon: ShieldCheck },
  { to: "/maintenance", label: "Maintenance", icon: Wrench },
  { to: "/invocations", label: "Invocations", icon: GitBranch },
  { to: "/jobs", label: "Jobs", icon: ClipboardList },
  { to: "/timeline", label: "Timeline", icon: History },
];

export function AppShell() {
  return (
    <div className="min-h-screen">
      <header className="fixed inset-x-0 top-0 z-20 flex h-[var(--topbar-height)] items-center border-b border-[var(--border)] bg-white/65 px-5 backdrop-blur-xl">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--accent-muted)] text-[var(--accent)]">
            <Bot size={18} />
          </div>
          <div>
            <div className="text-[14px] font-semibold text-[var(--text)]">YeQu Console</div>
            <div className="text-[11px] text-[var(--text-muted)]">Agent Runtime</div>
          </div>
        </div>
      </header>

      <div className="flex pt-[var(--topbar-height)]">
        <nav className="fixed bottom-0 left-0 top-[var(--topbar-height)] z-10 w-[220px] border-r border-[var(--border)] bg-white/55 p-3 backdrop-blur-xl">
          <div className="space-y-1">
            {navItems.map((item) => {
              const Icon = item.icon;
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  className="flex items-center gap-2 rounded-[var(--radius-sm)] px-3 py-2 text-[13px] text-[var(--text-muted)] transition hover:bg-[var(--accent-muted)] hover:text-[var(--accent)]"
                  activeProps={{ className: "bg-[var(--accent-muted)] text-[var(--accent)]" }}
                >
                  <Icon size={16} />
                  {item.label}
                </Link>
              );
            })}
          </div>
        </nav>
        <main className="ml-[220px] min-h-[calc(100vh-var(--topbar-height))] flex-1">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
