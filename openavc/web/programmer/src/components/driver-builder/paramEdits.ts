/** A command's (or an action's) parameters in the Driver Builder: the list's
 *  edits, what a parameter's type decides, and how each box reads what is
 *  typed. No React.
 *
 *  Some fields belong to some types only (`TYPE_FIELDS`): a type change drops
 *  the ones the new type has no use for, and the row shows each only on a type
 *  it belongs to, so the two cannot disagree. A unit and the free-text aids
 *  are shown by type as well, but also wherever one is already set, and a
 *  type change keeps them: a value written by hand stays visible and
 *  editable rather than silently kept. */

import { PARAM_TYPES, type DriverParamDef } from "../../api/types";

export type Params = Record<string, DriverParamDef>;

/** A field's write to one parameter: undefined removes the field. */
export type ParamUpdate = (partial: Partial<DriverParamDef>) => void;

/** The first free paramN, counting on from the number there are. */
export function newParamName(params: Params): string {
  let counter = Object.keys(params).length + 1;
  let name = `param${counter}`;
  while (name in params) {
    counter++;
    name = `param${counter}`;
  }
  return name;
}

/** A rename's answer. A refusal says why; an accepted rename with something
 *  to write carries the new name and the list renamed in place, and a blank
 *  name or the same one carries neither. */
export interface ParamRename {
  ok: boolean;
  reason?: string;
  name?: string;
  params?: Params;
}

export function paramsRenamed(params: Params, oldName: string, newName: string): ParamRename {
  const trimmed = newName.trim();
  if (!trimmed || trimmed === oldName) return { ok: true };
  if (trimmed in params) {
    return { ok: false, reason: `A parameter named "${trimmed}" already exists.` };
  }
  if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(trimmed)) {
    return {
      ok: false,
      reason: "Use letters, digits, and underscores only. Must start with a letter or underscore.",
    };
  }
  const next: Params = {};
  for (const [k, v] of Object.entries(params)) {
    next[k === oldName ? trimmed : k] = v;
  }
  return { ok: true, name: trimmed, params: next };
}

/** The parameter with `partial` written over it; a field written as
 *  undefined is removed, so nothing is saved as null. */
export function paramMerged(def: DriverParamDef, partial: Partial<DriverParamDef>): DriverParamDef {
  const merged = { ...def, ...partial } as Record<string, unknown>;
  for (const k of Object.keys(merged)) {
    if (merged[k] === undefined) delete merged[k];
  }
  return merged as unknown as DriverParamDef;
}

const NUMERIC: readonly string[] = ["integer", "number"];

/** The fields that belong to some parameter types only. */
const TYPE_FIELDS = {
  min: NUMERIC,
  max: NUMERIC,
  scale: NUMERIC,
  offset: NUMERIC,
  decimals: ["number"],
  values: ["enum"],
  child_type: ["child_id"],
} as const satisfies Partial<Record<keyof DriverParamDef, readonly string[]>>;

export type TypeField = keyof typeof TYPE_FIELDS;

function belongs(field: TypeField, type: string): boolean {
  return (TYPE_FIELDS[field] as readonly string[]).includes(type);
}

/** Whether the parameter's type has `field`. */
export function typeHas(def: DriverParamDef, field: TypeField): boolean {
  return belongs(field, def.type);
}

/** The Type select's word for each type the driver contract lets a parameter
 *  declare (`PARAM_TYPES`, generated from it), in the contract's order. */
const PARAM_TYPE_LABELS: Record<(typeof PARAM_TYPES)[number], string> = {
  string: "String",
  integer: "Integer",
  number: "Number",
  boolean: "Boolean",
  enum: "Enum",
  child_id: "Child ID",
};

export const PARAM_TYPE_OPTIONS = PARAM_TYPES.map((value) => ({
  value,
  label: PARAM_TYPE_LABELS[value],
}));

/** The write for a change to `type`: every field it has no use for cleared,
 *  and a Child ID started on the child type it already names, else the first
 *  declared, so it is valid the moment it is switched. */
export function retyped(
  def: DriverParamDef,
  type: string,
  childTypes: string[],
): Partial<DriverParamDef> {
  const partial: Partial<DriverParamDef> = { type };
  for (const field of Object.keys(TYPE_FIELDS) as TypeField[]) {
    if (!belongs(field, type)) partial[field] = undefined;
  }
  if (belongs("child_type", type)) partial.child_type = def.child_type ?? childTypes[0];
  return partial;
}

/** A box in a parameter's value row. */
export type ValueBox = "help" | "default" | "min" | "max" | "decimals" | "unit";

const VALUE_BOX_ORDER: readonly ValueBox[] = ["help", "default", "min", "max", "decimals", "unit"];

const isTypeField = (box: ValueBox): box is ValueBox & TypeField => box in TYPE_FIELDS;

/** The value row's boxes, in order: a box for a field only some types have
 *  (min, max, decimals) where the type has it, by `TYPE_FIELDS`; the unit on a
 *  numeric type, where it is the usual need, and wherever one is already set;
 *  the others always. */
export function valueBoxes(def: DriverParamDef): ValueBox[] {
  return VALUE_BOX_ORDER.filter((box) => {
    if (box === "unit") return NUMERIC.includes(def.type) || def.unit !== undefined;
    return !isTypeField(box) || typeHas(def, box);
  });
}

/** Whether the free-text aids (pattern, trim, masked) show: on a string,
 *  where the runtime applies them, and on any parameter already carrying one. */
export function showsTextAids(def: DriverParamDef): boolean {
  if (def.type === "string") return true;
  return def.pattern !== undefined || def.trim !== undefined || def.secret !== undefined;
}

const numberOrText = (n: number, raw: string) => (Number.isFinite(n) ? n : raw);

/** How a typed default reads for each type; the others keep the text. */
const DEFAULT_READERS = new Map<string, (raw: string) => unknown>([
  ["integer", (raw) => numberOrText(parseInt(raw, 10), raw)],
  ["number", (raw) => numberOrText(parseFloat(raw), raw)],
  ["boolean", (raw) => raw === "true"],
]);

/** A default as typed: blank removes it; an integer or number reads as one
 *  where the text starts with one (else it is kept as typed); a boolean is
 *  true only for "true". */
export function defaultFromText(def: DriverParamDef, raw: string): unknown {
  if (!raw) return undefined;
  const read = DEFAULT_READERS.get(def.type);
  return read ? read(raw) : raw;
}

/** A min or max as typed: blank removes it; an integer's drops any fraction. */
export function boundFromText(def: DriverParamDef, raw: string): number | undefined {
  if (raw === "") return undefined;
  return def.type === "integer" ? parseInt(raw, 10) : parseFloat(raw);
}

/** Decimals as typed: blank removes it. */
export function decimalsFromText(raw: string): number | undefined {
  return raw === "" ? undefined : parseInt(raw, 10);
}

/** What the default box says it takes. */
const DEFAULT_HINTS = new Map([
  ["boolean", "true / false"],
  ["enum", "must be one of values"],
]);

export function defaultHint(def: DriverParamDef): string {
  return DEFAULT_HINTS.get(def.type) ?? "";
}
