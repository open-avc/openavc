// Pure logic for the Response Builder, split out so it can be unit tested
// without React (see openavc/tests/test_response_builder_helpers.py). The
// editor component imports these; keep this file free of React/DOM imports.
// Mirrors configSchemaHelpers.ts / deviceSettingsHelpers.ts.

import type {
  DriverResponseDef,
  DriverResponseMapping,
} from "../../api/types";

export interface RenameResult {
  ok: boolean;
  reason?: string;
}

/** Minimal shape of a state-variable definition the helpers need. */
export interface StateVarDefLike {
  type?: string;
}

/** The runtime type a `set:` shorthand entry coerces to: the target state
 *  variable's declared type, "string" for undeclared variables. Mirrors the
 *  runtime's shorthand compile step (configurable.py). */
export function declaredStateType(
  stateVariables: Record<string, StateVarDefLike | undefined>,
  stateKey: string,
): string {
  return stateVariables[stateKey]?.type ?? "string";
}

/** Read the pattern from whichever key is present. */
export function getPattern(resp: DriverResponseDef): string {
  return resp.address ?? resp.pattern ?? resp.match ?? "";
}

/** Read mappings, converting set: shorthand if needed. The runtime coerces a
 *  shorthand entry — capture reference or static literal — by the target
 *  state variable's DECLARED type, so the converted mapping carries that type
 *  rather than a hardcoded "string" (which misrepresented how the response is
 *  parsed). Static literal values are preserved verbatim (so round-trip
 *  doesn't lose them on edit). */
export function getMappings(
  resp: DriverResponseDef,
  stateVariables: Record<string, StateVarDefLike | undefined>,
): DriverResponseMapping[] {
  if (resp.mappings) return resp.mappings;
  if (!resp.set) return [];
  const mappings: DriverResponseMapping[] = [];
  for (const [stateKey, valueExpr] of Object.entries(resp.set)) {
    const type = declaredStateType(stateVariables, stateKey);
    if (typeof valueExpr === "string" && /^\$\d+$/.test(valueExpr)) {
      // Capture-group reference like "$1"
      const group = parseInt(valueExpr.slice(1), 10);
      mappings.push({ group, state: stateKey, type });
    } else {
      // Static literal — preserve the value verbatim under `value`
      mappings.push({ group: 0, state: stateKey, value: valueExpr, type });
    }
  }
  return mappings;
}

/** True if every mapping fits the `set:` shorthand — i.e. writing the set:
 *  form would leave runtime behavior identical. A shorthand entry coerces by
 *  the state variable's declared type, while an explicit mapping coerces by
 *  its own `type` (default "string"), so a row only fits when its effective
 *  type equals the declared type — otherwise a type the author chose would be
 *  silently discarded on save. Rows with `map`/`arg` extras never fit. */
export function canUseSetShorthand(
  mappings: DriverResponseMapping[],
  stateVariables: Record<string, StateVarDefLike | undefined>,
): boolean {
  if (mappings.length === 0) return false;
  const seenStates = new Set<string>();
  for (const m of mappings) {
    if (!m.state) return false;
    if (seenStates.has(m.state)) return false;
    seenStates.add(m.state);
    if (m.arg !== undefined) return false;
    if (m.map !== undefined) return false;
    if ((m.type ?? "string") !== declaredStateType(stateVariables, m.state)) {
      return false;
    }
    // Static literal mapping: group=0, value present
    if (m.group === 0 && m.value !== undefined) continue;
    // Capture-group mapping
    if (m.group > 0) continue;
    return false;
  }
  return true;
}

/** Build a response def, preserving the original form (set: shorthand or
 *  mappings:) of the loaded response when the new mappings still fit.
 *  `child_set`, `throttle`, and the json-rule keys (`json`, `require`) ride
 *  along untouched — rebuilding from a pattern/mapping edit must never drop
 *  the child routing, the rate limit, or the rule's body-parsing mode. */
export function buildResponse(
  pattern: string,
  mappings: DriverResponseMapping[],
  original: DriverResponseDef,
  stateVariables: Record<string, StateVarDefLike | undefined>,
): DriverResponseDef {
  const childSet = original.child_set?.length
    ? { child_set: original.child_set }
    : {};
  const carry = {
    ...(original.throttle !== undefined ? { throttle: original.throttle } : {}),
    ...(original.json !== undefined ? { json: original.json } : {}),
    ...(original.require !== undefined ? { require: original.require } : {}),
  };
  // OSC responses always use mappings + address (no child_set — the loader
  // rejects it there; throttle is valid on any response kind).
  if (original.address !== undefined) {
    return { address: pattern, mappings, ...carry };
  }
  // A child_set-only response keeps its YAML clean: no empty mappings key.
  if (mappings.length === 0 && original.mappings === undefined && original.set === undefined) {
    return { match: pattern, ...childSet, ...carry };
  }
  // A json rule has no match pattern — its set values are JSON keys, not
  // capture refs. Preserve it verbatim apart from the edited mappings.
  if (original.json) {
    const base: DriverResponseDef = { ...original };
    delete base.match;
    delete base.pattern;
    return { ...base, ...carry };
  }
  // Choose set: shorthand when (a) the original used it AND (b) the
  // current mapping shape still fits the shorthand. Otherwise fall back
  // to the explicit mappings form.
  const originalWasSet = original.set !== undefined && original.mappings === undefined;
  if (originalWasSet && canUseSetShorthand(mappings, stateVariables)) {
    const set: Record<string, unknown> = {};
    for (const m of mappings) {
      if (m.group === 0 && m.value !== undefined) {
        set[m.state] = m.value;
      } else {
        set[m.state] = `$${m.group}`;
      }
    }
    return { match: pattern, set, ...childSet, ...carry };
  }
  return { match: pattern, mappings, ...childSet, ...carry };
}

/** Validate renaming a value-map raw key against its sibling keys. Renaming
 *  onto an existing key would merge the two rows in the backing record,
 *  silently dropping one, so it's rejected instead. */
export function checkValueMapKeyRename(
  next: string,
  current: string,
  existing: string[],
): RenameResult {
  if (next === current) return { ok: true };
  if (!next) return { ok: false, reason: "Raw value can't be empty." };
  if (existing.includes(next)) {
    return { ok: false, reason: `"${next}" is already mapped.` };
  }
  return { ok: true };
}

/** Rename a value-map key, preserving entry order. Callers validate with
 *  checkValueMapKeyRename first. */
export function renameValueMapKey(
  map: Record<string, string>,
  oldKey: string,
  newKey: string,
): Record<string, string> {
  const next: Record<string, string> = {};
  for (const [k, v] of Object.entries(map)) {
    next[k === oldKey ? newKey : k] = v;
  }
  return next;
}

/** Add a blank draft row ("" → "") unless one is already pending — spreading
 *  a second "" key into the record would silently reset the first draft's
 *  value. Returns null when a draft row already exists. */
export function addValueMapEntry(
  map: Record<string, string>,
): Record<string, string> | null {
  if ("" in map) return null;
  return { ...map, "": "" };
}
