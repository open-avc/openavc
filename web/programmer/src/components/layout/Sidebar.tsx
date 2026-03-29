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
} from "lucide-react";
import { usePluginStore } from "../../store/pluginStore";
import { useConnectionStore } from "../../store/connectionStore";
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
];

export function Sidebar({ activeView, onViewChange }: SidebarProps) {
  const pluginViews = usePluginStore((s) => s.extensions.views);
  const connected = useConnectionStore((s) => s.connected);

  return (
    <nav className={styles.sidebar}>
      <div className={styles.logo}>
        <Monitor size={24} />
      </div>
      {navItems.map((item) => (
        <button
          key={item.id}
          className={`${styles.navItem} ${activeView === item.id ? styles.active : ""}`}
          onClick={() => onViewChange(item.id)}
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
          >
            <Plug size={16} />
            <span className={styles.tooltip}>{view.label}</span>
          </button>
        );
      })}
      <div className={styles.spacer} />
      <div className={styles.connectionStatus}>
        <div
          className={styles.statusDot}
          style={{ background: connected ? "var(--success, #4caf50)" : "var(--error, #f44336)" }}
        />
        <span className={styles.tooltip}>
          {connected ? "Server connected" : "Server disconnected"}
        </span>
      </div>
    </nav>
  );
}
