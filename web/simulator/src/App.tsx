import { Monitor, Power } from "lucide-react";
import { useSimStore } from "./store/useSimStore";
import { DeviceCard } from "./components/DeviceCard";
import { ProtocolLog } from "./components/ProtocolLog";
import { NetworkConditions } from "./components/NetworkConditions";

export default function App() {
  const { devices, log, connected, stopped, clearLog } = useSimStore();

  const handleShutdown = async () => {
    if (!confirm("Stop the simulator and close this window?")) return;
    try {
      await fetch("/api/shutdown", { method: "POST" });
    } catch { /* process is shutting down */ }
  };

  return (
    <div className="app">
      {/* Stopped overlay */}
      {stopped && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 10000,
          display: "flex", alignItems: "center", justifyContent: "center",
          background: "rgba(0,0,0,0.7)", backdropFilter: "blur(4px)",
        }}>
          <div style={{
            background: "var(--bg-surface)", border: "1px solid var(--border-color)",
            borderRadius: 8, padding: "32px 36px", maxWidth: 400, textAlign: "center",
            boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
          }}>
            <div style={{
              width: 48, height: 48, borderRadius: "50%", margin: "0 auto 16px",
              background: "rgba(239,68,68,0.15)", display: "flex",
              alignItems: "center", justifyContent: "center",
            }}>
              <Power size={24} color="#ef4444" />
            </div>
            <h3 style={{ margin: "0 0 8px", fontSize: 18 }}>Simulator Stopped</h3>
            <p style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.6, margin: "0 0 8px" }}>
              The simulator has been shut down. Device connections have been restored to their original addresses.
            </p>
            <p style={{ fontSize: 12, color: "var(--text-muted)", margin: "0 0 20px" }}>
              This page will automatically reconnect if simulation is started again.
              You can also close this tab.
            </p>
            <button
              onClick={() => window.close()}
              style={{
                padding: "8px 24px", borderRadius: 4, fontSize: 13,
                background: "var(--bg-hover)", color: "var(--text-primary)",
              }}
            >
              Close Tab
            </button>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="header">
        <div className="header-title">
          <Monitor size={20} />
          OpenAVC Simulator
          <span style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 400 }}>
            {devices.length} device{devices.length !== 1 ? "s" : ""}
          </span>
        </div>
        <div className="header-right">
          <NetworkConditions />
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-muted)" }}>
            <div className={`connection-dot ${connected ? "connected" : "disconnected"}`} />
            {connected ? "Connected" : "Disconnected"}
          </div>
          <button
            onClick={handleShutdown}
            title="Stop simulator"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              padding: "4px 10px",
              borderRadius: "var(--border-radius)",
              fontSize: 12,
              background: "rgba(239, 68, 68, 0.15)",
              color: "#ef4444",
            }}
          >
            <Power size={14} />
            Stop
          </button>
        </div>
      </div>

      {/* Main content */}
      <div className="main-content">
        {/* Device grid */}
        <div className="device-grid">
          {devices.length === 0 && !stopped && (
            <div className="empty-state">
              <h3>No simulated devices</h3>
              <p>Start the simulator with driver paths and device configuration to see devices here.</p>
            </div>
          )}
          {devices.map((device) => (
            <DeviceCard key={device.device_id} device={device} />
          ))}
        </div>

        {/* Protocol log */}
        <ProtocolLog entries={log} onClear={clearLog} />
      </div>
    </div>
  );
}
