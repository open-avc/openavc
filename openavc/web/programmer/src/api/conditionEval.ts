/**
 * The runtime's comparison of a reading with what an author wrote, for a
 * screen that judges a condition without the server: the trigger editor's
 * "Evaluated now" and the page reviews.
 *
 * Mirrors openavc/core/condition_eval.py, THE rule, which macros, triggers
 * and the server's panel press ask; the panel's own copy is
 * web/panel/modules/compare.js. All three run the corpus in
 * tests/fixtures/condition_parity_cases.json in both modes
 * (tests/test_condition_parity.py, conditionEval.test.ts and
 * tests/test_panel_condition_parity.py), so a preview cannot say FALSE about a
 * trigger the runtime fires: text compares without regard to case, a boolean
 * compares with the words and numbers that mean it, a number written as text
 * equals the number, and a value that has not been reported never matches.
 *
 * State holds strings, numbers, booleans and null, and that is what is
 * mirrored. Two limits: text is lower-cased where Python case-folds (they differ
 * only outside ASCII, "ß" for one), and Python's int and float are one number
 * here, which changes no answer.
 */

import { asNumber } from "./monitorHelpers";
import { FALSE_WORDS, TRUE_WORDS } from "./types.gen";

/** A reader that acts (a macro, a trigger) makes no decision on a reading
 *  that has not arrived: never equal, never not-equal. The default. */
export const DECIDE = "decide";
/** A reader that shows or hides (the panel, a page review) reads one as
 *  unequal to every value, so `power != on` holds before the device reports. */
export const SURFACE = "surface";
export type ConditionMode = typeof DECIDE | typeof SURFACE;
/** Whether a reading that has not arrived holds `ne`, by mode: `MODES` in
 *  condition_eval.py. */
export const MODES: Record<ConditionMode, boolean> = { [DECIDE]: false, [SURFACE]: true };

const OPERATOR_ALIASES: Record<string, string> = {
  equals: "eq",
  not_equals: "ne",
  "==": "eq",
  "!=": "ne",
  ">": "gt",
  "<": "lt",
  ">=": "gte",
  "<=": "lte",
  equal: "eq",
  not_equal: "ne",
  greater_than: "gt",
  less_than: "lt",
  greater_or_equal: "gte",
  less_or_equal: "lte",
};

/** Every spelling the rule takes, its own eight and the aliases onto them. */
export const OPERATOR_SPELLINGS: ReadonlySet<string> = new Set([
  "eq", "ne", "gt", "lt", "gte", "lte", "truthy", "falsy", ...Object.keys(OPERATOR_ALIASES),
]);

/** A spelling as the operator it names, or itself when it names none. */
export function canonicalOperator(op: string): string {
  return OPERATOR_ALIASES[op] ?? op;
}

/** A value as a number to compare: `asNumber`'s reading, and a boolean as 1
 *  or 0 so `true < 2` orders as it always has. */
function coerceNumeric(value: unknown): number | null {
  if (typeof value === "boolean") return value ? 1 : 0;
  return asNumber(value);
}

/** A value read as true or false, or null when it is neither: the rule of
 *  openavc/drivers/truth_words.py, whose words types.gen.ts carries. A
 *  boolean is itself; a word trimmed and apart from case (on, true, yes,
 *  enabled; off, false, no, disabled); a number by value, non-zero true. */
export function readTruth(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    const word = value.trim().toLowerCase();
    if (TRUE_WORDS.has(word)) return true;
    if (FALSE_WORDS.has(word)) return false;
  }
  const number = asNumber(value);
  return number === null || !Number.isFinite(number) ? null : number !== 0;
}

/** Python's bool(): only None, False, 0 and "" are false (NaN is true). */
function pythonTruthy(value: unknown): boolean {
  if (value === null || value === undefined || value === false || value === "") return false;
  if (typeof value === "number") return value !== 0;
  return true;
}

type Order = <T extends number | string>(a: T, t: T) => boolean;
const ORDERINGS: Record<string, Order> = {
  gt: (a, t) => a > t,
  lt: (a, t) => a < t,
  gte: (a, t) => a >= t,
  lte: (a, t) => a <= t,
};

