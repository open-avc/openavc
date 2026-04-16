import { useState, useEffect, useMemo } from "react";
import { getStateHistory } from "../../api/restClient";
import type { StateHistoryEntry } from "../../api/types";
import { HelpBanner } from "./variablesShared";

export function ActivitySubTab() {
  const [entries, setEntries] = useState<StateHistoryEntry[]>([]);
  const [filter, setFilter] = useState<"all" | "device" | "var" | "system">("all");
  const [keyFilter, setKeyFilter] = useState("");
  const [loading, setLoading] = useState(true);

  // Poll for state history (expanded to 500)
  useEffect(() => {
    let cancelled = false;
    const fetchHistory = () => {
      getStateHistory(500)
        .then((data) => { if (!cancelled) { setEntries(data); setLoading(false); } })
        .catch(() => { if (!cancelled) setLoading(false); });
    };
    fetchHistory();
    const interval = setInterval(fetchHistory, 3000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  const filteredEntries = useMemo(() => {
    let result = entries;
    if (filter !== "all") {
      result = result.filter((e) => {
        if (filter === "device") return e.key.startsWith("device.");
        if (filter === "var") return e.key.startsWith("var.");
        if (filter === "system") return e.key.startsWith("system.");
        return true;
      });
    }
    if (keyFilter) {
      const q = keyFilter.toLowerCase();
      result = result.filter((e) => e.key.toLowerCase().includes(q));
    }
    return result;
  }, [entries, filter, keyFilter]);

  const formatTime = (ts: number) => {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  };

  const sourceColor = (source: string) => {
    switch (source) {
      case "device": return "#3b82f6";
      case "macro": return "#f59e0b";
      case "script": return "#10b981";
      case "api": return "#8b5cf6";
      case "ui": return "#ec4899";
      default: return "var(--text-muted)";
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <HelpBanner storageKey="openavc-help-activity">
        Every time a device property or variable changes, it appears here.
        The system is fully reactive — you never need to poll or check in a loop.
        Macros, UI bindings, and scripts all respond to these changes automatically.
      </HelpBanner>

      {/* Filter bar */}
      <div style={{ display: "flex", gap: "var(--space-sm)", padding: "var(--space-sm) var(--space-md)", borderBottom: "1px solid var(--border-color)", alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Filter:</span>
        {(["all", "device", "var", "system"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            style={{
              padding: "2px 10px",
              borderRadius: 12,
              fontSize: 11,
              border: "1px solid " + (filter === f ? "var(--accent)" : "var(--border-color)"),
              background: filter === f ? "rgba(33,150,243,0.15)" : "transparent",
              color: filter === f ? "var(--accent)" : "var(--text-secondary)",
              cursor: "pointer",
            }}
          >
            {f === "all" ? "All" : f === "var" ? "Variables" : f === "device" ? "Device" : "System"}
          </button>
        ))}
        <input
          value={keyFilter}
          onChange={(e) => setKeyFilter(e.target.value)}
          placeholder="Filter by key..."
          style={{
            marginLeft: "auto",
            padding: "2px 8px",
            fontSize: 11,
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            background: "var(--bg-surface)",
            color: "var(--text-primary)",
            width: 180,
            fontFamily: "var(--font-mono)",
          }}
        />
      </div>

      {/* Entries */}
      <div style={{ flex: 1, overflow: "auto" }}>
        {loading ? (
          <div style={{ padding: "var(--space-xl)", textAlign: "center", color: "var(--text-muted)" }}>Loading...</div>
        ) : filteredEntries.length === 0 ? (
          <div style={{ padding: "var(--space-xl)", textAlign: "center", color: "var(--text-muted)", fontSize: "var(--font-size-sm)", fontStyle: "italic" }}>
            No state changes recorded yet. Start the system to see activity.
          </div>
        ) : (
          [...filteredEntries].reverse().map((entry, i) => (
            <div
              key={`${entry.key}-${entry.timestamp}-${i}`}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-sm)",
                padding: "4px var(--space-md)",
                fontSize: "var(--font-size-sm)",
                borderBottom: "1px solid var(--border-color)",
              }}
            >
              <span style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)", flexShrink: 0, width: 70 }}>
                {formatTime(entry.timestamp)}
              </span>
              <code style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {entry.key}
              </code>
              <span style={{ fontSize: 11, color: "var(--text-muted)", flexShrink: 0 }}>
                {entry.old_value !== null && entry.old_value !== undefined ? String(entry.old_value) : "null"}
              </span>
              <span style={{ fontSize: 11, color: "var(--text-muted)", flexShrink: 0 }}>
                &rarr;
              </span>
              <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-primary)", fontWeight: 500, flexShrink: 0 }}>
                {entry.new_value !== null && entry.new_value !== undefined ? String(entry.new_value) : "null"}
              </span>
              <span style={{
                fontSize: 10, padding: "0 6px", borderRadius: 8, flexShrink: 0,
                background: `${sourceColor(entry.source)}20`,
                color: sourceColor(entry.source),
                fontWeight: 500,
              }}>
                {entry.source}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
