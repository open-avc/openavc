// Entering a device's login: the one save every form that takes a login uses.
//
// A login goes through the device's own settings save (PATCH
// /api/devices/{id}), which moves it into this system's login file and leaves
// the "saved on this system" marker in the project, then adds the device again
// with it. The body is built from the server's copy of the device, markers and
// all (the server never mistakes a marker for a new login), so nothing else
// the device has is changed by a stale or unsaved copy in this tab. Afterwards
// the store is re-synced from the server, so it holds the marker and never the
// value typed.

import * as api from "../../api/restClient";
import type { DeviceNeedingLogin } from "../../api/types";
import { syncDeviceFromServer } from "../../store/projectStore";

/** Save `logins` (setting name to value) for one device. */
export async function saveDeviceLogins(
  deviceId: string,
  logins: Record<string, string>,
): Promise<void> {
  const server = await api.getProject();
  const device = server.devices.find((d) => d.id === deviceId);
  if (!device) throw new Error(`The project no longer has a device called '${deviceId}'.`);
  await api.updateDevice(deviceId, { config: { ...(device.config ?? {}), ...logins } });
  await syncDeviceFromServer(deviceId);
}

/** The driver's published default for each of `fields` that has one: the
 *  value its default_config gives, which is what the platform falls back to
 *  for a setting left blank. Only a value somebody chooses to use: a form
 *  never fills one in. */
export function driverLoginDefaults(
  driverInfo: Record<string, unknown> | undefined,
  fields: string[],
): Record<string, string> {
  const defaults = driverInfo?.default_config;
  if (!defaults || typeof defaults !== "object" || Array.isArray(defaults)) return {};
  const found: Record<string, string> = {};
  for (const field of fields) {
    const value = (defaults as Record<string, unknown>)[field];
    if (typeof value === "string" && value !== "") found[field] = value;
  }
  return found;
}

const NAMES_SHOWN = 5;

/** What a restore, an open or an import says about the devices that wait for
 *  their login, or "" when none do. */
export function needsLoginSentence(devices: DeviceNeedingLogin[] | undefined): string {
  if (!devices || devices.length === 0) return "";
  const names = devices.slice(0, NAMES_SHOWN).map((d) => d.name || d.id);
  const more = devices.length - names.length;
  const list = more > 0 ? `${names.join(", ")} and ${more} more` : names.join(", ");
  if (devices.length === 1) {
    return `1 device needs its login entered: ${list}. Open it in Devices to enter it.`;
  }
  return `${devices.length} devices need their login entered: ${list}. Open each one in Devices to enter it.`;
}
