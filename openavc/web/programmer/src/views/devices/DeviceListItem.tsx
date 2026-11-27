import { useConnectionStore } from "../../store/connectionStore";
import { DeviceStatusDot } from "../../components/shared/DeviceStatusDot";
import { driverLabel } from "../../components/shared/driverLabel";
import { presenceKey, presenceWord } from "../../components/shared/devicePresence";
import { awayState } from "./deviceAway";

export function DeviceListItem({
  deviceId,
  name,
  driver,
  selected,
  groupNames,
  onClick,
}: {
  deviceId: string;
  name: string;
  driver: string;
  selected: boolean;
  groupNames?: string[];
  onClick: () => void;
}) {
  const presence = presenceWord(useConnectionStore((s) => s.liveState[presenceKey(deviceId)]));
  const pausedFor = useConnectionStore(
    (s) => s.liveState[`device.${deviceId}.paused_for`]
  );
  const offlineReason = useConnectionStore(
    (s) => s.liveState[`device.${deviceId}.offline_reason`]
  );
  // Waiting for a login this system does not have: said here too, so after a
  // restore the devices to open are the ones that say so. Marked exactly when
  // the device's page would ask for it, so a pause, a restart or a sleep wins
  // here as it does there.
  const needsLogin = awayState(
    { presence, offlineReason: String(offlineReason ?? ""), offlineDetail: "" },
  ) === "needs_login";
  const waitingForDriver = presence === "waiting_for_driver";
  // Not running: turned off in the project, or its driver is not installed.
  const dimmed = presence === "disabled" || waitingForDriver;

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
        opacity: dimmed ? 0.6 : 1,
      }}
    >
      <DeviceStatusDot presence={presence} pausedFor={pausedFor} />
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
          color: waitingForDriver ? "var(--color-warning, #f59e0b)" : "var(--text-muted)",
          display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap",
        }}>
          <span style={{ minWidth: 0, maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {driverLabel(driver)}{waitingForDriver ? " (not installed)" : ""}
          </span>
          {needsLogin && (
            <span style={{ color: "var(--color-warning, #f59e0b)", whiteSpace: "nowrap" }}>
              Needs login
            </span>
          )}
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
