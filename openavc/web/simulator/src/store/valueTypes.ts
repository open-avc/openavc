// What a value type is and how it is drawn, read from the driver contract's
// one table (VALUE_TYPE_TRAITS in controls.gen.ts, generated from
// drivers/spec.py). Ask it rather than listing types by hand: a list of its
// own is the copy that misses float.
import { VALUE_TYPE_TRAITS, type ValueTypeTraits } from "./controls.gen";

/** The row for a declared type. A type left out or null, or one with no row
 * (a Python driver with one still loads), reads as string, so it is drawn as
 * the text it reads as. */
export function valueTypeTraits(type: unknown): ValueTypeTraits {
  return typeof type === "string" && Object.prototype.hasOwnProperty.call(VALUE_TYPE_TRAITS, type)
    ? VALUE_TYPE_TRAITS[type]
    : VALUE_TYPE_TRAITS.string;
}
