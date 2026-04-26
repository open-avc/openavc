import { useState, useEffect, useRef, useMemo } from "react";
import { Trash2, Pause, Play } from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { useConnectionStore } from "../store/connectionStore";
import { useLogStore } from "../store/logStore";
import * as api from "../api/restClient";
import { useProjectStore } from "../store/projectStore";

type TabId = "log" | "state";

export function LogView() {
  const [activeTab, setActiveTab] = useState<TabId>("log");

  return (
    <ViewContainer
      title="Log"
      actions={
        <div style={{ display: "flex", gap: "var(--space-sm)" }} role="tablist">
          <TabButton
            label="System Log"
            active={activeTab === "log"}
            onClick={() => setActiveTab("log")}
          />
          <TabButton
            label="State Changes"
            active={activeTab === "state"}
            onClick={() => setActiveTab("state")}
          />
        </div>
      }
    >
      {activeTab === "log" ? <SystemLogTab /> : <StateChangeTab />}
    </ViewContainer>
  );
}

function TabButton({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      role="tab"
      aria-selected={active}
      onClick={onClick}
      style={{
        padding: "var(--space-xs) var(--space-md)",
        borderRadius: "var(--border-radius)",
        background: active ? "var(--accent)" : "var(--bg-hover)",
        color: active ? "#fff" : "var(--text-primary)",
        fontSize: "var(--font-size-sm)",
        fontWeight: active ? 600 : 400,
        border: "none",
        cursor: "pointer",
      }}
    >
      {label}
    </button>
  );
}

// --- System Log Tab ---

const CATEGORY_OPTIONS = ["all", "system", "device", "script", "macro"];
const LEVEL_OPTIONS = ["all", "DEBUG", "INFO", "WARNING", "ERROR"];

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: "var(--text-muted)",
  INFO: "var(--accent)",
  WARNING: "#f59e0b",
  ERROR: "#ef4444",
};

