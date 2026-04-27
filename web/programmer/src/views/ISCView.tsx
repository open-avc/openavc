import { useState, useEffect, useCallback } from "react";
import { getTunnelPrefix } from "../api/restClient";
import {
  Network,
  Wifi,
  WifiOff,
  Plus,
  Trash2,
  Send,
  RefreshCw,
  Shield,
  Eye,
} from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { useProjectStore } from "../store/projectStore";
import { useConnectionStore } from "../store/connectionStore";

interface ISCStatus {
  enabled: boolean;
  instance_id?: string;
  instance_name?: string;
  peer_count?: number;
  connected_count?: number;
  shared_patterns?: string[];
  auth_key_set?: boolean;
}

interface ISCPeer {
  instance_id: string;
  name: string;
  host: string;
  port: number;
  version: string;
  connected: boolean;
  source: string;
  last_seen: number;
}

export function ISCView() {
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const liveState = useConnectionStore((s) => s.liveState);

  const [status, setStatus] = useState<ISCStatus>({ enabled: false });
  const [peers, setPeers] = useState<ISCPeer[]>([]);

  // Config editing
  const [newPattern, setNewPattern] = useState("");
  const [newPeer, setNewPeer] = useState("");
  const [authKey, setAuthKey] = useState("");
  const [authVisible, setAuthVisible] = useState(false);

  // Load ISC config from project
  useEffect(() => {
    if (project?.isc) {
      setAuthKey(project.isc.auth_key ?? "");
    }
  }, [project?.isc?.auth_key]);

  const fetchStatus = useCallback(async () => {
    try {
      const prefix = getTunnelPrefix();
      const [statusRes, peersRes] = await Promise.all([
        fetch(`${prefix}/api/isc/status`).then((r) => r.json()),
        fetch(`${prefix}/api/isc/instances`).then((r) => r.json()),
      ]);
      setStatus(statusRes);
      setPeers(Array.isArray(peersRes) ? peersRes : []);
    } catch (err) {
      console.warn("ISC status fetch failed:", err);
      setStatus({ enabled: false });
      setPeers([]);
    }
  }, []);

  // Only poll when ISC is enabled in the project
  useEffect(() => {
    if (!project?.isc?.enabled) return;
    fetchStatus();
    const interval = setInterval(fetchStatus, 3000);
    return () => clearInterval(interval);
  }, [fetchStatus, project?.isc?.enabled]);

  const isc = project?.isc;
  const enabled = isc?.enabled ?? false;
  const sharedState = isc?.shared_state ?? [];
  const manualPeers = isc?.peers ?? [];

  const handleToggleEnabled = useCallback(() => {
    if (!project) return;
    update({
      isc: { ...project.isc, enabled: !enabled },
    });
    useProjectStore.getState().debouncedSave();
  }, [project, enabled, update]);

  const handleAddPattern = useCallback(() => {
    if (!project || !newPattern.trim()) return;
    const pattern = newPattern.trim();
    if (sharedState.includes(pattern)) return;
    update({
      isc: { ...project.isc, shared_state: [...sharedState, pattern] },
    });
    setNewPattern("");
    useProjectStore.getState().debouncedSave();
  }, [project, newPattern, sharedState, update]);

  const handleRemovePattern = useCallback(
    (pattern: string) => {
      if (!project) return;
      update({
        isc: {
          ...project.isc,
          shared_state: sharedState.filter((p) => p !== pattern),
        },
      });
      useProjectStore.getState().debouncedSave();
    },
    [project, sharedState, update]
  );

  const handleAddPeer = useCallback(() => {
    if (!project || !newPeer.trim()) return;
    const addr = newPeer.trim();
    if (!/^[\w.-]+(:\d{1,5})?$/.test(addr)) return;
    if (manualPeers.includes(addr)) return;
    update({
      isc: { ...project.isc, peers: [...manualPeers, addr] },
    });
    setNewPeer("");
    useProjectStore.getState().debouncedSave();
  }, [project, newPeer, manualPeers, update]);

  const handleRemovePeer = useCallback(
    (addr: string) => {
      if (!project) return;
      update({
        isc: {
          ...project.isc,
          peers: manualPeers.filter((p) => p !== addr),
        },
      });
      useProjectStore.getState().debouncedSave();
    },
    [project, manualPeers, update]
  );

  const handleSaveAuthKey = useCallback(() => {
    if (!project) return;
    update({
      isc: { ...project.isc, auth_key: authKey },
    });
    useProjectStore.getState().debouncedSave();
  }, [project, authKey, update]);

  const connectedCount = peers.filter((p) => p.connected).length;
  const iscEnabled = liveState["system.isc.enabled"] === true;

  return (
    <ViewContainer
      title="Inter-System Communication"
      actions={
        <button onClick={fetchStatus} style={headerBtnStyle} title="Refresh">
          <RefreshCw size={14} /> Refresh
        </button>
      }
    >
      <div style={{ display: "flex", gap: "var(--space-xl)", flexWrap: "wrap" }}>
        {/* Left column: Status + Peers */}
        <div style={{ flex: "1 1 400px", minWidth: 350 }}>
          {/* Status card */}
          <div style={cardStyle}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--space-md)" }}>
              <h3 style={sectionTitle}>Status</h3>
              <label style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", cursor: "pointer" }}>
                <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>
                  {enabled ? "Enabled" : "Disabled"}
                </span>
                <div
                  onClick={handleToggleEnabled}
                  style={{
                    width: 40,
                    height: 22,
                    borderRadius: 11,
                    background: enabled ? "var(--accent-bg)" : "var(--bg-hover)",
                    position: "relative",
                    cursor: "pointer",
                    transition: "background 0.2s",
                  }}
                >
                  <div
                    style={{
                      width: 18,
                      height: 18,
                      borderRadius: "50%",
                      background: "#fff",
                      position: "absolute",
                      top: 2,
                      left: enabled ? 20 : 2,
                      transition: "left 0.2s",
                    }}
                  />
                </div>
              </label>
            </div>

            {iscEnabled && status.enabled ? (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-sm)" }}>
                <div style={statBox}>
                  <div style={statLabel}>Instance ID</div>
                  <div style={statValue}>{status.instance_id?.slice(0, 8)}...</div>
                </div>
                <div style={statBox}>
                  <div style={statLabel}>Instance Name</div>
                  <div style={statValue}>{status.instance_name || "—"}</div>
                </div>
                <div style={statBox}>
                  <div style={statLabel}>Peers Discovered</div>
                  <div style={statValue}>{status.peer_count ?? 0}</div>
                </div>
                <div style={statBox}>
                  <div style={statLabel}>Connected</div>
                  <div style={{ ...statValue, color: connectedCount > 0 ? "#10b981" : "var(--text-muted)" }}>
                    {connectedCount}
                  </div>
                </div>
              </div>
            ) : (
              <div style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)", lineHeight: 1.6 }}>
                {!enabled
                  ? "ISC is disabled. Enable it to discover and communicate with other OpenAVC instances on your network."
                  : "ISC is enabled in the project but not running yet. Save and reload the project to start ISC."}
                <br /><br />
                ISC allows multiple OpenAVC instances to share state, forward events,
                and send commands to each other. Use it for multi-room systems where
                rooms need to coordinate — like a "turn off all projectors" button
                or a lobby sensor triggering a hallway display.
              </div>
            )}
          </div>

          {/* Peer list */}
          <div style={cardStyle}>
            <h3 style={sectionTitle}>
              <Network size={14} style={{ marginRight: 6 }} />
              Discovered Peers ({peers.length})
            </h3>
            {peers.length === 0 ? (
              <div style={emptyText}>
                {iscEnabled
                  ? "No peers discovered yet. Other OpenAVC instances on the same network will appear here automatically."
                  : "Enable ISC to discover peers."}
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
                {peers.map((peer) => (
                  <div key={peer.instance_id} style={peerRow}>
                    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                      {peer.connected ? (
                        <Wifi size={16} style={{ color: "#10b981", flexShrink: 0 }} />
                      ) : (
                        <WifiOff size={16} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
                      )}
                      <div>
                        <div style={{ fontWeight: 500, fontSize: "var(--font-size-sm)" }}>
                          {peer.name || peer.instance_id.slice(0, 8)}
                        </div>
                        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                          {peer.host ? `${peer.host}:${peer.port}` : "inbound"}{" "}
                          · {peer.source}{" "}
                          · {peer.connected ? "connected" : "disconnected"}
                        </div>
                      </div>
                    </div>
                    <span
                      style={{
                        display: "inline-block",
                        width: 8,
                        height: 8,
                        borderRadius: "50%",
                        background: peer.connected ? "#10b981" : "#ef4444",
                        flexShrink: 0,
                      }}
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right column: Configuration */}
        <div style={{ flex: "1 1 350px", minWidth: 300 }}>
          {/* Shared State Patterns */}
          <div style={cardStyle}>
            <h3 style={sectionTitle}>
              <Send size={14} style={{ marginRight: 6 }} />
              Shared State Patterns
            </h3>
            <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", marginBottom: "var(--space-md)", lineHeight: 1.5 }}>
              Glob patterns for state keys to share with peers. For example,{" "}
              <code style={codeStyle}>device.projector1.*</code> shares all projector1
              state, and <code style={codeStyle}>var.*</code> shares all variables.
            </div>

            {sharedState.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 2, marginBottom: "var(--space-sm)" }}>
                {sharedState.map((pattern) => (
                  <div key={pattern} style={listItemStyle}>
                    <code style={codeStyle}>{pattern}</code>
                    <button
                      onClick={() => handleRemovePattern(pattern)}
                      style={iconBtn}
                      title="Remove pattern"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                ))}
              </div>
            )}

            <div style={{ display: "flex", gap: "var(--space-xs)" }}>
              <input
                style={fieldInput}
                value={newPattern}
                onChange={(e) => setNewPattern(e.target.value)}
                placeholder="e.g. device.projector1.* or var.*"
                onKeyDown={(e) => e.key === "Enter" && handleAddPattern()}
              />
              <button onClick={handleAddPattern} style={btnSmall} disabled={!newPattern.trim()}>
                <Plus size={14} />
              </button>
            </div>
          </div>

          {/* Manual Peers */}
          <div style={cardStyle}>
            <h3 style={sectionTitle}>
              <Network size={14} style={{ marginRight: 6 }} />
              Manual Peers
            </h3>
            <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", marginBottom: "var(--space-md)", lineHeight: 1.5 }}>
              Add peers by IP address for cross-subnet setups where mDNS
              auto-discovery doesn't reach.
            </div>

            {manualPeers.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 2, marginBottom: "var(--space-sm)" }}>
                {manualPeers.map((addr) => (
                  <div key={addr} style={listItemStyle}>
                    <code style={codeStyle}>{addr}</code>
                    <button
                      onClick={() => handleRemovePeer(addr)}
                      style={iconBtn}
                      title="Remove peer"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                ))}
              </div>
            )}

            <div style={{ display: "flex", gap: "var(--space-xs)" }}>
              <input
                style={fieldInput}
                value={newPeer}
                onChange={(e) => setNewPeer(e.target.value)}
                placeholder="e.g. 192.168.1.10:8080"
                onKeyDown={(e) => e.key === "Enter" && handleAddPeer()}
              />
              <button onClick={handleAddPeer} style={btnSmall} disabled={!newPeer.trim()}>
                <Plus size={14} />
              </button>
            </div>
          </div>

          {/* Auth Key */}
          <div style={cardStyle}>
            <h3 style={sectionTitle}>
              <Shield size={14} style={{ marginRight: 6 }} />
              Authentication
            </h3>
            <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", marginBottom: "var(--space-md)", lineHeight: 1.5 }}>
              Shared secret key required for ISC connections.
              All instances must use the same key to communicate.
            </div>
            {!authKey && (
              <div style={{ fontSize: "var(--font-size-sm)", color: "var(--color-warning, #f59e0b)", marginBottom: "var(--space-sm)", lineHeight: 1.5 }}>
                No auth key set. ISC will reject all incoming connections until a key is configured.
              </div>
            )}

            <div style={{ display: "flex", gap: "var(--space-xs)", alignItems: "center" }}>
              <input
                type={authVisible ? "text" : "password"}
                style={{ ...fieldInput, flex: 1 }}
                value={authKey}
                onChange={(e) => setAuthKey(e.target.value)}
                placeholder="Shared auth key (required)"
              />
              <button
                onClick={() => setAuthVisible(!authVisible)}
                style={iconBtn}
                title={authVisible ? "Hide" : "Show"}
              >
                <Eye size={14} />
              </button>
              <button
                onClick={handleSaveAuthKey}
                style={btnSmall}
                disabled={authKey === (isc?.auth_key ?? "")}
              >
                Save
              </button>
            </div>
          </div>
        </div>
      </div>
    </ViewContainer>
  );
}

