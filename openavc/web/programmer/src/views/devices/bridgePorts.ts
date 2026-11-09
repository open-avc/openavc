/**
 * Bridges and the devices that reach their equipment through them.
 *
 * A bridge's driver advertises its ports (`DRIVER_INFO.bridge.ports`), and a
 * device bound to one names the bridge and the port in the project's
 * connections table (`bridge`, `bridge_port`). The bridge's own page lists
 * what is bound under each port, and the device list's bridge topology draws
 * the same tree, so both ask here.
 */
import type { BridgePort, DeviceConfig } from "../../api/types";

type Connections = Record<string, Record<string, unknown>> | undefined;

/** The project devices bound to one port of a bridge, in project order. */
export function devicesOnBridgePort(
  devices: DeviceConfig[],
  connections: Connections,
  bridgeId: string,
  portId: string,
): DeviceConfig[] {
  return devices.filter((d) => {
    const c = connections?.[d.id];
    return c?.bridge === bridgeId && c?.bridge_port === portId;
  });
}

/** The ports a running device's driver advertises; none for a device that
 *  is not a bridge. */
export function advertisedBridgePorts(driverInfo: Record<string, unknown> | undefined): BridgePort[] {
  return (driverInfo as { bridge?: { ports?: BridgePort[] } } | undefined)?.bridge?.ports ?? [];
}
