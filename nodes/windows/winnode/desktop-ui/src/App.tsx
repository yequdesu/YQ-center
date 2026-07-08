import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  LayoutDashboard,
  Wrench,
  Server,
  Cpu,
  ClipboardList,
  ScrollText,
  FileArchive,
  Settings,
} from "lucide-react";
import { call } from "./bridge/client";
import { ToastProvider } from "./components/Toast";
import { StatusDot } from "./components/StatusDot";
import { Badge } from "./components/Badge";
import { pageTransition } from "./components/motion";

import SetupPage from "./pages/SetupPage";
import DashboardPage from "./pages/DashboardPage";
import ServicePage from "./pages/ServicePage";
import CapabilitiesPage from "./pages/CapabilitiesPage";
import JobsPage from "./pages/JobsPage";
import LogsPage from "./pages/LogsPage";
import DiagnosticsPage from "./pages/DiagnosticsPage";
import SettingsPage from "./pages/SettingsPage";

const NAV_ITEMS = [
  { key: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { key: "setup", label: "Setup", icon: Wrench },
  { key: "service", label: "Service", icon: Server },
  { key: "capabilities", label: "Capabilities", icon: Cpu },
  { key: "jobs", label: "Jobs", icon: ClipboardList },
  { key: "logs", label: "Logs", icon: ScrollText },
  { key: "diagnostics", label: "Diagnostics", icon: FileArchive },
  { key: "settings", label: "Settings", icon: Settings },
];

export function App() {
  const [activePage, setActivePage] = useState("dashboard");
  const [daemonRunning, setDaemonRunning] = useState(false);
  const [centerConnected, setCenterConnected] = useState(false);
  const [centerReachable, setCenterReachable] = useState(false);
  const [nodeId, setNodeId] = useState("winClient");
  const [configPath, setConfigPath] = useState("config.local.yaml");

  const refreshStatus = useCallback(async () => {
    const res = await call<{
      node_id: string | null;
      config_path: string;
      daemon_running: boolean;
      center_connected: boolean;
      center_reachable: boolean;
    }>("get_local_status");
    if (res.ok && res.data) {
      if (res.data.node_id) setNodeId(res.data.node_id);
      if (res.data.config_path) setConfigPath(res.data.config_path);
      setDaemonRunning(res.data.daemon_running || false);
      setCenterConnected(res.data.center_connected || false);
      setCenterReachable(res.data.center_reachable || false);
    }
  }, []);

  useEffect(() => {
    refreshStatus();
    const timer = setInterval(refreshStatus, 5000);
    window.addEventListener("yequ-status-refresh", refreshStatus);
    return () => {
      clearInterval(timer);
      window.removeEventListener("yequ-status-refresh", refreshStatus);
    };
  }, [refreshStatus]);

  const PageComponent =
    activePage === "setup"
      ? SetupPage
      : activePage === "dashboard"
      ? DashboardPage
      : activePage === "service"
      ? ServicePage
      : activePage === "capabilities"
      ? CapabilitiesPage
      : activePage === "jobs"
      ? JobsPage
      : activePage === "logs"
      ? LogsPage
      : activePage === "diagnostics"
      ? DiagnosticsPage
      : SettingsPage;

  return (
    <ToastProvider>
      <div className="flex flex-col h-full">
        <header
          className="glass flex items-center justify-between px-5 shrink-0"
          style={{ height: 52, zIndex: 10 }}
        >
          <div className="flex items-center gap-3">
            <span className="text-sm font-semibold text-[var(--text)]">YeQu Windows Client</span>
          </div>
          <div className="flex items-center gap-4">
            <StatusDot
              status={centerConnected || centerReachable ? "online" : "offline"}
              label={
                centerConnected
                  ? "Center Connected"
                  : centerReachable
                  ? "Center Reachable"
                  : "Center Unreachable"
              }
            />
            <StatusDot
              status={daemonRunning ? "online" : "offline"}
              label={daemonRunning ? "Daemon Running" : "Daemon Stopped"}
            />
            <Badge variant="muted">{nodeId}</Badge>
            <span className="text-xs text-[var(--text-subtle)]">{configPath}</span>
          </div>
        </header>

        <div className="flex flex-1 min-h-0">
          <nav
            className="glass flex flex-col gap-0.5 px-3 py-3 shrink-0"
            style={{ width: 220, zIndex: 5 }}
          >
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const isActive = activePage === item.key;
              return (
                <button
                  key={item.key}
                  onClick={() => setActivePage(item.key)}
                  className={`flex items-center gap-2.5 px-3 py-2 text-sm rounded-sm transition-colors ${
                    isActive
                      ? "bg-[var(--accent-muted)] text-[var(--accent)] font-medium"
                      : "text-[var(--text-muted)] hover:bg-[var(--bg-subtle)] hover:text-[var(--text)]"
                  }`}
                >
                  <Icon size={16} />
                  {item.label}
                </button>
              );
            })}
          </nav>

          <main className="flex-1 p-5 overflow-auto">
            <AnimatePresence mode="wait">
              <motion.div
                key={activePage}
                variants={pageTransition}
                initial="initial"
                animate="animate"
                exit="exit"
                transition={{ duration: 0.15 }}
              >
                <PageComponent />
              </motion.div>
            </AnimatePresence>
          </main>
        </div>
      </div>
    </ToastProvider>
  );
}
