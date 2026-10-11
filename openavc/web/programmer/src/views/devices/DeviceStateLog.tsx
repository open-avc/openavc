import { useEffect, useRef, useState } from "react";
import { useConnectionStore } from "../../store/connectionStore";
import { getStateHistory } from "../../api/restClient";
import { valueText } from "../../components/shared/booleanWords";

/** Rows this tab shows: the device's most recent changes. */
const MAX_ROWS = 100;
/** The fewest milliseconds between two reads while the device keeps changing. */
const REFRESH_MS = 1000;

/** A fingerprint of one device's live values, so the tab knows to read the
 *  history again when one of them changes. */
function deviceSignature(liveState: Record<string, unknown>, prefix: string): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(liveState)) {
    if (key.startsWith(prefix)) parts.push(`${key}=${JSON.stringify(value)}`);
  }
  return parts.join("\n");
}

/**
 * The device's recent state changes, from the server's change history (the
 * record State > Activity shows), so changes made before the tab was opened
 * are listed too, first values included. Read again whenever one of the
 * device's live values changes, at most once a second.
 */
export function DeviceStateLog({ deviceId }: { deviceId: string }) {
  const devicePrefix = `device.${deviceId}.`;
  const signature = useConnectionStore((s) => deviceSignature(s.liveState, devicePrefix));
  const [entries, setEntries] = useState<
    { key: string; oldValue: unknown; newValue: unknown; timestamp: number }[]
  >([]);
  const [loaded, setLoaded] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const lastRead = useRef(0);
  const readSeq = useRef(0);

  useEffect(() => {
    setEntries([]);
    setLoaded(false);
  }, [devicePrefix]);

  useEffect(() => {
    const read = () => {
      lastRead.current = Date.now();
      const token = ++readSeq.current;
      getStateHistory(MAX_ROWS, devicePrefix)
        .then((history) => {
          // Latest wins: an older read answering late must not replace a newer one.
          if (token !== readSeq.current) return;
          setEntries(
            history
              .filter((e) => e.key.startsWith(devicePrefix))
              .map((e) => ({
                key: e.key.slice(devicePrefix.length),
                oldValue: e.old_value,
                newValue: e.new_value,
                timestamp: e.timestamp,
              })),
          );
          setLoaded(true);
        })
        .catch(() => {
          if (token === readSeq.current) setLoaded(true);
        });
    };
    const handle = window.setTimeout(read, Math.max(0, lastRead.current + REFRESH_MS - Date.now()));
    return () => window.clearTimeout(handle);
  }, [signature, devicePrefix]);

  useEffect(() => () => { readSeq.current++; }, []);

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
