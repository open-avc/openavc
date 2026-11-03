// The Dashboard's device figures, split out of DashboardView so the counting
// is unit-testable without React.

export interface DashboardDeviceSummary {
  /** Enabled devices that are connected. */
  connected: number;
  /** Every enabled device. */
  enabled: number;
  /** Enabled devices that are not connected and not asleep, by name. */
  disconnected: string[];
  /** Enabled devices a command put to sleep (`device.<id>.asleep`), by name.
   *  They are away on purpose until their wake command, so they are listed on
   *  their own line rather than among the disconnected ones. */
  asleep: string[];
}

export function summarizeDashboardDevices(
  devices: { id: string; name: string; enabled?: boolean }[],
  liveState: Record<string, unknown>,
): DashboardDeviceSummary {
  const out: DashboardDeviceSummary = { connected: 0, enabled: 0, disconnected: [], asleep: [] };
  for (const d of devices) {
    if (d.enabled === false) continue;
    out.enabled++;
    if (liveState[`device.${d.id}.connected`] === true) out.connected++;
    else if (liveState[`device.${d.id}.asleep`] === true) out.asleep.push(String(d.name));
    else out.disconnected.push(String(d.name));
  }
  return out;
}

/** "1 device asleep", "3 devices asleep". */
export function asleepHeadline(count: number): string {
  return `${count} device${count === 1 ? "" : "s"} asleep`;
}
