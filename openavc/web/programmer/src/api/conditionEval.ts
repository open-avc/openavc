/**
 * The runtime's condition rule, for a screen that shows what a condition
 * evaluates to now (the trigger editor's "Evaluated now").
 *
 * Mirrors `eval_operator` in openavc/core/condition_eval.py, which decides
 * whether a trigger's conditions pass. Both run the corpus in
 * tests/fixtures/condition_parity_cases.json (tests/test_condition_parity.py
 * and conditionEval.test.ts), so the preview cannot say FALSE about a trigger
 * the runtime fires: text compares without regard to case, "on" / "yes" / "1"
 * equal a boolean true, a number written as text equals the number, and a value
 * that has not been reported never matches.
 *
 * State holds strings, numbers, booleans and null, and that is what is
 * mirrored. Two limits: text is lower-cased where Python case-folds (they differ
 * only outside ASCII, "ß" for one), and Python's int and float are one number
 * here, which changes no answer.
 */

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

/** What Python's float() reads after trimming: a sign, digits with single
 * underscores between them, a point, an exponent, or inf / infinity / nan.
 * Never hex, never an empty string (Number() takes both). */
const DIGITS = String.raw`\d(?:_?\d)*`;
const PY_FLOAT = new RegExp(
  String.raw`^[+-]?(?:(?:(?:${DIGITS}(?:\.(?:${DIGITS})?)?|\.${DIGITS})(?:[eE][+-]?${DIGITS})?)|inf(?:inity)?|nan)$`,
  "i",
);

function coerceNumeric(value: unknown): number | null {
  if (typeof value === "number") return value;
  // Python's bool is an int.
  if (typeof value === "boolean") return value ? 1 : 0;
  if (typeof value === "string") {
    const text = value.trim();
    if (!PY_FLOAT.test(text)) return null;
    const word = text.replace(/^[+-]/, "").toLowerCase();
    if (word === "nan") return NaN;
    if (word === "inf" || word === "infinity") return text.startsWith("-") ? -Infinity : Infinity;
    return Number(text.replace(/_/g, ""));
  }
  return null;
}

function coerceBool(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    const low = value.toLowerCase();
    if (low === "true" || low === "1" || low === "yes" || low === "on") return true;
    if (low === "false" || low === "0" || low === "no" || low === "off") return false;
  }
  return null;
}

/** Python's bool(): only None, False, 0 and "" are false (NaN is true). */
function pythonTruthy(value: unknown): boolean {
  if (value === null || value === undefined || value === false || value === "") return false;
  if (typeof value === "number") return value !== 0;
  return true;
}

function compare<T extends number | string>(op: string, a: T, t: T): boolean {
  if (op === "gt") return a > t;
  if (op === "lt") return a < t;
  if (op === "gte") return a >= t;
  return a <= t;
}

/** `eval_operator(op, actual, target)`. Throws on an operator the runtime
 * does not know, as the runtime does; a trigger treats that as not passing. */
export function evalOperator(op: string, actual: unknown, target: unknown): boolean {
  const canonical = OPERATOR_ALIASES[op] ?? op;

  if (canonical === "eq" || canonical === "ne") {
    // Never equal and never not-equal while either side is unknown.
    if (actual == null || target == null) return false;
    const eq = canonical === "eq";
    if (typeof actual === "boolean" || typeof target === "boolean") {
      const a = coerceBool(actual);
      const t = coerceBool(target);
      if (a !== null && t !== null) return eq ? a === t : a !== t;
    }
    if (typeof actual !== typeof target) {
      const a = coerceNumeric(actual);
      const t = coerceNumeric(target);
      if (a !== null && t !== null) return eq ? a === t : a !== t;
    }
    const isEqual = typeof actual === "string" && typeof target === "string"
      ? actual.toLowerCase() === target.toLowerCase()
      : actual === target;
    return eq ? isEqual : !isEqual;
  }

  if (canonical === "gt" || canonical === "lt" || canonical === "gte" || canonical === "lte") {
    if (actual == null || target == null) return false;
    const a = coerceNumeric(actual);
    const t = coerceNumeric(target);
    if (a !== null && t !== null) return compare(canonical, a, t);
    // Python then compares the values as they are, which works only for two
    // strings; anything else is a TypeError and does not pass.
    if (typeof actual === "string" && typeof target === "string") {
      return compare(canonical, actual, target);
    }
    return false;
  }

  if (canonical === "truthy") return pythonTruthy(actual);
  if (canonical === "falsy") return !pythonTruthy(actual);
  throw new Error(
    `Unknown condition operator: '${op}'. Valid operators: eq, ne, gt, lt, gte, lte, truthy, falsy`,
  );
}
