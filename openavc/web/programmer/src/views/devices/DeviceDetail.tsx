import { InstalledEquipment } from "./InstalledEquipment";
import { Fragment, useState, useEffect, useCallback, useRef, useMemo } from "react";
import { Pencil, Trash2, Wifi, Power, RefreshCw, Copy, X, Search, ClipboardCheck } from "lucide-react";
import { CopyButton } from "../../components/shared/CopyButton";
import { DeviceStatusDot } from "../../components/shared/DeviceStatusDot";
import { useProjectStore } from "../../store/projectStore";
import { useConnectionStore } from "../../store/connectionStore";
import * as api from "../../api/restClient";
import { getAuditDeviceTarget } from "../../api/auditClient";
import { useAuditStore } from "../../store/auditStore";
import type { BridgePort, DeviceConfig, DeviceInfo, DriverInfo } from "../../api/types";
import {
  MonitorCell, MonitorControl, MonitorLimitsPanel, type DeclaredReading,
} from "../../components/shared/MonitorControl";
import type { MonitorConfig } from "../../api/types";
import { parentAwayFrom, scanChildTrouble, troubleSummary } from "./childPresence";
import type { ChildTypeInfo } from "./childPresence";
import { DEVICE_STALE_TITLE, LastHeard, staleValueStyle } from "./staleReading";
import { parseStateOptionList } from "../../components/shared/paramOptions";
import { DevicePanelSlot, ContextActionRenderer } from "../../components/plugins/PluginExtensions";
import { findDeviceReferences } from "./deviceUtils";
import { connectionTestText } from "./deviceAddress";
import { readingText, valueText } from "../../components/shared/booleanWords";
import { ChildEntities } from "./ChildEntities";
import { QuickActions } from "./QuickActions";
import { InlineProtocolEditor } from "./InlineProtocolEditor";
import { IrCodesEditor } from "./IrCodesEditor";
import { ConfigTableEditor } from "./ConfigTableEditor";
import { BridgeIrPort } from "./BridgeIrPort";
import { SshKeySection, canUseSshKey } from "./SshKeySection";
import { ApiError, parseApiError } from "../../api/errors";
import { useConnectionTestResult } from "./useConnectionTestResult";
import { driverLabel } from "../../components/shared/driverLabel";
import { DriverUpdateLine } from "./DriverUpdateLine";
import { NeedsLoginBanner } from "./NeedsLoginBanner";
import { ProblemReportDialog } from "./ProblemReportDialog";
import type { ProblemReportContext } from "../../api/problemReportClient";
import { awayBanner, hasSignInFields, readDeviceAway, reportableOffline, waitsForLogin } from "./deviceAway";
import { AsleepBanner, OfflineBanner, PausedBanner, RestartingBanner } from "./AwayBanners";
import { OrphanBanner } from "./OrphanBanner";
import { ChildTroubleBanner } from "./ChildTroubleBanner";
import { DeviceSettingsSection } from "./DeviceSettingsSection";
import { DeviceLog } from "./DeviceLog";
import { SendCommandSection } from "./SendCommandSection";
import { sectionStyle, sectionTitleStyle } from "./deviceSectionStyles";

