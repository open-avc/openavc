import { useConnectionStore } from "../../store/connectionStore";
import { DeviceStatusDot } from "../../components/shared/DeviceStatusDot";

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

  return (
    <button
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-md)",
        width: "100%",
        padding: "var(--space-md)",
        borderRadius: "var(--border-radius)",
        background: selected ? "var(--accent-dim)" : "transparent",
        textAlign: "left",
        marginBottom: "var(--space-xs)",
        transition: "background var(--transition-fast)",
        opacity: enabled && !orphaned ? 1 : 0.6,
      }}
    >
      <DeviceStatusDot connected={connected ?? false} orphaned={orphaned ?? false} />
      <div style={{ minWidth: 0 }}>
        <div
          style={{
            fontWeight: 500,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {name}
        </div>
        <div style={{
          fontSize: "var(--font-size-sm)",
          color: orphaned ? "var(--color-warning, #f59e0b)" : "var(--text-muted)",
          display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap",
        }}>
          <span>{driver}{orphaned ? " (not installed)" : ""}</span>
          {groupNames && groupNames.length > 0 && groupNames.map((gn) => (
            <span key={gn} style={{
              fontSize: 9, padding: "0 4px", borderRadius: 3,
              background: "rgba(138,180,147,0.12)", color: "var(--accent)",
              lineHeight: "16px",
            }}>{gn}</span>
          ))}
        </div>
      </div>
    </button>
  );
}
