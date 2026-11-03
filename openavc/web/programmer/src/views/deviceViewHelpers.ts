// Pure helpers for DeviceView, split out so the status-count logic is
// unit-testable without React.

/** Which status chip a device belongs under. */
export type DeviceListStatus = "orphaned" | "online" | "asleep" | "offline";

/**
 * One device's status for the list's chips, from the live state. Precedence
 * matches the device list: an orphaned device (its driver/connection is gone)
 * is orphaned regardless of `connected`; otherwise connected -> online; a
 * device a command put to sleep (`device.<id>.asleep`) is asleep, not
 * offline; anything else is offline.
 */
export function deviceStatusOf(
  deviceId: string,
  liveState: Record<string, unknown>,
): DeviceListStatus {
  if (liveState[`device.${deviceId}.orphaned`]) return "orphaned";
  if (liveState[`device.${deviceId}.connected`]) return "online";
  if (liveState[`device.${deviceId}.asleep`]) return "asleep";
  return "offline";
}

/**
 * Count devices by connection status from the live state, by the same rule
 * as `deviceStatusOf`, so the chip counts and the chip filters agree. `total`
 * is the length of the list passed in, so passing the search-filtered list
 * keeps the status-chip counts consistent with the visible, search-narrowed
 * device list.
 */
export function computeStatusCounts(
  devices: { id: string }[],
  liveState: Record<string, unknown>,
): { total: number; online: number; offline: number; asleep: number; orphaned: number } {
  const counts = { online: 0, offline: 0, asleep: 0, orphaned: 0 };
  for (const dev of devices) counts[deviceStatusOf(dev.id, liveState)]++;
  return { total: devices.length, ...counts };
}
