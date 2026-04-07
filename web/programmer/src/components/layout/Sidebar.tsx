import {
  Monitor,
  Cpu,
  Layout,
  LayoutDashboard,
  Zap,
  FileCode,
  Network,
  ScrollText,
  Variable,
  Cloud,
  Bot,
  Plug,
  ArrowUpCircle,
  Settings,
  PlayCircle,
} from "lucide-react";
import { usePluginStore } from "../../store/pluginStore";
import { useConnectionStore } from "../../store/connectionStore";
import { useProjectStore } from "../../store/projectStore";
import styles from "../../styles/sidebar.module.css";

export type ViewId =
  | "dashboard"
  | "project"
  | "devices"
  | "variables"
  | "ui-builder"
  | "macros"
  | "scripts"
  | "plugins"
  | "isc"
  | "ai"
  | "cloud"
  | "log"
  | "settings"
  | "updates"
  | `plugin-view:${string}`;

interface SidebarProps {
  activeView: ViewId;
  onViewChange: (view: ViewId) => void;
}

const navItems: { id: ViewId; label: string; icon: typeof Monitor }[] = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "project", label: "Program", icon: Monitor },
  { id: "devices", label: "Devices", icon: Cpu },
  { id: "variables", label: "State", icon: Variable },
  { id: "ui-builder", label: "UI Builder", icon: Layout },
  { id: "macros", label: "Macros", icon: Zap },
  { id: "scripts", label: "Scripts", icon: FileCode },
  { id: "plugins", label: "Plugins", icon: Plug },
  { id: "isc", label: "Inter-System", icon: Network },
  { id: "ai", label: "AI Assistant", icon: Bot },
  { id: "cloud", label: "Cloud", icon: Cloud },
  { id: "log", label: "Log", icon: ScrollText },
  { id: "settings", label: "Settings", icon: Settings },
];

export function Sidebar({ activeView, onViewChange }: SidebarProps) {
  const pluginViews = usePluginStore((s) => s.extensions.views);
  const connected = useConnectionStore((s) => s.connected);
  const updateAvailable = String(useConnectionStore((s) => s.liveState["system.update_available"]) ?? "");
  const dirty = useProjectStore((s) => s.dirty);
  const simulationActive = Boolean(useConnectionStore((s) => s.liveState["system.simulation_active"]));

  return (
    <nav className={styles.sidebar}>
      <div className={styles.logo} style={{ position: "relative" }}>
        <Monitor size={24} />
        {dirty && (
          <div
            title="Unsaved changes"
            style={{
              position: "absolute",
              top: 6,
              right: 6,
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: "#f59e0b",
            }}
          />
        )}
      </div>
      {navItems.map((item) => (
        <button
          key={item.id}
          className={`${styles.navItem} ${activeView === item.id ? styles.active : ""}`}
          onClick={() => onViewChange(item.id)}
          aria-label={item.label}
          aria-current={activeView === item.id ? "page" : undefined}
        >
          <item.icon size={20} />
          <span className={styles.tooltip}>{item.label}</span>
        </button>
      ))}
      {pluginViews.length > 0 && (
        <div style={{ width: "100%", borderTop: "1px solid var(--border-color)", margin: "var(--space-xs) 0", display: "flex", flexDirection: "column", alignItems: "center" }} />
      )}
      {pluginViews.map((view) => {
        const viewId: ViewId = `plugin-view:${view.plugin_id}.${view.id}`;
        return (
          <button
            key={viewId}
            className={`${styles.navItem} ${activeView === viewId ? styles.active : ""}`}
            onClick={() => onViewChange(viewId)}
            aria-label={view.label}
            aria-current={activeView === viewId ? "page" : undefined}
          >
            <Plug size={16} />
            <span className={styles.tooltip}>{view.label}</span>
          </button>
        );
      })}
      <div className={styles.spacer} />
      <button
        className={styles.navItem}
        onClick={async () => {
          try {
            if (simulationActive) {
              await fetch("/api/simulation/stop", { method: "POST" });
            } else {
              await fetch("/api/simulation/start", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({}),
              });
              // Open simulator UI in new tab after a short delay
              setTimeout(() => window.open("http://localhost:19500", "_blank"), 2500);
            }
          } catch { /* handled via state update */ }
        }}
        aria-label={simulationActive ? "Stop Simulation" : "Start Simulation"}
        style={{
          background: simulationActive ? "rgba(34, 197, 94, 0.15)" : undefined,
          color: simulationActive ? "#22c55e" : undefined,
          marginBottom: "var(--space-xs)",
        }}
      >
        <PlayCircle size={20} />
        <span className={styles.tooltip}>
          {simulationActive ? "Simulation Active (click to stop)" : "Simulate Devices"}
        </span>
      </button>
      {updateAvailable && (
        <button
          className={`${styles.navItem} ${activeView === "updates" ? styles.active : ""}`}
          onClick={() => onViewChange("updates")}
          aria-label={"Update available: v" + updateAvailable}
          style={{
            background: activeView === "updates" ? undefined : "rgba(33, 150, 243, 0.1)",
            color: "var(--accent)",
            marginBottom: "var(--space-sm)",
          }}
        >
          <ArrowUpCircle size={20} />
          <span className={styles.tooltip}>{"Update available: v" + updateAvailable}</span>
        </button>
      )}
      <div className={styles.connectionStatus} role="status" aria-label={connected ? "Server connected" : "Server disconnected"}>
        <div
          className={styles.statusDot}
          style={{ background: connected ? "var(--success, #4caf50)" : "var(--error, #f44336)" }}
          aria-hidden="true"
        />
        <span className={styles.tooltip}>
          {connected ? "Server connected" : "Server disconnected"}
        </span>
      </div>
    </nav>
  );
}
