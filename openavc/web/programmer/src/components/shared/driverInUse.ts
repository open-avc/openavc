/**
 * Which devices hold a driver, for the three places a driver is taken away:
 * the Driver Builder's delete, the Installed page's uninstall and the Code
 * view's delete. Each asks `devicesOnDriver` before it sends the delete, and
 * reads `inUseDevices` from the server's refusal when the project changed in
 * between. The server's rule is `openavc/drivers/driver_in_use.py`, which
 * names the same devices the same way.
 */
import { ApiError, apiErrorCode } from "../../api/errors";
import { useNavigationStore } from "../../store/navigationStore";

/** A project device on a driver, named as the Installed page lists it. */
export interface DeviceOnDriver {
  id: string;
  name: string;
}

interface ProjectDevice {
  id: string;
  name?: string;
  driver: string;
}

/** Each project device on `driverId`, in the project's order, named by its
 *  name, or its id when it has none. */
export function devicesOnDriver(
  devices: readonly ProjectDevice[] | null | undefined,
  driverId: string,
): DeviceOnDriver[] {
  return (devices ?? [])
    .filter((device) => device.driver === driverId)
    .map((device) => ({ id: device.id, name: device.name || device.id }));
}

function isDeviceOnDriver(item: unknown): item is DeviceOnDriver {
  const device = item as DeviceOnDriver | null;
  return typeof device?.id === "string" && typeof device?.name === "string";
}

/** The devices a delete refused as in use names (code `driver_in_use`), or
 *  null when the failure is another. */
export function inUseDevices(e: unknown): DeviceOnDriver[] | null {
  if (apiErrorCode(e) !== "driver_in_use" || !(e instanceof ApiError)) return null;
  try {
    const devices: unknown = JSON.parse(e.body)?.devices;
    return Array.isArray(devices) && devices.every(isDeviceOnDriver) ? devices : null;
  } catch {
    return null;
  }
}

/** Open a device's page, where another driver is chosen. */
export function openDevice(deviceId: string): void {
  useNavigationStore.getState().navigateTo("devices", { type: "device", id: deviceId });
}
