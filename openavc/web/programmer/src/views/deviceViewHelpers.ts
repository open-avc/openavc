// Pure helpers for DeviceView, split out so the status-count logic is
// unit-testable without React.

import { presenceIn, type PresenceWord } from "../components/shared/devicePresence";

/** Which status chip a device belongs under: its presence word, but for a
 *  device not connected yet with no fault known, which is counted as offline
 *  as it is drawn. */
export type DeviceListStatus = Exclude<PresenceWord, "connecting">;

/** One device's status for the list's chips, from its presence word. */
export function deviceStatusOf(
  deviceId: string,
  liveState: Record<string, unknown>,
): DeviceListStatus {
  const word = presenceIn(liveState, deviceId);
  return word === "connecting" ? "offline" : word;
}

/** Each chip's label, in the order the list shows them. A chip with no
 *  device under it is not shown. */
const CHIP_LABEL: Record<DeviceListStatus, string> = {
  online: "Online",
  offline: "Offline",
  restarting: "Restarting",
  paused: "Paused",
  asleep: "Asleep",
  disabled: "Disabled",
  waiting_for_driver: "Orphaned",
};

export const STATUS_CHIPS: readonly { key: DeviceListStatus; label: string }[] = (
  Object.keys(CHIP_LABEL) as DeviceListStatus[]
).map((key) => ({ key, label: CHIP_LABEL[key] }));

export type StatusCounts = { total: number } & Record<DeviceListStatus, number>;

/**
 * Count devices by status from the live state, by the same rule as
 * `deviceStatusOf`, so the chip counts and the chip filters agree. `total`
 * is the length of the list passed in, so passing the search-filtered list
 * keeps the status-chip counts consistent with the visible, search-narrowed
 * device list.
 */
export function computeStatusCounts(
  devices: { id: string }[],
  liveState: Record<string, unknown>,
): StatusCounts {
  const counts = Object.fromEntries(STATUS_CHIPS.map((chip) => [chip.key, 0])) as Record<DeviceListStatus, number>;
  for (const dev of devices) counts[deviceStatusOf(dev.id, liveState)]++;
  return { total: devices.length, ...counts };
}
