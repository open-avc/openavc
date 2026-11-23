// Pure logic for the Child Entity Types editor, split out so it can be unit
// tested without React (see openavc/tests/test_child_entity_types_helpers.py).
// The editor component imports these; keep this file free of React/DOM imports.
import type { DriverChildInstances, DriverChildStateVarDef } from "../../api/types";

export const CHILD_TYPE_ID_RE = /^[a-z][a-z0-9_]*$/;

export interface RenameResult {
  ok: boolean;
  reason?: string;
}

/** Sanitize raw input into a legal child-field id (lowercase alnum + underscore). */
export function sanitizeFieldId(raw: string): string {
  return raw.replace(/[^a-zA-Z0-9_]/g, "").toLowerCase();
}

/** Sanitize raw input into a legal child-type id (lowercase alnum + underscore). */
export function sanitizeTypeId(raw: string): string {
  return raw.replace(/[^a-z0-9_]/gi, "").toLowerCase();
}

/**
 * Validate renaming `current` to `cleaned` against the sibling `existing` keys.
 * Returns ok:true when the rename should apply (a no-op where cleaned === current
 * is also ok, so the caller just re-syncs the input), or ok:false + a reason to
 * surface inline when it must be rejected.
 */
export function checkRename(
  cleaned: string,
  current: string,
  existing: string[],
): RenameResult {
  if (!cleaned) return { ok: false, reason: "ID can't be empty." };
  if (cleaned === current) return { ok: true };
  if (existing.includes(cleaned)) {
    return { ok: false, reason: `"${cleaned}" already exists.` };
  }
  return { ok: true };
}

/** Smallest `field_N` not already present — never overwrites an existing field. */
export function nextChildFieldId(existing: string[]): string {
  let counter = existing.length + 1;
  let name = `field_${counter}`;
  while (existing.includes(name)) {
    counter++;
    name = `field_${counter}`;
  }
  return name;
}

/** Smallest `child_type_N` not already present — never overwrites an existing type. */
export function nextChildTypeId(existing: string[]): string {
  let counter = existing.length + 1;
  let name = `child_type_${counter}`;
  while (existing.includes(name)) {
    counter++;
    name = `child_type_${counter}`;
  }
  return name;
}

const NUMERIC_ONLY_FIELDS = ["min", "max", "step", "unit", "scale", "offset", "unknown"] as const;

/**
 * Compute the updated child state-var def when its `type` changes, as a SINGLE
 * object so the write is atomic. Drops numeric bounds when leaving
 * integer/number/float and enum values when leaving enum. Replaces the old
 * sequence of updateVar() calls that each read a stale snapshot and clobbered
 * the type back to its previous value.
 */
export function applyChildVarTypeChange(
  current: DriverChildStateVarDef,
  newType: string,
): DriverChildStateVarDef {
  const next = { ...current, type: newType } as Record<string, unknown>;
  if (newType !== "integer" && newType !== "number" && newType !== "float") {
    for (const f of NUMERIC_ONLY_FIELDS) delete next[f];
  }
  if (newType !== "enum") {
    delete next.values;
  }
  return next as unknown as DriverChildStateVarDef;
}

export type RosterSource = "none" | "count" | "count_from" | "ids_from" | "ids";

/**
 * The instances block a change of roster source writes. The label and a
 * `reported` presence ride along to every source: they are facts about the
 * roster, not about which field sizes it. `count_from_state` rides along to
 * `count_from` only: a count, a literal list and an ID-list field fix the
 * roster, so the platform refuses it beside them and no reader follows it.
 */
export function instancesForSource(
  next: RosterSource,
  inst: DriverChildInstances | undefined,
  configFields: string[],
): DriverChildInstances | undefined {
  const kept: DriverChildInstances = {
    ...(inst?.presence === "reported" ? { presence: "reported" as const } : {}),
    ...(inst?.label ? { label: inst.label } : {}),
  };
  const field = configFields[0] ?? "";
  switch (next) {
    case "none":
      return undefined;
    case "count":
      return { count: 2, ...kept };
    case "count_from":
      return { count_from: field, ...followed(inst), ...kept };
    case "ids":
      return { ids: [], ...kept };
    case "ids_from":
      return { ids_from: field, ...kept };
  }
}

/** The instances block a new config field for `count_from` or `ids_from`
 *  writes, keeping what `instancesForSource` keeps for that source. */
export function instancesForField(
  key: "count_from" | "ids_from",
  value: string,
  inst: DriverChildInstances | undefined,
): DriverChildInstances {
  const base = instancesForSource(key, inst, [value]);
  return { ...base, [key]: value };
}

function followed(inst: DriverChildInstances | undefined): DriverChildInstances {
  return inst?.count_from_state ? { count_from_state: inst.count_from_state } : {};
}
