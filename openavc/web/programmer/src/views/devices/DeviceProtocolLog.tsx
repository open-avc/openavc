import { useEffect, useRef, useState } from "react";
import { getRecentLogs } from "../../api/systemClient";
import { useLogStore, type LogEntry } from "../../store/logStore";
import { deviceFilterPredicate, withEarlierLines } from "../logViewHelpers";

const SHOWN = 50;

type Line = Pick<LogEntry, "timestamp" | "level" | "source" | "message">;

/** The device page's log: the most recent lines about this device. */
export function DeviceProtocolLog({ deviceId }: { deviceId: string }) {
  const listRef = useRef<HTMLDivElement>(null);
  // This device's lines, kept here as the live stream brings them: the log
  // store holds only the last few hundred lines of the whole system, which a
  // few polling devices turn over in about a minute, so a page left open lost
  // the lines that say why its device went offline. Seeded with what the
  // server kept for the device from before the page loaded.
  const [recent, setRecent] = useState<Line[]>([]);
  useEffect(() => {
    // The same rule as the System Log's Device filter.
    const belongs = deviceFilterPredicate(deviceId);
    let seenId = -Infinity;
    let current = true;
    const take = (entries: LogEntry[]) => {
      const fresh: LogEntry[] = [];
      for (let i = entries.length - 1; i >= 0 && entries[i].id > seenId; i--) {
        if (belongs(entries[i])) fresh.unshift(entries[i]);
      }
      if (entries.length > 0) seenId = Math.max(seenId, entries[entries.length - 1].id);
      if (fresh.length > 0) setRecent((prev) => [...prev, ...fresh].slice(-SHOWN));
    };
    setRecent([]);
    take(useLogStore.getState().logEntries);
    const unsubscribe = useLogStore.subscribe((state, prev) => {
      if (state.logEntries !== prev.logEntries) take(state.logEntries);
    });
    getRecentLogs(SHOWN, "", deviceId)
      .then((earlier) => {
        if (current) setRecent((prev) => withEarlierLines(earlier, prev).slice(-SHOWN));
      })
      .catch(() => { /* the live lines still show */ });
    return () => {
      current = false;
      unsubscribe();
    };
  }, [deviceId]);

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
