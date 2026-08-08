// Pure helpers for DeviceView, split out so the status-count logic is
// unit-testable without React.

/**
 * Count devices by connection status from the live state. Precedence matches
 * the device list: an orphaned device (its driver/connection is gone) is
 * counted orphaned regardless of `connected`; otherwise connected -> online,
 * else offline. `total` is the length of the list passed in — so passing the
 * search-filtered list keeps the status-chip counts consistent with the
 * visible, search-narrowed device list.
 */
export function computeStatusCounts(
  devices: { id: string }[],
  liveState: Record<string, unknown>,
): { total: number; online: number; offline: number; orphaned: number } {
  let online = 0;
  let offline = 0;
  let orphaned = 0;
  for (const dev of devices) {
    if (liveState[`device.${dev.id}.orphaned`]) orphaned++;
    else if (liveState[`device.${dev.id}.connected`]) online++;
    else offline++;
  }
  return { total: devices.length, online, offline, orphaned };
}
