/** What a command's Sets State and Reports fields in the Driver Builder may
 *  name, and the edits a Sets State row makes. No React.
 *
 *  `sets` maps each state variable a command changes to a literal or a
 *  "{param}" reference taking that parameter's value; `query_for` names the
 *  variable a status query's reply reports. The auto-generated simulator acts
 *  on both, which is what lets Live Test and simulation show a command's
 *  effect.
 *
 *  A command addressed to exactly one child (a single child_id parameter whose
 *  child type is declared) may also name that type's state variables, and the
 *  runtime resolves a name declared in both scopes to the child's, so the
 *  device's variable of that name is not offered. The platform's own check is
 *  `_addressed_child_vars` in `openavc/drivers/command_checks.py`; it also
 *  accepts the reserved child properties the runtime adds (online, label, the
 *  offline pair), which these fields do not offer. */

import type { DriverCommandDef, DriverDefinition } from "../../api/types";

export type SetValue = string | number | boolean;
export type SetsTable = Record<string, SetValue>;

type Declared = Record<string, { type?: string }>;

/** The state variables one command may name. */
export interface CommandStateScope {
  /** The child type the command is addressed to, or null. */
  childType: string | null;
  /** The device's variables, less those a child variable of the same name hides. */
  deviceVarNames: string[];
  childVarNames: string[];
  /** Every name offered: the device's, then the child's. */
  allVarNames: string[];
  /** A variable's declared type, the child's before the device's. */
  varType: (varName: string) => string | undefined;
}

/** The one child type a command is addressed to: a single child_id parameter
 *  whose child type the driver declares. */
function addressedChildType(cmd: DriverCommandDef, draft: DriverDefinition): string | null {
  const childParamTypes = Object.values(cmd.params ?? {})
    .filter((p) => p && typeof p === "object" && p.type === "child_id")
    .map((p) => p.child_type);
  const single = childParamTypes.length === 1 ? childParamTypes[0] : undefined;
  return single !== undefined && (draft.child_entity_types ?? {})[single] ? single : null;
}

export function commandStateScope(
  cmd: DriverCommandDef,
  draft: DriverDefinition,
): CommandStateScope {
  const deviceVars = (draft.state_variables ?? {}) as Declared;
  const childType = addressedChildType(cmd, draft);
  const childVars = (
    childType !== null ? (draft.child_entity_types?.[childType]?.state_variables ?? {}) : {}
  ) as Declared;
  const deviceVarNames = Object.keys(deviceVars).filter((v) => !(v in childVars));
  const childVarNames = Object.keys(childVars);
  return {
    childType,
    deviceVarNames,
    childVarNames,
    allVarNames: [...deviceVarNames, ...childVarNames],
    varType: (varName) => childVars[varName]?.type ?? deviceVars[varName]?.type,
  };
}

/** The command's `sets` table; anything but a mapping reads as none. */
export function readSets(cmd: DriverCommandDef): SetsTable {
  return cmd.sets && typeof cmd.sets === "object" && !Array.isArray(cmd.sets) ? cmd.sets : {};
}

/** The variable the command reports; anything but a string reads as none. */
export function readQueryFor(cmd: DriverCommandDef): string {
  return typeof cmd.query_for === "string" ? cmd.query_for : "";
}

/** Nothing to name and nothing written: the fields are left off the card. */
export function nothingToDeclare(scope: CommandStateScope, sets: SetsTable, queryFor: string): boolean {
  const noNames = scope.allVarNames.length === 0;
  const noRows = Object.keys(sets).length === 0;
  return noNames && noRows && !queryFor;
}

const readNumber = (trimmed: string) =>
  trimmed !== "" && Number.isFinite(Number(trimmed)) ? Number(trimmed) : undefined;

/** How a trimmed literal reads as each type, or undefined when it does not. */
const LITERAL_READERS = new Map<string, (trimmed: string) => SetValue | undefined>([
  ["boolean", (t) => (t === "true" || t === "false" ? t === "true" : undefined)],
  ["integer", (t) => (/^-?\d+$/.test(t) ? parseInt(t, 10) : undefined)],
  ["number", readNumber],
  ["float", readNumber],
]);

/** A literal typed for a variable, read as the variable's declared type:
 *  "true"/"false" for a boolean, a whole number for an integer, any finite
 *  number for a number or float. Anything else is kept as typed. */
export function coerceLiteral(raw: string, varType: string | undefined): SetValue {
  const read = LITERAL_READERS.get(varType ?? "");
  return read?.(raw.trim()) ?? raw;
}

/** Whether a row's value is a "{param}" reference to one of the command's own
 *  parameters; anything else is edited as a literal. */
export function isParamReference(value: SetValue, paramNames: string[]): boolean {
  const ref = typeof value === "string" ? /^\{(\w+)\}$/.exec(value) : null;
  return ref !== null && paramNames.includes(ref[1]);
}

/** The write for a changed table: an emptied one removes the key. */
export function setsWrite(next: SetsTable): { sets: SetsTable | undefined } {
  return { sets: Object.keys(next).length > 0 ? next : undefined };
}

/** The table with row `index` naming `newKey`, in the same place. */
export function withRowKey(sets: SetsTable, index: number, newKey: string): SetsTable {
  const next: SetsTable = {};
  Object.entries(sets).forEach(([k, v], i) => {
    next[i === index ? newKey : k] = v;
  });
  return next;
}

/** The table with row `index` set to `value`. */
export function withRowValue(sets: SetsTable, index: number, value: SetValue): SetsTable {
  const next: SetsTable = {};
  Object.entries(sets).forEach(([k, v], i) => {
    next[k] = i === index ? value : v;
  });
  return next;
}

/** The table without row `index`. */
export function withoutRow(sets: SetsTable, index: number): SetsTable {
  const next: SetsTable = {};
  Object.entries(sets).forEach(([k, v], i) => {
    if (i !== index) next[k] = v;
  });
  return next;
}

/** Whether another row can be added: a variable no row names yet. */
export function canAddRow(scope: CommandStateScope, sets: SetsTable): boolean {
  const used = new Set(Object.keys(sets));
  return scope.allVarNames.some((v) => !used.has(v));
}

/** The table with a row for the first unused variable, set to the first
 *  parameter (the usual case: the command sets what was passed), or null
 *  when there is none to add. */
export function withNewRow(scope: CommandStateScope, sets: SetsTable, paramNames: string[]): SetsTable | null {
  const used = new Set(Object.keys(sets));
  const key = scope.allVarNames.find((v) => !used.has(v));
  if (!key) return null;
  return { ...sets, [key]: paramNames.length > 0 ? `{${paramNames[0]}}` : "" };
}
