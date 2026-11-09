/**
 * The top of a device's page: its status and name, the actions on the device,
 * and the line under the name (driver, id, Report a problem, a driver update).
 *
 * Which actions a device gets is decided here, from three facts the page
 * reads: whether the running system has answered for the device (`info`),
 * whether it is product hardware, and whether the page may manage it. Enable,
 * Test and Audit this device need the running system's answer; Audit is never
 * offered for product hardware, which connects locally. Reconnect is for an
 * enabled device that is not connected. Edit, Duplicate and Delete are for a
 * device the page can manage (`manageable` on the page: never product
 * hardware, and a device whose driver failed to load still gets them).
 */
import { useCallback, useState, type CSSProperties } from "react";
import { ClipboardCheck, Copy, Pencil, Power, RefreshCw, Trash2, Wifi } from "lucide-react";
import * as api from "../../api/restClient";
import type { DeviceConfig, DeviceInfo, ProjectConfig } from "../../api/types";
import { CopyButton } from "../../components/shared/CopyButton";
import { DeviceStatusDot } from "../../components/shared/DeviceStatusDot";
import { driverLabel } from "../../components/shared/driverLabel";
import { useProjectStore } from "../../store/projectStore";
import type { DeviceAway } from "./deviceAway";
import { findDeviceReferences } from "./deviceUtils";
import { DriverUpdateLine } from "./DriverUpdateLine";
import type { DeviceChecks } from "./useDeviceChecks";

export interface DeviceHeaderProps {
  deviceId: string;
  deviceName: string;
  away: DeviceAway;
  /** The running system's answer for the device; null until it answers. */
  info: DeviceInfo | null;
  /** The device as the project holds it. */
  config: DeviceConfig | undefined;
  isEnabled: boolean;
  installed: boolean;
  manageable: boolean;
  checks: DeviceChecks;
  onEdit: (config: DeviceConfig) => void;
  onDuplicate: (config: DeviceConfig) => void;
  onDeleted: (deviceId: string) => void;
  onReport: () => void;
  onDriverUpdated: () => void;
}

export function DeviceHeader(props: DeviceHeaderProps) {
  const { deviceId, away, info, manageable } = props;
  return (
    <div style={{ marginBottom: "var(--space-xl)" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-md)",
          flexWrap: "wrap",
        }}
      >
        <DeviceStatusDot
          connected={away.connected}
          orphaned={away.orphaned}
          paused={away.paused}
          pausedFor={away.pausedFor}
          restarting={away.restarting}
          asleep={away.asleep}
          size={12}
        />
        <h2 style={{ fontSize: "var(--font-size-xl)", flex: 1 }}>{props.deviceName}</h2>
        <HeaderActions {...props} />
      </div>
      <DriverLine driver={info?.driver} deviceId={deviceId} onReport={props.onReport} />
      {/* Product hardware's driver is part of OpenAVC, and a device whose
          driver is not installed has the orphan banner instead. */}
      {manageable && !away.orphaned && info?.driver && (
        <DriverUpdateLine driverId={info.driver} onUpdated={props.onDriverUpdated} />
      )}
    </div>
  );
}

/** The actions, each offered only where it applies (see the module's
 *  docstring), in the row beside the name. */
function HeaderActions(props: DeviceHeaderProps) {
  const { deviceId, info } = props;
  return (
    <>
      {info && <EnableToggle deviceId={deviceId} config={props.config} isEnabled={props.isEnabled} />}
      {info && <CheckButtons checks={props.checks} installed={props.installed} />}
      {!props.away.connected && props.isEnabled && <ReconnectButton deviceId={deviceId} />}
      {props.manageable && (
        <ManageButtons
          deviceId={deviceId}
          config={props.config}
          onEdit={props.onEdit}
          onDuplicate={props.onDuplicate}
          onDeleted={props.onDeleted}
        />
      )}
    </>
  );
}

/** The frame every action in the header is drawn in. */
function actionStyle(extra: CSSProperties = {}): CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: "var(--space-xs)",
    padding: "var(--space-xs) var(--space-md)",
    borderRadius: "var(--border-radius)",
    background: "var(--bg-hover)",
    fontSize: "var(--font-size-sm)",
    ...extra,
  };
}

const busyOpacity = (busy: boolean) => ({ opacity: busy ? 0.6 : 1 });

/** Turns the device on or off in the project, saved at once. */
function EnableToggle({
  deviceId,
  config,
  isEnabled,
}: {
  deviceId: string;
  config: DeviceConfig | undefined;
  isEnabled: boolean;
}) {
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const toggle = () => {
    if (!project || !config) return;
    const updatedDevices = project.devices.map((d) =>
      d.id === deviceId ? { ...d, enabled: !isEnabled } : d
    );
    update({ devices: updatedDevices });
    useProjectStore.getState().debouncedSave();
  };
  return (
    <button
      onClick={toggle}
      title={isEnabled ? "Disable device" : "Enable device"}
      style={actionStyle({
        background: isEnabled ? "rgba(76,175,80,0.15)" : "var(--bg-hover)",
        color: isEnabled ? "var(--color-success)" : "var(--text-muted)",
      })}
    >
      <Power size={14} /> {isEnabled ? "Enabled" : "Disabled"}
    </button>
  );
}

