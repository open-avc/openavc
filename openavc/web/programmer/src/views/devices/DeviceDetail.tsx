import { InstalledEquipment } from "./InstalledEquipment";
import { useState, useMemo } from "react";
import { useProjectStore } from "../../store/projectStore";
import { useConnectionStore } from "../../store/connectionStore";
import type { BridgePort, DeviceConfig, DriverInfo } from "../../api/types";
import { useMonitorEdits } from "../../components/shared/useMonitorEdits";
import { parentAwayFrom, scanChildTrouble, troubleSummary } from "./childPresence";
import type { ChildTypeInfo } from "./childPresence";
import { DevicePanelSlot, ContextActionRenderer } from "../../components/plugins/PluginExtensions";
import { ChildEntities } from "./ChildEntities";
import { QuickActions } from "./QuickActions";
import { InlineProtocolEditor } from "./InlineProtocolEditor";
import { IrCodesEditor } from "./IrCodesEditor";
import { ConfigTableEditor } from "./ConfigTableEditor";
import { BridgeIrPort } from "./BridgeIrPort";
import { SshKeySection, canUseSshKey } from "./SshKeySection";
import { useDeviceInfo } from "./useDeviceInfo";
import { useDeviceChecks } from "./useDeviceChecks";
import { DeviceHeader } from "./DeviceHeader";
import { AuditRefusalLine, ConnectionTestLine } from "./DeviceCheckResults";
import { DeviceFilter } from "./DeviceFilter";
import { LiveStateSection } from "./LiveStateSection";
import { childTypeNames, deviceStateRows, type StateVariables } from "./liveStateRows";
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
  const liveState = useConnectionStore((s) => s.liveState);
  // Report a problem is open, from where and with what the page showed.
  const [reportContext, setReportContext] = useState<ProblemReportContext | null>(null);
  const [searchTerm, setSearchTerm] = useState("");

  const monitors = project?.monitors ?? [];
  const handleMonitors = useMonitorEdits();

  const away = readDeviceAway(liveState, deviceId);
  const connected = away.connected;
  const checks = useDeviceChecks(deviceId, connected);
  const runtimeEnabled = liveState[`device.${deviceId}.enabled`] === true;
  const { deviceInfo, notRunning, refetch: refetchDeviceInfo, takeActivated } = useDeviceInfo(deviceId, {
    connected,
    webUiUrl: liveState[`device.${deviceId}.web_ui_url`],
    runtimeEnabled,
    awaitingLogin: waitsForLogin(away),
  });

  const deviceConfig = project?.devices.find((d) => d.id === deviceId);
  const isEnabled = deviceConfig?.enabled !== false;
  const installed = deviceInfo?.ownership === "manufacturer";
  // Product hardware is never edited or deleted here, and only the running
  // system says which devices are; until it answers, nothing is offered.
  const manageable = deviceInfo ? !installed : notRunning && Boolean(deviceConfig);
  const banner = awayBanner(away, isEnabled, deviceInfo?.needs_login?.length ?? 0);

  const childTypes = useMemo(() => childTypeNames(deviceInfo?.driver_info), [deviceInfo]);

  // Which sub-units are not answering, for the banner below. A wedged
  // endpoint used to be one word in a column inside a collapsed panel, which
  // is how an afternoon went into hunting a power fault that did not exist.
  // It belongs on the page the way an offline device's reason is on its card.
  const childTrouble = useMemo(() => {
    const cet = (deviceInfo?.driver_info?.child_entity_types ?? {}) as
      Record<string, ChildTypeInfo>;
    return troubleSummary(scanChildTrouble(liveState, deviceId, cet));
  }, [liveState, deviceId, deviceInfo]);

  // What the driver declares about each reading: label, unit, type, range,
  // allowed values and precision. Monitor fills in from this, which is why
  // tagging lamp hours is one click rather than a form: the projector's
  // driver already said it is a number called "Lamp Hours" measured in hours.
  const stateVariables = (deviceInfo?.driver_info?.state_variables ?? {}) as StateVariables;
  const { rows: stateRows, childKeyCount } = deviceStateRows(liveState, deviceId, {
    childTypes,
    declared: stateVariables,
    filter: searchTerm.trim().toLowerCase(),
  });

  const deviceName = String(liveState[`device.${deviceId}.name`] ?? deviceConfig?.name ?? deviceId);
  // Optional driver-provided troubleshooting hint (DRIVER_INFO.help.connection),
  // surfaced under the offline reason. The driver supplies device-specific
  // guidance the generic classifier can't know (e.g. "enable SSH first").
  const connectionHint = String(
    (deviceInfo?.driver_info as { help?: { connection?: string } } | undefined)
      ?.help?.connection ?? ""
  );


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
      <DeviceHeader
        deviceId={deviceId}
        deviceName={deviceName}
        away={away}
        info={deviceInfo}
        config={deviceConfig}
        isEnabled={isEnabled}
        installed={installed}
        manageable={manageable}
        checks={checks}
        onEdit={onEdit}
        onDuplicate={onDuplicate}
        onDeleted={onDeleted}
        onReport={() => setReportContext({ source: "driver_line" })}
        onDriverUpdated={refetchDeviceInfo}
      />

      {/* Why the device is away: one banner at a time, chosen in deviceAway.ts.
          The first four sit above product hardware's own panel, the login box
          and the Offline banner below it. */}
      {banner === "orphaned" && (
        <OrphanBanner
          driverId={deviceConfig?.driver ?? ""}
          onReassign={() => deviceConfig && onEdit(deviceConfig)}
          onBrowseDrivers={onBrowseDrivers}
          onActivated={takeActivated}
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

      <AuditRefusalLine refusal={checks.auditRefusal} onDismiss={checks.dismissAuditRefusal} />
      <ConnectionTestLine result={checks.testResult} devices={project?.devices} />

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

      <DeviceFilter value={searchTerm} onChange={setSearchTerm} />

      {/* Child Entities (only renders when the driver declares any).
          childKeyCount is the count the Live State panel below reports as
          living up here, and it is also how this panel hears that the roster
          changed — the two used to contradict each other on one screen. */}
      <ChildEntities
        deviceId={deviceId}
        search={searchTerm}
        connected={connected}
        parentAway={parentAwayFrom(liveState, deviceId)}
        childKeyCount={childKeyCount}
        config={deviceConfig?.config}
        driverInfo={deviceInfo?.driver_info}
        monitors={monitors}
        onMonitorsChange={handleMonitors}
      />

      <LiveStateSection
        deviceId={deviceId}
        rows={stateRows}
        childKeyCount={childKeyCount}
        searchTerm={searchTerm}
        connected={connected}
        stateVariables={stateVariables}
        commands={deviceInfo?.commands}
        driverInfo={deviceInfo?.driver_info}
        monitors={monitors}
        onMonitorsChange={handleMonitors}
      />

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
