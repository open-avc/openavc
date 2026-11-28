// Pure logic for the Response Builder, split out so it can be unit tested
// without React (see openavc/tests/test_response_builder_helpers.py). The
// editor component imports these; keep this file free of React/DOM imports.
// Mirrors configSchemaHelpers.ts / deviceSettingsHelpers.ts.

import type {
  DriverChildListEntry,
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

/** The types a reply mapping's Type picker offers. */
export const MAPPING_TYPES = ["string", "integer", "float", "number", "boolean"] as const;

/** The type the driver reads a text or OSC rule's row by (reply_rule.py):
 *  its own `type`, else its state variable's declared type. One the picker
 *  does not offer (an enum variable's, or a hand-written name) is read as
 *  text, so it shows as String. */
export function mappingType(
  mapping: DriverResponseMapping,
  stateVariables: Record<string, StateVarDefLike | undefined>,
): string {
  const type = mapping.type ?? declaredStateType(stateVariables, mapping.state ?? "");
  return (MAPPING_TYPES as readonly string[]).includes(type) ? type : "string";
}

/** Read the pattern from whichever key is present. */
export function getPattern(resp: DriverResponseDef): string {
  return resp.address ?? resp.match ?? "";
}

/** Read a rule's rows as the driver reads them (reply_rule.py): a text
 *  rule's `mappings:` entries first, then its `set:` shorthand's items, a
 *  `set:` item for a state variable a `mappings:` entry names left out (the
 *  `mappings:` entry wins). An OSC rule's rows are its `mappings:`, else its
 *  `set:`. The runtime coerces a shorthand entry — capture reference or
 *  static literal — by the target state variable's DECLARED type, so the
 *  converted row carries that type rather than a hardcoded "string" (which
 *  misrepresented how the response is parsed). Static literal values are
 *  preserved verbatim (so round-trip doesn't lose them on edit). */
export function getMappings(
  resp: DriverResponseDef,
  stateVariables: Record<string, StateVarDefLike | undefined>,
): DriverResponseMapping[] {
  if (resp.address !== undefined) {
    return resp.mappings ?? setRows(resp.set, stateVariables, new Set());
  }
  const listed = Array.isArray(resp.mappings) ? resp.mappings : [];
  return [...listed, ...setRows(resp.set, stateVariables, mappedStates(listed))];
}

/** The state variables a rule's `mappings:` entries name. */
function mappedStates(mappings: DriverResponseMapping[] | undefined): Set<string> {
  return new Set((mappings ?? []).map((m) => m.state).filter((state): state is string => !!state));
}

/** A `set:` shorthand's items as rows, typed by their state variables, but
 *  for the states in `leftOut`. */
function setRows(
  set: DriverResponseDef["set"],
  stateVariables: Record<string, StateVarDefLike | undefined>,
  leftOut: Set<string>,
): DriverResponseMapping[] {
  const mappings: DriverResponseMapping[] = [];
  for (const [stateKey, valueExpr] of Object.entries(set ?? {})) {
    if (leftOut.has(stateKey)) continue;
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

/** The capture a regex rule's mapping reads, as the driver reads it: its
 *  `group`, else the whole match (0). */
export function mappingGroup(mapping: { group?: number }): number {
  return mapping.group ?? 0;
}

/** The capture group a mapping added to a regex rule takes: the one after
 *  the highest any of its mappings reads, else the first. */
export function nextMappingGroup(mappings: { group?: number }[]): number {
  return mappings.length > 0 ? Math.max(...mappings.map(mappingGroup)) + 1 : 1;
}

/** The message argument an OSC rule's mapping reads, as the driver reads it:
 *  its `arg`, else the first (0). An OSC mapping's `group` is not read. */
export function mappingArg(mapping: { arg?: number }): number {
  return mapping.arg ?? 0;
}

/** The argument a mapping added to an OSC rule takes: the one after the
 *  highest any of its mappings reads, else the first. */
export function nextMappingArg(mappings: { arg?: number }[]): number {
  return mappings.length > 0 ? Math.max(...mappings.map(mappingArg)) + 1 : 0;
}

/** True if every mapping fits the `set:` shorthand — i.e. writing the set:
 *  form would leave runtime behavior identical. A shorthand entry coerces by
 *  the state variable's declared type, while an explicit mapping coerces by
 *  its own `type` (default "string"), so a row only fits when its effective
 *  type equals the declared type — otherwise a type the author chose would be
 *  silently discarded on save. Rows with `map`/`arg`/`json_path` extras never
 *  fit — the shorthand has nowhere to put them, so choosing it would drop
 *  what the author just typed. */
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
    if (m.json_path !== undefined) return false;
    if ((m.type ?? "string") !== declaredStateType(stateVariables, m.state)) {
      return false;
    }
    // Static literal mapping: group=0, value present
    if (m.group === 0 && m.value !== undefined) continue;
    // Capture-group mapping
    if (mappingGroup(m) > 0) continue;
    return false;
  }
  return true;
}

/** The `set:` shorthand of mappings that fit it (`canUseSetShorthand`): a
 *  literal value as written, a capture as its `$N` reference. */
function setShorthand(mappings: DriverResponseMapping[]): Record<string, unknown> {
  const set: Record<string, unknown> = {};
  for (const m of mappings) {
    if (!m.state) continue; // the driver reads no mapping without a state
    set[m.state] = m.group === 0 && m.value !== undefined ? m.value : `$${mappingGroup(m)}`;
  }
  return set;
}

/** The rows of a text rule that carried both forms, written back into both:
 *  a row goes back to `set:` when its state variable was a `set:` item (and
 *  no `mappings:` entry named it), no other row writes that variable, and it
 *  still fits the shorthand on its own; every other row is a `mappings:`
 *  entry. So saving an edit keeps the file's own split, the one the driver
 *  guide recommends: the mapped fields in `mappings:`, the plain ones in
 *  `set:`. A form left with no rows is left out. */
function bothForms(
  mappings: DriverResponseMapping[],
  original: DriverResponseDef,
  stateVariables: Record<string, StateVarDefLike | undefined>,
): Pick<DriverResponseDef, "mappings" | "set"> {
  const mapped = mappedStates(original.mappings);
  const fromSet = new Set(Object.keys(original.set ?? {}).filter((state) => !mapped.has(state)));
  const rowsFor = new Map<string, number>();
  for (const m of mappings) {
    if (m.state) rowsFor.set(m.state, (rowsFor.get(m.state) ?? 0) + 1);
  }
  const backToSet = (m: DriverResponseMapping) =>
    !!m.state &&
    fromSet.has(m.state) &&
    rowsFor.get(m.state) === 1 &&
    canUseSetShorthand([m], stateVariables);
  const plain = mappings.filter(backToSet);
  const listed = mappings.filter((m) => !backToSet(m));
  return {
    ...(listed.length > 0 ? { mappings: listed } : {}),
    ...(plain.length > 0 ? { set: setShorthand(plain) } : {}),
  };
}

/** Build a response def, preserving the original form (set: shorthand,
 *  mappings:, or both) of the loaded response when the new mappings still fit.
 *  `child_set`, `throttle`, `after_json`, `then_query`, and the json-rule
 *  keys (`json`, `require`) ride along untouched — rebuilding from a
 *  pattern/mapping edit must never drop the child routing, the rate limit,
 *  the rule's place in the json/regex order, its queries, or its
 *  body-parsing mode. */
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
    ...(original.after_json !== undefined
      ? { after_json: original.after_json }
      : {}),
    ...(original.only_when !== undefined ? { only_when: original.only_when } : {}),
    ...(original.child_list !== undefined
      ? { child_list: original.child_list }
      : {}),
    ...(original.then_query !== undefined
      ? { then_query: original.then_query }
      : {}),
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
  // A rule that carried both forms keeps both (the driver applies both).
  if (original.set !== undefined && original.mappings !== undefined) {
    return { match: pattern, ...bothForms(mappings, original, stateVariables), ...childSet, ...carry };
  }
  // Choose set: shorthand when (a) the original used it AND (b) the
  // current mapping shape still fits the shorthand. Otherwise fall back
  // to the explicit mappings form.
  const originalWasSet = original.set !== undefined && original.mappings === undefined;
  if (originalWasSet && canUseSetShorthand(mappings, stateVariables)) {
    return { match: pattern, set: setShorthand(mappings), ...childSet, ...carry };
  }
  return { match: pattern, mappings, ...childSet, ...carry };
}

