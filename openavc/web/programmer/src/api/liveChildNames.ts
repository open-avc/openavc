/** What a child is called now, read from live state.
 *
 *  A device usually reports its names a moment after a list of its children
 *  is fetched, and changes them later, so a name taken from the fetch alone is
 *  the slot's ("Input 1") rather than the device's ("CH1"). These read the
 *  type's `label_field` and the reserved `label` live, the fetched state where
 *  live state has none, in the server's order (`childNameFrom`). Two lookups a
 *  child: cheap enough to run on every live-state tick. The Child Entities
 *  panel, the State tab's Monitor form and a parameter's child picker read
 *  names here. */
import { childDisplayName, childNameFrom } from "./childStateVars";
import type { ChildEntitiesListResponse, ChildEntityEntry } from "./types";

type LabelledType = { label_field?: string } | null | undefined;

/** What each child is called now, keyed `<type>.<padded id>`: its live name,
 *  else `childDisplayName`. */
export function liveChildNames(
  resp: ChildEntitiesListResponse | null,
  liveState: Record<string, unknown>,
  deviceId: string,
): Map<string, string> {
  const out = new Map<string, string>();
  if (!resp) return out;
  for (const [ctype, tdef] of Object.entries(resp.child_entity_types ?? {})) {
    for (const entry of resp.children?.[ctype] ?? []) {
      out.set(
        `${ctype}.${entry.local_id_padded}`,
        liveChildName(entry, tdef, liveState, `device.${deviceId}.${ctype}.`)
          || childDisplayName(ctype, tdef, entry),
      );
    }
  }
  return out;
}

/** What one fetched child is called now. `typePrefix` is
 *  `device.<id>.<type>.`. "" when nothing names it; the caller words the
 *  absence. */
export function liveChildName(
  entry: ChildEntityEntry,
  tdef: LabelledType,
  liveState: Record<string, unknown>,
  typePrefix: string,
): string {
  const read = liveOrFetched(entry, liveState, `${typePrefix}${entry.local_id_padded}.`);
  return childNameFrom(entry.label, namingState(read, labelFieldOf(tdef)), tdef);
}

/** The state variable a type names its children by, "" for none. */
function labelFieldOf(tdef: LabelledType): string {
  return tdef?.label_field?.trim() || "";
}

/** The two keys a child is named from: its reserved `label`, and `field`. */
function namingState(read: (key: string) => unknown, field: string): Record<string, unknown> {
  const state: Record<string, unknown> = { label: read("label") };
  if (field) state[field] = read(field);
  return state;
}

/** A reader of one child's key: live state's value, else the fetched one. */
function liveOrFetched(
  entry: ChildEntityEntry,
  liveState: Record<string, unknown>,
  prefix: string,
): (key: string) => unknown {
  return (key) => {
    const live = liveState[prefix + key];
    return live !== undefined ? live : entry.state?.[key];
  };
}
