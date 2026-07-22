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
  return resp.address ?? resp.match ?? "";
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
  // OSC responses use mappings + address; child_set rides along (the id is
  // an address segment / literal there — no capture groups). A child_set-only
  // rule keeps its YAML clean: no empty mappings key.
  if (original.address !== undefined) {
    const oscMappings =
      mappings.length === 0 && original.mappings === undefined
        ? {}
        : { mappings };
    return { address: pattern, ...oscMappings, ...childSet, ...carry };
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

// ── json: true rules ──
// A json rule parses the whole reply body as a JSON object; every set /
// mappings entry reads one field from it (dot path). The runtime half is
// build_json_mappings + _apply_json_responses (compiled_protocol.py /
// configurable.py) — these helpers mirror the exact shapes it accepts.

/** One editable row of a `json: true` rule: state variable ← JSON field. */
export interface JsonRuleRow {
  /** Target state variable. */
  state: string;
  /** JSON field to read: dot-separated keys / list indices ("status.power"). */
  path: string;
  /** Effective coercion type (what the runtime will actually apply). */
  type: string;
  /** Optional lookup table translating raw values to friendly values. */
  map?: Record<string, string>;
}

/** True when two coercion type names behave identically at runtime: the
 *  coercers fold "number" into the float branch and treat "enum" like
 *  "string" (coerce_json_value / coerce_value in compiled_protocol.py).
 *  Used to pick the minimal serialization without changing behavior. */
export function coercionTypesEquivalent(a: string, b: string): boolean {
  const norm = (t: string) =>
    t === "number" ? "float" : t === "enum" ? "string" : t;
  return norm(a) === norm(b);
}

/** Read the rows of a `json: true` rule, mirroring the runtime's
 *  build_json_mappings: a non-empty `mappings` list wins (entries carry
 *  {state, key, type?, map?}; type defaults to "string" there); otherwise
 *  each `set` entry is a string JSON path or a {key|path, type, map} spec
 *  whose path defaults to the state name and whose type defaults to the
 *  state variable's DECLARED type. */
export function getJsonRows(
  resp: DriverResponseDef,
  stateVariables: Record<string, StateVarDefLike | undefined>,
): JsonRuleRow[] {
  const rows: JsonRuleRow[] = [];
  const mappings = resp.mappings;
  if (Array.isArray(mappings) && mappings.length > 0) {
    for (const m of mappings) {
      const entry = m as { state?: string; key?: unknown; type?: string; map?: Record<string, string> };
      rows.push({
        state: entry.state ?? "",
        path: entry.key == null ? "" : String(entry.key),
        type: entry.type ?? "string",
        ...(entry.map ? { map: entry.map } : {}),
      });
    }
    return rows;
  }
  const set = resp.set;
  if (!set || typeof set !== "object" || Array.isArray(set)) return rows;
  for (const [state, spec] of Object.entries(set)) {
    const declared = declaredStateType(stateVariables, state);
    if (spec !== null && typeof spec === "object" && !Array.isArray(spec)) {
      const s = spec as { key?: unknown; path?: unknown; type?: unknown; map?: unknown };
      const path = s.key ?? s.path ?? state;
      rows.push({
        state,
        path: String(path),
        type: typeof s.type === "string" ? s.type : declared,
        ...(s.map && typeof s.map === "object"
          ? { map: s.map as Record<string, string> }
          : {}),
      });
    } else {
      rows.push({ state, path: String(spec), type: declared });
    }
  }
  return rows;
}

/** Rebuild a `json: true` rule from its edited rows + require keys, always
 *  choosing the minimal serialization: a row with no value map whose type
 *  matches the declared state type becomes the string form
 *  (`set: {var: "path"}`); a row needing a type override or map becomes a
 *  {key, type?, map?} object; blank/duplicate state names (which a set map
 *  can't carry) fall back to the explicit mappings list with type spelled
 *  out (the mappings form defaults to "string", not the declared type).
 *  Unknown keys on the original rule (and throttle) ride through verbatim;
 *  child_set is dropped — the runtime rejects it on json rules. */
export function buildJsonResponse(
  original: DriverResponseDef,
  rows: JsonRuleRow[],
  requireKeys: string[],
  stateVariables: Record<string, StateVarDefLike | undefined>,
): DriverResponseDef {
  const next: DriverResponseDef = { ...original, json: true };
  delete next.match;
  delete next.address;
  delete next.set;
  delete next.mappings;
  delete next.require;
  delete next.child_set;

  if (requireKeys.length === 1) next.require = requireKeys[0];
  else if (requireKeys.length > 1) next.require = [...requireKeys];

  const canUseSet =
    rows.every((r) => r.state) &&
    new Set(rows.map((r) => r.state)).size === rows.length;
  if (canUseSet) {
    const set: Record<string, unknown> = {};
    for (const r of rows) {
      const declared = declaredStateType(stateVariables, r.state);
      const hasMap = r.map !== undefined && Object.keys(r.map).length > 0;
      if (!hasMap && coercionTypesEquivalent(r.type, declared)) {
        set[r.state] = r.path;
      } else {
        set[r.state] = {
          key: r.path,
          ...(coercionTypesEquivalent(r.type, declared) ? {} : { type: r.type }),
          ...(hasMap ? { map: r.map } : {}),
        };
      }
    }
    next.set = set;
  } else {
    next.mappings = rows.map((r) => ({
      state: r.state,
      key: r.path,
      type: r.type,
      ...(r.map && Object.keys(r.map).length > 0 ? { map: r.map } : {}),
    })) as unknown as DriverResponseMapping[];
  }
  return next;
}

/** The require: scope as a list for editing (string → one entry). */
export function requireToList(require: unknown): string[] {
  if (typeof require === "string") return require.trim() ? [require] : [];
  if (Array.isArray(require)) {
    return require.map((k) => String(k)).filter((k) => k.trim());
  }
  return [];
}

/** Parse the comma-separated require input into clean key names. */
export function parseRequireText(text: string): string[] {
  return text
    .split(",")
    .map((k) => k.trim())
    .filter((k) => k);
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

/** The text shown in the child_set ID input: the long form {group, map}
 *  renders as its capture ref ("$1"); plain forms render verbatim. */
export function childIdToText(id: unknown): string {
  if (id !== null && typeof id === "object") {
    const group = (id as { group?: unknown }).group;
    const text = String(group ?? "");
    return text.startsWith("$") ? text : `$${text}`;
  }
  return String(id ?? "");
}

/** The wire-id map carried by a long-form child_set id, if any. */
export function childIdMap(
  id: unknown,
): Record<string, string | number> | undefined {
  if (id !== null && typeof id === "object") {
    const map = (id as { map?: unknown }).map;
    if (map !== null && typeof map === "object") {
      return map as Record<string, string | number>;
    }
  }
  return undefined;
}

/** Rebuild a child_set id from the ID input's text + the map rows. A capture
 *  ref with map rows becomes the long form {group, map}; a capture ref
 *  without rows stays "$N"; anything else is a literal (numeric text becomes
 *  a number) and the map — meaningless for a literal — is dropped. */
export function childIdFromParts(
  text: string,
  map: Record<string, string | number> | undefined,
): string | number | { group: number; map: Record<string, string | number> } {
  const refMatch = /^\$(\d+)$/.exec(text.trim());
  if (refMatch && map && Object.keys(map).length > 0) {
    return { group: parseInt(refMatch[1], 10), map };
  }
  if (refMatch) return text.trim();
  return /^\d+$/.test(text.trim()) ? parseInt(text.trim(), 10) : text;
}

/** The text shown in an OSC child_set ID input: the long form
 *  {segment, map} renders as "seg:N" (a 0-based index into the /-split
 *  address); plain literals render verbatim. */
export function oscChildIdToText(id: unknown): string {
  if (id !== null && typeof id === "object") {
    const segment = (id as { segment?: unknown }).segment;
    return `seg:${String(segment ?? "")}`;
  }
  return String(id ?? "");
}

/** Rebuild an OSC child_set id from the ID input's text + the map rows.
 *  "seg:N" becomes {segment: N} (carrying the map when rows exist);
 *  anything else is a literal (numeric text becomes a number) and the map —
 *  meaningless for a literal — is dropped. */
export function oscChildIdFromParts(
  text: string,
  map: Record<string, string | number> | undefined,
):
  | string
  | number
  | { segment: number; map?: Record<string, string | number> } {
  const segMatch = /^seg:(\d+)$/.exec(text.trim());
  if (segMatch) {
    const segment = parseInt(segMatch[1], 10);
    return map && Object.keys(map).length > 0 ? { segment, map } : { segment };
  }
  return /^\d+$/.test(text.trim()) ? parseInt(text.trim(), 10) : text;
}

/** The text shown in an OSC child_set property input: {arg: N} renders as
 *  "arg:N" (a 0-based positional OSC arg); {value: X} and plain literals
 *  render as the value text. */
export function oscChildPropToText(expr: unknown): string {
  if (expr !== null && typeof expr === "object") {
    const arg = (expr as { arg?: unknown }).arg;
    if (arg !== undefined) return `arg:${String(arg)}`;
    const value = (expr as { value?: unknown }).value;
    return String(value ?? "");
  }
  return String(expr ?? "");
}

/** Rebuild an OSC child_set property from its input text: "arg:N" becomes
 *  {arg: N}, preserving a value map the original expression carried (the
 *  editor has no map rows for props; edits must not drop them); anything
 *  else is a static literal. */
export function oscChildPropFromText(text: string, original: unknown): unknown {
  const argMatch = /^arg:(\d+)$/.exec(text.trim());
  if (argMatch) {
    const arg = parseInt(argMatch[1], 10);
    if (original !== null && typeof original === "object") {
      const map = (original as { map?: unknown }).map;
      if (map !== null && typeof map === "object") {
        return { arg, map };
      }
    }
    return { arg };
  }
  return text;
}
