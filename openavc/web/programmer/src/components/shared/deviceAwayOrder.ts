/**
 * The ways a device can be away that are said whether or not it still reads
 * connected, in the order they win: its driver is not installed; Live Test or
 * a Device Audit holds it; a command is restarting it; a command put it to
 * sleep. The device page's banner (views/devices/deviceAway.ts) and the status
 * dot (DeviceStatusDot.tsx) both read this order, so the two never name
 * different states for one device.
 */
export const AWAY_FIRST = ["orphaned", "paused", "restarting", "asleep"] as const;

export type AwayFirst = (typeof AWAY_FIRST)[number];

/** The first of those the device is in, or undefined for none. */
export function firstAway(flags: Partial<Record<AwayFirst, unknown>>): AwayFirst | undefined {
  return AWAY_FIRST.find((state) => Boolean(flags[state]));
}
