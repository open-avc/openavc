import { InstalledEquipment } from "./InstalledEquipment";
import { BuiltInEquipmentNotice } from "../../components/shared/BuiltInEquipmentNotice";
import { Fragment, useState, useEffect, useCallback, useRef, useMemo } from "react";
import { Send, Pencil, Trash2, Wifi, WifiOff, Power, RefreshCw, Copy, Settings, Check, X, Loader2, Search, Pause, Play, AlertTriangle, ClipboardCheck } from "lucide-react";
import { CopyButton } from "../../components/shared/CopyButton";
import { DeviceStatusDot } from "../../components/shared/DeviceStatusDot";
import { pausedBannerText } from "../../components/shared/pausedText";
import { useProjectStore } from "../../store/projectStore";
import { SearchableSelect } from "../../components/shared/SearchableSelect";
import { commandOptions } from "../../components/shared/pickerOptions";
import { useConnectionStore } from "../../store/connectionStore";
import * as api from "../../api/restClient";
import { getAuditDeviceTarget } from "../../api/auditClient";
import { useAuditStore } from "../../store/auditStore";
import type { BridgePort, DeviceConfig, DeviceInfo, DeviceSettingValue, DriverParamDef } from "../../api/types";
import { CommandParamForm } from "../../components/shared/CommandParamForm";
import {
  commandConfirmMessage,
  commandResultText,
  commandParamsBlocked,
  seedCommandParams,
  type CommandParamDefs,
} from "../../components/shared/commandParams";
import { ConfirmDialog } from "../../components/shared/ConfirmDialog";
import {
  MonitorCell, MonitorControl, MonitorLimitsPanel, type DeclaredReading,
} from "../../components/shared/MonitorControl";
import type { MonitorConfig } from "../../api/types";
import { scanChildTrouble, troubleSummary } from "./childPresence";
import type { ChildTypeInfo } from "./childPresence";
import { DEVICE_STALE_TITLE, LastHeard, staleValueStyle } from "./staleReading";
import { normalizeOptionList, parseStateOptionList, settingValueText } from "../../components/shared/paramOptions";
import { DevicePanelSlot, ContextActionRenderer } from "../../components/plugins/PluginExtensions";
import { findDeviceReferences, validateSettingValue } from "./deviceUtils";
import { connectionTestText } from "./deviceAddress";
import { isSecretConfigField } from "./deviceConfigCoerce";
import { BooleanOptions } from "../../components/shared/BooleanOptions";
import { readingText, valueText } from "../../components/shared/booleanWords";
import { ChildEntities } from "./ChildEntities";
import { DeviceStateLog } from "./DeviceStateLog";
import { DeviceProtocolLog } from "./DeviceProtocolLog";
import { QuickActions } from "./QuickActions";
import { InlineProtocolEditor } from "./InlineProtocolEditor";
import { IrCodesEditor } from "./IrCodesEditor";
import { ConfigTableEditor } from "./ConfigTableEditor";
import { BridgeIrPort } from "./BridgeIrPort";
import { ApiError, parseApiError } from "../../api/errors";
import { useConnectionTestResult } from "./useConnectionTestResult";

