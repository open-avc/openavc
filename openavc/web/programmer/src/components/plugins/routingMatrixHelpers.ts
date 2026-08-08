// React-free helpers for the plugin routing matrix (SurfaceConfigurator).

/**
 * Crosspoint truthiness. State values are flat primitives and plugins
 * commonly report route status as a string — the Dante plugin writes
 * "none" when a channel is unsubscribed — so JS Boolean() coercion reads
 * unrouted cells as routed and the first click sends the wrong action to
 * routing hardware. A cell is routed when the value is boolean true, a
 * nonzero number, or a string that isn't a conventional "off" word.
 */
const UNROUTED_STRINGS = new Set(["", "false", "0", "none", "off", "no"]);

export function isCellRouted(value: unknown): boolean {
  if (typeof value === "string") {
    return !UNROUTED_STRINGS.has(value.trim().toLowerCase());
  }
  if (typeof value === "number") return value !== 0;
  return value === true;
}

export interface StatePatternMatch {
  /** Full state key. */
  key: string;
  /** The text the wildcard matched — the {row}/{col} token. */
  name: string;
}

/**
 * Enumerate state keys matching a rows/columns pattern. `*` matches any
 * non-empty run of characters anywhere in the pattern (the documented
 * semantics) — not just as a trailing suffix, which is all the previous
 * prefix-slice derivation supported (a mid-string pattern like
 * `plugin.x.tx.*.name` silently produced an empty matrix).
 */
export function matchStateKeys(keys: string[], pattern: string): StatePatternMatch[] {
  if (!pattern || !pattern.includes("*")) return [];
  const escaped = pattern
    .split("*")
    .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const rx = new RegExp("^" + escaped.join("(.+)") + "$");
  const matches: StatePatternMatch[] = [];
  for (const key of keys) {
    const m = key.match(rx);
    if (m) matches.push({ key, name: m.slice(1).join(".") });
  }
  matches.sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
  return matches;
}
