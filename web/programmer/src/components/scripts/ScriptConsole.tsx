import { useEffect, useRef, useMemo, useState } from "react";
import { Trash2, Pause, Play } from "lucide-react";
import { useLogStore } from "../../store/logStore";

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: "var(--text-muted)",
  INFO: "var(--accent)",
  WARNING: "#f59e0b",
  ERROR: "#ef4444",
};

export function ScriptConsole() {
  const entries = useLogStore((s) => s.logEntries);
  const paused = useLogStore((s) => s.logPaused);
  const setPaused = useLogStore((s) => s.setLogPaused);
  const clear = useLogStore((s) => s.clearLogEntries);

  const [localPaused, setLocalPaused] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  // Filter to script entries only
  const scriptEntries = useMemo(
    () => entries.filter((e) => e.category === "script"),
    [entries]
  );

  // Auto-scroll
  useEffect(() => {
    if (!localPaused && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [scriptEntries, localPaused]);

  const formatTime = (ts: number) => {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString(undefined, { hour12: false });
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "4px var(--space-md)",
          borderBottom: "1px solid var(--border-color)",
          flexShrink: 0,
        }}
      >
        <span
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            textTransform: "uppercase",
            letterSpacing: "0.5px",
            fontWeight: 600,
          }}
        >
          Console
        </span>
        <div style={{ display: "flex", gap: "var(--space-xs)" }}>
          <button
            onClick={() => setLocalPaused(!localPaused)}
            style={toolBtnStyle}
            title={localPaused ? "Resume" : "Pause"}
          >
            {localPaused ? <Play size={12} /> : <Pause size={12} />}
          </button>
          <button onClick={clear} style={toolBtnStyle} title="Clear">
            <Trash2 size={12} />
          </button>
        </div>
      </div>

      {/* Output */}
      <div
        ref={listRef}
        style={{
          flex: 1,
          overflow: "auto",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          padding: "var(--space-xs)",
          background: "var(--bg-primary)",
        }}
      >
        {scriptEntries.length === 0 ? (
          <div style={{ color: "var(--text-muted)", padding: "var(--space-sm)" }}>
            Script output will appear here. Click Run to reload scripts.
          </div>
        ) : (
          scriptEntries.map((e, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                gap: "var(--space-sm)",
                padding: "1px 0",
                lineHeight: 1.4,
              }}
            >
              <span style={{ color: "var(--text-muted)", flexShrink: 0 }}>
                {formatTime(e.timestamp)}
              </span>
              <span
                style={{
                  color: LEVEL_COLORS[e.level] ?? "var(--text-primary)",
                  fontWeight: e.level === "ERROR" ? 600 : 400,
                  flexShrink: 0,
                  minWidth: 50,
                }}
              >
                [{e.level}]
              </span>
              <span style={{ color: "var(--text-primary)", wordBreak: "break-word" }}>
                {e.message}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

const toolBtnStyle: React.CSSProperties = {
  display: "flex",
  padding: 4,
  borderRadius: "var(--border-radius)",
  background: "transparent",
  color: "var(--text-muted)",
  border: "none",
  cursor: "pointer",
};
