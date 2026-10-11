import { useEffect, useRef, useState } from "react";
import { useConnectionStore } from "../../store/connectionStore";
import { getStateHistory } from "../../api/restClient";
import { valueText } from "../../components/shared/booleanWords";

/** Rows this tab shows: the device's most recent changes. */
const MAX_ROWS = 100;

type Row = { key: string; oldValue: unknown; newValue: unknown; timestamp: number };

/** One device's live values, by full key. */
function deviceValues(liveState: Record<string, unknown>, prefix: string): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(liveState)) {
    if (key.startsWith(prefix)) out[key] = value;
  }
  return out;
}

/**
 * The device's recent state changes. Opening the tab reads the server's
 * change history for the device once (the record State > Activity shows), so
 * changes made before it was opened are listed too, first values included;
 * after that it follows the live values over the WebSocket. One request per
 * opening: the history route shares the standard rate budget with the rest
 * of a remote Programmer session.
 */
export function DeviceStateLog({ deviceId }: { deviceId: string }) {
  const liveState = useConnectionStore((s) => s.liveState);
  const devicePrefix = `device.${deviceId}.`;
  const [entries, setEntries] = useState<Row[]>([]);
  const [loaded, setLoaded] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const liveRef = useRef(liveState);
  liveRef.current = liveState;
  // The live values the next change is compared against; null until the
  // history has been read, so nothing is listed twice.
  const baseline = useRef<Record<string, unknown> | null>(null);
  // The newest value the history listed for each key. The same change can
  // reach the WebSocket a moment after the history was read; it is listed once.
  const listedLast = useRef(new Map<string, unknown>());

  useEffect(() => {
    let cancelled = false;
    baseline.current = null;
    setEntries([]);
    setLoaded(false);
    const start = (rows: Row[]) => {
      listedLast.current = new Map(rows.map((r) => [r.key, r.newValue]));
      baseline.current = deviceValues(liveRef.current, devicePrefix);
      setEntries(rows);
      setLoaded(true);
    };
    getStateHistory(MAX_ROWS, devicePrefix)
      .then((history) => {
        if (cancelled) return;
        start(
          history
            .filter((e) => e.key.startsWith(devicePrefix))
            .map((e) => ({
              key: e.key.slice(devicePrefix.length),
              oldValue: e.old_value,
              newValue: e.new_value,
              timestamp: e.timestamp,
            })),
        );
      })
      .catch(() => {
        if (!cancelled) start([]);
      });
    return () => { cancelled = true; };
  }, [devicePrefix]);

  useEffect(() => {
    const prev = baseline.current;
    if (prev === null) return;
    const next = deviceValues(liveState, devicePrefix);
    const rows: Row[] = [];
    for (const [key, value] of Object.entries(next)) {
      if (prev[key] === value) continue;
      const short = key.slice(devicePrefix.length);
      const alreadyListed = listedLast.current.has(short) && listedLast.current.get(short) === value;
      listedLast.current.delete(short);
      if (alreadyListed) continue;
      rows.push({ key: short, oldValue: prev[key], newValue: value, timestamp: Date.now() / 1000 });
    }
    baseline.current = next;
    if (rows.length > 0) setEntries((e) => [...e, ...rows].slice(-MAX_ROWS));
  }, [liveState, devicePrefix]);

  // Tail the newest entry — keying on length stalls once the 100-row window
  // saturates; the newest entry's identity changes with every append.
  const newestEntry = entries[entries.length - 1];
  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [newestEntry]);

  const formatValue = (v: unknown) => {
    // "—", not "null": a reading the device has not sent is the normal state of
    // every variable until it does, and the word belongs to a debugger rather
    // than to somebody commissioning a room. Same mark the Dashboard tile and
    // the panel use for the same thing.
    return valueText(v, "—");
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
      {entries.length === 0 ? (
        <div
          style={{
            padding: "var(--space-lg)",
            color: "var(--text-muted)",
            fontSize: "var(--font-size-sm)",
            textAlign: "center",
            fontFamily: "var(--font-primary, inherit)",
          }}
        >
          {loaded ? "No recent changes for this device." : "Loading..."}
        </div>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border-color)", position: "sticky", top: 0, background: "var(--bg-surface)" }}>
              <th style={devLogThStyle}>Time</th>
              <th style={devLogThStyle}>Property</th>
              <th style={devLogThStyle}>Old</th>
              <th style={devLogThStyle}>New</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e, i) => {
              const time = new Date(e.timestamp * 1000);
              return (
                <tr key={i} style={{ borderBottom: "1px solid var(--border-color)" }}>
                  <td style={devLogTdStyle}>
                    {time.toLocaleTimeString(undefined, { hour12: false })}
                  </td>
                  <td style={{ ...devLogTdStyle, color: "var(--accent)" }}>{e.key}</td>
                  <td style={{ ...devLogTdStyle, color: "var(--text-muted)" }}>
                    {formatValue(e.oldValue)}
                  </td>
                  <td style={devLogTdStyle}>{formatValue(e.newValue)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

const devLogThStyle: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-md)",
  textAlign: "left",
  fontWeight: 600,
  color: "var(--text-secondary)",
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: "0.5px",
};

const devLogTdStyle: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-md)",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
  maxWidth: 150,
};
