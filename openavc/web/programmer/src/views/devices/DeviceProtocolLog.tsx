import { useEffect, useRef } from "react";
import { useLogStore } from "../../store/logStore";
import { deviceFilterPredicate } from "../logViewHelpers";

/** The device page's log: the most recent lines about this device. */
export function DeviceProtocolLog({ deviceId }: { deviceId: string }) {
  const logEntries = useLogStore((s) => s.logEntries);
  const listRef = useRef<HTMLDivElement>(null);

  // The same rule as the System Log's Device filter.
  const deviceLogs = logEntries.filter(deviceFilterPredicate(deviceId));
  const recent = deviceLogs.slice(-50);

  // Tail the newest entry. Depending on the slice LENGTH stops working the
  // moment the window saturates (50 entries, length pinned) — key on the
  // newest entry's identity instead, which changes with every append.
  const newest = recent[recent.length - 1];
  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [newest]);

  const LEVEL_COLORS: Record<string, string> = {
    DEBUG: "var(--text-muted)",
    INFO: "var(--accent)",
    WARNING: "#f59e0b",
    ERROR: "#ef4444",
  };

  return (
    <div
      ref={listRef}
      style={{
        background: "var(--bg-surface)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
        overflow: "auto",
        maxHeight: 250,
        fontFamily: "var(--font-mono)",
        fontSize: "var(--font-size-sm)",
      }}
    >
      {recent.length === 0 ? (
        <div
          style={{
            padding: "var(--space-lg)",
            color: "var(--text-muted)",
            fontSize: "var(--font-size-sm)",
            textAlign: "center",
            fontFamily: "var(--font-primary, inherit)",
          }}
        >
          No log entries for this device yet.
        </div>
      ) : (
        recent.map((e, i) => {
          const time = new Date(e.timestamp * 1000);
          return (
            <div
              key={i}
              style={{
                padding: "var(--space-xs) var(--space-md)",
                borderTop: i > 0 ? "1px solid var(--border-color)" : undefined,
                display: "flex",
                gap: "var(--space-sm)",
                alignItems: "baseline",
              }}
            >
              <span style={{ color: "var(--text-muted)", fontSize: 11, flexShrink: 0 }}>
                {time.toLocaleTimeString(undefined, { hour12: false })}
              </span>
              <span
                style={{
                  color: LEVEL_COLORS[e.level] ?? "var(--text-primary)",
                  fontWeight: e.level === "ERROR" ? 600 : 400,
                  fontSize: 11,
                  flexShrink: 0,
                  width: 40,
                }}
              >
                {e.level}
              </span>
              <span style={{ color: "var(--text-primary)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                {String(e.message)}
              </span>
            </div>
          );
        })
      )}
    </div>
  );
}
