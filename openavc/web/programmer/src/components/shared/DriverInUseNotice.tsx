import { Modal } from "./Modal";
import type { DeviceOnDriver } from "./driverInUse";

interface DriverInUseNoticeProps {
  /** The project devices on the driver (`devicesOnDriver`, or a refusal's). */
  devices: DeviceOnDriver[];
  /** What is refused: "delete" or "uninstall". */
  action: string;
  /** Open one device's page, where another driver is chosen. */
  onOpenDevice: (deviceId: string) => void;
}

/**
 * Why a driver cannot be taken away, and the way out: each device that uses it,
 * with a button that opens the device to choose another driver. Shared by the
 * Installed page's uninstall, the Driver Builder's delete and the Code view's
 * delete.
 */
export function DriverInUseNotice({ devices, action, onOpenDevice }: DriverInUseNoticeProps) {
  const one = devices.length === 1;
  return (
    <div
      role="note"
      data-testid="driver-in-use"
      style={{
        padding: "var(--space-sm) var(--space-md)",
        background: "rgba(244,67,54,0.08)",
        borderRadius: "var(--radius)",
        fontSize: 12,
        color: "var(--text-secondary)",
      }}
    >
      <div>
        <strong>In use:</strong>{" "}
        {one ? "1 device uses this driver." : `${devices.length} devices use this driver.`}
      </div>
      <ul style={{ margin: "4px 0", padding: 0, listStyle: "none", maxHeight: 200, overflowY: "auto" }}>
        {devices.map((device) => (
          <li
            key={device.id}
            style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", padding: "2px 0" }}
          >
            <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
              {device.name}
            </span>
            <button
              onClick={() => onOpenDevice(device.id)}
              style={{
                padding: "2px var(--space-sm)",
                borderRadius: "var(--border-radius)",
                background: "var(--bg-hover)",
                fontSize: 12,
                flexShrink: 0,
              }}
            >
              Open Device
            </button>
          </li>
        ))}
      </ul>
      <div>
        {one ? "Open the device" : "Open each device"} and choose another driver in Edit
        Device, then {action} this driver.
      </div>
    </div>
  );
}

interface DriverInUseDialogProps extends DriverInUseNoticeProps {
  onClose: () => void;
}

/** The in-use notice as the answer to a delete button in a list. */
export function DriverInUseDialog({ devices, action, onOpenDevice, onClose }: DriverInUseDialogProps) {
  return (
    <Modal
      onClose={onClose}
      role="alertdialog"
      labelledBy="driver-in-use-title"
      initialFocus="button[data-close]"
      panelStyle={{ padding: "var(--space-xl)", minWidth: 320, maxWidth: 480 }}
    >
      <h3
        id="driver-in-use-title"
        style={{ marginBottom: "var(--space-md)", fontSize: "var(--font-size-lg)" }}
      >
        Driver In Use
      </h3>
      <DriverInUseNotice devices={devices} action={action} onOpenDevice={onOpenDevice} />
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "var(--space-xl)" }}>
        <button
          data-close
          onClick={onClose}
          style={{
            padding: "var(--space-sm) var(--space-lg)",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-hover)",
          }}
        >
          Close
        </button>
      </div>
    </Modal>
  );
}
