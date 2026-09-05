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
 *
 * TWO questions, not one. `ok` is "in service"; `trouble` is "something is
 * wrong". They used to be the same boolean, which worked while every reason in
 * the taxonomy was a fault. `not_fitted` is not: a mixer with seven empty
 * AT-LINK extension slots is a correctly-configured mixer, and counting those
 * as "7 down" would trade the old lie (seven green dots on hardware that does
 * not exist) for a new one. So the dot reads `ok` — an empty slot is not in
 * service, and drawing it green would be the original bug — while the tab
 * badge, the trouble filter and the device banner read `trouble`.
 */

/** Codes this module knows by name. The vocabulary itself lives server-side in
 *  core/connection_fault.py; these are the two the UI has to branch on. */
const NOT_FITTED = "not_fitted";
const PARENT_OFFLINE = "parent_offline";
const SERVICE_FAULT = "service_fault";

/** A child's state as the list holds it: merged live state over the fetch. */
export type ChildState = Record<string, unknown>;

export interface ChildPresence {
  /** False when this child is not in service — including an empty slot. The
   *  presence dot keys off this. */
  ok: boolean;
  /** True when something is WRONG, as opposed to absent by design. The tab
   *  count, the trouble filter, the row order and the device banner key off
   *  this. Always implies `!ok`. */
  trouble: boolean;
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
  // An unrecognised code counts as trouble: a driver writing one this
  // taxonomy does not define is a bug, and the safe reading of "I do not know
  // this code" is not "everything is fine".
  return { ok, trouble: !ok && reason !== NOT_FITTED, reason, detail };
}

/**
 * How to finish the sentence "N of M inputs are ___" for a set of trouble
 * codes. One phrase when they all agree, a neutral one when they do not —
 * "are not answering" was hardcoded, and it is wrong for an endpoint that is
 * answering fine but not running, and wrong again for one whose parent device
 * is simply unreachable.
 */
const TROUBLE_PHRASES: Record<string, string> = {
  [SERVICE_FAULT]: "reachable, but not running",
  [PARENT_OFFLINE]: "unavailable while the device is offline",
};

export function troublePhrase(reasons: readonly string[]): string {
  const distinct = new Set(reasons);
  if (distinct.size === 1) {
    const [only] = distinct;
    // `not_responding` and "" (every driver predating the taxonomy) both mean
    // the same thing to a reader, and it is the phrase this has always used.
    return TROUBLE_PHRASES[only] ?? "not answering";
  }
  return distinct.size === 0 ? "not answering" : "not in service";
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

/** How many of these children are in trouble — an empty slot is not. */
export function countTrouble(states: (ChildState | undefined)[]): number {
  let n = 0;
  for (const s of states) if (childPresence(s).trouble) n++;
  return n;
}

/** The trouble codes present across these children, for `troublePhrase`. */
export function troubleReasons(states: (ChildState | undefined)[]): string[] {
  const out: string[] = [];
  for (const s of states) {
    const p = childPresence(s);
    if (p.trouble) out.push(p.reason);
  }
  return out;
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

export interface TroubleGroup {
  noun: string;
  nounPlural: string;
  names: string[];
  /** One code per name, same order — what makes the headline's verb honest. */
  reasons: string[];
  total: number;
}

export function troubleSummary(
  groups: TroubleGroup[],
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
    } ${troublePhrase(live.flatMap((g) => g.reasons))}.`,
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
 *
 * One difference from the server's version, and it is forced: the `label` key
 * in live state holds the project label when somebody set one and the roster's
 * own `instances.label` template ("Extension 3") when nobody did, and from
 * live state alone those two cannot be told apart. child_display_name gets the
 * project label as a separate argument and can rank the template below the
 * device's name; here the conflated key stays first, which is right whenever
 * it holds an authored name and harmless when it holds a template -- a slot
 * that is not answering has no device name to lose to anyway.
 */
export function scanChildTrouble(
  liveState: Record<string, unknown>,
  deviceId: string,
  childTypes: Record<string, ChildTypeInfo>,
): TroubleGroup[] {
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

  const groups: TroubleGroup[] = [];
  for (const ctype of types) {
    const bucket = byType.get(ctype);
    if (!bucket || bucket.size === 0) continue;
    const info = childTypes[ctype];
    const labelField = info.label_field;
    const names: string[] = [];
    const reasons: string[] = [];
    for (const [padded, state] of bucket) {
      const presence = childPresence(state);
      if (!presence.trouble) continue;
      const authored = typeof state.label === "string" ? state.label : "";
      const fromDevice = labelField && typeof state[labelField] === "string"
        ? (state[labelField] as string)
        : "";
      names.push(authored || fromDevice || padded);
      reasons.push(presence.reason);
    }
    if (names.length === 0) continue;
    groups.push({
      noun: info.label ?? ctype,
      nounPlural: info.label_plural ?? info.label ?? ctype,
      names,
      reasons,
      total: bucket.size,
    });
  }
  return groups;
}