export function DeviceDetail({
  deviceId,
  onEdit,
  onDeleted,
  onDuplicate,
  onBrowseDrivers,
  onOpenDevice,
  installedDriver,
}: {
  deviceId: string;
  onEdit: (config: DeviceConfig) => void;
  onDeleted: (deviceId: string) => void;
  onDuplicate: (config: DeviceConfig) => void;
  onBrowseDrivers?: () => void;
  onOpenDevice?: (deviceId: string) => void;
  /** The installed definition of this device's driver: what the page knows
   *  of the driver while the device is disabled and nothing is running it. */
  installedDriver?: DriverInfo;
}) {
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const liveState = useConnectionStore((s) => s.liveState);
  const [deviceInfo, setDeviceInfo] = useState<DeviceInfo | null>(null);
  // The running system has no such device (its driver failed while loading;
  // the device log says why). It is still in the project, so it can still be
  // edited, duplicated or deleted.
  const [notRunning, setNotRunning] = useState(false);
  // Report a problem is open, from where and with what the page showed.
  const [reportContext, setReportContext] = useState<ProblemReportContext | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [testing, setTesting] = useState(false);
  // Why "Audit this device" could not start (a device on a serial port, say).
  const [auditRefusal, setAuditRefusal] = useState("");
  const [openingAudit, setOpeningAudit] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  // Which Live State row has its Monitor limits open. One at a time: the panel
  // is drawn in a full-width row of its own, and a table with six of them open
  // stops being a table.
  const [openMonitor, setOpenMonitor] = useState<string | null>(null);

  const monitors = project?.monitors ?? [];
  const updateWithUndo = useProjectStore((s) => s.updateWithUndo);
  const handleMonitors = useCallback(
    (next: MonitorConfig[], description: string) => {
      updateWithUndo({ monitors: next }, description);
      useProjectStore.getState().debouncedSave(1500);
    },
    [updateWithUndo],
  );

  // The id this page is showing, as of the last render. A fetch that resolves
  // after the user has moved to another device would otherwise draw that
  // device's commands, settings and driver info under this one's name.
  const shownDeviceId = useRef(deviceId);
  shownDeviceId.current = deviceId;
  const refetchDeviceInfo = useCallback(() => {
    const forDevice = deviceId;
    api.getDevice(deviceId)
      .then((info) => {
        if (shownDeviceId.current !== forDevice) return;
        setDeviceInfo(info);
        setNotRunning(false);
      })
      .catch((e) => {
        if (shownDeviceId.current === forDevice && e instanceof ApiError && e.status === 404) {
          setDeviceInfo(null);
          setNotRunning(true);
        }
        console.error(e);
      });
  }, [deviceId]);

  // Refetch on mount and whenever the connection flips: a role-adaptive
  // driver narrows its commands/actions to what the hardware reports at
  // connect, so info fetched while the device was offline goes stale the
  // moment it comes up (and vice versa).
  // The same when the device's web page is found: the platform looks for it
  // in the background after connect, and Open Web UI comes with the answer.
  const away = readDeviceAway(liveState, deviceId);
  const connected = away.connected;
  const webUiUrl = liveState[`device.${deviceId}.web_ui_url`];
  const [testResult, setTestResult] = useConnectionTestResult(deviceId, connected);
  const runtimeEnabled = liveState[`device.${deviceId}.enabled`] === true;
  // And when it starts or stops waiting for a login: which settings wait is
  // in the device info, and a login saved anywhere changes it.
  const awaitingLogin = waitsForLogin(away);
  useEffect(() => {
    refetchDeviceInfo();
  }, [refetchDeviceInfo, connected, webUiUrl, runtimeEnabled, awaitingLogin]);

  const deviceConfig = project?.devices.find((d) => d.id === deviceId);
  const isEnabled = deviceConfig?.enabled !== false;
  const installed = deviceInfo?.ownership === "manufacturer";
  // Product hardware is never edited or deleted here, and only the running
  // system says which devices are; until it answers, nothing is offered.
  const manageable = deviceInfo ? !installed : notRunning && Boolean(deviceConfig);
  const banner = awayBanner(away, isEnabled, deviceInfo?.needs_login?.length ?? 0);

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

  const handleTestConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.testDeviceConnection(deviceId);
      setTestResult(result);
    } catch (e) {
      setTestResult({ success: false, error: parseApiError(e), latency_ms: null });
    } finally {
      setTesting(false);
    }
  };

  // Open the Device Audit wizard on this device: its address, driver and
  // saved settings filled in, and the device paused while the audit runs.
  const handleAudit = async () => {
    setAuditRefusal("");
    setOpeningAudit(true);
    try {
      const target = await getAuditDeviceTarget(deviceId);
      if (!target.auditable) {
        setAuditRefusal(target.reason);
        return;
      }
      useAuditStore.getState().openWizard({ address: target.address, deviceId });
    } catch (e) {
      setAuditRefusal(parseApiError(e));
    } finally {
      setOpeningAudit(false);
    }
  };

  const handleToggleEnabled = async () => {
    if (!project || !deviceConfig) return;
    const updatedDevices = project.devices.map((d) =>
      d.id === deviceId ? { ...d, enabled: !isEnabled } : d
    );
    update({ devices: updatedDevices });
    useProjectStore.getState().debouncedSave();
  };

  const handleReconnect = async () => {
    setReconnecting(true);
    try {
      await api.reconnectDevice(deviceId);
    } catch (e) {
      console.error(e);
    } finally {
      setTimeout(() => setReconnecting(false), 2000);
    }
  };

  // Drivers that manage child entities declare them in DRIVER_INFO; those
  // keys (device.<id>.<type>.<paddedId>.<prop>) belong to the Child Entities
  // tab, not this flat list — a fully-loaded controller has tens of thousands
  // of them. We hide them here only; the StateStore, scripts, macros,
  // triggers, and the cloud relay still see every key (presentation only).
  const childTypeNames = useMemo(() => {
    const cet = deviceInfo?.driver_info?.child_entity_types as
      | Record<string, unknown>
      | undefined;
    return cet ? new Set(Object.keys(cet)) : new Set<string>();
  }, [deviceInfo]);

  // Which sub-units are not answering, for the banner below. A wedged
  // endpoint used to be one word in a column inside a collapsed panel, which
  // is how an afternoon went into hunting a power fault that did not exist.
  // It belongs on the page the way an offline device's reason is on its card.
  const childTrouble = useMemo(() => {
    const cet = (deviceInfo?.driver_info?.child_entity_types ?? {}) as
      Record<string, ChildTypeInfo>;
    return troubleSummary(scanChildTrouble(liveState, deviceId, cet));
  }, [liveState, deviceId, deviceInfo]);

  // Extract device state from flat liveState
  const prefix = `device.${deviceId}.`;
  // [key, the value as shown, the full value when rounding shortened it]
  const stateEntries: [string, string, string | undefined][] = [];
  const declaredReadings = (deviceInfo?.driver_info?.state_variables ?? {}) as
    Record<string, { step?: unknown }>;
  let hiddenChildKeyCount = 0;
  const stateTerm = searchTerm.trim().toLowerCase();
  for (const [key, value] of Object.entries(liveState)) {
    if (!key.startsWith(prefix)) continue;
    const rest = key.slice(prefix.length);
    if (childTypeNames.size > 0) {
      const dot = rest.indexOf(".");
      if (dot > 0 && childTypeNames.has(rest.slice(0, dot))) {
        hiddenChildKeyCount++;
        continue;
      }
    }
    const valueStr = readingText(value, declaredReadings[rest]);
    const fullValue = valueText(value);
    if (
      stateTerm &&
      !rest.toLowerCase().includes(stateTerm) &&
      !valueStr.toLowerCase().includes(stateTerm)
    ) {
      continue;
    }
    stateEntries.push([rest, valueStr, fullValue !== valueStr ? fullValue : undefined]);
  }

  const deviceName = String(liveState[`device.${deviceId}.name`] ?? deviceConfig?.name ?? deviceId);
  // Optional driver-provided troubleshooting hint (DRIVER_INFO.help.connection),
  // surfaced under the offline reason. The driver supplies device-specific
  // guidance the generic classifier can't know (e.g. "enable SSH first").
  const connectionHint = String(
    (deviceInfo?.driver_info as { help?: { connection?: string } } | undefined)
      ?.help?.connection ?? ""
  );

  // What the driver declares about each state variable — label, unit, type,
  // range, allowed values. Monitor pre-fills from this, which is why tagging
  // lamp hours is one click rather than a form: the projector's driver already
  // said it is a number called "Lamp Hours" measured in hours.
  const declaredStateVars = (deviceInfo?.driver_info?.state_variables ?? {}) as
    Record<string, DeclaredReading & { values?: string[] }>;

  // State vars that back a param dropdown (any command/action param declaring
  // `options_state`) hold a JSON-encoded {value,label} list. The Live State
  // table renders those as a readable option summary instead of the raw JSON.
  const optionFeedKeys = useMemo(() => {
    const keys = new Set<string>();
    const scan = (params: unknown) => {
      if (!params || typeof params !== "object") return;
      for (const def of Object.values(params as Record<string, unknown>)) {
        const os = (def as { options_state?: unknown } | null)?.options_state;
        if (typeof os === "string" && os) keys.add(os);
      }
    };
    for (const cmd of Object.values(
      (deviceInfo?.commands ?? {}) as Record<string, unknown>,
    )) {
      scan((cmd as { params?: unknown } | null)?.params);
    }
    const actions = (deviceInfo?.driver_info as { actions?: unknown } | undefined)
      ?.actions;
    if (Array.isArray(actions)) {
      for (const a of actions) scan((a as { params?: unknown } | null)?.params);
    }
    return keys;
  }, [deviceInfo]);

  // Bridge: when this device's driver advertises bridge ports, it's a bridge
  // other devices route through. The card below lists each port + what's bound
  // to it, plus a link to the unit's own web UI (at its connection host).
  const bridgePorts =
    ((deviceInfo?.driver_info as { bridge?: { ports?: BridgePort[] } } | undefined)
      ?.bridge?.ports) ?? [];
  const isBridge = bridgePorts.length > 0;
  const bridgeHost = (project?.connections?.[deviceId]?.host as string | undefined) || "";
  const devicesBoundToPort = (portId: string) =>
    (project?.devices ?? []).filter((d) => {
      const c = project?.connections?.[d.id];
      return c?.bridge === deviceId && c?.bridge_port === portId;
    });

  return (
    <div>
      {/* Header */}
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
            connected={connected}
            orphaned={away.orphaned}
            paused={away.paused}
            pausedFor={away.pausedFor}
            restarting={away.restarting}
            asleep={away.asleep}
            size={12}
          />
          <h2 style={{ fontSize: "var(--font-size-xl)", flex: 1 }}>{deviceName}</h2>
          {deviceInfo && <>
          <button
            onClick={handleToggleEnabled}
            title={isEnabled ? "Disable device" : "Enable device"}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: isEnabled ? "rgba(76,175,80,0.15)" : "var(--bg-hover)",
              color: isEnabled ? "var(--color-success)" : "var(--text-muted)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            <Power size={14} /> {isEnabled ? "Enabled" : "Disabled"}
          </button>
          </>}
          {deviceInfo && <>
          <button
            onClick={handleTestConnection}
            disabled={testing}
            title="Test device connection"
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              fontSize: "var(--font-size-sm)",
              opacity: testing ? 0.6 : 1,
            }}
          >
            <Wifi size={14} /> {testing ? "Testing..." : "Test"}
          </button>
          <button
            onClick={() => void handleAudit()}
            disabled={openingAudit || installed}
            title={installed ? "Product hardware uses a local connection. Network audit is unavailable for this connection." : "Check this device in detail and write a report about it"}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              fontSize: "var(--font-size-sm)",
              opacity: openingAudit ? 0.6 : 1,
            }}
          >
            <ClipboardCheck size={14} /> Audit this device
          </button>
          </>}
          {!connected && isEnabled && (
            <button
              onClick={handleReconnect}
              disabled={reconnecting}
              title="Force reconnect"
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "var(--border-radius)",
                background: "var(--bg-hover)",
                color: "var(--accent)",
                fontSize: "var(--font-size-sm)",
                opacity: reconnecting ? 0.6 : 1,
              }}
            >
              <RefreshCw size={14} /> {reconnecting ? "Reconnecting..." : "Reconnect"}
            </button>
          )}
          {manageable && <>
          <button
            onClick={() => deviceConfig && onEdit(deviceConfig)}
            title="Edit device settings"
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            <Pencil size={14} /> Edit
          </button>
          <button
            onClick={() => deviceConfig && onDuplicate(deviceConfig)}
            title="Duplicate device"
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            <Copy size={14} /> Duplicate
          </button>
          {confirmDelete ? (
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
              {project && (() => {
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
              })()}
            </div>
          ) : (
            <button
              onClick={() => setConfirmDelete(true)}
              title="Delete device"
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "var(--border-radius)",
                background: "var(--bg-hover)",
                color: "var(--color-error)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              <Trash2 size={14} /> Delete
            </button>
          )}
          </>}
        </div>
        <div style={{ marginLeft: 22, display: "flex", alignItems: "center", gap: "var(--space-sm)", flexWrap: "wrap" }}>
          <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
            {driverLabel(deviceInfo?.driver)}
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
            onClick={() => setReportContext({ source: "driver_line" })}
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
        {/* Product hardware's driver is part of OpenAVC, and a device whose
            driver is not installed has the orphan banner below instead. */}
        {manageable && !away.orphaned && deviceInfo?.driver && (
          <DriverUpdateLine driverId={deviceInfo.driver} onUpdated={refetchDeviceInfo} />
        )}
      </div>

      {/* Why the device is away: one banner at a time, chosen in deviceAway.ts.
          The first four sit above product hardware's own panel, the login box
          and the Offline banner below it. */}
      {banner === "orphaned" && (
        <OrphanBanner
          driverId={deviceConfig?.driver ?? ""}
          onReassign={() => deviceConfig && onEdit(deviceConfig)}
          onBrowseDrivers={onBrowseDrivers}
          onActivated={() => api.getDevice(deviceId).then(setDeviceInfo).catch(console.error)}
        />
      )}
      {banner === "restarting" && <RestartingBanner detail={away.offlineDetail} />}
      {banner === "asleep" && <AsleepBanner detail={away.offlineDetail} />}
      {banner === "paused" && <PausedBanner deviceId={deviceId} pausedFor={away.pausedFor} />}

      {connected && childTrouble && (
        <ChildTroubleBanner
          headline={childTrouble.headline}
          names={childTrouble.names}
        />
      )}

      {installed && (
        <InstalledEquipment
          key={deviceId}
          deviceId={deviceId}
          name={deviceName}
          completionWait={deviceInfo?.completion_wait}
        />
      )}

      {/* Saving the login adds the device again, so there is nothing to retry. */}
      {banner === "needs_login" && (
        <NeedsLoginBanner
          key={deviceId}
          deviceId={deviceId}
          fields={deviceInfo?.needs_login ?? []}
          driverInfo={deviceInfo?.driver_info}
          onSaved={refetchDeviceInfo}
        />
      )}
      {banner === "offline" && (
        <OfflineBanner
          installed={installed}
          detail={away.offlineDetail}
          reason={away.offlineReason}
          failed={away.reconnectFailed}
          hint={connectionHint}
          signInFields={hasSignInFields(
            deviceInfo?.driver_info?.config_schema as Record<string, unknown> | undefined,
          )}
          onReport={
            reportableOffline(away.offlineReason)
              ? () => setReportContext({ source: "offline_banner", error: `Offline. ${away.offlineDetail}` })
              : undefined
          }
        />
      )}

      {/* Why "Audit this device" did not open the wizard */}
      {auditRefusal && (
        <div
          role="status"
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-sm)",
            padding: "var(--space-sm) var(--space-md)",
            borderRadius: "var(--border-radius)",
            marginBottom: "var(--space-md)",
            fontSize: "var(--font-size-sm)",
            background: "var(--bg-hover)",
            color: "var(--text-secondary)",
          }}
        >
          <ClipboardCheck size={14} style={{ flexShrink: 0 }} />
          <span style={{ flex: 1 }}>{auditRefusal}</span>
          <button
            onClick={() => setAuditRefusal("")}
            aria-label="Dismiss"
            title="Dismiss"
            style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: "var(--text-muted)" }}
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* Test connection result */}
      {testResult && (
        <div
          style={{
            padding: "var(--space-sm) var(--space-md)",
            borderRadius: "var(--border-radius)",
            marginBottom: "var(--space-md)",
            fontSize: "var(--font-size-sm)",
            background: testResult.success
              ? "rgba(76,175,80,0.15)"
              : "var(--color-error-bg)",
            color: testResult.success ? "var(--color-success)" : "var(--color-error)",
          }}
        >
          {connectionTestText(
            testResult,
            (id) => project?.devices.find((d) => d.id === id)?.name || id,
          )}
          {testResult.note && (
            <div style={{ marginTop: "var(--space-xs)", color: "var(--text-muted)" }}>
              {testResult.note}
            </div>
          )}
        </div>
      )}

      {/* OpenAVC's own SSH key, for a device that connects over SSH and can
          sign in with a key: key sign-in uses it whenever the device's key
          path is blank. */}
      {deviceInfo?.connection?.transport === "ssh" && canUseSshKey(
        Object.keys(deviceInfo.driver_info ?? {}).length > 0 ? deviceInfo.driver_info : installedDriver,
        { ...deviceConfig?.config, ...project?.connections?.[deviceId] },
      ) && (
        <SshKeySection sectionStyle={sectionStyle} sectionTitleStyle={sectionTitleStyle} />
      )}

      {/* Bridge Ports — only for bridge devices (driver advertises ports other
          devices connect through). Lists each port, what's bound to it, and a
          link to the unit's own web UI. */}
      {isBridge && (
        <div style={sectionStyle}>
          <h3 style={sectionTitleStyle}>Bridge Ports</h3>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
            Other devices connect through this bridge's ports. To bind one, open
            that device's Connection settings and choose "Through a bridge".
          </div>
          {bridgeHost && (
            <div style={{ marginBottom: "var(--space-sm)", fontSize: "var(--font-size-sm)" }}>
              <a
                href={`http://${bridgeHost}`}
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: "var(--accent-bg)", textDecoration: "underline" }}
              >
                Open this unit's web interface
              </a>
            </div>
          )}
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
            {bridgePorts.map((p) => {
              const bound = devicesBoundToPort(p.id);
              return (
                <div
                  key={p.id}
                  style={{
                    border: "1px solid var(--border-color)",
                    borderRadius: "var(--border-radius)",
                    padding: "var(--space-sm) var(--space-md)",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ fontSize: "var(--font-size-sm)" }}>
                      {p.label || p.id}
                      <span style={{ marginLeft: 6, fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase" }}>
                        {p.kind}
                      </span>
                    </span>
                    <code style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                      {p.id}
                    </code>
                  </div>
                  <div style={{ marginTop: 4, fontSize: 11, color: "var(--text-muted)" }}>
                    {bound.length === 0
                      ? `Nothing bound${
                          p.kind === "serial" && p.passthrough_port
                            ? ` (pass-through TCP ${p.passthrough_port})`
                            : ""
                        }`
                      : `Bound: ${bound.map((d) => d.name || d.id).join(", ")}`}
                  </div>
                  {p.kind === "ir" && (
                    <BridgeIrPort
                      bridgeId={deviceId}
                      portId={p.id}
                      portLabel={p.label || p.id}
                      connected={connected}
                      bound={bound}
                      onOpenDevice={onOpenDevice}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Quick Actions — driver-promoted one-click buttons. Additive: the full
          Send Command list below stays complete. */}
      <QuickActions
        deviceId={deviceId}
        actions={deviceInfo?.actions ?? []}
        connected={connected}
        liveState={liveState}
        onInvoked={refetchDeviceInfo}
      />

      {/* Unified filter — one box narrows both the child-entity rows (across
          every value in each row, not just the summary columns) and the Live
          State list below. */}
      <div style={{ position: "relative", marginBottom: "var(--space-lg)" }}>
        <Search
          size={16}
          style={{
            position: "absolute",
            left: 10,
            top: "50%",
            transform: "translateY(-50%)",
            color: "var(--text-muted)",
            pointerEvents: "none",
          }}
        />
        <input
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          placeholder="Filter children and live state by id, label, name, or any value"
          data-testid="device-filter"
          style={{ width: "100%", padding: "var(--space-sm) 34px" }}
        />
        {searchTerm && (
          <button
            onClick={() => setSearchTerm("")}
            title="Clear filter"
            style={{
              position: "absolute",
              right: 8,
              top: "50%",
              transform: "translateY(-50%)",
              padding: 2,
              background: "transparent",
              color: "var(--text-muted)",
              border: "none",
              cursor: "pointer",
            }}
          >
            <X size={16} />
          </button>
        )}
      </div>

      {/* Child Entities (only renders when the driver declares any).
          hiddenChildKeyCount is the count the Live State panel below reports as
          living up here, and it is also how this panel hears that the roster
          changed — the two used to contradict each other on one screen. */}
      <ChildEntities
        deviceId={deviceId}
        search={searchTerm}
        connected={connected}
        parentAway={parentAwayFrom(liveState, deviceId)}
        childKeyCount={hiddenChildKeyCount}
        config={deviceConfig?.config}
        driverInfo={deviceInfo?.driver_info}
        monitors={monitors}
        onMonitorsChange={handleMonitors}
      />

      {/* Live State */}
      <div style={sectionStyle}>
        <h3 style={sectionTitleStyle}>Live State</h3>
        {hiddenChildKeyCount > 0 && (
          <div
            style={{
              fontSize: 11,
              color: "var(--text-muted)",
              marginTop: "-4px",
              marginBottom: "var(--space-sm)",
            }}
          >
            {hiddenChildKeyCount.toLocaleString()} child-entity state{" "}
            {hiddenChildKeyCount === 1 ? "key is" : "keys are"} shown in the
            Child Entities tab above.
          </div>
        )}
        <div
          style={{
            background: "var(--bg-surface)",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            overflow: "hidden",
          }}
        >
          {stateEntries.length === 0 ? (
            <div
              style={{
                padding: "var(--space-lg)",
                color: "var(--text-muted)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              {stateTerm ? `No state matches "${searchTerm}"` : "No state values yet"}
            </div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <tbody>
                {stateEntries.map(([key, value, fullValue]) => {
                  const fullKey = `device.${deviceId}.${key}`;
                  const monitor = monitors.find((m) => m.key === fullKey);
                  // A value the device is no longer reporting. Only the
                  // driver's own readings: `connected`, `enabled`, the fault
                  // pair and the rest of the platform's keys are statements
                  // about the device that are true right now.
                  const stale =
                    !connected && value !== "" && key in declaredStateVars;
                  return (
                  <Fragment key={key}>
                  <tr
                    data-testid={`live-state-${key}`}
                    style={{ borderBottom: monitor && openMonitor === fullKey ? undefined : "1px solid var(--border-color)" }}
                  >
                    <td
                      style={{
                        padding: "var(--space-sm) var(--space-md)",
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--font-size-sm)",
                        color: "var(--text-secondary)",
                        width: "40%",
                      }}
                    >
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                        {key}
                        <CopyButton value={`device.${deviceId}.${key}`} size={11} title="Copy full state key" />
                      </span>
                    </td>
                    <td
                      title={stale ? DEVICE_STALE_TITLE : fullValue}
                      style={{
                        padding: "var(--space-sm) var(--space-md)",
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--font-size-sm)",
                        ...(stale ? staleValueStyle : null),
                      }}
                    >
                      {optionFeedKeys.has(key) ? (
                        <OptionFeedValue raw={value} />
                      ) : (
                        value
                      )}
                      {stale && <LastHeard />}
                    </td>
                    {/* The second authoring door. Most of what a room's health
                        actually is lives here — lamp hours, fault flags, input
                        signal presence, a DSP's temperature — and until now
                        none of it could be tagged at all. */}
                    <MonitorCell padding="var(--space-xs) var(--space-md)">
                      <MonitorControl
                        compact
                        toggleOnly
                        stateKey={fullKey}
                        declared={declaredStateVars[key]}
                        monitors={monitors}
                        liveValue={liveState[fullKey]}
                        onChange={handleMonitors}
                        open={openMonitor === fullKey}
                        onOpenChange={(next) => setOpenMonitor(next ? fullKey : null)}
                      />
                    </MonitorCell>
                  </tr>
                  {monitor && openMonitor === fullKey && (
                    <tr style={{ borderBottom: "1px solid var(--border-color)" }}>
                      <td colSpan={3} style={{ padding: "0 var(--space-md) var(--space-md)" }}>
                        <MonitorLimitsPanel
                          monitor={monitor}
                          declared={declaredStateVars[key]}
                          monitors={monitors}
                          onChange={handleMonitors}
                        />
                      </td>
                    </tr>
                  )}
                  </Fragment>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Device Settings */}
      {isEnabled && runtimeEnabled && deviceInfo?.id === deviceId && !deviceInfo.orphaned && (
        <DeviceSettingsSection key={`settings:${deviceId}`} deviceId={deviceId} connected={connected} />
      )}

      {/* Commands & Responses — no-code protocol editor for the generic devices
          (driver opts in via DRIVER_INFO.inline_protocol). Writes commands /
          responses / state_variables into the device config. */}
      {deviceInfo?.driver_info?.inline_protocol === true && (
        <InlineProtocolEditor
          deviceId={deviceId}
          driverInfo={deviceInfo.driver_info as Record<string, unknown>}
          connected={connected}
          onSaved={refetchDeviceInfo}
        />
      )}

      {/* IR Codes — the code-set editor for IR devices (driver opts in via
          DRIVER_INFO.ir_codes). Writes ir_codes into the device config. */}
      {deviceInfo?.driver_info?.ir_codes === true && (
        <IrCodesEditor
          deviceId={deviceId}
          connected={connected}
          onSaved={refetchDeviceInfo}
        />
      )}

      {/* Table config fields — a friendly row editor for any config_schema field
          declared `type: "table"` (e.g. a Modbus register map). Detected
          generically from the schema, not a bespoke driver flag. Writes the
          list of row objects into the device config. */}
      {Object.entries(
        (deviceInfo?.driver_info?.config_schema ?? {}) as Record<string, Record<string, unknown>>,
      )
        .filter(([, schema]) => schema?.type === "table")
        .map(([key, schema]) => (
          <ConfigTableEditor
            key={key}
            deviceId={deviceId}
            fieldKey={key}
            fieldSchema={schema}
            connected={connected}
            onSaved={refetchDeviceInfo}
          />
        ))}

      <SendCommandSection
        deviceId={deviceId}
        deviceName={deviceName}
        commands={deviceInfo?.commands ?? {}}
        connected={connected}
        onReport={setReportContext}
      />

      {/* Plugin Device Panels */}
      <DevicePanelSlot
        deviceId={deviceId}
        driverId={deviceConfig?.driver ?? ""}
      />

      {/* Plugin Context Actions */}
      <ContextActionRenderer context="device" deviceId={deviceId} driverId={deviceConfig?.driver} />

      {/* Device Log */}
      <DeviceLog deviceId={deviceId} />

      {reportContext && (
        <ProblemReportDialog
          deviceId={deviceId}
          deviceName={deviceName}
          context={reportContext}
          onClose={() => setReportContext(null)}
        />
      )}
    </div>
  );
}

/** Readable rendering for a picker-feed state value — a JSON-encoded option
 *  list a command param references via `options_state`. Shows the option
 *  labels instead of the raw JSON; falls back to the raw string when the
 *  value doesn't parse (empty, not yet published, malformed). */
function OptionFeedValue({ raw }: { raw: string }) {
  const options = parseStateOptionList(raw);
  if (options.length === 0) return <>{raw}</>;
  const labels = options.map((o) => o.label);
  const MAX_SHOWN = 8;
  const shown = labels.slice(0, MAX_SHOWN).join(", ");
  const more = labels.length - MAX_SHOWN;
  return (
    <span title={`${labels.length} options: ${labels.join(", ")}`}>
      <span style={{ color: "var(--text-muted)" }}>
        {labels.length} option{labels.length === 1 ? "" : "s"}:{" "}
      </span>
      {shown}
      {more > 0 && (
        <span style={{ color: "var(--text-muted)" }}> (+{more} more)</span>
      )}
    </span>
  );
}
