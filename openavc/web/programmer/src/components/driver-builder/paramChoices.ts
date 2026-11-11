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
import type { Params } from "./paramEdits";

export type CascadeDef = NonNullable<DriverParamDef["options_from"]>;
export type CascadeType = NonNullable<CascadeDef["types"]>[number];
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

interface Source {
  /** What the source select calls it. */
  label: string;
  /** The field it is written to; none for whatever the type allows. */
  field?: "options_state" | "options_from";
  /** What its field starts as when the source is chosen. */
  start?: (def: DriverParamDef, context: ChoiceContext) => Partial<DriverParamDef>;
}

/** The sources, in the select's order. A published list starts on the one it
 *  already names, else the first state variable; a cascade on the parameter
 *  it already reads, else the first child_id sibling. */
const SOURCES: Record<ChoiceSource, Source> = {
  fixed: { label: "Whatever the type allows" },
  state: {
    label: "A list the device publishes",
    field: "options_state",
    start: (def, { stateKeys }) => ({ options_state: def.options_state ?? stateKeys[0] ?? "" }),
  },
  cascade: {
    label: "The controls of a child picked in another parameter",
    field: "options_from",
    start: (def, { childIds }) => ({
      options_from: { param: def.options_from?.param ?? childIds[0] ?? "", source: "child_schema" },
    }),
  },
};

export const CHOICE_SOURCES = (Object.keys(SOURCES) as ChoiceSource[]).map((value) => ({
  value,
  label: SOURCES[value].label,
}));

const isSource = (value: string): value is ChoiceSource =>
  Object.prototype.hasOwnProperty.call(SOURCES, value);

/** The write for a change of source: every other source's field cleared, in
 *  the sources' order, then the chosen one's start. A value that names no
 *  source is a cascade. */
export function sourceChanged(
  next: string,
  def: DriverParamDef,
  context: ChoiceContext,
): Partial<DriverParamDef> {
  const chosen: ChoiceSource = isSource(next) ? next : "cascade";
  const cleared: Partial<DriverParamDef> = {};
  for (const [source, { field }] of Object.entries(SOURCES)) {
    if (field && source !== chosen) cleared[field] = undefined;
  }
  return { ...cleared, ...SOURCES[chosen].start?.(def, context) };
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
export function typesTicked(types: CascadeType[], type: CascadeType, on: boolean): CascadeType[] {
  return on ? [...types, type] : types.filter((x) => x !== type);
}

/** A unit list as typed: comma-separated, spaces and blanks dropped. */
export function unitsFromText(text: string): string[] {
  return text.split(",").map((u) => u.trim()).filter(Boolean);
}
