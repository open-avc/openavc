/**
 * What a child-entity state key declares, and what to call it.
 *
 * A device-level key's declaration is the driver's `state_variables`. A child
 * key's (`channel.01.fader`) is never there: it lives in the children payload,
 * on the child's own schema when the type is dynamic and on the type otherwise.
 * Anything that asks only the first question sees every child key as
 * undeclared — no type, no unit, no range — and an undeclared reading is
 * offered the tick-the-values form, so there is no way to say "normal is -20 to
 * 0 dB" about a per-channel level. On multi-channel gear that is most of what
 * is worth watching.
 *
 * The NAME is here for the same reason the lookup is. A child reading carrying
 * only its own label is "Output Level" thirty-two times over on a thirty-two
 * channel amplifier — on the Dashboard tile, in the alert and in the health
 * card — with nothing saying which channel. The label a reading is authored
 * against therefore names the child first, and one home for that rule is what
 * stops the two authoring doors disagreeing about it.
 */
import type {
  ChildEntitiesListResponse,
  ChildEntityEntry,
  ChildEntityStateVarDef,
  ChildEntityTypeSchema,
} from "./types";

/** Effective var defs for one child: a dynamic child's own discovered schema
 *  when present, else the type-level schema its siblings all share. */
export function childSchemaFor(
  resp: ChildEntitiesListResponse,
  ctype: string,
  entry: ChildEntityEntry,
): Record<string, ChildEntityStateVarDef> {
  return entry.schema ?? resp.child_entity_types[ctype]?.state_variables ?? {};
}

/** Resolve a bound suffix like "input.01.fader_db" against the device's
 *  children payload (child type -> registered child -> var def). */
export function childVarDefForSuffix(
  resp: ChildEntitiesListResponse | null,
  suffix: string,
): ChildEntityStateVarDef | null {
  if (!resp) return null;
  const parts = suffix.split(".");
  if (parts.length < 3) return null;
  const [ctype, padded] = parts;
  const prop = parts.slice(2).join(".");
  const entry = (resp.children?.[ctype] ?? []).find(
    (c) => c.local_id_padded === padded,
  );
  if (!entry) return null;
  return childSchemaFor(resp, ctype, entry)[prop] ?? null;
}

/** What to call one child: what somebody named it here, else the name the
 *  device reports (the server resolves both into `display_name`), else the
 *  type and the id — "Encoder 7" reads better than a bare 7 in a list that
 *  also holds outputs. */
export function childDisplayName(
  ctype: string,
  tdef: ChildEntityTypeSchema | undefined,
  entry: ChildEntityEntry,
): string {
  return (
    entry.display_name
    || entry.label
    || `${tdef?.label || ctype} ${entry.local_id}`
  );
}

/** Every child reading this device has, as the declaration it is authored
 *  against, keyed by its suffix under `device.<id>.`.
 *
 *  Built once per payload rather than resolved per key: a loaded controller
 *  carries tens of thousands of these and the callers look them up on every
 *  live-state tick. */
export function childReadingDeclarations(
  resp: ChildEntitiesListResponse | null,
): Map<string, ChildEntityStateVarDef> {
  const out = new Map<string, ChildEntityStateVarDef>();
  if (!resp) return out;
  for (const [ctype, tdef] of Object.entries(resp.child_entity_types ?? {})) {
    for (const entry of resp.children?.[ctype] ?? []) {
      const child = childDisplayName(ctype, tdef, entry);
      for (const [prop, def] of Object.entries(childSchemaFor(resp, ctype, entry))) {
        out.set(`${ctype}.${entry.local_id_padded}.${prop}`, {
          ...def,
          label: `${child} · ${def?.label || prop}`,
        });
      }
    }
  }
  return out;
}
