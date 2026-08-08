import type {
  DriverDefinition,
  DriverEachChildQuery,
  DriverOscConnectItem,
  DriverQueryEntry,
} from "../../api/types";

/**
 * Shape helpers for the entries in `polling.queries` and `on_connect`, shared
 * by PollingConfig and LifecycleEditor so the two can't drift.
 *
 * An entry is one of:
 *   "PWR?\r"                                        plain wire string
 *   { each_child, send }                            one query per child
 *   { send, when }                                  gated on a config field
 *   { send, query_for }                             declared state pairing
 *   { each_child, send, when }                      both
 *   { address, args }                               OSC message with typed args
 *   { address, args, when }                         OSC args, gated
 *
 * `when: <config_field>` runs the entry only while that config field is truthy
 * — how a driver arms a chatty subscription (a level-meter stream) behind an
 * integrator checkbox instead of forcing it on every site.
 *
 * `query_for: <state_var>` names the state variable the reply reports, so the
 * auto-generated simulator answers the query without name-guessing. On the
 * plain `{send}` form it names a device-level variable; on an `each_child`
 * form it names one of that child type's state variables (each child answers
 * from its own state). The OSC args form doesn't carry one.
 *
 * OSC on_connect items with arguments are keyed on `address` (paired with
 * `args`); every other form keys the wire content on `send` (or is a bare
 * string). The runtime reads either key, so a gated OSC subscription can be a
 * plain `{send, when}` while a value-setting message is `{address, args}`.
 */
export type QueryEntry =
  | string
  | DriverEachChildQuery
  | DriverQueryEntry
  | DriverOscConnectItem
  | Record<string, unknown>;

export function isEachChild(q: QueryEntry): q is DriverEachChildQuery {
  return typeof q === "object" && q !== null && "each_child" in q;
}

export function isGated(q: QueryEntry): q is DriverQueryEntry {
  return (
    typeof q === "object" &&
    q !== null &&
    !("each_child" in q) &&
    typeof (q as DriverQueryEntry).send === "string"
  );
}

/** An OSC on_connect item carrying typed args (`{address, args}`). Editable via
 *  the OSC args editor, keyed on `address` rather than `send`. */
export function isOscItem(q: QueryEntry): q is DriverOscConnectItem {
  return (
    typeof q === "object" &&
    q !== null &&
    !("each_child" in q) &&
    typeof (q as DriverOscConnectItem).address === "string"
  );
}

/** An object entry we have no inline editor for. Shown read-only rather than
 *  corrupted. Everything with a known shape (each_child / gated / OSC args) is
 *  editable, so this only catches genuinely-malformed objects. */
export function isOpaque(q: QueryEntry): boolean {
  return (
    typeof q !== "string" && !isEachChild(q) && !isGated(q) && !isOscItem(q)
  );
}

export function querySend(q: QueryEntry): string {
  if (typeof q === "string") return q;
  if (isEachChild(q) || isGated(q)) return q.send;
  if (isOscItem(q)) return q.address;
  return "";
}

export function queryWhen(q: QueryEntry): string {
  if (isEachChild(q) || isGated(q)) return q.when ?? "";
  if (isOscItem(q)) return q.when ?? "";
  return "";
}

/** Declared state pairing on a plain `{send}` or each_child entry ("" when
 *  absent — the OSC args form doesn't carry one). */
export function queryQueryFor(q: QueryEntry): string {
  if (isEachChild(q) || isGated(q)) return q.query_for ?? "";
  return "";
}

/** Typed OSC args on an entry, or `undefined` when it isn't an OSC args item.
 *  Bare strings, each_child, and gated entries have no args. */
export function queryArgs(
  q: QueryEntry,
): { type: string; value: string }[] | undefined {
  if (isOscItem(q)) return q.args ?? [];
  return undefined;
}

/** Rebuild an entry from its parts, collapsing to the simplest form that can
 *  carry them: a plain string when it needs neither a child type, a gate, a
 *  state pairing, nor args. OSC args force the `{address, args}` form
 *  (each_child is address-only, so args are dropped when a child type is
 *  chosen); a `query_for` pairing rides both the plain and each_child forms,
 *  and is dropped only when args force the OSC form that can't carry it. */
export function buildQueryEntry(
  send: string,
  eachChild: string,
  when: string,
  args?: { type: string; value: string }[],
  queryFor?: string,
): QueryEntry {
  if (eachChild) {
    const entry: DriverEachChildQuery = { each_child: eachChild, send };
    if (when) entry.when = when;
    if (queryFor) entry.query_for = queryFor;
    return entry;
  }
  if (args && args.length) {
    return when ? { address: send, args, when } : { address: send, args };
  }
  if (when || queryFor) {
    const entry: DriverQueryEntry = { send };
    if (when) entry.when = when;
    if (queryFor) entry.query_for = queryFor;
    return entry;
  }
  return send;
}

/** Config fields a `when:` gate can name — declared in either block, deduped.
 *  Booleans come first: a gate is nearly always a checkbox. */
export function gateFieldNames(draft: DriverDefinition): string[] {
  const schema = (draft.config_schema ?? {}) as Record<
    string,
    { type?: string } | undefined
  >;
  const names = new Set([
    ...Object.keys(schema),
    ...Object.keys((draft.default_config ?? {}) as Record<string, unknown>),
  ]);
  return [...names].sort((a, b) => {
    const aBool = schema[a]?.type === "boolean" ? 0 : 1;
    const bBool = schema[b]?.type === "boolean" ? 0 : 1;
    return aBool - bBool || a.localeCompare(b);
  });
}
