/**
 * Reading what an author wrote the way the Python side reads it.
 *
 * The page review, its floors and its matrix model each exist twice, once here
 * and once under openavc/ui/, and the two are compared message for message
 * (tests/test_ui_review_parity.py). They read the same authored JSON, so they
 * have to agree on its shapes: what counts as an object, and what a lookup keyed
 * by authored text answers. Each mirrored module asks these two.
 */

/** A table lookup keyed by an element TYPE, which is authored text.
 *
 *  A plain object inherits `constructor`, `toString` and friends, so a bare
 *  `TABLE[el.type]` hands back a function for a type named after one of them
 *  and the next line throws. Python's `.get` has no such hole; this closes it. */
export function own<T>(table: Record<string, T>, key: string): T | undefined {
  return Object.prototype.hasOwnProperty.call(table, key) ? table[key] : undefined;
}

/**
 * An object written as `{...}`, not a list and not null: what the AI side's
 * review means by a `Mapping`. Ask this where page_review.py asks
 * `isinstance(..., Mapping)`, so the two read the same shapes as a dict. Five
 * older tests still admit a list where the AI side does not (styleFinding's
 * style, masterBox's and reviewMasterElement's placements,
 * statesHaveWords' states, matrixFindings' configured test); moving them here
 * changes what a list does, so each waits for its own change.
 */
export function isPlainObject(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}
