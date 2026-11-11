/** Where a parameter's choices come from, in the Driver Builder. No React.
 *
 *  Three fields answer it, and to an author they are one decision:
 *
 *  - `options_state`: the device publishes the list as a state variable.
 *  - `options_from`: the list is the controls of a child picked in a sibling
 *    child_id parameter (a cascade). Its `source` has one legal value, so it
 *    is written, never asked; `types` and `units` narrow which controls.
 *  - `type_from`: a companion to a cascade: this parameter's input type
 *    follows whichever control the cascade picked, so a Value box becomes a
 *    dB spinner for a level and Yes/No for a mute.
 *
 *  Switching source clears the other field, and nothing is written just by
 *  showing a parameter: one that arrived with an odd combination keeps it
 *  until the author changes something. The picker that reads these when a
 *  command runs is `components/shared/paramOptions.ts`. */

import type { DriverParamDef } from "../../api/types";

type Params = Record<string, DriverParamDef>;
export type CascadeDef = NonNullable<DriverParamDef["options_from"]>;
export type ChoiceSource = "fixed" | "state" | "cascade";

/** The source a parameter declares: a cascade before a published list. */
export function choiceSource(def: DriverParamDef): ChoiceSource {
  if (def.options_from) return "cascade";
  return def.options_state !== undefined ? "state" : "fixed";
}

/** The other parameters a cascade may read: a child_id one, never itself
 *  (the platform's rule). */
export function childIdSiblings(siblings: Params, name: string): string[] {
  return Object.entries(siblings)
    .filter(([sName, sDef]) => sName !== name && sDef?.type === "child_id")
    .map(([sName]) => sName);
}

/** The other parameters whose input type this one may follow: cascades. */
export function cascadeSiblings(siblings: Params, name: string): string[] {
  return Object.entries(siblings)
    .filter(([sName, sDef]) => sName !== name && sDef?.options_from?.source === "child_schema")
    .map(([sName]) => sName);
}

/** What the parameter can offer next to its own: a published list's
 *  suggestions and a cascade's sources. */
export interface ChoiceContext {
  stateKeys: string[];
  childIds: string[];
}

/** The write for a change of source. A published list starts on the one it
 *  already names, else the first state variable; a cascade on the parameter
 *  it already reads, else the first child_id sibling. */
export function sourceChanged(
  next: string,
  def: DriverParamDef,
  { stateKeys, childIds }: ChoiceContext,
): Partial<DriverParamDef> {
  if (next === "fixed") return { options_state: undefined, options_from: undefined };
  if (next === "state") {
    return { options_from: undefined, options_state: def.options_state ?? stateKeys[0] ?? "" };
  }
  return {
    options_state: undefined,
    options_from: { param: def.options_from?.param ?? childIds[0] ?? "", source: "child_schema" },
  };
}

/** The write for a cascade pointed at another parameter: its narrowing kept. */
export function cascadeRepointed(def: DriverParamDef, param: string): Partial<DriverParamDef> {
  return { options_from: { ...def.options_from, param, source: "child_schema" } };
}

/** The write for Input Type Follows; the empty choice ("Nothing") removes it. */
export function typeFollows(param: string): Partial<DriverParamDef> {
  return { type_from: param ? { param } : undefined };
}

/** Whether Input Type Follows shows: beside a cascade to follow, or when one
 *  is already set, which must stay visible to be cleared. */
export function showsTypeFrom(def: DriverParamDef, cascades: string[]): boolean {
  return cascades.length > 0 || !!def.type_from;
}

/** A cascade with `patch` written over it; an emptied list is removed. */
export function narrowed(optionsFrom: CascadeDef, patch: Partial<CascadeDef>): CascadeDef {
  const next: CascadeDef = { ...optionsFrom, ...patch };
  if (!next.types?.length) delete next.types;
  if (!next.units?.length) delete next.units;
  return next;
}

/** The type list with `type` ticked on or off. */
export function typesTicked(types: string[], type: string, on: boolean): CascadeDef["types"] {
  return (on ? [...types, type] : types.filter((x) => x !== type)) as CascadeDef["types"];
}

/** A unit list as typed: comma-separated, spaces and blanks dropped. */
export function unitsFromText(text: string): string[] {
  return text.split(",").map((u) => u.trim()).filter(Boolean);
}
