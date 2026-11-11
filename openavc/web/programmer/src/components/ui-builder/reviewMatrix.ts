/**
 * The page review's matrix findings: a config nothing reads, an axis that is
 * empty or names one value twice, a lock nothing backs, a destination that
 * never shows what is routed to it.
 *
 * What a matrix resolves to is matrixModel.ts. Mirrors `matrix_findings` and
 * `_MATRIX_CHECKS` in openavc/ui/page_review.py.
 */

import type { UIElement } from "../../api/types";
import { MATRIX_CONFIG_KEYS, MATRIX_PANEL_WRITABLE_PREFIXES } from "../../api/uiBindingReach.gen";
import {
  matrixRouteMatches,
  matrixSourceReports,
  resolveMatrixAxis,
  type MatrixAxisName,
  type MatrixEntry,
} from "./matrixModel";
import { joined, type ReviewFinding } from "./reviewFinding";

/** Which direction each axis routes, for a sentence that has to say which. */
const MATRIX_AXIS_DIRECTION: Record<MatrixAxisName, string> = {
  sources: "route from",
  destinations: "route to",
};

/** How many entries a matrix finding names before it counts the rest. */
const MATRIX_NAMED = 4;

function matrixNamed(labels: string[]): string {
  const shown = labels.slice(0, MATRIX_NAMED).join(", ");
  const rest = labels.length - MATRIX_NAMED;
  return rest > 0 ? `${shown} and ${rest} more` : shown;
}

/**
 * What a review sentence calls one matrix row: the name it was given, else its
 * value. Not the panel's caption (`_entryLabel`), which can read a live name off
 * the device: a review has no state to read. The AI side's twin is
 * `_entry_name` in page_review.py.
 */
export function matrixEntryName(entry: MatrixEntry): string {
  return String(entry.label ?? entry.value);
}

/** `1 destination` or `3 destinations`. */
function destinationsCounted(count: number): string {
  return `${count} destination${count === 1 ? "" : "s"}`;
}

type MatrixCheck = (elId: string, config: Record<string, unknown>) => ReviewFinding[];

/** Keys in `matrix_config` the matrix renderer never reads. */
function unreadConfigFindings(elId: string, config: Record<string, unknown>): ReviewFinding[] {
  const unread = Object.keys(config).filter((k) => !MATRIX_CONFIG_KEYS.includes(k)).sort();
  if (!unread.length) return [];
  return [{
    elementId: elId,
    kind: "matrix_config_unread",
    message:
      `${elId} (matrix) sets matrix_config ${joined(unread.map((k) => `'${k}'`))}, ` +
      `which the matrix renderer does not read. The keys it reads are: ` +
      `${[...MATRIX_CONFIG_KEYS].sort().join(", ")}.`,
    key: `matrix_config_unread|${elId}|${unread.join("|")}`,
  }];
}

/** Each axis that resolves to nothing, or has two rows claiming one value. */
function axisFindings(elId: string, config: Record<string, unknown>): ReviewFinding[] {
  const findings: ReviewFinding[] = [];
  for (const axis of ["sources", "destinations"] as const) {
    const entries = resolveMatrixAxis(config, axis);
    if (!entries.length) {
      findings.push({
        elementId: elId,
        kind: "matrix_default_size",
        message:
          `${elId} (matrix) resolves to no ${axis}, so it draws nothing to ` +
          `${MATRIX_AXIS_DIRECTION[axis]}. List them under ` +
          `matrix_config.${axis}, or generate them with ${axis}.from.count.`,
        key: `matrix_default_size|${elId}|${axis}`,
      });
      continue;
    }
    const collided = collidingEntries(entries, axis);
    if (collided.length) {
      findings.push({
        elementId: elId,
        kind: "matrix_duplicate_values",
        message:
          `${elId} (matrix) has ${collided.length} of its ${axis} claiming a value ` +
          `another one already has (${matrixNamed(collided)}), and a routed ` +
          `source is matched by value -- so they cannot be told apart. Give ` +
          `each one the value its device actually reports.`,
        key: `matrix_duplicate_values|${elId}|${axis}|${collided.join("|")}`,
      });
    }
  }
  return findings;
}

/**
 * The rows claiming a value an earlier row already has, by name.
 *
 * Two entries claiming one value is new in 0.10.0 and is only possible because a
 * value is now opaque and authored. The crosspoint match lights every entry the
 * device's report names, and a list dropdown can only hold one option per value,
 * so the second is either a duplicate light or a row that cannot be selected.
 */
function collidingEntries(entries: MatrixEntry[], axis: MatrixAxisName): string[] {
  const seen: unknown[] = [];
  const collided: string[] = [];
  for (const entry of entries) {
    // By what the DEVICE calls it, which is what a report is matched against --
    // the same thing as the value on all but a couple of drivers, and the only
    // thing that can collide on those.
    const value = axis === "sources" ? matrixSourceReports(entry) : entry.value;
    if (seen.some((other) => matrixRouteMatches(value, other))) {
      collided.push(matrixEntryName(entry));
    } else {
      seen.push(value);
    }
  }
  return collided;
}