// --- Styles ---

const headerBtnStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-xs)",
  padding: "var(--space-xs) var(--space-md)",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  color: "var(--text-secondary)",
  fontSize: "var(--font-size-sm)",
  border: "none",
  cursor: "pointer",
};

const cardStyle: React.CSSProperties = {
  background: "var(--bg-surface)",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  padding: "var(--space-lg)",
  marginBottom: "var(--space-md)",
};

const sectionTitle: React.CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  textTransform: "uppercase",
  letterSpacing: "0.5px",
  fontWeight: 600,
  margin: 0,
  display: "flex",
  alignItems: "center",
};

const statBox: React.CSSProperties = {
  padding: "var(--space-sm)",
  background: "var(--bg-primary)",
  borderRadius: "var(--border-radius)",
};

const statLabel: React.CSSProperties = {
  fontSize: 10,
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.5px",
  marginBottom: 2,
};

const statValue: React.CSSProperties = {
  fontSize: "var(--font-size-md)",
  fontWeight: 600,
  fontFamily: "var(--font-mono)",
};

const emptyText: React.CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-muted)",
  fontStyle: "italic",
  lineHeight: 1.5,
};

const peerRow: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "var(--space-sm) var(--space-md)",
  background: "var(--bg-primary)",
  borderRadius: "var(--border-radius)",
};

const codeStyle: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-sm)",
  color: "var(--accent)",
};

const listItemStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "var(--space-xs) var(--space-sm)",
  background: "var(--bg-primary)",
  borderRadius: "var(--border-radius)",
};

const fieldInput: React.CSSProperties = {
  flex: 1,
  padding: "4px 8px",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
};

const btnSmall: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: "4px 8px",
  borderRadius: "var(--border-radius)",
  background: "var(--accent-bg)",
  color: "#fff",
  fontSize: "var(--font-size-sm)",
  border: "none",
  cursor: "pointer",
};

const iconBtn: React.CSSProperties = {
  display: "flex",
  padding: 4,
  borderRadius: "var(--border-radius)",
  background: "transparent",
  color: "var(--text-muted)",
  border: "none",
  cursor: "pointer",
};
