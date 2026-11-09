import { useState, useMemo } from "react";
import { useProjectStore } from "../../store/projectStore";
import { useConnectionStore } from "../../store/connectionStore";
import type { DeviceConfig, DriverInfo } from "../../api/types";
import { useMonitorEdits } from "../../components/shared/useMonitorEdits";
import { parentAwayFrom } from "./childPresence";
import { DevicePanelSlot, ContextActionRenderer } from "../../components/plugins/PluginExtensions";
import { ChildEntities } from "./ChildEntities";
import { QuickActions } from "./QuickActions";
import { DeviceConfigEditors } from "./DeviceConfigEditors";
import { BridgePortsSection } from "./BridgePortsSection";
import { advertisedBridgePorts } from "./bridgePorts";
import { DeviceSshKey } from "./SshKeySection";
import { useDeviceInfo } from "./useDeviceInfo";
import { useDeviceChecks } from "./useDeviceChecks";
import { DeviceHeader } from "./DeviceHeader";
import { AuditRefusalLine, ConnectionTestLine } from "./DeviceCheckResults";
import { DeviceFilter } from "./DeviceFilter";
import { LiveStateSection } from "./LiveStateSection";
import { childTypeNames, deviceStateRows, type StateVariables } from "./liveStateRows";
import { ProblemReportDialog } from "./ProblemReportDialog";
import type { ProblemReportContext } from "../../api/problemReportClient";
import { readDeviceAway, waitsForLogin } from "./deviceAway";
import { DeviceBanners } from "./DeviceBanners";
import { DeviceSettingsSection } from "./DeviceSettingsSection";
import { DeviceLog } from "./DeviceLog";
import { SendCommandSection } from "./SendCommandSection";

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

      <DeviceBanners
        deviceId={deviceId}
        deviceName={deviceName}
        away={away}
        isEnabled={isEnabled}
        info={deviceInfo}
        config={deviceConfig}
        installed={installed}
        liveState={liveState}
        onEdit={onEdit}
        onBrowseDrivers={onBrowseDrivers}
        onActivated={takeActivated}
        onLoginSaved={refetchDeviceInfo}
        onReport={setReportContext}
      />

      <AuditRefusalLine refusal={checks.auditRefusal} onDismiss={checks.dismissAuditRefusal} />
      <ConnectionTestLine result={checks.testResult} devices={project?.devices} />

      <DeviceSshKey
        info={deviceInfo}
        installedDriver={installedDriver}
        config={deviceConfig?.config}
        connection={project?.connections?.[deviceId]}
      />

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

      {/* What the driver asks to have edited here: the no-code protocol, the
          IR code set, each table setting. */}
      <DeviceConfigEditors
        deviceId={deviceId}
        driverInfo={deviceInfo?.driver_info}
        connected={connected}
        onSaved={refetchDeviceInfo}
      />

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
