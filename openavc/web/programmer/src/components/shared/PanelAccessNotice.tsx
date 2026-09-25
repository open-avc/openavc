import { usePanelDevicesStore } from "../../store/panelDevicesStore";
import { useNavigationStore } from "../../store/navigationStore";
import { PANEL_ACCESS_UPGRADE_NOTICE } from "./panelDevicesCopy";

/**
 * The one-time notice on a system that was updated with its panels connected.
 *
 * Such a system comes up with Panel access set to Anyone on the network, so
 * nothing in the room changes on update day; this says what that means and
 * where the switch is. Shown on every view until Dismiss, or until Panel
 * access is switched to approved, either of which the server remembers.
 * Never shown on a fresh install, which starts approved.
 */
export function PanelAccessNotice() {
  const access = usePanelDevicesStore((s) => s.access);
  const due = usePanelDevicesStore((s) => s.upgradeNotice);
  const dismiss = usePanelDevicesStore((s) => s.dismissUpgradeNotice);

  if (access !== "open" || !due) return null;

  const buttonStyle: React.CSSProperties = {
    padding: "2px 10px",
    borderRadius: 4,
    border: "1px solid var(--border-color)",
    background: "transparent",
    color: "var(--text-primary)",
    fontSize: 12,
    cursor: "pointer",
  };

  return (
    <div
      role="status"
      data-testid="panel-access-notice"
      style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        gap: "var(--space-md)",
        padding: "var(--space-sm) var(--space-md)",
        background: "var(--bg-surface)",
        borderBottom: "1px solid var(--border-color)",
        borderLeft: "3px solid var(--accent)",
        fontSize: 13, color: "var(--text-primary)", flexShrink: 0,
      }}
    >
      <span>{PANEL_ACCESS_UPGRADE_NOTICE}</span>
      <div style={{ display: "flex", gap: "var(--space-sm)", flexShrink: 0 }}>
        <button
          type="button"
          onClick={() => useNavigationStore.getState().navigateTo("settings")}
          style={{ ...buttonStyle, border: "none", background: "var(--accent-bg)", color: "var(--text-on-accent)" }}
        >
          Settings
        </button>
        <button type="button" onClick={() => { void dismiss(); }} style={buttonStyle}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
