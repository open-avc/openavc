// What a value type is and how each reader treats it, read from the driver
// contract's one table (VALUE_TYPE_TRAITS in types.gen.ts, generated from
// drivers/spec.py). Ask a column here rather than listing types by hand: a
// list of its own is the copy that misses float.
import { VALUE_TYPE_TRAITS, VALUE_TYPES, type ValueTypeTraits } from "../../api/types";

/** The row for a declared type. A type left out or null reads as string; a
 * name with no row (a draft the checks have not passed) is undefined. */
export function valueTypeTraits(type: unknown): ValueTypeTraits | undefined {
  if (type === undefined || type === null) return VALUE_TYPE_TRAITS.string;
  return typeof type === "string" && Object.prototype.hasOwnProperty.call(VALUE_TYPE_TRAITS, type)
    ? VALUE_TYPE_TRAITS[type]
    : undefined;
}

/** Whether `type` is a number (integer, number or float). */
export function isNumericType(type: unknown): boolean {
  return valueTypeTraits(type)?.numeric === true;
}

/** Whether `type` is read as a real number rather than a whole one. */
export function isRealType(type: unknown): boolean {
  return valueTypeTraits(type)?.real === true;
}

/** The choices a value type picker offers, in the contract's order. */
export const VALUE_TYPE_OPTIONS: readonly { value: string; label: string }[] = VALUE_TYPES.map(
  (value) => ({ value, label: VALUE_TYPE_TRAITS[value].label }),
);
