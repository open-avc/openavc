// The Dashboard's device figures, split out of DashboardView so the counting
// is unit-testable without React. Each device is counted by its presence word
// (components/shared/devicePresence.ts), the one the device list and the
// device page read.

import { AWAY_ASKED_FOR, presenceIn, type PresenceWord } from "../components/shared/devicePresence";

export interface DashboardDeviceSummary {
  /** Devices that are connected. */
  connected: number;
  /** Every device the project does not turn off. */
  enabled: number;
  /** Devices that are not connected and not away because someone asked, by
   *  name: offline, not connected yet, or waiting for their driver. */
  disconnected: string[];
  /** Devices a command put to sleep, by name. They are away on purpose until
   *  their wake command, so they are listed on their own line rather than
   *  among the disconnected ones. */
  asleep: string[];
}

/** Whether the Dashboard counts a device among the disconnected: neither
 *  connected, nor away because someone asked (a restart, a sleep, a pause),
 *  nor turned off in the project, which leaves it out of every figure. */
export function dashboardDisconnected(word: PresenceWord): boolean {
  return word !== "online" && word !== "disabled" && !AWAY_ASKED_FOR.has(word);
}

export function summarizeDashboardDevices(
  devices: { id: string; name: string }[],
  liveState: Record<string, unknown>,
): DashboardDeviceSummary {
  const out: DashboardDeviceSummary = { connected: 0, enabled: 0, disconnected: [], asleep: [] };
  for (const d of devices) {
    const word = presenceIn(liveState, d.id);
    if (word === "disabled") continue;
    out.enabled++;
    if (word === "online") out.connected++;
    else if (word === "asleep") out.asleep.push(String(d.name));
    else if (dashboardDisconnected(word)) out.disconnected.push(String(d.name));
  }
  return out;
}

/** A device tile's border: green while connected, red while counted among
 *  the disconnected, the plain border otherwise. */
export function tileBorder(word: PresenceWord): string {
  if (word === "online") return "rgba(76,175,80,0.3)";
  return dashboardDisconnected(word) ? "rgba(239,68,68,0.3)" : "var(--border-color)";
}

/** "1 device asleep", "3 devices asleep". */
export function asleepHeadline(count: number): string {
  return `${count} device${count === 1 ? "" : "s"} asleep`;
}
