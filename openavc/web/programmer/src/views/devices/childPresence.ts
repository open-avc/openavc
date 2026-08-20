/**
 * What shape a child entity is in, read off its live state.
 *
 * A sub-unit used to get one word — `online`, true or false — rendered as the
 * literal text "true" / "false" in a monospace column with no dot, no colour,
 * no sort and no filter. An endpoint somebody carried out of the rack and an
 * endpoint sitting right there with a wedged service read identically, and
 * the remedy for the two is nothing alike (go find it vs power-cycle it).
 *
 * The platform now injects `offline_reason` (a stable code from the taxonomy
 * in core/connection_fault.py) and `offline_detail` (the sentence a person
 * reads) beside it. This module is the one place the IDE turns those three
 * keys into something to draw, so the row treatment, the tab count and the
 * device-page banner cannot disagree about which endpoints are in trouble.
 *
 * `online` is the single glanceable signal on purpose: a driver setting a
 * fault code takes the boolean down with it (BaseDriver.child_fault), so
 * "something is wrong here" never hides in a column somebody has to know to
 * read. WHICH kind of trouble is the reason's job.
 */

/** A child's state as the list holds it: merged live state over the fetch. */
export type ChildState = Record<string, unknown>;

export interface ChildPresence {
  /** False when this child is not in service. The dot, the sort and the
   *  counts all key off this and nothing else. */
  ok: boolean;
  /** The stable code, or "" when the driver did not say (which is what every
   *  driver that predates the taxonomy does, and is fine). */
  reason: string;
  /** The sentence to show. "" when the driver did not word one. */
  detail: string;
}

export function childPresence(state: ChildState | undefined): ChildPresence {
  const online = state?.online;
  // Undefined rather than false is the pre-registration / stale-snapshot
  // case. Treat it as present: a child the platform has not heard about yet
  // must not draw as a fault, or every list flashes red while it loads.
  const ok = online !== false;
  const reason = typeof state?.offline_reason === "string" ? state.offline_reason : "";
  const detail = typeof state?.offline_detail === "string" ? state.offline_detail : "";
  return { ok, reason, detail };
}

/**
 * One child's state, read out of the flat live-state map.
 *
 * The list component keeps its own indexed version of this for the active tab
 * (a 1500-child controller cannot afford a scan per row per keystroke). This
 * is the direct form, for the callers that need a handful of lookups across
 * every type: the tab badges and the device-page banner.
 */
export function childStateFor(
  liveState: Record<string, unknown>,
  deviceId: string,
  childType: string,
  entry: { local_id_padded: string; state?: ChildState },
): ChildState {
  const prefix = `device.${deviceId}.${childType}.${entry.local_id_padded}.`;
  const out: ChildState = { ...(entry.state ?? {}) };
  for (const key of RESERVED) {
    const v = liveState[prefix + key];
    if (v !== undefined) out[key] = v;
  }
  return out;
}

/** The only keys childPresence reads, so the lookup above stays O(1) per
 *  child instead of scanning the whole live-state map. */
const RESERVED = ["online", "offline_reason", "offline_detail"] as const;

/** How many of these children are not in service. */
export function countNotOk(states: (ChildState | undefined)[]): number {
  let n = 0;
  for (const s of states) if (!childPresence(s).ok) n++;
  return n;
}

/**
 * The device-page line: which sub-units are in trouble, and what they are
 * called. Returns null when everything is fine — the banner does not exist
 * on a healthy device, the way an offline banner does not exist on a
 * connected one.
 *
 * Names, not ids: "Podium PC, Rear Cam" is what somebody walks to. Capped,
 * because a 1500-child controller having a bad day must not push the rest of
 * the page off the screen, and the count already carries the scale.
 */
const NAMES_SHOWN = 6;

export function troubleSummary(
  groups: { noun: string; nounPlural: string; names: string[]; total: number }[],
): { headline: string; names: string } | null {
  const live = groups.filter((g) => g.names.length > 0);
  if (live.length === 0) return null;

  const parts = live.map((g) => {
    const n = g.names.length;
    const noun = n === 1 ? g.noun : g.nounPlural;
    return `${n} of ${g.total} ${noun.toLowerCase()}`;
  });
  const all = live.flatMap((g) => g.names);
  const shown = all.slice(0, NAMES_SHOWN);
  const rest = all.length - shown.length;
  return {
    headline: `${parts.join(" and ")} ${
      all.length === 1 ? "is" : "are"
    } not answering.`,
    names: rest > 0
      ? `${shown.join(", ")} and ${rest} more`
      : shown.join(", "),
  };
}


/** What one child type declares, as much of it as this module needs. */
export interface ChildTypeInfo {
  label?: string;
  label_plural?: string;
  /** Which declared field carries the device's own name for the unit. */
  label_field?: string;
}

/**
 * Sweep the flat live-state map for children that are not in service.
 *
 * Reads live state rather than the children listing on purpose: the device
 * page does not fetch that listing (the Child Entities panel below does), and
 * a banner that needed its own request would be a second source of truth for
 * the same question one section apart.
 *
 * What a row is CALLED follows child_display_name's order -- the label
 * somebody authored, else the device's own name for it via the type's
 * label_field, else its id. A banner naming `0a1d` where the tab says
 * "Podium PC" would send somebody looking for the wrong thing.
 */
export function scanChildTrouble(
  liveState: Record<string, unknown>,
  deviceId: string,
  childTypes: Record<string, ChildTypeInfo>,
): { noun: string; nounPlural: string; names: string[]; total: number }[] {
  const types = Object.keys(childTypes);
  if (types.length === 0) return [];

  const prefix = `device.${deviceId}.`;
  // type -> padded id -> {props}
  const byType = new Map<string, Map<string, ChildState>>();
  for (const [key, value] of Object.entries(liveState)) {
    if (!key.startsWith(prefix)) continue;
    const rest = key.slice(prefix.length);
    const firstDot = rest.indexOf(".");
    if (firstDot <= 0) continue;
    const ctype = rest.slice(0, firstDot);
    if (!(ctype in childTypes)) continue;
    const after = rest.slice(firstDot + 1);
    const secondDot = after.indexOf(".");
    if (secondDot <= 0) continue;
    const padded = after.slice(0, secondDot);
    const prop = after.slice(secondDot + 1);
    let bucket = byType.get(ctype);
    if (!bucket) byType.set(ctype, (bucket = new Map()));
    let child = bucket.get(padded);
    if (!child) bucket.set(padded, (child = {}));
    child[prop] = value;
  }

  const groups = [];
  for (const ctype of types) {
    const bucket = byType.get(ctype);
    if (!bucket || bucket.size === 0) continue;
    const info = childTypes[ctype];
    const labelField = info.label_field;
    const names: string[] = [];
    for (const [padded, state] of bucket) {
      if (childPresence(state).ok) continue;
      const authored = typeof state.label === "string" ? state.label : "";
      const fromDevice = labelField && typeof state[labelField] === "string"
        ? (state[labelField] as string)
        : "";
      names.push(authored || fromDevice || padded);
    }
    if (names.length === 0) continue;
    groups.push({
      noun: info.label ?? ctype,
      nounPlural: info.label_plural ?? info.label ?? ctype,
      names,
      total: bucket.size,
    });
  }
  return groups;
}