/** `eq` or `ne`: never equal, and not-equal only in the surface mode, while
 *  the reading has not arrived; never either against an authored null. */
function equality(op: string, actual: unknown, target: unknown, mode: ConditionMode): boolean {
  if (target == null) return false;
  if (actual == null) return op === "ne" && MODES[mode];
  return same(actual, target) === (op === "eq");
}

/** An ordering: as numbers when both read as one, else two words as text. */
function ordering(order: Order, actual: unknown, target: unknown): boolean {
  if (actual == null || target == null) return false;
  const a = coerceNumeric(actual);
  const t = coerceNumeric(target);
  if (a !== null && t !== null) return order(a, t);
  return typeof actual === "string" && typeof target === "string" && order(actual, target);
}

/** Equal as booleans when one side is a boolean and both read as one; else null. */
function booleansEqual(actual: unknown, target: unknown): boolean | null {
  if (typeof actual !== "boolean" && typeof target !== "boolean") return null;
  const a = readTruth(actual);
  const t = readTruth(target);
  return a === null || t === null ? null : a === t;
}

/** Equal as numbers when the types differ and both read as one; else null. */
function numbersEqual(actual: unknown, target: unknown): boolean | null {
  if (typeof actual === typeof target) return null;
  const a = coerceNumeric(actual);
  const t = coerceNumeric(target);
  return a === null || t === null ? null : a === t;
}

/** Whether two values, neither of them missing, are equal under the rule:
 *  as booleans, else as numbers, else as words apart from case. */
function same(actual: unknown, target: unknown): boolean {
  const answer = booleansEqual(actual, target) ?? numbersEqual(actual, target);
  if (answer !== null) return answer;
  return typeof actual === "string" && typeof target === "string"
    ? actual.toLowerCase() === target.toLowerCase()
    : actual === target;
}

/** `eval_operator(op, actual, target, mode)`. Throws on an operator the
 *  runtime does not know, as the runtime does; a trigger treats that as not
 *  passing. One that is not text names none, though a one-item list would
 *  look up as its item. */
export function evalOperator(
  op: string, actual: unknown, target: unknown, mode: ConditionMode = DECIDE,
): boolean {
  const canonical = typeof op === "string" ? canonicalOperator(op) : "";
  if (canonical === "eq" || canonical === "ne") return equality(canonical, actual, target, mode);
  if (canonical in ORDERINGS) return ordering(ORDERINGS[canonical], actual, target);
  if (canonical === "truthy") return pythonTruthy(actual);
  if (canonical === "falsy") return !pythonTruthy(actual);
  throw new Error(
    `Unknown condition operator: '${op}'. Valid operators: eq, ne, gt, lt, gte, lte, truthy, falsy`,
  );
}

/** Whether a reading equals an authored value: `eq`, the same in both modes. */
export function sameValue(actual: unknown, target: unknown): boolean {
  return evalOperator("eq", actual, target);
}

/** A value's words, as `value_words` in condition_eval.py spells them. */
function valueWords(value: unknown): string {
  return String(value);
}

const byWords = (a: unknown, b: unknown): number => {
  const x = valueWords(a);
  const y = valueWords(b);
  return x < y ? -1 : x > y ? 1 : 0;
};

/** The key of an authored map a reading picks, or null: `match_key` in
 *  condition_eval.py (the exact key, then one apart from case, then one the
 *  reading equals as a boolean or number; ties go to code point order). */
export function matchKey<K>(keys: Iterable<K>, reading: unknown): K | null {
  if (reading == null) return null;
  const candidates = [...keys].filter((key) => key != null);
  const words = valueWords(reading);
  const tiers: ((key: K) => boolean)[] = [
    (key) => valueWords(key) === words,
    (key) => valueWords(key).toLowerCase() === words.toLowerCase(),
    (key) => sameValue(reading, key),
  ];
  for (const matches of tiers) {
    const found = candidates.filter(matches).sort(byWords);
    if (found.length) return found[0];
  }
  return null;
}
