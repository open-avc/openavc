/**
 * Typed-value helpers for macro step editors.
 *
 * Macro step values (state.set value, event.emit payload fields) are stored
 * as real JSON primitives and sent to the runtime verbatim, so the type the
 * user authors is the type the system runs with. These helpers make that
 * choice explicit instead of guessing a type from how the text looks —
 * a text value of "0" or "true" must stay a string when that's what the
 * user wants.
 */

export type ValueKind = "text" | "number" | "boolean";

/** The kind a stored step value renders and edits as. */
export function valueKind(value: unknown): ValueKind {
  if (typeof value === "number") return "number";
  if (typeof value === "boolean") return "boolean";
  return "text";
}

/** Convert a stored value when the user switches its type. */
export function convertValue(value: unknown, kind: ValueKind): string | number | boolean {
  if (kind === "number") {
    const n = parseFloat(String(value));
    return Number.isNaN(n) ? 0 : n;
  }
  if (kind === "boolean") {
    return value === true || String(value) === "true";
  }
  return value == null ? "" : String(value);
}

/**
 * Parse a value-field edit for the given kind. Returns undefined when the
 * input isn't usable yet (blank or unparseable number) so callers keep the
 * prior value instead of snapping to 0.
 */
export function parseTypedInput(
  raw: string,
  kind: ValueKind,
): string | number | boolean | undefined {
  if (kind === "number") {
    const n = parseFloat(raw);
    return Number.isNaN(n) ? undefined : n;
  }
  if (kind === "boolean") return raw === "true";
  return raw;
}

/**
 * Update one payload row by index, preserving insertion order. An emptied
 * key drops the row (same contract as the driver-builder KeyValueList).
 */
export function updatePayloadRow(
  payload: Record<string, unknown>,
  index: number,
  key: string,
  value: unknown,
): Record<string, unknown> {
  const next: Record<string, unknown> = {};
  Object.entries(payload).forEach(([k, v], i) => {
    if (i === index) {
      if (key) next[key] = value;
    } else {
      next[k] = v;
    }
  });
  return next;
}

/** Remove one payload row by index. Returns undefined when nothing is left
 *  so the step drops its payload key entirely instead of keeping `{}`. */
export function removePayloadRow(
  payload: Record<string, unknown>,
  index: number,
): Record<string, unknown> | undefined {
  const next: Record<string, unknown> = {};
  Object.entries(payload).forEach(([k, v], i) => {
    if (i !== index) next[k] = v;
  });
  return Object.keys(next).length > 0 ? next : undefined;
}

/** Append a new payload row with a placeholder field name. */
export function addPayloadRow(
  payload: Record<string, unknown>,
): Record<string, unknown> {
  let key = "";
  let counter = 1;
  while (key === "" || key in payload) {
    key = `field${counter}`;
    counter++;
  }
  return { ...payload, [key]: "" };
}