/**
 * Lock buttons drawn for destinations no variable backs.
 *
 * A lock only the panel that pressed it remembers is not a lock: it is gone the
 * next time the page draws, the panel by the other door never hears about it,
 * and the rack does not know either. Backing it with a variable is what makes it
 * one, and `var.`/`plugin.` are the only prefixes a panel is allowed to write, so
 * the check is the prefix rather than merely presence.
 */
function unbackedLockFindings(elId: string, config: Record<string, unknown>): ReviewFinding[] {
  if (config.show_lock !== true) return [];
  const unbacked = resolveMatrixAxis(config, "destinations")
    .filter((entry) => !MATRIX_PANEL_WRITABLE_PREFIXES.some(
      (prefix) => String(entry.lock_key ?? "").startsWith(prefix)))
    .map(matrixEntryName);
  if (!unbacked.length) return [];
  const one = unbacked.length === 1;
  return [{
    elementId: elId,
    kind: "matrix_lock_unbacked",
    message:
      `${elId} (matrix) draws lock buttons, but ${destinationsCounted(unbacked.length)} ` +
      `(${matrixNamed(unbacked)}) ` +
      `${one ? "has" : "have"} no lock_key under ` +
      `${MATRIX_PANEL_WRITABLE_PREFIXES.map((p) => `'${p}'`).join(" or ")}, so locking ` +
      `${one ? "it" : "them"} is this panel's own memory ` +
      `-- forgotten when the page redraws, and invisible to every other ` +
      `panel in the space. Give each one a variable, e.g. ` +
      `'var.${elId}_lock_1'.`,
    key: `matrix_lock_unbacked|${elId}`,
  }];
}

/** Destinations with no `route_key`, which never show what is routed to them. */
function blindDestinationFindings(elId: string, config: Record<string, unknown>): ReviewFinding[] {
  const blind = resolveMatrixAxis(config, "destinations")
    .filter((entry) => !entry.route_key)
    .map(matrixEntryName);
  if (!blind.length) return [];
  return [{
    elementId: elId,
    kind: "matrix_no_route_feedback",
    message:
      `${elId} (matrix) has ${destinationsCounted(blind.length)} with no route_key ` +
      `(${matrixNamed(blind)}), so those never show what is routed -- the ` +
      `matrix routes, and the panel cannot say which source is selected. Give ` +
      `each one the state key holding its routed source, e.g. ` +
      `'device.<id>.output.1.input'.`,
    key: `matrix_no_route_feedback|${elId}`,
  }];
}

/** The checks a configured matrix gets, in the order they report. A new one is
 *  an entry here and one in `_MATRIX_CHECKS`, its twin in page_review.py. */
const MATRIX_CHECKS: readonly MatrixCheck[] = [
  unreadConfigFindings,
  axisFindings,
  unbackedLockFindings,
  blindDestinationFindings,
];

/**
 * A matrix that will draw, and route, and never show what is routed.
 *
 * `route_key` is what makes a destination visible rather than merely operable:
 * without one the panel registers no state binding for that row, so it keeps its
 * inactive colour for the life of the panel. Clicking still routes, so the
 * control is half-alive and a bench test that only asks "does it switch" passes.
 *
 * It is per destination now rather than per element, which is the whole shape of
 * project format 0.10.0: each destination owns its key, so half a matrix can be
 * blind while the other half reports. The rest of MATRIX_CHECKS are what make
 * that hard to notice.
 */
export function matrixFindings(el: UIElement): ReviewFinding[] {
  if (el.type !== "matrix") return [];
  const config = el.matrix_config as Record<string, unknown> | undefined;
  if (!hasMatrixConfig(config)) {
    return [{
      elementId: el.id,
      kind: "matrix_not_configured",
      message:
        `${el.id} (matrix) has no matrix_config, so it draws an empty box. Set ` +
        `sources and destinations -- each is a list of entries, or a generator ` +
        `standing for one, e.g. destinations: ` +
        `{'from': {'count': 8, 'route_key': 'device.<id>.output.*.input'}}.`,
      key: `matrix_not_configured|${el.id}`,
    }];
  }
  return MATRIX_CHECKS.flatMap((check) => check(el.id, config));
}

/**
 * Whether a matrix carries a config to check: any object with a key of its own.
 *
 * A list with entries counts, its positions read as keys, where `matrix_findings`
 * in page_review.py takes only a mapping; that is left as it is, since narrowing it
 * here would change what such a matrix is told.
 */
function hasMatrixConfig(config: unknown): config is Record<string, unknown> {
  return !!config && typeof config === "object" && Object.keys(config).length > 0;
}
