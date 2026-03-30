import { useState, useEffect } from "react";
import { Cpu, Zap, Cloud, FileCode, AlertTriangle, Clock, ArrowRight, ArrowUpCircle } from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { DeviceStatusDot } from "../components/shared/DeviceStatusDot";
import { useProjectStore } from "../store/projectStore";
import { useConnectionStore } from "../store/connectionStore";
import { useLogStore } from "../store/logStore";
import { useNavigationStore } from "../store/navigationStore";
import { StatusCardSlot } from "../components/plugins/PluginExtensions";
import * as api from "../api/restClient";
import type { CloudStatus } from "../api/restClient";

export function DashboardView() {
  const project = useProjectStore((s) => s.project);
  const liveState = useConnectionStore((s) => s.liveState);
  const [cloudStatus, setCloudStatus] = useState<CloudStatus | null>(null);
  const [systemStatus, setSystemStatus] = useState<Record<string, unknown> | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);

  useEffect(() => {
    api.getCloudStatus().then(s => setCloudStatus(s)).catch(console.error);
    api.getSystemStatus().then(s => setSystemStatus(s)).catch(console.error);
    const interval = setInterval(() => {
      api.getSystemStatus().then(s => setSystemStatus(s)).catch(console.error);
      api.getCloudStatus().then(s => setCloudStatus(s)).catch(console.error);
      setRefreshTick(t => t + 1); // trigger re-read of activity feed
    }, 30000);
    return () => clearInterval(interval);
  }, []);

  if (!project) {
    return <ViewContainer title="Dashboard"><p style={{ color: "var(--text-muted)" }}>Loading...</p></ViewContainer>;
  }

  const devices = project.devices;
  const connectedCount = devices.filter(d => {
    if (d.enabled === false) return false;
    return liveState[`device.${d.id}.connected`] === true;
  }).length;
  const enabledCount = devices.filter(d => d.enabled !== false).length;
  const disconnectedCount = enabledCount - connectedCount;

  const triggerCount = project.macros.reduce((sum, m) => sum + (m.triggers?.filter(t => t.enabled !== false).length ?? 0), 0);
  const scriptCount = project.scripts.filter(s => s.enabled).length;

  const isCloudConnected = cloudStatus?.connected === true;
  const isCloudEnabled = cloudStatus?.enabled === true;

  const uptimeSeconds = typeof (systemStatus as any)?.uptime_seconds === "number"
    ? (systemStatus as any).uptime_seconds as number
    : undefined;
  const formatUptime = (s: number) => {
    if (s < 60) return `${Math.floor(s)}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m`;
    const h = Math.floor(s / 3600);
    const mn = Math.floor((s % 3600) / 60);
    return `${h}h ${mn}m`;
  };

  const iscEnabled = project.isc?.enabled === true;
  const iscPeerCount = (project.isc?.peers as unknown[])?.length ?? 0;
  const iscPatternCount = (project.isc?.shared_state as unknown[])?.length ?? 0;

  const activeTriggers = project.macros.flatMap(m =>
    (m.triggers ?? []).filter(t => t.enabled !== false).map(t => ({
      macroName: String(m.name),
      triggerType: String(t.type),
      detail: String(
        t.type === "schedule" ? t.cron ?? ""
          : t.type === "state_change" ? `${t.state_key ?? ""} ${t.state_operator ?? "any"}`
          : t.type === "event" ? t.event_pattern ?? ""
          : t.type === "startup" ? `delay ${t.delay_seconds ?? 0}s`
          : ""
      ),
    }))
  );

  const trackedVars = project.variables.filter(v => v.dashboard);

  // Snapshot of recent log entries (non-reactive to avoid rapid re-renders)
  const logEntries = useLogStore.getState().logEntries;
  const recentActivity = logEntries
    .filter(e => e.level !== "DEBUG")
    .slice(-15)
    .reverse();

  const cardStyle: React.CSSProperties = {
    background: "var(--bg-surface)",
    border: "1px solid var(--border-color)",
    borderRadius: "var(--border-radius)",
    padding: "var(--space-lg)",
  };

  const sectionTitle: React.CSSProperties = {
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    textTransform: "uppercase",
    letterSpacing: "0.5px",
    fontWeight: 600,
    marginBottom: "var(--space-md)",
  };

  return (
    <ViewContainer title="Dashboard">
      <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: "var(--space-xl)", maxWidth: 1100 }}>
        {/* Main column */}
        <div>
          {/* Top bar */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--space-lg)" }}>
            <div>
              <div style={{ fontSize: "var(--font-size-lg)", fontWeight: 600 }}>{String(project.project.name)}</div>
              <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
                {"OpenAVC " + String(project.openavc_version)}
              </div>
            </div>
            {uptimeSeconds != null && (
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
                <Clock size={14} />
                <span>{"Uptime: " + formatUptime(uptimeSeconds)}</span>
              </div>
            )}
          </div>

          {/* Summary row */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "var(--space-md)", marginBottom: "var(--space-xl)" }}>
            <div style={cardStyle}>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", marginBottom: "var(--space-xs)" }}>
                <Cpu size={14} style={{ color: "var(--accent)" }} />
                <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>Devices</span>
              </div>
              <div style={{ fontSize: "var(--font-size-xl)", fontWeight: 700 }}>{String(connectedCount) + "/" + String(enabledCount)}</div>
            </div>
            <div style={cardStyle}>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", marginBottom: "var(--space-xs)" }}>
                <Zap size={14} style={{ color: "#f59e0b" }} />
                <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>Triggers</span>
              </div>
              <div style={{ fontSize: "var(--font-size-xl)", fontWeight: 700 }}>{String(triggerCount)}</div>
            </div>
            <div style={cardStyle}>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", marginBottom: "var(--space-xs)" }}>
                <FileCode size={14} style={{ color: "#8b5cf6" }} />
                <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>Scripts</span>
              </div>
              <div style={{ fontSize: "var(--font-size-xl)", fontWeight: 700 }}>{String(scriptCount)}</div>
            </div>
            <div style={cardStyle}>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", marginBottom: "var(--space-xs)" }}>
                <Cloud size={14} style={{ color: isCloudConnected ? "var(--color-success)" : "var(--text-muted)" }} />
                <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>Cloud</span>
              </div>
              <div style={{ fontSize: "var(--font-size-xl)", fontWeight: 700, color: !isCloudEnabled ? "var(--text-muted)" : isCloudConnected ? "var(--color-success)" : "var(--color-error)" }}>
                {!isCloudEnabled ? "—" : isCloudConnected ? "Online" : "Offline"}
              </div>
            </div>
          </div>

          {/* Update available card */}
          {!!liveState["system.update_available"] && (
            <div
              onClick={() => useNavigationStore.getState().navigateTo("updates")}
              style={{
                ...cardStyle,
                marginBottom: "var(--space-xl)",
                borderColor: "rgba(33,150,243,0.3)",
                display: "flex",
                alignItems: "center",
                gap: "var(--space-md)",
                cursor: "pointer",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "var(--bg-surface)")}
            >
              <ArrowUpCircle size={20} style={{ color: "var(--accent)", flexShrink: 0 }} />
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 500, fontSize: "var(--font-size-sm)" }}>
                  {"OpenAVC v" + String(liveState["system.update_available"]) + " available"}
                </div>
                <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
                  View changelog and install
                </div>
              </div>
              <ArrowRight size={14} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
            </div>
          )}

          {/* Getting Started — shown when project is empty */}
          {devices.length === 0 && project.macros.length === 0 && (
            <div style={{ ...cardStyle, marginBottom: "var(--space-xl)", borderColor: "var(--accent)", background: "var(--color-info-bg)" }}>
              <div style={{ fontSize: "var(--font-size-md)", fontWeight: 600, marginBottom: "var(--space-md)" }}>
                Getting Started
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
                {[
                  { step: "1", label: "Add a device", desc: "Connect to a projector, display, switcher, or audio processor.", view: "devices" as const },
                  { step: "2", label: "Create a macro", desc: "Build a sequence of commands — power on, switch inputs, set levels.", view: "macros" as const },
                  { step: "3", label: "Build your panel", desc: "Design a touch panel UI with buttons, sliders, and status indicators.", view: "ui-builder" as const },
                ].map((item) => (
                  <div
                    key={item.step}
                    onClick={() => useNavigationStore.getState().navigateTo(item.view)}
                    style={{
                      display: "flex", alignItems: "center", gap: "var(--space-md)",
                      padding: "var(--space-sm) var(--space-md)", borderRadius: "var(--border-radius)",
                      cursor: "pointer", background: "var(--bg-surface)",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "var(--bg-surface)")}
                  >
                    <div style={{ width: 24, height: 24, borderRadius: "50%", background: "var(--accent)", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 700, flexShrink: 0 }}>
                      {item.step}
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 500, fontSize: "var(--font-size-sm)" }}>{item.label}</div>
                      <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{item.desc}</div>
                    </div>
                    <ArrowRight size={14} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Disconnected warning */}
          {disconnectedCount > 0 && (
            <div style={{
              ...cardStyle,
              borderColor: "rgba(239,68,68,0.3)",
              display: "flex",
              alignItems: "center",
              gap: "var(--space-md)",
              marginBottom: "var(--space-xl)",
            }}>
              <AlertTriangle size={18} style={{ color: "var(--color-error)", flexShrink: 0 }} />
              <div>
                <div style={{ fontWeight: 500, fontSize: "var(--font-size-sm)" }}>
                  {String(disconnectedCount) + " device" + (disconnectedCount !== 1 ? "s" : "") + " disconnected"}
                </div>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                  {devices.filter(d => d.enabled !== false && liveState[`device.${d.id}.connected`] !== true).map(d => String(d.name)).join(", ")}
                </div>
              </div>
            </div>
          )}

          {/* Device grid */}
          <div style={{ marginBottom: "var(--space-xl)" }}>
            <h3 style={sectionTitle}>Devices</h3>
            {devices.length === 0 ? (
              <div style={{ ...cardStyle, color: "var(--text-muted)", textAlign: "center", fontSize: "var(--font-size-sm)" }}>
                No devices configured yet.
              </div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: "var(--space-sm)" }}>
                {devices.map(dev => {
                  const isEnabled = dev.enabled !== false;
                  const isConnected = liveState[`device.${dev.id}.connected`] === true;
                  return (
                    <div
                      key={dev.id}
                      style={{
                        ...cardStyle,
                        padding: "var(--space-sm) var(--space-md)",
                        opacity: isEnabled ? 1 : 0.5,
                        borderColor: !isEnabled ? "var(--border-color)" : isConnected ? "rgba(76,175,80,0.3)" : "rgba(239,68,68,0.3)",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                        <DeviceStatusDot connected={isConnected} />
                        <div style={{ minWidth: 0 }}>
                          <div style={{ fontWeight: 500, fontSize: "var(--font-size-sm)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {String(dev.name)}
                          </div>
                          <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                            {String(dev.driver)}{!isEnabled ? " (disabled)" : ""}
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Active Triggers */}
          {activeTriggers.length > 0 && (
            <div style={{ marginBottom: "var(--space-xl)" }}>
              <h3 style={sectionTitle}>Active Triggers</h3>
              <div style={{ ...cardStyle, padding: 0, overflow: "hidden" }}>
                {activeTriggers.map((t, i) => (
                  <div
                    key={i}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--space-sm)",
                      padding: "var(--space-sm) var(--space-md)",
                      borderTop: i > 0 ? "1px solid var(--border-color)" : undefined,
                      fontSize: "var(--font-size-sm)",
                    }}
                  >
                    <span style={{
                      fontSize: 10,
                      fontWeight: 600,
                      color: "var(--text-muted)",
                      background: "var(--bg-hover)",
                      padding: "1px 6px",
                      borderRadius: 3,
                      textTransform: "uppercase" as const,
                      letterSpacing: "0.5px",
                      flexShrink: 0,
                    }}>
                      {t.triggerType === "schedule" ? "cron" : t.triggerType === "state_change" ? "state" : t.triggerType}
                    </span>
                    <span style={{ fontWeight: 500 }}>{t.macroName}</span>
                    {t.detail && (
                      <code style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                        {t.detail}
                      </code>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Right sidebar */}
        <div>
          {/* Tracked Variables */}
          {trackedVars.length > 0 && (
            <div style={{ marginBottom: "var(--space-xl)" }}>
              <h3 style={sectionTitle}>Variables</h3>
              <div style={{ ...cardStyle, padding: 0, overflow: "hidden" }}>
                {trackedVars.map((v, i) => {
                  const live = liveState[`var.${v.id}`];
                  return (
                    <div
                      key={v.id}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        padding: "var(--space-sm) var(--space-md)",
                        borderTop: i > 0 ? "1px solid var(--border-color)" : undefined,
                      }}
                    >
                      <div>
                        <div style={{ fontSize: "var(--font-size-sm)", fontWeight: 500 }}>
                          {String(v.label || v.id)}
                        </div>
                        <code style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                          {"var." + String(v.id)}
                        </code>
                      </div>
                      <div style={{
                        fontSize: "var(--font-size-sm)",
                        fontWeight: 600,
                        fontFamily: "var(--font-mono)",
                        color: live !== undefined ? "var(--text-primary)" : "var(--text-muted)",
                        background: "var(--bg-hover)",
                        padding: "2px 8px",
                        borderRadius: "var(--border-radius)",
                      }}>
                        {live !== undefined ? String(live) : "—"}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* ISC Status */}
          {iscEnabled && (
            <div style={{ marginBottom: "var(--space-xl)" }}>
              <h3 style={sectionTitle}>Inter-System</h3>
              <div style={{ ...cardStyle, fontSize: "var(--font-size-sm)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                  <DeviceStatusDot connected={true} size={8} />
                  <span>ISC Enabled</span>
                </div>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: "var(--space-xs)" }}>
                  {String(iscPeerCount) + " manual peer" + (iscPeerCount !== 1 ? "s" : "") + " · " + String(iscPatternCount) + " shared pattern" + (iscPatternCount !== 1 ? "s" : "")}
                </div>
              </div>
            </div>
          )}

          {/* Recent Activity */}
          <div>
            <h3 style={sectionTitle}>Recent Activity</h3>
            <div style={{ ...cardStyle, padding: 0, overflow: "hidden", maxHeight: 400, overflowY: "auto" }}>
              {recentActivity.length === 0 ? (
                <div style={{ padding: "var(--space-lg)", color: "var(--text-muted)", textAlign: "center", fontSize: "var(--font-size-sm)" }}>
                  No activity yet. Events will appear here as the system runs.
                </div>
              ) : (
                recentActivity.map((e, i) => (
                  <div
                    key={i}
                    style={{
                      padding: "var(--space-xs) var(--space-md)",
                      borderTop: i > 0 ? "1px solid var(--border-color)" : undefined,
                      fontSize: 12,
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-sm)" }}>
                      <span style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)", fontSize: 11, flexShrink: 0 }}>
                        {new Date(e.timestamp * 1000).toLocaleTimeString(undefined, { hour12: false })}
                      </span>
                      {e.level === "ERROR" && (
                        <span style={{ color: "var(--color-error)", fontWeight: 600 }}>{"ERROR"}</span>
                      )}
                      {e.level === "WARNING" && (
                        <span style={{ color: "#f59e0b" }}>{"WARN"}</span>
                      )}
                      <span style={{ color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {String(e.message)}
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        {/* Plugin Status Cards */}
        <StatusCardSlot />
      </div>
    </ViewContainer>
  );
}