function CheckButtons({ checks, installed }: { checks: DeviceChecks; installed: boolean }) {
  return (
    <>
      <button
        onClick={checks.runTest}
        disabled={checks.testing}
        title="Test device connection"
        style={actionStyle(busyOpacity(checks.testing))}
      >
        <Wifi size={14} /> {checks.testing ? "Testing..." : "Test"}
      </button>
      <button
        onClick={() => void checks.openAudit()}
        disabled={checks.openingAudit || installed}
        title={installed ? "Product hardware uses a local connection. Network audit is unavailable for this connection." : "Check this device in detail and write a report about it"}
        style={actionStyle(busyOpacity(checks.openingAudit))}
      >
        <ClipboardCheck size={14} /> Audit this device
      </button>
    </>
  );
}

/** Asks the server to drop and redial the device. The button reads
 *  Reconnecting for two seconds whatever the answer: the device's status dot
 *  and banners say how the redial went. */
function ReconnectButton({ deviceId }: { deviceId: string }) {
  const [reconnecting, setReconnecting] = useState(false);
  const reconnect = async () => {
    setReconnecting(true);
    try {
      await api.reconnectDevice(deviceId);
    } catch (e) {
      console.error(e);
    } finally {
      setTimeout(() => setReconnecting(false), 2000);
    }
  };
  return (
    <button
      onClick={reconnect}
      disabled={reconnecting}
      title="Force reconnect"
      style={actionStyle({ color: "var(--accent)", ...busyOpacity(reconnecting) })}
    >
      <RefreshCw size={14} /> {reconnecting ? "Reconnecting..." : "Reconnect"}
    </button>
  );
}

function ManageButtons({
  deviceId,
  config,
  onEdit,
  onDuplicate,
  onDeleted,
}: {
  deviceId: string;
  config: DeviceConfig | undefined;
  onEdit: (config: DeviceConfig) => void;
  onDuplicate: (config: DeviceConfig) => void;
  onDeleted: (deviceId: string) => void;
}) {
  return (
    <>
      <button
        onClick={() => config && onEdit(config)}
        title="Edit device settings"
        style={actionStyle()}
      >
        <Pencil size={14} /> Edit
      </button>
      <button
        onClick={() => config && onDuplicate(config)}
        title="Duplicate device"
        style={actionStyle()}
      >
        <Copy size={14} /> Duplicate
      </button>
      <DeleteDevice deviceId={deviceId} onDeleted={onDeleted} />
    </>
  );
}

/** Delete, after a question that lists what still uses the device. */
function DeleteDevice({ deviceId, onDeleted }: { deviceId: string; onDeleted: (deviceId: string) => void }) {
  const project = useProjectStore((s) => s.project);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const handleDelete = useCallback(async () => {
    setDeleting(true);
    try {
      await api.deleteDevice(deviceId);
      onDeleted(deviceId);
    } catch (e) {
      console.error(e);
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  }, [deviceId, onDeleted]);

  if (!confirmDelete) {
    return (
      <button
        onClick={() => setConfirmDelete(true)}
        title="Delete device"
        style={actionStyle({ color: "var(--color-error)" })}
      >
        <Trash2 size={14} /> Delete
      </button>
    );
  }
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
        <span style={{ fontSize: "var(--font-size-sm)", color: "var(--color-error)" }}>
          Delete this device?
        </span>
        <button
          onClick={handleDelete}
          disabled={deleting}
          style={{
            padding: "var(--space-xs) var(--space-md)",
            borderRadius: "var(--border-radius)",
            background: "var(--color-error)",
            color: "#fff",
            fontSize: "var(--font-size-sm)",
            opacity: deleting ? 0.6 : 1,
          }}
        >
          {deleting ? "Deleting..." : "Yes, Delete"}
        </button>
        <button
          onClick={() => setConfirmDelete(false)}
          style={{
            padding: "var(--space-xs) var(--space-md)",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-hover)",
            fontSize: "var(--font-size-sm)",
          }}
        >
          Cancel
        </button>
      </div>
      {project && <ReferencesWarning project={project} deviceId={deviceId} />}
    </div>
  );
}

/** The places in the project that still use the device: the first five,
 *  then how many more. */
function ReferencesWarning({ project, deviceId }: { project: ProjectConfig; deviceId: string }) {
  const refs = findDeviceReferences(project, deviceId);
  if (refs.length === 0) return null;
  return (
    <div style={{ marginTop: "var(--space-xs)", padding: "var(--space-sm)", background: "rgba(244,67,54,0.08)", borderRadius: "var(--border-radius)", fontSize: 12, color: "var(--text-secondary)" }}>
      <strong>Warning:</strong> This device is referenced in {refs.length} place(s):
      <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
        {refs.slice(0, 5).map((r, i) => <li key={i}>{r}</li>)}
        {refs.length > 5 && <li>...and {refs.length - 5} more</li>}
      </ul>
    </div>
  );
}

/** The driver, the device's id with a copy button, and Report a problem. */
function DriverLine({
  driver,
  deviceId,
  onReport,
}: {
  driver: string | undefined;
  deviceId: string;
  onReport: () => void;
}) {
  return (
    <div style={{ marginLeft: 22, display: "flex", alignItems: "center", gap: "var(--space-sm)", flexWrap: "wrap" }}>
      <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
        {driverLabel(driver)}
      </span>
      <span style={{ color: "var(--border-color)" }}>&middot;</span>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
        <code style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
          {deviceId}
        </code>
        <CopyButton value={deviceId} title="Copy device ID" />
      </span>
      <span style={{ color: "var(--border-color)" }}>&middot;</span>
      <button
        type="button"
        onClick={onReport}
        onMouseEnter={(e) => (e.currentTarget.style.textDecoration = "underline")}
        onMouseLeave={(e) => (e.currentTarget.style.textDecoration = "none")}
        style={{
          background: "none",
          border: "none",
          padding: 0,
          cursor: "pointer",
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
        }}
      >
        Report a problem
      </button>
    </div>
  );
}
