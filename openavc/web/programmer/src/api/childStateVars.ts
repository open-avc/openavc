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

/** What one child is called, read from its state: the project label, else the
 *  name the device reports under the type's `label_field`, else what the
 *  driver's roster calls the slot (its reserved `label`), else "".
 *
 *  The same order as the server's `child_display_name` (drivers/child_ids.py),
 *  which is where `display_name` comes from. The Child Entities panel reads it
 *  here from live state because a device usually reports its names a moment
 *  after the list is fetched, and `display_name` is only as new as the fetch. */
export function childNameFrom(
  projectLabel: string | null | undefined,
  state: Record<string, unknown>,
  tdef: { label_field?: string } | null | undefined,
): string {
  const authored = (projectLabel ?? "").trim();
  if (authored) return authored;
  const field = tdef?.label_field?.trim();
  if (field) {
    const reported = state[field];
    // A bool is never a name: label_field pointed at a flag.
    if (reported != null && typeof reported !== "boolean") {
      const name = String(reported).trim();
      if (name) return name;
    }
  }
  const seeded = state.label;
  if (seeded == null || typeof seeded === "boolean") return "";
  return String(seeded).trim();
}

/** What each child is called now, keyed `<type>.<padded id>`: `childNameFrom`
 *  over the live values of the type's `label_field` and the reserved `label`
 *  (the fetched state where live state has none), else `childDisplayName`.
 *
 *  A device usually reports its names a moment after the list is fetched, so
 *  a name taken from the fetch alone is the slot's ("Input 1") rather than the
 *  device's ("CH1"). Two lookups per child: cheap enough to run on every
 *  live-state tick, which building the declarations below is not. */
export function liveChildNames(
  resp: ChildEntitiesListResponse | null,
  liveState: Record<string, unknown>,
  deviceId: string,
): Map<string, string> {
  const out = new Map<string, string>();
  if (!resp) return out;
  for (const [ctype, tdef] of Object.entries(resp.child_entity_types ?? {})) {
    const field = tdef?.label_field?.trim();
    for (const entry of resp.children?.[ctype] ?? []) {
      const prefix = `device.${deviceId}.${ctype}.${entry.local_id_padded}.`;
      const read = (key: string) => {
        const live = liveState[prefix + key];
        return live !== undefined ? live : entry.state?.[key];
      };
      const state: Record<string, unknown> = { label: read("label") };
      if (field) state[field] = read(field);
      out.set(
        `${ctype}.${entry.local_id_padded}`,
        childNameFrom(entry.label, state, tdef) || childDisplayName(ctype, tdef, entry),
      );
    }
  }
  return out;
}

/** Every child reading this device has, as the declaration it is authored
 *  against, keyed by its suffix under `device.<id>.`. `names` (from
 *  `liveChildNames`) names each child; without it, the fetched name.
 *
 *  Built once per payload rather than resolved per key: a loaded controller
 *  carries tens of thousands of these and the callers look them up on every
 *  live-state tick. */
export function childReadingDeclarations(
  resp: ChildEntitiesListResponse | null,
  names?: Map<string, string>,
): Map<string, ChildEntityStateVarDef> {
  const out = new Map<string, ChildEntityStateVarDef>();
  if (!resp) return out;
  for (const [ctype, tdef] of Object.entries(resp.child_entity_types ?? {})) {
    for (const entry of resp.children?.[ctype] ?? []) {
      const child = names?.get(`${ctype}.${entry.local_id_padded}`)
        || childDisplayName(ctype, tdef, entry);
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
