import { useState } from "react";
import { getTunnelPrefix } from "../../api/restClient";
import { hasSession, logout } from "../../api/auth";
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
  LogOut,
} from "lucide-react";
import { usePluginStore } from "../../store/pluginStore";
import { useConnectionStore } from "../../store/connectionStore";
import { useProjectStore } from "../../store/projectStore";
import { showError } from "../../store/toastStore";
import styles from "../../styles/sidebar.module.css";

// Inline splash shown in the simulator tab while the subprocess is starting.
// Mirrors the OpenAVC server startup splash so the experience feels
// continuous rather than dropping the user onto a blank page for several
// seconds. Replaced by the real simulator UI as soon as we navigate the tab.
const SIM_SPLASH_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OpenAVC Simulator</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #1a1a2e; color: #fff;
    font-family: 'Inter', system-ui, -apple-system, sans-serif;
    display: flex; align-items: center; justify-content: center;
    height: 100vh; overflow: hidden;
  }
  .container { text-align: center; }
  .logo {
    font-size: 2rem; font-weight: 700; letter-spacing: 0.02em;
    margin-bottom: 2rem; opacity: 0.95;
  }
  .logo span { color: #8AB493; }
  .spinner {
    width: 36px; height: 36px; margin: 0 auto 1.5rem;
    border: 3px solid rgba(255,255,255,0.1);
    border-top-color: #8AB493;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .message { font-size: 1rem; opacity: 0.6; }
</style>
</head>
<body>
<div class="container">
  <div class="logo">Open<span>AVC</span> Simulator</div>
  <div class="spinner"></div>
  <div class="message">Starting simulator&hellip;</div>
</div>
</body>
</html>`;

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
  const [simBusy, setSimBusy] = useState(false);
  const [showSimConfirm, setShowSimConfirm] = useState(false);

  const startSimulation = async () => {
    // Open the simulator tab synchronously so the user-gesture flag is still
    // valid and popup blockers don't intercept it. We navigate it once we
    // know the actual URL — or close it if the start fails.
    const simWindow = window.open("about:blank", "openavc-simulator");
    if (simWindow) {
      try {
        simWindow.document.open();
        simWindow.document.write(SIM_SPLASH_HTML);
        simWindow.document.close();
      } catch {
        // Cross-origin or other write failure — leave it blank, the navigate
        // below will replace whatever's there.
      }
    }
    setSimBusy(true);
    try {
      const res = await fetch(`${getTunnelPrefix()}/api/simulation/start`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        const url = data.ui_url || "http://localhost:19500";
        if (simWindow && !simWindow.closed) {
          simWindow.location.href = url;
        } else {
          // Popup was blocked — surface a clickable link so the user can open
          // it manually instead of leaving them stranded.
          showError(
            `Simulator started but the browser blocked the new tab. Open ${url} manually.`,
          );
        }
      } else {
        if (simWindow && !simWindow.closed) simWindow.close();
        const err = await res.json().catch(() => ({ detail: "Unknown error" }));
        showError(err.detail || "Failed to start simulation");
      }
    } catch {
      if (simWindow && !simWindow.closed) simWindow.close();
      showError("Failed to connect to server");
    } finally {
      setSimBusy(false);
    }
  };

  return (
    <nav className={styles.sidebar}>
      <div className={styles.logo} style={{ position: "relative" }}>
        <img src={`${import.meta.env.BASE_URL}logo-square.png`} alt="OpenAVC" style={{ width: 28, height: 28, borderRadius: 4 }} />
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
        <div role="dialog" aria-modal="true" aria-label="Start Device Simulation" style={{
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
                background: "var(--accent-bg)", color: "#fff",
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
      {hasSession() && (
        <button
          className={styles.navItem}
          onClick={() => {
            // Revoke server-side first so the token is dead even if another
            // tab copied it; the reload lands on the login screen either way.
            void logout().finally(() => window.location.reload());
          }}
          aria-label="Sign out"
          style={{ marginBottom: "var(--space-xs)" }}
        >
          <LogOut size={20} />
          <span className={styles.tooltip}>Sign out</span>
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
