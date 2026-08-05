// Pure parsing for numeric property inputs, split out so it can be unit
// tested without React (see openavc/tests/test_numeric_field_helpers.py).
// Mirrors the other UI Builder helper modules.

/**
 * Parse a numeric property input. Clearing the field ("" — usually to
 * retype) means "unset": the property key is removed and the runtime
 * default applies, instead of committing a literal 0 that breaks controls
 * (digits=0 keypad, step=0 slider). Unparseable input is likewise dropped,
 * never stored as 0 or NaN. The editors pair this with `value={x ?? ""}`
 * and a placeholder showing the effective default.
 */
export function numOrUndefined(raw: string): number | undefined {
  if (raw.trim() === "") return undefined;
  const n = Number(raw);
  return Number.isFinite(n) ? n : undefined;
}

/** numOrUndefined for integer-typed fields: same unset semantics, value
 *  truncated toward zero (matching the old parseInt reading of "2.7"). */
export function intOrUndefined(raw: string): number | undefined {
  const n = numOrUndefined(raw);
  return n === undefined ? undefined : Math.trunc(n);
}

export interface NumericRange {
  min?: number;
  max?: number;
  /** Truncate toward zero, like the parseInt-era fields did. */
  integer?: boolean;
}

/**
 * Parse a FINISHED edit (blur / Enter) and clamp it into range. This is the
 * only place a clamp belongs: clamping every keystroke is what turned a
 * cleared width field into a live 0.1%-wide element. `undefined` means the
 * field was left empty or unparseable — the caller unsets or reverts.
 */
export function commitNumeric(raw: string, range: NumericRange = {}): number | undefined {
  const n = range.integer ? intOrUndefined(raw) : numOrUndefined(raw);
  if (n === undefined) return undefined;
  let v = n;
  if (typeof range.min === "number") v = Math.max(range.min, v);
  if (typeof range.max === "number") v = Math.min(range.max, v);
  return v;
}

/**
 * A keystroke worth committing live, for fields that preview while you type:
 * it must parse AND already be in range. Out-of-range mid-edit states ("0"
 * on the way to "0.5" with min 0.1) are tolerated, not fought — the clamp
 * waits for commitNumeric.
 */
export function liveNumeric(raw: string, range: NumericRange = {}): number | undefined {
  const n = range.integer ? intOrUndefined(raw) : numOrUndefined(raw);
  if (n === undefined) return undefined;
  if (typeof range.min === "number" && n < range.min) return undefined;
  if (typeof range.max === "number" && n > range.max) return undefined;
  return n;
}
