import { InstalledEquipment } from "./InstalledEquipment";
import { Fragment, useState, useEffect, useCallback, useRef, useMemo } from "react";
import { Send, Pencil, Trash2, Wifi, Power, RefreshCw, Copy, Settings, Check, X, Loader2, Search, AlertTriangle, ClipboardCheck } from "lucide-react";
import { CopyButton } from "../../components/shared/CopyButton";
import { DeviceStatusDot } from "../../components/shared/DeviceStatusDot";
import { useProjectStore } from "../../store/projectStore";
import { SearchableSelect } from "../../components/shared/SearchableSelect";
import { commandOptions } from "../../components/shared/pickerOptions";
import { useConnectionStore } from "../../store/connectionStore";
import * as api from "../../api/restClient";
import { getAuditDeviceTarget } from "../../api/auditClient";
import { useAuditStore } from "../../store/auditStore";
import type { BridgePort, DeviceConfig, DeviceInfo, DeviceSettingValue, DriverInfo, DriverParamDef } from "../../api/types";
import { CommandParamForm } from "../../components/shared/CommandParamForm";
import { fileParamNames, fileParamsBlocked, isFileCommand } from "../../components/shared/commandFiles";
import { CommandFileRunStatus, useCommandFileRun } from "./CommandFileRun";
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
import { parentAwayFrom, scanChildTrouble, troubleSummary } from "./childPresence";
import type { ChildTypeInfo } from "./childPresence";
import { DEVICE_STALE_TITLE, LastHeard, staleValueStyle } from "./staleReading";
import { normalizeOptionList, parseStateOptionList, settingValueText } from "../../components/shared/paramOptions";
import { DevicePanelSlot, ContextActionRenderer } from "../../components/plugins/PluginExtensions";
import { findDeviceReferences, validateSettingValue } from "./deviceUtils";
import { connectionTestText } from "./deviceAddress";
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
import { SshKeySection, canUseSshKey } from "./SshKeySection";
import { ApiError, parseApiError } from "../../api/errors";
import { useConnectionTestResult } from "./useConnectionTestResult";
import { driverLabel } from "../../components/shared/driverLabel";
import { DriverUpdateLine } from "./DriverUpdateLine";
import { NeedsLoginBanner } from "./NeedsLoginBanner";
import { ProblemReportDialog, ReportLink } from "./ProblemReportDialog";
import type { ProblemReportContext } from "../../api/problemReportClient";
import { reportableCommandFailure } from "./problemReport";
import { awayBanner, hasSignInFields, readDeviceAway, reportableOffline, waitsForLogin } from "./deviceAway";
import { AsleepBanner, OfflineBanner, PausedBanner, RestartingBanner } from "./AwayBanners";
import { OrphanBanner } from "./OrphanBanner";
import { ChildTroubleBanner } from "./ChildTroubleBanner";

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
  const [commandResult, setCommandResult] = useState<
    {
      ok: boolean;
      text: string;
      detail?: string;
      // A failure's HTTP status and code, and what was sent, for Report a problem.
      status?: number;
      code?: string;
      command?: string;
      params?: Record<string, unknown>;
    } | null
  >(null);
  // Report a problem is open, from where and with what the page showed.
  const [reportContext, setReportContext] = useState<ProblemReportContext | null>(null);
  const [selectedCommand, setSelectedCommand] = useState("");
  const [commandParams, setCommandParams] = useState<Record<string, string>>({});
  // The files chosen for a command that takes one, by param.
  const [commandFiles, setCommandFiles] = useState<Record<string, File | null>>({});
  const fileRun = useCommandFileRun(deviceId);
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
    const def = commands[selectedCommand] as { label?: string } | undefined;
    const label = def?.label || selectedCommand;
    const fileParams = new Set(fileParamNames(def));
    const params: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(commandParams)) {
      if (v === "" || fileParams.has(k)) continue;
      params[k] = v;
    }
    try {
      if (isFileCommand(def)) {
        // Uploaded first, then run in the background; CommandFileRunStatus
        // follows it.
        const files: Record<string, File> = {};
        for (const [k, f] of Object.entries(commandFiles)) {
          if (f && fileParams.has(k)) files[k] = f;
        }
        await fileRun.start(selectedCommand, label, params, files);
        return;
      }
      const result = await api.sendCommand(deviceId, selectedCommand, params);
      setCommandResult({ ok: true, ...commandResultText(label, result) });
    } catch (e) {
      setCommandResult({
        ok: false,
        text: parseApiError(e),
        status: e instanceof ApiError ? e.status : undefined,
        code: e instanceof ApiError ? apiErrorCode(e) : undefined,
        command: selectedCommand,
        params,
      });
    } finally {
      setSending(false);
    }
  }, [deviceId, selectedCommand, commandParams, commandFiles, commands, fileRun]);

  // Get param fields for selected command
  const commandDef = commands[selectedCommand] as Record<string, unknown> | undefined;
  const commandParamDefs =
    (commandDef?.params as Record<string, Partial<DriverParamDef>>) ?? {};
  const paramKeys = Object.keys(commandParamDefs);
  // Block Send on an invalid literal param (out of range / pattern mismatch)
  // or a required one left blank — the per-field inline error says which.
  // Authoring aid; the runtime gates both too.
  const sendBlocked =
    commandParamsBlocked(commandParamDefs, commandParams) ||
    fileParamsBlocked(commandParamDefs, commandFiles) ||
    fileRun.busy;
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
                    // Seed so enum params show the driver's default (or the
                    // first choice) instead of a blank box, and flags their
                    // default or No.
                    const pdefs = (commands[cmd] as Record<string, unknown>)
                      ?.params as CommandParamDefs | undefined;
                    setCommandParams(seedCommandParams(pdefs ?? {}, { fillChoices: true }));
                    setCommandFiles({});
                    setCommandResult(null);
                    if (!fileRun.busy) fileRun.clear();
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
                    files={commandFiles}
                    onFileChange={(name, file) => setCommandFiles((f) => ({ ...f, [name]: file }))}
                  />
                </div>
              )}

              {/* A command that takes or hands back a file, while it runs and after */}
              <CommandFileRunStatus
                view={fileRun.view}
                deviceId={deviceId}
                deviceName={deviceName}
                onStop={() => void fileRun.stop()}
                onReport={setReportContext}
              />

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
                    {!commandResult.ok &&
                      reportableCommandFailure(commandResult.status, commandResult.code) && (
                        <ReportLink
                          onClick={() =>
                            setReportContext({
                              source: "command_failure",
                              error: commandResult.text,
                              command: commandResult.command,
                              params: commandResult.params,
                              status: commandResult.status,
                              code: commandResult.code ?? "",
                            })
                          }
                        />
                      )}
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

/** The `code` a structured refusal carries beside `detail`, or "". */
function apiErrorCode(e: ApiError): string {
  try {
    const code = JSON.parse(e.body)?.code;
    return typeof code === "string" ? code : "";
  } catch {
    return "";
  }
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
  // The one picker that offers a command taking or handing back a file.
  const options = useMemo(() => commandOptions(commands, { fileCommands: true }), [commands]);
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