export function DeviceDetail({
  deviceId,
  onEdit,
  onDeleted,
  onDuplicate,
  onBrowseDrivers,
  onOpenDevice,
}: {
  deviceId: string;
  onEdit: (config: DeviceConfig) => void;
  onDeleted: (deviceId: string) => void;
  onDuplicate: (config: DeviceConfig) => void;
  onBrowseDrivers?: () => void;
  onOpenDevice?: (deviceId: string) => void;
}) {
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const liveState = useConnectionStore((s) => s.liveState);
  const [deviceInfo, setDeviceInfo] = useState<DeviceInfo | null>(null);
  // The running system has no such device (its driver failed while loading;
  // the device log says why). It is still in the project, so it can still be
  // edited, duplicated or deleted.
  const [notRunning, setNotRunning] = useState(false);
  const [commandResult, setCommandResult] = useState<
    { ok: boolean; text: string; detail?: string } | null
  >(null);
  const [selectedCommand, setSelectedCommand] = useState("");
  const [commandParams, setCommandParams] = useState<Record<string, string>>({});
  const [sending, setSending] = useState(false);
  // What the driver asks before the chosen command is sent, while asking.
  const [confirmSend, setConfirmSend] = useState<string | null>(null);
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
  const connected = Boolean(liveState[`device.${deviceId}.connected`]);
  const webUiUrl = liveState[`device.${deviceId}.web_ui_url`];
  const [testResult, setTestResult] = useConnectionTestResult(deviceId, connected);
  const runtimeEnabled = liveState[`device.${deviceId}.enabled`] === true;
  useEffect(() => {
    refetchDeviceInfo();
  }, [refetchDeviceInfo, connected, webUiUrl, runtimeEnabled]);

  const deviceConfig = project?.devices.find((d) => d.id === deviceId);
  const isEnabled = deviceConfig?.enabled !== false;
  const installed = deviceInfo?.ownership === "manufacturer";
  // Product hardware is never edited or deleted here, and only the running
  // system says which devices are; until it answers, nothing is offered.
  const manageable = deviceInfo ? !installed : notRunning && Boolean(deviceConfig);

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
  const orphaned = Boolean(liveState[`device.${deviceId}.orphaned`]);
  // Paused by Live Test or a Device Audit (device.<id>.paused; paused_for
  // names which) — auto-reconnect is suspended until a resume, a manual
  // reconnect, or the server's pause TTL.
  const paused = Boolean(liveState[`device.${deviceId}.paused`]);
  const pausedFor = liveState[`device.${deviceId}.paused_for`];
  // Away because a command restarted it. `offline_reason` is deliberately null
  // for the whole window (core/device_manager.py), so the offline banner below
  // never fires and this one takes its place.
  const restarting = Boolean(liveState[`device.${deviceId}.restarting`]);
  // Server-built, human-readable offline reason (device.<id>.offline_detail).
  // The taxonomy lives server-side; this view only renders the message.
  const offlineDetail = String(liveState[`device.${deviceId}.offline_detail`] ?? "");
  const offlineReason = String(liveState[`device.${deviceId}.offline_reason`] ?? "");
  const reconnectFailed = Boolean(liveState[`device.${deviceId}.reconnect_failed`]);
  // Optional driver-provided troubleshooting hint (DRIVER_INFO.help.connection),
  // surfaced under the offline reason. The driver supplies device-specific
  // guidance the generic classifier can't know (e.g. "enable SSH first").
  const connectionHint = String(
    (deviceInfo?.driver_info as { help?: { connection?: string } } | undefined)
      ?.help?.connection ?? ""
  );

  const commands = deviceInfo?.commands ?? {};
  const commandNames = Object.keys(commands);

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

  // child_id params render a dropdown of the device's live children — the
  // shared ParamInput fetches them per-field (children register dynamically as
  // the driver discovers them).

  const handleSendCommand = useCallback(async () => {
    if (!selectedCommand) return;
    setSending(true);
    setCommandResult(null);
    try {
      const params: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(commandParams)) {
        if (v === "") continue;
        params[k] = v;
      }
      const result = await api.sendCommand(deviceId, selectedCommand, params);
      const def = commands[selectedCommand] as { label?: string } | undefined;
      setCommandResult({ ok: true, ...commandResultText(def?.label || selectedCommand, result) });
    } catch (e) {
      setCommandResult({ ok: false, text: parseApiError(e) });
    } finally {
      setSending(false);
    }
  }, [deviceId, selectedCommand, commandParams, commands]);

  // Get param fields for selected command
  const commandDef = commands[selectedCommand] as Record<string, unknown> | undefined;
  const commandParamDefs =
    (commandDef?.params as Record<string, Partial<DriverParamDef>>) ?? {};
  const paramKeys = Object.keys(commandParamDefs);
  // Block Send on an invalid literal param (out of range / pattern mismatch)
  // or a required one left blank — the per-field inline error says which.
  // Authoring aid; the runtime gates both too.
  const sendBlocked = commandParamsBlocked(commandParamDefs, commandParams);
  const commandLabel = (commandDef?.label as string | undefined) || selectedCommand;
  // A command that erases or resets the device asks first.
  const requestSend = () => {
    const ask = commandConfirmMessage(commandDef, commandLabel);
    if (ask) setConfirmSend(ask);
    else void handleSendCommand();
  };

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

  const sectionStyle: React.CSSProperties = {
    marginBottom: "var(--space-xl)",
  };

  const sectionTitleStyle: React.CSSProperties = {
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    textTransform: "uppercase",
    letterSpacing: "0.5px",
    marginBottom: "var(--space-md)",
    fontWeight: 600,
  };

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
            orphaned={Boolean(liveState[`device.${deviceId}.orphaned`])}
            paused={paused}
            pausedFor={pausedFor}
            restarting={restarting}
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
            {deviceInfo?.driver ?? ""}
          </span>
          <span style={{ color: "var(--border-color)" }}>&middot;</span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            <code style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {deviceId}
            </code>
            <CopyButton value={deviceId} title="Copy device ID" />
          </span>
        </div>
      </div>

      {/* Orphaned device banner */}
      {orphaned && (
        <OrphanBanner
          driverId={deviceConfig?.driver ?? ""}
          onReassign={() => deviceConfig && onEdit(deviceConfig)}
          onBrowseDrivers={onBrowseDrivers}
          onActivated={() => api.getDevice(deviceId).then(setDeviceInfo).catch(console.error)}
        />
      )}

      {/* Restarting banner — a command took the device away on purpose and it
          is expected back. Not the offline banner in a different colour: that
          one names a fault and tells somebody to go and check something, and
          here the honest instruction is to wait. `offline_detail` carries the
          sentence (and counts down), so the wording lives in
          core/connection_fault.py with every other thing that key can say. */}
      {restarting && !orphaned && !paused && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-sm)",
            padding: "var(--space-sm) var(--space-md)",
            borderRadius: "var(--border-radius)",
            marginBottom: "var(--space-md)",
            fontSize: "var(--font-size-sm)",
            background: "rgba(245, 158, 11, 0.1)",
            border: "1px solid var(--color-warning, #f59e0b)",
            color: "var(--color-warning, #f59e0b)",
          }}
        >
          <RefreshCw size={14} style={{ flexShrink: 0 }} />
          <span style={{ flex: 1 }}>
            {offlineDetail || "Restarting."}
          </span>
        </div>
      )}

      {/* Paused banner — Live Test or a Device Audit holds the device.
          Auto-reconnect is suspended; offer Resume so a pause left behind by
          a closed test is recoverable here. */}
      {paused && !orphaned && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-sm)",
            padding: "var(--space-sm) var(--space-md)",
            borderRadius: "var(--border-radius)",
            marginBottom: "var(--space-md)",
            fontSize: "var(--font-size-sm)",
            background: "var(--bg-info, #1a2a3a)",
            border: "1px solid var(--color-info, #6aa3d6)",
            color: "var(--color-info, #6aa3d6)",
          }}
        >
          <Pause size={14} style={{ flexShrink: 0 }} />
          <span style={{ flex: 1 }}>{pausedBannerText(pausedFor)}</span>
          <button
            onClick={async () => {
              try {
                await api.resumeDevice(deviceId);
              } catch (e) {
                console.error(e);
              }
            }}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "2px 10px",
              borderRadius: 12,
              border: "1px solid var(--color-info, #6aa3d6)",
              background: "transparent",
              color: "var(--color-info, #6aa3d6)",
              fontSize: 11,
              cursor: "pointer",
            }}
            title="Clear the pause and reconnect now"
          >
            <Play size={12} /> Resume
          </button>
        </div>
      )}

      {/* Sub-units that are not answering. Shown while the device itself is
          up: when the device is offline its own banner is the story, and
          every port under it reads down for the same one reason. */}
      {connected && childTrouble && (
        <ChildTroubleBanner
          headline={childTrouble.headline}
          names={childTrouble.names}
        />
      )}

      {installed && <InstalledEquipment key={deviceId} deviceId={deviceId} name={deviceName} />}

      {/* Offline reason banner — actionable cause from device.<id>.offline_detail */}
      {!connected && isEnabled && !orphaned && !paused && !restarting && offlineDetail && (
        <OfflineBanner
          installed={installed}
          detail={offlineDetail}
          reason={offlineReason}
          failed={reconnectFailed}
          hint={connectionHint}
          signInFields={hasSignInFields(
            deviceInfo?.driver_info?.config_schema as Record<string, unknown> | undefined,
          )}
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

      {/* Send Command */}
      <div style={sectionStyle}>
        <h3 style={sectionTitleStyle}>Send Command</h3>
        <div
          style={{
            background: "var(--bg-surface)",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            padding: "var(--space-lg)",
          }}
        >
          {commandNames.length === 0 ? (
            <div style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
              {!connected
                ? "Device is not connected. Commands will be available once the device connects."
                : "No commands available. The driver may not be loaded or may not define any commands."}
            </div>
          ) : (
            <>
              <div style={{ marginBottom: "var(--space-md)" }}>
                <div style={{ display: "flex", gap: "var(--space-sm)" }}>
                <CommandPicker
                  commands={commands}
                  value={selectedCommand}
                  onChange={(cmd) => {
                    setSelectedCommand(cmd);
                    // Seed so enum params show a real selection (not a blank
                    // box) and booleans default to No.
                    const pdefs = (commands[cmd] as Record<string, unknown>)
                      ?.params as CommandParamDefs | undefined;
                    setCommandParams(seedCommandParams(pdefs ?? {}, { firstOption: true }));
                    setCommandResult(null);
                  }}
                />
                <button
                  onClick={requestSend}
                  disabled={!selectedCommand || sending || sendBlocked}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "var(--space-xs)",
                    padding: "var(--space-sm) var(--space-lg)",
                    borderRadius: "var(--border-radius)",
                    background: selectedCommand && !sendBlocked ? "var(--accent-bg)" : "var(--bg-hover)",
                    color: selectedCommand && !sendBlocked ? "var(--text-on-accent)" : "var(--text-muted)",
                    opacity: sending ? 0.6 : 1,
                  }}
                >
                  <Send size={14} /> Send
                </button>
                </div>
                {selectedCommand && (() => {
                  const cmdDef = commands[selectedCommand] as Record<string, unknown> | undefined;
                  const cmdHelp = cmdDef?.help as string | undefined;
                  return cmdHelp ? (
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                      {cmdHelp}
                    </div>
                  ) : null;
                })()}
              </div>

              {/* Param fields */}
              {paramKeys.length > 0 && (
                <div style={{ marginBottom: "var(--space-md)" }}>
                  <CommandParamForm
                    layout="inline"
                    params={commandParamDefs}
                    values={commandParams}
                    onChange={(name, val) => setCommandParams((p) => ({ ...p, [name]: val }))}
                    deviceId={deviceId}
                  />
                </div>
              )}

              {/* Result */}
              {commandResult !== null && (
                <div role="status" style={{ fontSize: "var(--font-size-sm)" }}>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--space-xs)",
                      color: commandResult.ok ? "var(--color-success)" : "var(--color-error)",
                    }}
                  >
                    {commandResult.ok ? <Check size={14} /> : <AlertTriangle size={14} />}
                    <span>{commandResult.text}</span>
                  </div>
                  {commandResult.detail && (
                    <pre
                      style={{
                        background: "var(--bg-base)",
                        padding: "var(--space-md)",
                        borderRadius: "var(--border-radius)",
                        fontFamily: "var(--font-mono)",
                        overflow: "auto",
                        maxHeight: 200,
                        whiteSpace: "pre-wrap",
                        margin: "var(--space-xs) 0 0",
                      }}
                    >
                      {commandResult.detail}
                    </pre>
                  )}
                </div>
              )}

              {confirmSend !== null && (
                <ConfirmDialog
                  title={commandLabel}
                  message={confirmSend}
                  confirmLabel="Send"
                  destructive
                  onConfirm={() => {
                    setConfirmSend(null);
                    void handleSendCommand();
                  }}
                  onCancel={() => setConfirmSend(null)}
                />
              )}
            </>
          )}
        </div>
      </div>

      {/* Plugin Device Panels */}
      <DevicePanelSlot
        deviceId={deviceId}
        driverId={deviceConfig?.driver ?? ""}
      />

      {/* Plugin Context Actions */}
      <ContextActionRenderer context="device" deviceId={deviceId} driverId={deviceConfig?.driver} />

      {/* Device Log */}
      <DeviceLog deviceId={deviceId} />
    </div>
  );
}

