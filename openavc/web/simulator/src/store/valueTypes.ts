// What a value type is and how it is drawn, read from the driver contract's
// one table (VALUE_TYPE_TRAITS in controls.gen.ts, generated from
// drivers/spec.py). Ask it rather than listing types by hand: a list of its
// own is the copy that misses float.
import { VALUE_TYPE_TRAITS, type ValueTypeTraits } from "./controls.gen";

/** The row for a declared type; a type left out or null reads as string.
 * The server sends only types a loaded driver declares, and the loader
 * refuses any other, so a name with no row is a platform fault and throws. */
export function valueTypeTraits(type: unknown): ValueTypeTraits {
  if (type === undefined || type === null) return VALUE_TYPE_TRAITS.string;
  if (typeof type === "string" && Object.prototype.hasOwnProperty.call(VALUE_TYPE_TRAITS, type)) {
    return VALUE_TYPE_TRAITS[type];
  }
  throw new Error(`${JSON.stringify(type)} is not a value type`);
}
