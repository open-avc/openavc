import { useState } from "react";
import { getTunnelPrefix } from "../../api/restClient";
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
  StopCircle,
  Loader2,
} from "lucide-react";
import { usePluginStore } from "../../store/pluginStore";
import { useConnectionStore } from "../../store/connectionStore";
import { useProjectStore } from "../../store/projectStore";
import { showError } from "../../store/toastStore";
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
  { id: "scripts", label: "Code", icon: FileCode },
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
  const simUiUrl = String(useConnectionStore((s) => s.liveState["system.simulation_ui_url"]) ?? "");
  const [simBusy, setSimBusy] = useState(false);
  const [showSimConfirm, setShowSimConfirm] = useState(false);

  const startSimulation = async () => {
    setSimBusy(true);
    try {
      const res = await fetch(`${getTunnelPrefix()}/api/simulation/start`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        const url = data.ui_url || "http://localhost:19500";
        window.open(url, "openavc-simulator");
      } else {
        const err = await res.json().catch(() => ({ detail: "Unknown error" }));
        showError(err.detail || "Failed to start simulation");
      }
    } catch {
      showError("Failed to connect to server");
    } finally {
      setSimBusy(false);
    }
  };

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
        disabled={simBusy}
        onClick={async () => {
          if (simBusy) return;
          if (simulationActive) {
            setSimBusy(true);
            try {
              await fetch(`${getTunnelPrefix()}/api/simulation/stop`, { method: "POST" });
            } catch {
              showError("Failed to stop simulation");
            } finally {
              setSimBusy(false);
            }
          } else {
            // Show confirmation unless user has dismissed it
            if (localStorage.getItem("sim_confirm_dismissed") !== "true") {
              setShowSimConfirm(true);
            } else {
              await startSimulation();
            }
          }
        }}
        aria-label={simulationActive ? "Stop Simulation" : simBusy ? "Starting..." : "Simulate Devices"}
        style={{
          background: simulationActive ? "rgba(34, 197, 94, 0.15)" : undefined,
          color: simulationActive ? "#22c55e" : undefined,
          opacity: simBusy ? 0.6 : 1,
          marginBottom: "var(--space-xs)",
        }}
      >
        {simBusy ? <Loader2 size={20} style={{ animation: "spin 1s linear infinite" }} /> :
         simulationActive ? <StopCircle size={20} /> : <PlayCircle size={20} />}
        <span className={styles.tooltip}>
          {simulationActive ? "Stop Simulation" : simBusy ? "Starting Simulation..." : "Simulate Devices"}
        </span>
      </button>
      {showSimConfirm && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 10000,
          display: "flex", alignItems: "center", justifyContent: "center",
          background: "rgba(0,0,0,0.6)",
        }} onClick={(e) => { if (e.target === e.currentTarget) setShowSimConfirm(false); }}>
          <div style={{
            background: "var(--bg-surface)", border: "1px solid var(--border-color)",
            borderRadius: 8, padding: "24px 28px", maxWidth: 420, width: "90%",
            boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
          }}>
            <h3 style={{ margin: "0 0 12px", fontSize: 16 }}>Start Device Simulation</h3>
            <p style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.6, margin: "0 0 8px" }}>
              This will redirect all device connections to simulated virtual devices on your local machine.
            </p>
            <ul style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.8, margin: "0 0 16px", paddingLeft: 18 }}>
              <li>Devices will disconnect from real hardware</li>
              <li>Only drivers with simulation support will respond</li>
              <li>IP addresses and ports from your project are assumed correct</li>
              <li>Stop simulation to reconnect to real devices</li>
            </ul>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
              <input type="checkbox" id="sim-dismiss" style={{ accentColor: "var(--accent)" }} />
              <label htmlFor="sim-dismiss" style={{ fontSize: 12, color: "var(--text-muted)" }}>
                Don't show this again
              </label>
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button onClick={() => setShowSimConfirm(false)} style={{
                padding: "6px 16px", borderRadius: 4, fontSize: 13,
                background: "var(--bg-hover)", color: "var(--text-secondary)",
              }}>Cancel</button>
              <button onClick={async () => {
                const dismiss = (document.getElementById("sim-dismiss") as HTMLInputElement)?.checked;
                if (dismiss) localStorage.setItem("sim_confirm_dismissed", "true");
                setShowSimConfirm(false);
                await startSimulation();
              }} style={{
                padding: "6px 16px", borderRadius: 4, fontSize: 13,
                background: "var(--accent)", color: "#fff",
              }}>Start Simulation</button>
            </div>
          </div>
        </div>
      )}
      <button
        className={`${styles.navItem} ${activeView === "updates" ? styles.active : ""}`}
        onClick={() => onViewChange("updates")}
        aria-label={updateAvailable ? "Update available: v" + updateAvailable : "Updates"}
        style={{
          background: updateAvailable && activeView !== "updates" ? "rgba(33, 150, 243, 0.1)" : undefined,
          color: updateAvailable ? "var(--accent)" : undefined,
          marginBottom: "var(--space-sm)",
        }}
      >
        <ArrowUpCircle size={20} />
        <span className={styles.tooltip}>{updateAvailable ? "Update available: v" + updateAvailable : "Updates"}</span>
      </button>
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