// --- Command Picker ---

/**
 * The device page's command dropdown, which is where a searchable one was
 * first needed and for a long time the only place there was one. It is now
 * `SearchableSelect` like every other command dropdown in the product, so a
 * command reads the same here as it does in a macro step or a panel binding.
 *
 * Kept as a named wrapper rather than inlined: the call site cares about
 * commands, not about option shapes.
 */
function CommandPicker({
  commands,
  value,
  onChange,
}: {
  commands: Record<string, unknown>;
  value: string;
  onChange: (cmd: string) => void;
}) {
  const options = useMemo(() => commandOptions(commands), [commands]);
  return (
    <SearchableSelect
      value={value}
      onChange={onChange}
      options={options}
      allowEmpty={false}
      placeholder="Select a command..."
      searchPlaceholder="Search commands..."
      emptyHint="This driver declares no commands."
      testId="command-picker"
      style={{ flex: 1 }}
    />
  );
}

// --- Device Settings Section ---

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

function DeviceSettingsSection({ deviceId, connected }: { deviceId: string; connected: boolean }) {
  const liveState = useConnectionStore((s) => s.liveState);
  const devices = useProjectStore((s) => s.project?.devices);
  const pendingSettings = useMemo(() => {
    const dev = devices?.find((d) => d.id === deviceId);
    return dev?.pending_settings ?? {};
  }, [devices, deviceId]);

  const [settings, setSettings] = useState<Record<string, DeviceSettingValue>>({});
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [saving, setSaving] = useState<string | null>(null);
  const [saveResult, setSaveResult] = useState<{ key: string; success: boolean; error?: string } | null>(null);
  const [loaded, setLoaded] = useState(false);
  const mounted = useRef(false);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadSettings = useCallback(() => {
    if (!mounted.current) return;
    api.getDeviceSettings(deviceId).then((data) => {
      if (!mounted.current) return;
      setSettings(data.settings);
      setLoaded(true);
    }).catch(() => { if (mounted.current) setLoaded(true); });
  }, [deviceId]);

  useEffect(() => {
    mounted.current = true;
    loadSettings();
    const interval = setInterval(loadSettings, 5000);
    return () => {
      mounted.current = false;
      clearInterval(interval);
      if (refreshTimer.current !== null) clearTimeout(refreshTimer.current);
    };
  }, [loadSettings]);

  const settingKeys = Object.keys(settings);
  if (!loaded || settingKeys.length === 0) return null;

  const handleStartEdit = (key: string) => {
    const stateKey = `device.${deviceId}.${String(settings[key]?.state_key ?? key)}`;
    const current = stateKey in liveState ? liveState[stateKey] : settings[key]?.current_value;
    setEditingKey(key);
    setEditValue(current != null ? String(current) : String(settings[key]?.default ?? ""));
    setSaveResult(null);
  };

  const handleSave = async (key: string) => {
    setSaving(key);
    setSaveResult(null);
    try {
      const def = settings[key];
      // Reject invalid input instead of coercing it: the old parseInt(v)||0
      // silently wrote 0 to the hardware for a blank or mistyped value and
      // never enforced the definition's min/max/regex.
      const validated = validateSettingValue(def, editValue);
      if (!validated.ok) {
        setSaveResult({ key, success: false, error: validated.error });
        setSaving(null);
        return;
      }

      await api.setDeviceSetting(deviceId, key, validated.value);
      setSaveResult({ key, success: true });
      setEditingKey(null);
      // Refresh settings to get updated current_value
      if (mounted.current) refreshTimer.current = setTimeout(loadSettings, 1000);
    } catch (e) {
      setSaveResult({ key, success: false, error: parseApiError(e) });
    } finally {
      setSaving(null);
    }
  };

  const handleCancel = () => {
    setEditingKey(null);
    setSaveResult(null);
  };

  const sectionTitleStyle: React.CSSProperties = {
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    textTransform: "uppercase",
    letterSpacing: "0.5px",
    marginBottom: "var(--space-md)",
    fontWeight: 600,
    display: "flex",
    alignItems: "center",
    gap: "var(--space-sm)",
  };

  return (
    <div style={{ marginBottom: "var(--space-xl)" }}>
      <h3 style={sectionTitleStyle}>
        <Settings size={14} /> Device Settings
      </h3>
      <div
        style={{
          background: "var(--bg-surface)",
          borderRadius: "var(--border-radius)",
          border: "1px solid var(--border-color)",
          overflow: "hidden",
        }}
      >
        {settingKeys.map((key) => {
          const def = settings[key];
          const label = String(def?.label ?? key);
          const help = String(def?.help ?? "");
          const fieldType = String(def?.type ?? "string");
          const values = def?.values as unknown[] | undefined;
          const enumOptions = normalizeOptionList(values ?? []);
          const stateKey = `device.${deviceId}.${String(def?.state_key ?? key)}`;
          const currentValue = stateKey in liveState ? liveState[stateKey] : def?.current_value;
          const isPending = key in pendingSettings;
          const isEditing = editingKey === key;
          const isSaving = saving === key;
          const result = saveResult?.key === key ? saveResult : null;

          return (
            <div
              key={key}
              style={{
                padding: "var(--space-md)",
                borderBottom: "1px solid var(--border-color)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: "var(--font-size-sm)", fontWeight: 500 }}>{label}</div>
                  {help && (
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>{help}</div>
                  )}
                </div>
                {isEditing ? (
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                    {fieldType === "boolean" ? (
                      <select
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        style={{ fontSize: "var(--font-size-sm)", padding: "2px 6px" }}
                      >
                        <BooleanOptions />
                      </select>
                    ) : fieldType === "enum" && values ? (
                      <select
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        style={{ fontSize: "var(--font-size-sm)", padding: "2px 6px" }}
                      >
                        {enumOptions.map((o) => (
                          <option key={o.value} value={o.value}>{o.label}</option>
                        ))}
                      </select>
                    ) : (
                      <input
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        type={fieldType === "integer" || fieldType === "number" || fieldType === "float" ? "number" : "text"}
                        min={def?.min}
                        max={def?.max}
                        step={fieldType === "integer" ? 1 : undefined}
                        placeholder={
                          def?.min !== undefined && def?.max !== undefined
                            ? `${def.min}-${def.max}`
                            : undefined
                        }
                        style={{
                          fontSize: "var(--font-size-sm)",
                          padding: "2px 6px",
                          width: 180,
                        }}
                        autoFocus
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleSave(key);
                          if (e.key === "Escape") handleCancel();
                        }}
                      />
                    )}
                    <button
                      onClick={() => handleSave(key)}
                      disabled={isSaving}
                      title="Save"
                      style={{
                        padding: "2px 6px",
                        borderRadius: "var(--border-radius)",
                        background: "var(--color-success-bg)",
                        color: "var(--color-success)",
                        fontSize: "var(--font-size-sm)",
                        display: "flex",
                        alignItems: "center",
                      }}
                    >
                      {isSaving ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <Check size={14} />}
                    </button>
                    <button
                      onClick={handleCancel}
                      title="Cancel"
                      style={{
                        padding: "2px 6px",
                        borderRadius: "var(--border-radius)",
                        background: "var(--bg-hover)",
                        fontSize: "var(--font-size-sm)",
                        display: "flex",
                        alignItems: "center",
                      }}
                    >
                      <X size={14} />
                    </button>
                  </div>
                ) : (
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--font-size-sm)",
                        color: currentValue != null ? "var(--text-primary)" : "var(--text-muted)",
                      }}
                    >
                      {currentValue != null
                        ? settingValueText(def, currentValue)
                        : "(not set)"}
                    </span>
                    {isPending && (
                      <span
                        style={{
                          fontSize: 10,
                          color: "var(--accent)",
                          padding: "1px 6px",
                          borderRadius: "var(--border-radius)",
                          background: "var(--accent-dim)",
                        }}
                        title={
                          connected
                            ? `Pending: ${settingValueText(def, pendingSettings[key])}. Sent, but the device has not reported it back yet. It will be sent again on the next connection.`
                            : `Pending: ${settingValueText(def, pendingSettings[key])}, will be applied when device connects`
                        }
                      >
                        pending
                      </span>
                    )}
                    <button
                      onClick={() => handleStartEdit(key)}
                      disabled={!connected}
                      title={connected ? "Edit setting" : "Device must be connected to change settings"}
                      style={{
                        padding: "2px 8px",
                        borderRadius: "var(--border-radius)",
                        background: "var(--bg-hover)",
                        fontSize: "var(--font-size-sm)",
                        opacity: connected ? 1 : 0.4,
                      }}
                    >
                      <Pencil size={12} />
                    </button>
                  </div>
                )}
              </div>
              {result && (
                <div
                  style={{
                    marginTop: "var(--space-xs)",
                    fontSize: 11,
                    color: result.success ? "var(--color-success)" : "var(--color-error)",
                  }}
                >
                  {result.success ? "Setting saved successfully" : `Error: ${result.error}`}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// --- Device Log ---

type DeviceLogTab = "protocol" | "state";

function DeviceLog({ deviceId }: { deviceId: string }) {
  const [tab, setTab] = useState<DeviceLogTab>("protocol");

  return (
    <div style={{ marginBottom: "var(--space-xl)" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          marginBottom: "var(--space-md)",
        }}
      >
        <h3
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            textTransform: "uppercase",
            letterSpacing: "0.5px",
            fontWeight: 600,
            margin: 0,
          }}
        >
          Device Log
        </h3>
        <div style={{ flex: 1 }} />
        <button
          onClick={() => setTab("protocol")}
          style={{
            padding: "2px 8px",
            borderRadius: "var(--border-radius)",
            background: tab === "protocol" ? "var(--accent-bg)" : "var(--bg-hover)",
            color: tab === "protocol" ? "#fff" : "var(--text-secondary)",
            fontSize: 11,
            fontWeight: tab === "protocol" ? 600 : 400,
            border: "none",
            cursor: "pointer",
          }}
        >
          Protocol
        </button>
        <button
          onClick={() => setTab("state")}
          style={{
            padding: "2px 8px",
            borderRadius: "var(--border-radius)",
            background: tab === "state" ? "var(--accent-bg)" : "var(--bg-hover)",
            color: tab === "state" ? "#fff" : "var(--text-secondary)",
            fontSize: 11,
            fontWeight: tab === "state" ? 600 : 400,
            border: "none",
            cursor: "pointer",
          }}
        >
          State Changes
        </button>
      </div>
      {tab === "protocol" ? (
        <DeviceProtocolLog deviceId={deviceId} />
      ) : (
        <DeviceStateLog deviceId={deviceId} />
      )}
    </div>
  );
}

// Offline reasons the server stops retrying on, because only a human can
// clear them. Mirrors _PERMANENT_FAULT_CODES in openavc/core/connection_fault.py
// (auth_failed is in that set too, but gets its own lockout-specific wording
// below). Keep the two lists in step.
const PERMANENT_OFFLINE_REASONS = new Set([
  "host_key_rejected",
  "tls_cert_untrusted",
  "invalid_config",
  "client_missing",
]);

/** The device is fine; some of what it manages is not. Deliberately quieter
 *  than OfflineBanner (one border, no icon column of its own): the room still
 *  works, and this is a "go look at that endpoint" not a "nothing responds". */
function ChildTroubleBanner({ headline, names }: { headline: string; names: string }) {
  const accent = "var(--color-warning, #f59e0b)";
  return (
    <div
      data-testid="child-trouble-banner"
      style={{
        padding: "var(--space-sm) var(--space-md)",
        borderRadius: "var(--border-radius)",
        marginBottom: "var(--space-md)",
        background: "rgba(245, 158, 11, 0.08)",
        border: "1px solid rgba(245, 158, 11, 0.35)",
        display: "flex",
        gap: "var(--space-sm)",
        alignItems: "flex-start",
      }}
    >
      <AlertTriangle size={16} style={{ color: accent, flexShrink: 0, marginTop: 2 }} />
      <div>
        <div style={{ fontSize: "var(--font-size-sm)", fontWeight: 600 }}>
          {headline}
        </div>
        <div
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            marginTop: 2,
          }}
        >
          {names}
        </div>
      </div>
    </div>
  );
}


/** The Offline banner's last line: what happens next, and what to do. */
/** True when the driver has a password, key or other sign-in field, so a
 *  refused sign-in may be fixed under Edit. A device that pairs (a TV
 *  accepting a prompt, a bridge's link button) or has no credentials does not. */
export function hasSignInFields(schema: Record<string, unknown> | undefined): boolean {
  return Object.keys(schema ?? {}).some((key) => isSecretConfigField(schema, key));
}

export function offlineNextStep(
  reason: string | undefined,
  failed: boolean,
  signInFields: boolean,
): string {
  if (reason === "no_simulator") {
    // Refused on purpose, and retried by nothing: it is the simulation that
    // keeps it off, so stopping it is what brings the device back.
    return "Not connecting while simulation runs. Stopping simulation connects it to the real device again.";
  }
  if (!failed) {
    // Deliberately no attempt count. There's no ceiling to count toward, and
    // a device left off over a weekend would show a number in the thousands,
    // which tells the integrator nothing except that something looks broken.
    return "Reconnecting automatically, and will keep trying until the device comes back.";
  }
  if (reason === "auth_failed") {
    // Retrying a rejected login would only trip the device's brute-force
    // lockout, so the server holds off on purpose.
    // The line above is the driver's sentence and names what was refused,
    // which is not always a password: a declined pairing prompt, a bridge to
    // pair with its link button, a setting on the device itself.
    return signInFields
      ? "Not retrying, so repeated sign-ins cannot lock OpenAVC out of the device. Fix the cause above (the sign-in details are under Edit), then press Reconnect."
      : "Not retrying, so repeated sign-ins cannot lock OpenAVC out of the device. Fix the cause above, then press Reconnect.";
  }
  if (PERMANENT_OFFLINE_REASONS.has(reason ?? "")) {
    // Stopped early on purpose, not out of attempts: the detail line above
    // already names what to change. Saying "gave up" here would read as "we
    // tried everything", sending the integrator hunting a network problem
    // that isn't there.
    return "Not retrying: this won't clear on its own. Fix the cause above, then press Reconnect.";
  }
  // Only a fault someone has to clear reaches `failed` now, and both branches
  // above name one. A network fault never lands here: it retries for as long
  // as the device is in the project.
  return "Not retrying. Fix the cause above, then press Reconnect.";
}

function OfflineBanner({
  installed = false,
  detail,
  reason,
  failed,
  hint,
  signInFields = false,
}: {
  installed?: boolean;
  detail: string;
  reason?: string;
  failed: boolean;
  hint?: string;
  signInFields?: boolean;
}) {
  const accent = "var(--color-warning, #f59e0b)";
  return (
    <div
      style={{
        padding: "var(--space-md)",
        borderRadius: "var(--border-radius)",
        marginBottom: "var(--space-md)",
        background: "rgba(245, 158, 11, 0.1)",
        border: "2px solid rgba(245, 158, 11, 0.4)",
        display: "flex",
        gap: "var(--space-sm)",
        alignItems: "flex-start",
      }}
    >
      <WifiOff size={18} style={{ color: accent, flexShrink: 0, marginTop: 2 }} />
      <div>
        <div style={{ fontWeight: 600, color: accent, fontSize: "var(--font-size-md)" }}>
          Offline
        </div>
        <div style={{ fontSize: "var(--font-size-sm)", marginTop: 2 }}>{detail}</div>
        {hint && (
          <div
            style={{
              fontSize: "var(--font-size-sm)",
              marginTop: "var(--space-xs)",
              whiteSpace: "pre-line",
            }}
          >
            {hint}
          </div>
        )}
        <div
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-muted)",
            marginTop: "var(--space-xs)",
          }}
        >
          {installed && failed && reason !== "no_simulator"
            ? "Automatic reconnect is paused. Check this product's OpenAVC status, then press Reconnect."
            : offlineNextStep(reason, failed, signInFields)}
        </div>
      </div>
    </div>
  );
}

