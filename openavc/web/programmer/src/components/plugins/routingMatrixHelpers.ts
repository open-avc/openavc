// React-free helpers for the plugin routing matrix (SurfaceConfigurator).
import {
  MATRIX_ROUTE_MATCH_MODES,
  matrixRouteMatches,
  type MatrixRouteMatchMode,
} from "../ui-builder/matrixModel";

/** The mode a layout that names none, or one this release does not know, is
 *  compared in: the one that never lights a crosspoint from a shared number.
 *  `SURFACE_ROUTE_MATCH_DEFAULT` in core/plugin_extension_rules.py, held to it
 *  by tests/test_plugin_extension_rules.py. */
export const SURFACE_ROUTE_MATCH_DEFAULT: MatrixRouteMatchMode = "exact";

/** The mode a layout's `route_match` names, or the default. */
export function routeMatchMode(declared: unknown): MatrixRouteMatchMode {
  return typeof declared === "string" && Object.prototype.hasOwnProperty.call(MATRIX_ROUTE_MATCH_MODES, declared)
    ? (declared as MatrixRouteMatchMode)
    : SURFACE_ROUTE_MATCH_DEFAULT;
}

/**
 * Whether a crosspoint is routed: the source its row's route key names is
 * the one its column's label names.
 *
 * Each destination (a row) carries one route key, `route_state_pattern` with
 * `{row}` filled in, whose value names the source routed to it; each source
 * (a column) is named by the value of its own key. The two are compared by
 * the matrix's route rule (`matrixRouteMatches`, the Builder's copy of
 * ui/matrix_model.py's) in the layout's mode. A layout that names no route
 * key reads every crosspoint as unrouted.
 */
export function crosspointRouted(
  routeStatePattern: string | undefined,
  mode: MatrixRouteMatchMode,
  read: (key: string) => unknown,
  row: string,
  columnLabel: unknown,
): boolean {
  if (!routeStatePattern) return false;
  return matrixRouteMatches(read(routeStatePattern.replace("{row}", row)), columnLabel, mode);
}

/** What a row or column is called: its key's own value, the name the plugin
 *  publishes for it ("MIC-01 / 01"), else the token its pattern matched, which
 *  is what a click sends back either way. */
export function axisCaption(value: unknown, token: string): string {
  return typeof value === "string" && value.trim() !== "" ? value : token;
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
