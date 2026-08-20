import { useConnectionStore } from "../../store/connectionStore";

/** What the gutter bar says about a device, worst state first.
 *
 * A dot beside every row makes the healthy ones as loud as the broken ones,
 * which is the wrong way round in a list you scan to find the problem. So a
 * row that is fine carries no mark at all, and the colour is spent only where
 * something needs doing. */
function faultColor(
  orphaned: boolean,
  connected: boolean,
  paused: boolean,
  enabled: boolean
): string {
  if (orphaned) return "var(--color-warning)";
  if (!enabled) return "var(--text-muted)";
  if (paused) return "var(--color-warning)";
  if (!connected) return "var(--color-error)";
  return "transparent";
}

export function DeviceListItem({
  deviceId,
  name,
  driver,
  selected,
  enabled,
  groupNames,
  onClick,
}: {
  deviceId: string;
  name: string;
  driver: string;
  selected: boolean;
  enabled: boolean;
  groupNames?: string[];
  onClick: () => void;
}) {
  const connected = useConnectionStore(
    (s) => s.liveState[`device.${deviceId}.connected`] as boolean | undefined
  );
  const orphaned = useConnectionStore(
    (s) => s.liveState[`device.${deviceId}.orphaned`] as boolean | undefined
  );
  const paused = useConnectionStore(
    (s) => s.liveState[`device.${deviceId}.paused`] as boolean | undefined
  );

  const bar = faultColor(orphaned ?? false, connected ?? false, paused ?? false, enabled);

  return (
    <button
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-sm)",
        width: "100%",
        height: 38,
        paddingRight: "var(--space-sm)",
        background: selected ? "var(--accent-dim)" : "transparent",
        borderBottom: "1px solid var(--bg-elevated)",
        textAlign: "left",
        transition: "background var(--transition-fast)",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: 3,
          height: 22,
          flexShrink: 0,
          marginLeft: "var(--space-sm)",
          borderRadius: "var(--radius-sm)",
          background: bar,
        }}
      />
      <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: "var(--space-2xs)" }}>
        <div
          style={{
            fontSize: "var(--font-size-sm)",
            color: enabled ? "var(--text-primary)" : "var(--text-muted)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {name}
        </div>
        <div style={{
          fontFamily: "var(--font-mono)",
          fontSize: "var(--font-size-xs)",
          color: orphaned ? "var(--color-warning)" : "var(--text-muted)",
          display: "flex", alignItems: "center", gap: "var(--space-xs)",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          <span>{driver}{orphaned ? " (not installed)" : ""}</span>
          {groupNames && groupNames.length > 0 && groupNames.map((gn) => (
            <span key={gn} style={{
              fontFamily: "var(--font-family)",
              fontSize: "var(--font-size-2xs)", padding: "0 var(--space-xs)", borderRadius: "var(--radius-sm)",
              background: "var(--accent-dim)", color: "var(--accent)",
              lineHeight: "14px",
            }}>{gn}</span>
          ))}
        </div>
      </div>
    </button>
  );
}
