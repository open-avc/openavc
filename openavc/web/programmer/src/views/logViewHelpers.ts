// Pure helpers for LogView filtering, kept free of React so the test
// harness can exercise them directly.

import type { LogEntry } from "../store/logStore";

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Predicate for the System Log Device filter. An entry belongs to a device
 * when the structured device field matches (driver/transport lines carry a
 * "[id] " prefix the server extracts), or when the message mentions the id
 * as a whole token — device lifecycle lines phrase it loosely, e.g.
 * "Failed to connect 'proj1'". Token boundaries exclude id characters so
 * "proj1" never matches "proj12" or "my-proj1".
 */
export function deviceFilterPredicate(
  deviceId: string,
): (entry: Pick<LogEntry, "device" | "message">) => boolean {
  const id = deviceId.toLowerCase();
  const mention = new RegExp(
    `(^|[^a-z0-9_-])${escapeRegExp(id)}([^a-z0-9_-]|$)`,
    "i",
  );
  return (entry) =>
    entry.device.toLowerCase() === id || mention.test(entry.message);
}

/**
 * Render loaded log entries to plain text for the "Download logs" button.
 * One line per entry: an ISO timestamp, the level, the source category, then
 * the message. Exports exactly what it is given — the caller passes the
 * currently-filtered set, so the Source/Level/Device filters carry through to
 * the file a user attaches to a bug report.
 */
export function formatLogsForExport(
  entries: Array<Pick<LogEntry, "timestamp" | "level" | "category" | "message">>,
): string {
  return entries
    .map((e) => {
      const ts = new Date(e.timestamp * 1000).toISOString();
      return `[${ts}] ${e.level} ${e.category}: ${e.message}`;
    })
    .join("\n");
}

/**
 * A filesystem-safe, sortable name for a downloaded log file, e.g.
 * "openavc-log-20260722-143005.txt". `now` is passed in (not read from the
 * clock) so the helper stays pure and testable.
 */
export function logExportFilename(now: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  const stamp =
    `${now.getFullYear()}${p(now.getMonth() + 1)}${p(now.getDate())}` +
    `-${p(now.getHours())}${p(now.getMinutes())}${p(now.getSeconds())}`;
  return `openavc-log-${stamp}.txt`;
}
