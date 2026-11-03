/**
 * Folding the simulator's stored protocol log into what the page shows.
 *
 * The live feed only carries entries logged while this page's socket is open.
 * A device's busiest moment is its first second -- the driver connects, signs
 * in, subscribes and reads every value back -- and that happens before the
 * Simulator window has loaded, so a page that only listened showed "No protocol
 * traffic yet" over a device that had already said a hundred things. Each time
 * the socket opens, the page asks every device for what it has stored and
 * merges it in with this.
 */

import type { LogEntry } from "./api";

function entryKey(e: LogEntry): string {
  return `${e.device_id}\u0000${e.timestamp}\u0000${e.direction}\u0000${e.client_id}\u0000${e.data}`;
}

/**
 * The page's log with the stored entries added, oldest first, newest `max` kept.
 *
 * An entry already shown (it arrived live while the stored copy was being
 * fetched) is not added twice. Nothing at or before `clearedAt` is added, so a
 * reconnect after Clear does not bring back what was cleared. Returns `current`
 * itself when there is nothing to add.
 */
export function mergeStoredLog(
  current: LogEntry[],
  stored: LogEntry[],
  clearedAt: number,
  max: number,
): LogEntry[] {
  const seen = new Set(current.map(entryKey));
  const added: LogEntry[] = [];
  for (const e of stored) {
    if (e.timestamp <= clearedAt) continue;
    const key = entryKey(e);
    if (seen.has(key)) continue;
    seen.add(key);
    added.push(e);
  }
  if (added.length === 0) return current;
  // Stable: entries with the same timestamp keep the order they were logged in.
  const merged = [...current, ...added].sort((a, b) => a.timestamp - b.timestamp);
  return merged.length > max ? merged.slice(-max) : merged;
}
