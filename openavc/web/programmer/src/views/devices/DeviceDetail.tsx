import { InstalledEquipment } from "./InstalledEquipment";
import { useState, useMemo } from "react";
import { useProjectStore } from "../../store/projectStore";
import { useConnectionStore } from "../../store/connectionStore";
import type { DeviceConfig, DriverInfo } from "../../api/types";
import { useMonitorEdits } from "../../components/shared/useMonitorEdits";
import { parentAwayFrom } from "./childPresence";
import { DevicePanelSlot, ContextActionRenderer } from "../../components/plugins/PluginExtensions";
import { ChildEntities } from "./ChildEntities";
import { QuickActions } from "./QuickActions";
import { InlineProtocolEditor } from "./InlineProtocolEditor";
import { IrCodesEditor } from "./IrCodesEditor";
import { ConfigTableEditor } from "./ConfigTableEditor";
import { BridgePortsSection } from "./BridgePortsSection";
import { advertisedBridgePorts } from "./bridgePorts";
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
import { awayBanner, readDeviceAway, waitsForLogin } from "./deviceAway";
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

      <ChildTroubleBanner
        deviceId={deviceId}
        connected={connected}
        childTypes={deviceInfo?.driver_info?.child_entity_types}
        liveState={liveState}
      />

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
          away={away}
          driverInfo={deviceInfo?.driver_info}
          installed={installed}
          onReport={setReportContext}
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

      <BridgePortsSection
        deviceId={deviceId}
        ports={advertisedBridgePorts(deviceInfo?.driver_info)}
        devices={project?.devices ?? []}
        connections={project?.connections}
        connected={connected}
        onOpenDevice={onOpenDevice}
      />

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
