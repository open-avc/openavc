// Device logins in the Programmer. The browser never receives a saved login:
// the project holds the "saved on this system" marker in its place, and a
// form shows that marker as "Saved on this system" with Change, never as a
// value.
//
// A login goes through the device's own settings save (PATCH
// /api/devices/{id}), which moves it into this system's login file and leaves
// the marker in the project, then adds the device again with it. The Edit
// Device dialog sends it with the rest of its form; saveDeviceLogins below
// sends only logins, built from the server's copy of the device, markers and
// all (the server never mistakes a marker for a new login), so nothing else
// the device has is changed by a stale or unsaved copy in this tab. Either
// way the store is re-read from the server afterwards, so it holds the marker
// and never the value typed.

import * as api from "../../api/restClient";
import type { DeviceNeedingLogin, PluginNeedingLogin } from "../../api/types";
import { isSecretSetting } from "../../components/shared/secretSetting";
import { syncDeviceFromServer } from "../../store/projectStore";

/** What the project holds in a setting whose login is saved on this system,
 *  in place of the login. The server never sends the login itself, and never
 *  mistakes this for a new one: sent back, it keeps the saved login. Mirrors
 *  SAVED_LOGIN in openavc/core/device_logins.py
 *  (tests/test_saved_login_marker_parity.py). */
export const SAVED_LOGIN = "<saved on this system>";

/** The Edit Device form ready to save: a setting whose saved login was left
 *  blank after Change keeps the saved login (the marker goes back), so an
 *  empty box never removes one. `stored` is the form the dialog opened with.
 *  Only for the driver the logins were saved for: another driver's form has
 *  none. */
export function keepSavedLogins(
  values: Record<string, string>,
  stored: Record<string, string>,
): Record<string, string> {
  const kept = { ...values };
  for (const [key, value] of Object.entries(stored)) {
    if (value === SAVED_LOGIN && (kept[key] ?? "") === "") kept[key] = SAVED_LOGIN;
  }
  return kept;
}

/** A form with every saved login left out, for a form whose device has none
 *  of them: a duplicate, whose logins are saved under the device it was
 *  copied from, or another driver. */
export function withoutSavedLogins<T>(values: Record<string, T>): Record<string, T> {
  return Object.fromEntries(Object.entries(values).filter(([, v]) => v !== SAVED_LOGIN));
}

/** Whether `config` holds a login somebody typed: a secret setting with a
 *  value that is not the marker. A store that saved one re-syncs from the
 *  server afterwards (syncDeviceFromServer), so it keeps the marker instead. */
export function holdsTypedLogin(
  config: Record<string, unknown>,
  schema: Record<string, unknown> | undefined,
): boolean {
  return Object.entries(config).some(
    ([key, value]) =>
      isSecretSetting(key, schema?.[key]) &&
      typeof value === "string" && value !== "" && value !== SAVED_LOGIN,
  );
}

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

function counted(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** What a restore, an open or an import says about the devices and plugins
 *  that wait for their login, or "" when none do. */
export function needsLoginSentence(
  devices: DeviceNeedingLogin[] | undefined,
  plugins: PluginNeedingLogin[] | undefined = [],
): string {
  const waiting = [...(devices ?? []), ...(plugins ?? [])];
  if (waiting.length === 0) return "";
  const names = waiting.slice(0, NAMES_SHOWN).map((d) => d.name || d.id);
  const more = waiting.length - names.length;
  const list = more > 0 ? `${names.join(", ")} and ${more} more` : names.join(", ");
  const deviceCount = devices?.length ?? 0;
  const pluginCount = plugins?.length ?? 0;
  if (deviceCount && pluginCount) {
    const who = `${counted(deviceCount, "device", "devices")} and ${counted(pluginCount, "plugin", "plugins")}`;
    return `${who} need their login entered: ${list}. Open each one in Devices or Plugins to enter it.`;
  }
  const where = deviceCount ? "Devices" : "Plugins";
  const kind = deviceCount ? ["device", "devices"] : ["plugin", "plugins"];
  if (waiting.length === 1) {
    return `1 ${kind[0]} needs its login entered: ${list}. Open it in ${where} to enter it.`;
  }
  return `${waiting.length} ${kind[1]} need their login entered: ${list}. Open each one in ${where} to enter it.`;
}

/** Whether `project` marks any setting as a login saved on this system, in a
 *  device's settings or its connection: an export of it could include one. */
export function projectHoldsSavedLogin(
  project: {
    devices?: { config?: Record<string, unknown> }[];
    connections?: Record<string, Record<string, unknown>>;
  } | null | undefined,
): boolean {
  if (!project) return false;
  const holders = [
    ...(project.devices ?? []).map((d) => d.config ?? {}),
    ...Object.values(project.connections ?? {}),
  ];
  return holders.some((h) => Object.values(h ?? {}).some((v) => v === SAVED_LOGIN));
}
