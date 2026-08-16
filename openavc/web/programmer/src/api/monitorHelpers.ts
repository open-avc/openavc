/**
 * THE monitor rule, in the IDE: whether a monitored reading is all right.
 *
 * THIS MIRRORS `openavc/core/monitors.py` AND MUST NOT DRIFT FROM IT. The
 * server compiles the same declaration into the alert that reaches somebody's
 * phone; this file decides what the Dashboard tile beside them says. A tile
 * reading calm while an alert is firing over it is worse than either alone, so
 * `tests/test_monitor_parity.py` and `monitorHelpers.test.ts` push the shared
 * corpus in `tests/fixtures/monitor_parity_cases.json` through both sides and
 * compare answer for answer. Change a rule here and it lands there in the same
 * commit, or the suite goes red.
 *
 * Two rules the corpus exists to hold:
 *   - A monitor with no limits declared is UNSET, never NORMAL. Green is a
 *     claim, and nobody made it.
 *   - A boolean never renders as `true` / `false`.
 */

import type { MonitorConfig, MonitorStateEntry } from "./types";

export const UNSET = "unset";
export const NO_VALUE = "no_value";
export const NORMAL = "normal";
export const ABNORMAL = "abnormal";

export type MonitorStatus =
  | typeof UNSET
  | typeof NO_VALUE
  | typeof NORMAL
  | typeof ABNORMAL;

/** What a bare boolean reads as when the author wrote no words for it. */
const BOOLEAN_WORDS: Record<string, string> = { true: "Yes", false: "No" };

/** A value as the string both sides of a comparison agree on.
 *  Booleans are the reason: the live value is `true`, the project spells it
 *  `"true"`, and those must be the same value from either direction. Every
 *  other enum comparison stays case-sensitive — "Mic" and "mic" are different
 *  inputs on plenty of frames. */
function norm(value: unknown): string {
  if (typeof value === "boolean") return value ? "true" : "false";
  const text = String(value);
  const lowered = text.toLowerCase();
  return lowered === "true" || lowered === "false" ? lowered : text;
}

/** What counts as a number when a range is compared against a reading.
 *  Deliberately narrower than `Number()`, and pinned to the same spelling
 *  Python's `as_number` uses — otherwise "0x10" is 16 on one side and not a
 *  number on the other. */
const NUMERIC_RE = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;

/** The reading as a number, or null when a range cannot address it.
 *  A boolean is not a number here: `Number(true)` is 1, and without this a
 *  range would quietly start judging an unrelated boolean. */
export function asNumber(value: unknown): number | null {
  if (typeof value === "boolean") return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && NUMERIC_RE.test(value.trim())) {
    return Number(value.trim());
  }
  return null;
}

/** The values this monitor calls normal, normalised, in declared order.
 *  A states entry carrying only a label is vocabulary, not a limit. */
export function normalValues(monitor: MonitorConfig): string[] {
  const states = monitor.states;
  if (!states || typeof states !== "object") return [];
  const out: string[] = [];
  for (const [value, entry] of Object.entries(states)) {
    if (entry && (entry as MonitorStateEntry).normal === true) out.push(norm(value));
  }
  return out;
}

/** The declared range, as the two numbers this rule can actually compare.
 *
 *  A bound that is not a number is not a limit — `normal_max: "warm"` states
 *  nothing that can be evaluated, so nothing is claimed and the reading stays
 *  informational. Same answer `monitorStatus` already gives when the *reading*
 *  is not a number under a declared range.
 *
 *  The types say this cannot happen and the types are not the authority: the
 *  same declaration is read on the cloud straight off the wire as raw JSON.
 *  Before this, the two sides answered a word-valued bound three different
 *  ways — Python raised, and here `num < "warm"` is `false`, so a reading
 *  outside a limit nobody could evaluate quietly drew as NORMAL. */
export function normalBounds(monitor: MonitorConfig): [number | null, number | null] {
  return [asNumber(monitor.normal_min), asNumber(monitor.normal_max)];
}

/** Whether anybody said what normal looks like for this reading. "Said
 *  something" is not enough — it has to be something this rule can evaluate. */
export function hasLimits(monitor: MonitorConfig): boolean {
  const [low, high] = normalBounds(monitor);
  if (low !== null || high !== null) return true;
  return normalValues(monitor).length > 0;
}

/** Is this reading all right. `value` is undefined/null for a key that has
 *  never reported — which is NO_VALUE even under limits, because "it has not
 *  told us" is a different sentence from "it is wrong". */
export function monitorStatus(monitor: MonitorConfig, value: unknown): MonitorStatus {
  if (!hasLimits(monitor)) return UNSET;
  if (value === undefined || value === null) return NO_VALUE;

  const allowed = normalValues(monitor);
  if (allowed.length > 0) {
    return allowed.includes(norm(value)) ? NORMAL : ABNORMAL;
  }

  const [low, high] = normalBounds(monitor);
  const num = asNumber(value);
  // A range was declared and the reading is not a number. Saying ABNORMAL
  // would be a judgement about a value the limits cannot address.
  if (num === null) return UNSET;
  if (low !== null && num < low) return ABNORMAL;
  if (high !== null && num > high) return ABNORMAL;
  return NORMAL;
}

/** The word for this value, or null when the value speaks for itself. */
export function monitorWord(monitor: MonitorConfig, value: unknown): string | null {
  if (value === undefined || value === null) return null;
  const key = norm(value);
  const states = monitor.states;
  if (states && typeof states === "object") {
    for (const [candidate, entry] of Object.entries(states)) {
      if (norm(candidate) === key && entry && (entry as MonitorStateEntry).label) {
        return String((entry as MonitorStateEntry).label);
      }
    }
  }
  if (typeof value === "boolean") return BOOLEAN_WORDS[key];
  return null;
}

/** What to call this reading. Falls back to the key so a tile is never blank. */
export function monitorLabel(monitor: MonitorConfig): string {
  const label = monitor.label;
  if (typeof label === "string" && label.trim()) return label;
  return monitor.key ?? "";
}

/** Value plus unit, as a tile shows it. "—" when nothing has reported —
 *  no value is not zero. */
export function monitorReading(monitor: MonitorConfig, value: unknown): string {
  if (value === undefined || value === null) return "—";
  const word = monitorWord(monitor, value);
  if (word) return word;
  const unit = monitor.unit?.trim();
  return unit ? `${String(value)} ${unit}` : String(value);
}

// --- Keeping the list honest (mirrors monitors.py) ---

/** Monitors left after a device goes, its child entities included. */
export function dropMonitorsForDevice(
  monitors: MonitorConfig[],
  deviceId: string,
): MonitorConfig[] {
  const prefix = `device.${deviceId}.`;
  return monitors.filter((m) => !m.key.startsWith(prefix));
}

/** Monitors left after a variable goes. */
export function dropMonitorsForVariable(
  monitors: MonitorConfig[],
  variableId: string,
): MonitorConfig[] {
  const key = `var.${variableId}`;
  return monitors.filter((m) => m.key !== key);
}

/** Follow a renamed key, so a rename does not quietly orphan what it watched. */
export function renameMonitorKey(
  monitors: MonitorConfig[],
  oldKey: string,
  newKey: string,
): MonitorConfig[] {
  return monitors.map((m) => (m.key === oldKey ? { ...m, key: newKey } : m));
}