function OrphanBanner({
  driverId,
  onReassign,
  onBrowseDrivers,
  onActivated,
}: {
  driverId: string;
  onReassign: () => void;
  onBrowseDrivers?: () => void;
  onActivated: () => void;
}) {
  // Look up the missing driver in the catalog so the button can install it
  // directly (the previous behavior of switching tabs left the user stranded
  // — they had to find it manually). When the driver isn't in the catalog,
  // surface that fact inline rather than silently failing.
  const [match, setMatch] = useState<{ file_url: string; min_platform_version: string | null } | null>(null);
  const [lookupDone, setLookupDone] = useState(false);
  const [builtIn, setBuiltIn] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setMatch(null);
    setBuiltIn(false);
    setLookupDone(false);
    if (!driverId) return;
    let cancelled = false;
    api
      .listMissingDrivers()
      .then((items) => {
        if (cancelled) return;
        const entry = items.find((m) => m.driver_id === driverId);
        setBuiltIn(Boolean(entry?.built_in_equipment));
        if (entry?.community_match) {
          setMatch({
            file_url: entry.community_match.file_url,
            min_platform_version: entry.community_match.min_platform_version,
          });
        }
        setLookupDone(true);
      })
      .catch(() => setLookupDone(true));
    return () => {
      cancelled = true;
    };
  }, [driverId]);

  const handleInstall = async () => {
    if (!match) return;
    setInstalling(true);
    setError(null);
    try {
      await api.installCommunityDriver(driverId, match.file_url, match.min_platform_version || undefined);
      // Server retries orphans automatically; refresh device info to pick
      // up the now-active state.
      onActivated();
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setInstalling(false);
    }
  };

  return (
    <div
      style={{
        padding: "var(--space-md)",
        borderRadius: "var(--border-radius)",
        marginBottom: "var(--space-md)",
        background: "rgba(239, 68, 68, 0.1)",
        border: "2px solid rgba(239, 68, 68, 0.4)",
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: "var(--space-sm)", color: "#ef4444", fontSize: "var(--font-size-md)" }}>
        {builtIn ? "Product hardware unavailable" : "Driver Not Installed"}
      </div>
      <div style={{ fontSize: "var(--font-size-sm)", marginBottom: "var(--space-md)" }}>
        {builtIn ? <BuiltInEquipmentNotice /> : <>This device needs the driver "{driverId}" which is not installed.</>}
        {lookupDone && !match && !builtIn && (
          <div style={{ marginTop: "var(--space-xs)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
            This driver isn't in the community catalog. Reassign the device to a different
            driver, or upload the driver file from the Drivers tab.
          </div>
        )}
        {error && (
          <div style={{ marginTop: "var(--space-xs)", color: "#ef4444", fontSize: "var(--font-size-sm)" }}>
            Install failed: {error}
          </div>
        )}
      </div>
      <div style={{ display: "flex", gap: "var(--space-sm)" }}>
        {match && (
          <button
            onClick={handleInstall}
            disabled={installing}
            style={{
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--color-warning, #f59e0b)",
              color: "#000",
              fontSize: "var(--font-size-sm)",
              fontWeight: 500,
              cursor: installing ? "not-allowed" : "pointer",
              opacity: installing ? 0.7 : 1,
            }}
          >
            {installing ? "Installing..." : "Install from Community"}
          </button>
        )}
        {lookupDone && !match && !builtIn && onBrowseDrivers && (
          <button
            onClick={onBrowseDrivers}
            style={{
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            Browse Drivers
          </button>
        )}
        <button
          onClick={onReassign}
          style={{
            padding: "var(--space-xs) var(--space-md)",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-hover)",
            fontSize: "var(--font-size-sm)",
          }}
        >
          Reassign Driver
        </button>
      </div>
    </div>
  );
}
