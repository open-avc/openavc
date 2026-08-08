// Pure logic for the state-variable editor, split out so it can be unit
// tested without React (see openavc/tests/test_state_variable_helpers.py).
// The editor component imports these; keep this file free of React/DOM imports.
// Mirrors childEntityTypesHelpers.ts, which backs the same editing surface for
// child-entity state variables.

/** Smallest `variable_N` not already present — never overwrites an existing variable. */
export function nextStateVariableName(existing: string[]): string {
  let counter = existing.length + 1;
  let name = `variable_${counter}`;
  while (existing.includes(name)) {
    counter++;
    name = `variable_${counter}`;
  }
  return name;
}

const NUMERIC_ONLY_FIELDS = ["min", "max", "step", "unit"] as const;

/**
 * Compute the updated state-var def when its `type` changes, as a SINGLE
 * object so the write is atomic. Drops numeric bounds when leaving
 * integer/number/float and enum values when leaving enum. Replaces the old
 * sequence of updateVariable() calls that each read a stale snapshot and
 * clobbered the type back to its previous value.
 */
export function applyStateVarTypeChange<T extends { type: string }>(
  current: T,
  newType: string,
): T {
  const next = { ...current, type: newType } as Record<string, unknown>;
  if (newType !== "integer" && newType !== "number" && newType !== "float") {
    for (const f of NUMERIC_ONLY_FIELDS) delete next[f];
  }
  if (newType !== "enum") {
    delete next.values;
  }
  return next as unknown as T;
}