function SystemLogTab() {
  // Throttle log entries to max 4 updates/sec to prevent render thrashing
  const [entries, setEntries] = useState(() => useLogStore.getState().logEntries);
  useEffect(() => {
    let rafId: number | null = null;
    const unsub = useLogStore.subscribe((state) => {
      if (rafId !== null) return;
      rafId = requestAnimationFrame(() => {
        setEntries(state.logEntries);
        rafId = null;
      });
    });
    return () => { unsub(); if (rafId !== null) cancelAnimationFrame(rafId); };
  }, []);
  const paused = useLogStore((s) => s.logPaused);
  const setPaused = useLogStore((s) => s.setLogPaused);
  const clearEntries = useLogStore((s) => s.clearLogEntries);

  const project = useProjectStore((s) => s.project);
  const deviceIds = project?.devices.map(d => d.id) ?? [];

  const [categoryFilter, setCategoryFilter] = useState("all");
  const [levelFilter, setLevelFilter] = useState("all");
  const [deviceFilter, setDeviceFilter] = useState("all");

  const listRef = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => {
    let result = entries;
    if (categoryFilter !== "all") {
      result = result.filter((e) => e.category === categoryFilter);
    }
    if (levelFilter !== "all") {
      result = result.filter((e) => e.level === levelFilter);
    }
    if (deviceFilter !== "all") {
      // Match on source field (contains device ID) or message mentioning the device
      const df = deviceFilter.toLowerCase();
      result = result.filter((e) => {
        const parts = e.source.toLowerCase().split(".");
        return parts.includes(df);
      });
    }
    return result;
  }, [entries, categoryFilter, levelFilter, deviceFilter]);

  // Auto-scroll
  useEffect(() => {
    if (!paused && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [filtered, paused]);

  const formatTime = (ts: number) => {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString(undefined, { hour12: false });
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "var(--space-sm)" }}>
      {/* Filter bar */}
      <div style={{ display: "flex", gap: "var(--space-md)", alignItems: "center", flexShrink: 0 }}>
        <label style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>
          Source:
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            style={selectStyle}
          >
            {CATEGORY_OPTIONS.map((o) => (
              <option key={o} value={o}>
                {o === "all" ? "All" : o.charAt(0).toUpperCase() + o.slice(1)}
              </option>
            ))}
          </select>
        </label>
        <label style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>
          Level:
          <select
            value={levelFilter}
            onChange={(e) => setLevelFilter(e.target.value)}
            style={selectStyle}
          >
            {LEVEL_OPTIONS.map((o) => (
              <option key={o} value={o}>
                {o === "all" ? "All" : o}
              </option>
            ))}
          </select>
        </label>
        <label style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>
          Device:
          <select
            value={deviceFilter}
            onChange={(e) => setDeviceFilter(e.target.value)}
            style={selectStyle}
          >
            <option value="all">All</option>
            {deviceIds.map(id => (
              <option key={id} value={id}>{id}</option>
            ))}
          </select>
        </label>
        <div style={{ flex: 1 }} />
        <button onClick={() => setPaused(!paused)} style={actionBtnStyle(paused)}>
          {paused ? <Play size={14} /> : <Pause size={14} />}
          {paused ? "Resume" : "Pause"}
        </button>
        <button onClick={clearEntries} style={actionBtnStyle(false)}>
          <Trash2 size={14} /> Clear
        </button>
      </div>

      {/* Log entries */}
      <div
        ref={listRef}
        style={{
          flex: 1,
          overflow: "auto",
          fontFamily: "var(--font-mono)",
          fontSize: "var(--font-size-sm)",
          background: "var(--bg-surface)",
          borderRadius: "var(--border-radius)",
          border: "1px solid var(--border-color)",
        }}
      >
        {filtered.length === 0 ? (
          <div style={{ padding: "var(--space-xl)", textAlign: "center", color: "var(--text-muted)" }}>
            No log entries yet. System activity will appear here.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border-color)", position: "sticky", top: 0, background: "var(--bg-surface)" }}>
                <th style={thStyle}>Time</th>
                <th style={thStyle}>Level</th>
                <th style={thStyle}>Source</th>
                <th style={{ ...thStyle, width: "100%" }}>Message</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length > 200 && (
                <tr><td colSpan={4} style={{ ...tdStyle, textAlign: "center", color: "var(--text-muted)", fontSize: "11px" }}>
                  Showing last 200 of {filtered.length} entries
                </td></tr>
              )}
              {filtered.slice(-200).map((e, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--border-color)" }}>
                  <td style={tdStyle}>{formatTime(e.timestamp)}</td>
                  <td style={tdStyle}>
                    <span style={{
                      color: LEVEL_COLORS[e.level] ?? "var(--text-primary)",
                      fontWeight: e.level === "ERROR" ? 600 : 400,
                    }}>
                      {e.level}
                    </span>
                  </td>
                  <td style={{ ...tdStyle, color: "var(--text-muted)" }}>
                    {e.category}
                  </td>
                  <td style={{ ...tdStyle, whiteSpace: "pre-wrap", wordBreak: "break-word", maxWidth: "none" }}>
                    {e.message}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// --- State Change Tab (original LogView behavior) ---

interface StateLogEntry {
  key: string;
  oldValue: unknown;
  newValue: unknown;
  source: string;
  timestamp: number;
}

function StateChangeTab() {
  const [entries, setEntries] = useState<StateLogEntry[]>([]);
  const [paused, setPaused] = useState(false);
  const [deviceFilter, setDeviceFilter] = useState("all");
  const liveState = useConnectionStore((s) => s.liveState);
  const prevStateRef = useRef<Record<string, unknown>>({});
  const listRef = useRef<HTMLDivElement>(null);

  const project = useProjectStore((s) => s.project);
  const deviceIds = project?.devices.map(d => d.id) ?? [];

  // Load history on mount
  useEffect(() => {
    api.getStateHistory(100).then((history) => {
      setEntries(
        history.map((h) => ({
          key: h.key,
          oldValue: h.old_value,
          newValue: h.new_value,
          source: h.source,
          timestamp: h.timestamp,
        }))
      );
    }).catch(console.error);
  }, []);

  // Track live state changes
  useEffect(() => {
    if (paused) return;
    const prev = prevStateRef.current;
    const newEntries: StateLogEntry[] = [];
    for (const [key, value] of Object.entries(liveState)) {
      if (prev[key] !== value && prev[key] !== undefined) {
        newEntries.push({
          key,
          oldValue: prev[key],
          newValue: value,
          source: "live",
          timestamp: Date.now() / 1000,
        });
      }
    }
    prevStateRef.current = { ...liveState };
    if (newEntries.length > 0) {
      setEntries((prev) => [...prev, ...newEntries].slice(-500));
    }
  }, [liveState, paused]);

  const filtered = useMemo(() => {
    if (deviceFilter === "all") return entries;
    return entries.filter((e) =>
      e.key.toLowerCase().includes(deviceFilter.toLowerCase())
    );
  }, [entries, deviceFilter]);

  // Auto-scroll
  useEffect(() => {
    if (!paused && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [filtered, paused]);

  const formatTime = (ts: number) => {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString(undefined, { hour12: false });
  };

  const formatValue = (v: unknown) => {
    if (v === null || v === undefined) return "null";
    return String(v);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "var(--space-sm)" }}>
      <div style={{ display: "flex", gap: "var(--space-md)", alignItems: "center", flexShrink: 0 }}>
        <label style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>
          Device:
          <select
            value={deviceFilter}
            onChange={(e) => setDeviceFilter(e.target.value)}
            style={selectStyle}
          >
            <option value="all">All</option>
            {deviceIds.map(id => (
              <option key={id} value={id}>{id}</option>
            ))}
          </select>
        </label>
        <div style={{ flex: 1 }} />
        <button onClick={() => setPaused(!paused)} style={actionBtnStyle(paused)}>
          {paused ? <Play size={14} /> : <Pause size={14} />}
          {paused ? "Resume" : "Pause"}
        </button>
        <button onClick={() => setEntries([])} style={actionBtnStyle(false)}>
          <Trash2 size={14} /> Clear
        </button>
      </div>
      <div
        ref={listRef}
        style={{
          flex: 1,
          overflow: "auto",
          fontFamily: "var(--font-mono)",
          fontSize: "var(--font-size-sm)",
          background: "var(--bg-surface)",
          borderRadius: "var(--border-radius)",
          border: "1px solid var(--border-color)",
        }}
      >
        {filtered.length === 0 ? (
          <div style={{ padding: "var(--space-xl)", textAlign: "center", color: "var(--text-muted)" }}>
            No state changes recorded yet. Interact with the system to see live updates.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border-color)", position: "sticky", top: 0, background: "var(--bg-surface)" }}>
                <th style={thStyle}>Time</th>
                <th style={thStyle}>Key</th>
                <th style={thStyle}>Old</th>
                <th style={thStyle}>New</th>
                <th style={thStyle}>Source</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((e, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--border-color)" }}>
                  <td style={tdStyle}>{formatTime(e.timestamp)}</td>
                  <td style={{ ...tdStyle, color: "var(--accent)" }}>{e.key}</td>
                  <td style={{ ...tdStyle, color: "var(--text-muted)" }}>{formatValue(e.oldValue)}</td>
                  <td style={tdStyle}>{formatValue(e.newValue)}</td>
                  <td style={{ ...tdStyle, color: "var(--text-muted)" }}>{e.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// --- Shared styles ---

const thStyle: React.CSSProperties = {
  padding: "var(--space-sm) var(--space-md)",
  textAlign: "left",
  fontWeight: 600,
  color: "var(--text-secondary)",
  fontSize: "11px",
  textTransform: "uppercase",
  letterSpacing: "0.5px",
};

const tdStyle: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-md)",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
  maxWidth: 250,
};

const selectStyle: React.CSSProperties = {
  marginLeft: "var(--space-xs)",
  padding: "2px 6px",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  color: "var(--text-primary)",
  border: "1px solid var(--border-color)",
  fontSize: "var(--font-size-sm)",
};

function actionBtnStyle(highlighted: boolean): React.CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: "var(--space-xs)",
    padding: "var(--space-xs) var(--space-md)",
    borderRadius: "var(--border-radius)",
    background: highlighted ? "var(--color-warning)" : "var(--bg-hover)",
    color: highlighted ? "#000" : "var(--text-primary)",
    fontSize: "var(--font-size-sm)",
    border: "none",
    cursor: "pointer",
  };
}
