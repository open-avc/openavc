import { useEffect, useRef, useMemo, useState, useCallback } from "react";
import { Trash2, Pause, Play } from "lucide-react";
import { useLogStore } from "../../store/logStore";
import { useNavigationStore } from "../../store/navigationStore";

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
            Script output will appear here. Click Save &amp; Reload or press Ctrl+Shift+R.
          </div>
        ) : (
          scriptEntries.map((e, i) => (
            <ConsoleEntry key={i} entry={e} formatTime={formatTime} />
          ))
        )}
      </div>
    </div>
  );
}

/** Render a single console entry with multi-line support and clickable line numbers. */
function ConsoleEntry({ entry, formatTime }: { entry: { timestamp: number; level: string; message: string }; formatTime: (ts: number) => string }) {
  const lines = entry.message.split("\n");
  const isMultiLine = lines.length > 1;

  // Parse "line N" references and make them clickable
  const renderMessage = (text: string) => {
    const parts: React.ReactNode[] = [];
    const lineRefPattern = /\bline (\d+)\b/g;
    let lastIndex = 0;
    let match;

    while ((match = lineRefPattern.exec(text)) !== null) {
      if (match.index > lastIndex) {
        parts.push(text.slice(lastIndex, match.index));
      }
      const lineNum = parseInt(match[1], 10);
      parts.push(
        <span
          key={match.index}
          onClick={() => {
            // Navigate to the line in the editor
            // Scroll to line in the current editor — the ScriptView picks up the focus
            const nav = useNavigationStore.getState();
            nav.navigateTo("scripts", { type: "script", id: "", detail: `line:${lineNum}` });
          }}
          style={{
            color: "var(--accent)",
            cursor: "pointer",
            textDecoration: "underline",
            textDecorationStyle: "dotted",
          }}
          title={`Go to line ${lineNum}`}
        >
          line {lineNum}
        </span>
      );
      lastIndex = match.index + match[0].length;
    }
    if (lastIndex < text.length) {
      parts.push(text.slice(lastIndex));
    }
    return parts.length > 0 ? parts : text;
  };

  return (
    <div
      style={{
        display: "flex",
        gap: "var(--space-sm)",
        padding: "1px 0",
        lineHeight: 1.4,
      }}
    >
      <span style={{ color: "var(--text-muted)", flexShrink: 0 }}>
        {formatTime(entry.timestamp)}
      </span>
      <span
        style={{
          color: LEVEL_COLORS[entry.level] ?? "var(--text-primary)",
          fontWeight: entry.level === "ERROR" ? 600 : 400,
          flexShrink: 0,
          minWidth: 50,
        }}
      >
        [{entry.level}]
      </span>
      <span style={{ color: "var(--text-primary)", wordBreak: "break-word", whiteSpace: "pre-wrap" }}>
        {isMultiLine ? (
          lines.map((line, li) => (
            <span key={li}>
              {li > 0 && "\n"}
              {renderMessage(line)}
            </span>
          ))
        ) : (
          renderMessage(entry.message)
        )}
      </span>
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
