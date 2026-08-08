/**
 * Pure helpers behind the plugin extension renderers (PluginExtensions.tsx):
 * glob matching for state patterns and driver ids, metric formatting, and
 * the plugin log filter.
 */

// Convert a glob-style pattern to an anchored RegExp.
//
// Replaces every `*` with `.*` (multi-segment, matches across `.`) and
// escapes all other regex specials. Anchoring with ^…$ keeps a pattern
// like `plugin.foo.*` from accidentally matching `plugin.football.*`,
// the bug A70 called out.
export function compileStatePattern(pattern: string): RegExp | null {
  const trimmed = pattern.trim();
  if (!trimmed) return null;
  // Escape every regex special except `*`, then turn `*` into `.*`.
  const escaped = trimmed
    .replace(/[.+?^${}()|[\]\\]/g, "\\$&")
    .replace(/\*/g, ".*");
  try {
    return new RegExp(`^${escaped}$`);
  } catch {
    return null;
  }
}

// Match a driver id against the documented glob syntax (`dante_*`).
// Every `*` is a wildcard wherever it appears; a pattern without `*`
// must match exactly. Same compiler as state patterns so the two glob
// surfaces can't drift apart.
export function matchesDriverGlob(driverId: string, pattern: string): boolean {
  const re = compileStatePattern(pattern);
  return re ? re.test(driverId) : false;
}

// Format a status-card metric value. State values are flat primitives, so a
// plugin can publish a boolean as the string "false" or "0" — JS truthiness
// would render those as "Yes".
export function formatMetric(value: unknown, format: string): string {
  if (value === null || value === undefined) return "—";
  if (format === "boolean") {
    if (typeof value === "string") {
      const lowered = value.trim().toLowerCase();
      return lowered === "false" || lowered === "0" || lowered === "" ? "No" : "Yes";
    }
    return value ? "Yes" : "No";
  }
  return String(value);
}

export interface PluginLogEntry {
  timestamp: number;
  level: string;
  source: string;
  message: string;
}

// Filter the log buffer down to one plugin's recent entries.
export function filterPluginLog<T extends PluginLogEntry>(
  entries: T[],
  pluginId: string,
): T[] {
  return entries
    .filter(
      (e) =>
        e.source === "openavc.core.plugin_loader" ||
        e.message.includes(`[Plugin:${pluginId}]`),
    )
    .slice(-50);
}

// Cheap change check for the append-only log ring buffer: same length and
// same last entry means the filtered view is unchanged.
export function sameLogTail(a: PluginLogEntry[], b: PluginLogEntry[]): boolean {
  if (a.length !== b.length) return false;
  if (a.length === 0) return true;
  const la = a[a.length - 1];
  const lb = b[b.length - 1];
  return la.timestamp === lb.timestamp && la.message === lb.message;
}