// ── json: true rules ──
// A json rule parses the whole reply body as a JSON object; every set /
// mappings entry reads one field from it (dot path). The runtime half is
// reply_rule.read_reply_rule + _apply_json_responses (reply_rule.py /
// configurable.py) — these helpers mirror the exact shapes it accepts.

/** One editable row of a `json: true` rule: state variable ← JSON field. */
export interface JsonRuleRow {
  /** Target state variable. */
  state: string;
  /** JSON field to read: dot-separated keys / list indices ("status.power"). */
  path: string;
  /** Effective coercion type (what the runtime will actually apply). */
  type: string;
  /** Optional lookup table translating raw values to friendly values (a
   *  driver file may map to a number or a boolean as well as text). */
  map?: Record<string, unknown>;
  /** Optional: store whether the value at `path` holds this (membership
   *  in an array, a key of an object, a substring) instead of the value
   *  itself -- how an array of flag names becomes one boolean per flag.
   *  Kept as the raw scalar the YAML carries. */
  contains?: string | number | boolean;
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
 *  reading of a json rule (reply_rule.py): a non-empty `mappings` list
 *  wins (entries carry {state, key, type?, map?}, the field named by `key`
 *  alone); otherwise each `set` entry is a string JSON path or a
 *  {key|path, type, map} spec whose path defaults to the state name. Either
 *  way the type defaults to the state variable's DECLARED type. */
export function getJsonRows(
  resp: DriverResponseDef,
  stateVariables: Record<string, StateVarDefLike | undefined>,
): JsonRuleRow[] {
  const rows: JsonRuleRow[] = [];
  const mappings = resp.mappings;
  if (Array.isArray(mappings) && mappings.length > 0) {
    for (const m of mappings) {
      const entry = m as {
        state?: string;
        key?: unknown;
        type?: string;
        map?: Record<string, string>;
        contains?: unknown;
      };
      rows.push({
        state: entry.state ?? "",
        path: entry.key == null ? "" : String(entry.key),
        type: entry.type ?? declaredStateType(stateVariables, entry.state ?? ""),
        ...(entry.map ? { map: entry.map } : {}),
        ...(isContainsScalar(entry.contains) ? { contains: entry.contains } : {}),
      });
    }
    return rows;
  }
  const set = resp.set;
  if (!set || typeof set !== "object" || Array.isArray(set)) return rows;
  for (const [state, spec] of Object.entries(set)) {
    const declared = declaredStateType(stateVariables, state);
    if (spec !== null && typeof spec === "object" && !Array.isArray(spec)) {
      const s = spec as {
        key?: unknown;
        path?: unknown;
        type?: unknown;
        map?: unknown;
        contains?: unknown;
      };
      const path = s.key ?? s.path ?? state;
      rows.push({
        state,
        path: String(path),
        type: typeof s.type === "string" ? s.type : declared,
        ...(s.map && typeof s.map === "object"
          ? { map: s.map as Record<string, string> }
          : {}),
        ...(isContainsScalar(s.contains) ? { contains: s.contains } : {}),
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
 *  out.
 *  Unknown keys on the original rule (and throttle) ride through verbatim,
 *  child_set included — a json rule routes to children by literal id and
 *  JSON path, so an edit to the field rows must not drop it. after_json is
 *  dropped (a json rule reads the body itself; there is nothing to run
 *  after). */
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
  delete next.after_json;

  if (requireKeys.length === 1) next.require = requireKeys[0];
  else if (requireKeys.length > 1) next.require = [...requireKeys];

  // A rule that only routes to children keeps its YAML clean — no empty
  // set: key — the way a child_set-only regex rule grows no empty mappings.
  if (rows.length === 0 && (next.child_set?.length ?? 0) > 0) return next;

  const canUseSet =
    rows.every((r) => r.state) &&
    new Set(rows.map((r) => r.state)).size === rows.length;
  if (canUseSet) {
    const set: Record<string, unknown> = {};
    for (const r of rows) {
      const declared = declaredStateType(stateVariables, r.state);
      const hasMap = r.map !== undefined && Object.keys(r.map).length > 0;
      const hasContains = isContainsScalar(r.contains);
      if (!hasMap && !hasContains && coercionTypesEquivalent(r.type, declared)) {
        set[r.state] = r.path;
      } else {
        set[r.state] = {
          key: r.path,
          ...(coercionTypesEquivalent(r.type, declared) ? {} : { type: r.type }),
          ...(hasMap ? { map: r.map } : {}),
          ...(hasContains ? { contains: r.contains } : {}),
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
      ...(isContainsScalar(r.contains) ? { contains: r.contains } : {}),
    })) as unknown as DriverResponseMapping[];
  }
  return next;
}

/** A `contains` value is a scalar to look for; anything else is not one the
 *  runtime reads (the validator refuses it), so the editor drops it. */
export function isContainsScalar(
  value: unknown,
): value is string | number | boolean {
  return (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  );
}

/** Read the `contains` text of a row for its input (a number or boolean
 *  renders as its literal; absent renders blank). */
export function containsToText(value: unknown): string {
  return isContainsScalar(value) ? String(value) : "";
}

/** Rebuild a `contains` value from its input: blank clears it; `true` /
 *  `false` and plain numbers become the JSON scalar they read as (a
 *  device that reports `[1, 3]` is asked for the number 1, not "1");
 *  anything else stays a string. */
export function containsFromText(
  text: string,
): string | number | boolean | undefined {
  const t = text.trim();
  if (t === "") return undefined;
  if (t === "true") return true;
  if (t === "false") return false;
  if (/^-?\d+(\.\d+)?$/.test(t)) return Number(t);
  return t;
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
export function renameValueMapKey<V>(
  map: Record<string, V>,
  oldKey: string,
  newKey: string,
): Record<string, V> {
  const next: Record<string, V> = {};
  for (const [k, v] of Object.entries(map)) {
    next[k === oldKey ? newKey : k] = v;
  }
  return next;
}

/** Add a blank draft row ("" → "") unless one is already pending — spreading
 *  a second "" key into the record would silently reset the first draft's
 *  value. Returns null when a draft row already exists. */
export function addValueMapEntry<V>(
  map: Record<string, V | string>,
): Record<string, V | string> | null {
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

/** The text shown in a json child_set property input: a {key, type, map}
 *  spec renders as its JSON path; a plain path renders verbatim. */
export function jsonChildPropToText(expr: unknown): string {
  if (expr !== null && typeof expr === "object") {
    const spec = expr as { key?: unknown; path?: unknown };
    return String(spec.key ?? spec.path ?? "");
  }
  return String(expr ?? "");
}

/** Rebuild a json child_set property from its input text: the path alone
 *  when nothing else is carried, otherwise {key, ...} keeping the type
 *  override, value map and `contains` the original spec had (the editor has
 *  no rows for them; editing the path must not drop them). */
export function jsonChildPropFromText(text: string, original: unknown): unknown {
  const key = text.trim();
  if (original !== null && typeof original === "object") {
    const spec = original as { type?: unknown; map?: unknown; contains?: unknown };
    const extra: Record<string, unknown> = {};
    if (typeof spec.type === "string") extra.type = spec.type;
    if (spec.map !== null && typeof spec.map === "object") extra.map = spec.map;
    if (isContainsScalar(spec.contains)) extra.contains = spec.contains;
    if (Object.keys(extra).length > 0) return { key, ...extra };
  }
  return key;
}

// ── child_list (a list in one reply, spread across children) ──
// The runtime half is compile_child_list + ConfigurableDriver._resolve_child_list
// (child_routes.py / text_replies.py). `group` and `start` count the
// rule's own captures; `id` and every state value count the item's (element).

/** A capture-ref-or-number input (`group`, `start`): "$2" stays a capture
 *  ref, a whole number becomes a number, blank means the default. */
export function refOrNumberFromText(text: string): string | number | undefined {
  const trimmed = text.trim();
  if (!trimmed) return undefined;
  if (/^\$\d+$/.test(trimmed)) return trimmed;
  if (/^-?\d+$/.test(trimmed)) return parseInt(trimmed, 10);
  return trimmed;
}

/** The text shown for a child_list state value: "$1" for a capture (with or
 *  without a value map), the literal otherwise. */
export function childListValueToText(expr: unknown): string {
  if (expr !== null && typeof expr === "object") {
    const group = (expr as { group?: unknown }).group;
    if (group !== undefined) {
      const text = String(group);
      return text.startsWith("$") ? text : `$${text}`;
    }
    const value = (expr as { value?: unknown }).value;
    return String(value ?? "");
  }
  return String(expr ?? "");
}

/** The value map on a child_list state value, if it has one. */
export function childListValueMap(
  expr: unknown,
): Record<string, string> | undefined {
  if (expr !== null && typeof expr === "object") {
    const map = (expr as { map?: unknown }).map;
    if (map !== null && typeof map === "object") {
      const out: Record<string, string> = {};
      for (const [k, v] of Object.entries(map as Record<string, unknown>)) {
        out[k] = String(v);
      }
      return out;
    }
  }
  return undefined;
}

/** Rebuild a child_list state value from its text and value map: a capture
 *  ref with map rows becomes {group, map}; a capture ref alone stays "$N";
 *  anything else is a literal, and a map means nothing for a literal. */
export function childListValueFromParts(
  text: string,
  map: Record<string, string> | undefined,
): unknown {
  const refMatch = /^\$(\d+)$/.exec(text.trim());
  if (refMatch && map && Object.keys(map).length > 0) {
    return { group: parseInt(refMatch[1], 10), map };
  }
  if (refMatch) return text.trim();
  return text;
}

/** A rule with its child_list replaced, or removed when `list` is undefined. */
export function withChildList(
  resp: DriverResponseDef,
  list: DriverChildListEntry | undefined,
): DriverResponseDef {
  const next: DriverResponseDef = { ...resp };
  if (list) next.child_list = list;
  else delete next.child_list;
  return next;
}

/** A rule with its then_query replaced, or removed when the list is empty
 *  (the validator refuses an empty one). */
export function withThenQuery(
  resp: DriverResponseDef,
  queries: NonNullable<DriverResponseDef["then_query"]>,
): DriverResponseDef {
  const next: DriverResponseDef = { ...resp };
  if (queries.length) next.then_query = queries;
  else delete next.then_query;
  return next;
}

/** The child type `{child_id}` names in a then_query sent once: the one type
 *  this rule's child_set writes. Undefined when it writes none, or children
 *  of more than one type. */
export function routedChildType(resp: DriverResponseDef): string | undefined {
  const types = new Set(
    (resp.child_set ?? [])
      .map((entry) => entry?.type)
      .filter((t): t is string => typeof t === "string" && t !== ""),
  );
  return types.size === 1 ? [...types][0] : undefined;
}
