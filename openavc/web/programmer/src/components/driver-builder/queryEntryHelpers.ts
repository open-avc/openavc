import type {
  DriverDefinition,
  DriverEachChildQuery,
  DriverOscConnectItem,
  DriverQueryEntry,
  DriverWhenState,
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
 *   { each_child, send, page_size }                 one query per page of children
 *   { address, args }                               OSC message with typed args
 *   { address, args, when }                         OSC args, gated
 *   { send, when_state }                            gated on the device's state
 *
 * `when: <config_field>` runs the entry only while that config field is truthy
 * — how a driver arms a chatty subscription (a level-meter stream) behind an
 * integrator checkbox instead of forcing it on every site.
 *
 * `when_state: {<state var>: <value or values>}` runs the entry only while the
 * device's own state allows: a unit that any command wakes from standby is
 * left in standby. Every form but a bare string can carry it.
 *
 * `query_for: <state_var>` names the state variable the reply reports, so the
 * auto-generated simulator answers the query without name-guessing. On the
 * plain `{send}` form it names a device-level variable, or
 * `<child type>.<variable>` for a list read a `child_list` rule spreads across
 * every child; on an `each_child` form it names one of that child type's state
 * variables (each child answers from its own state). The OSC args form
 * doesn't carry one.
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

/** `page_size` on a per-child entry: one query per page of that many children
 *  (a list read that answers a page from the child asked for). */
export function queryPageSize(q: QueryEntry): number | undefined {
  if (isEachChild(q) && typeof q.page_size === "number") return q.page_size;
  return undefined;
}

/** An entry with its `page_size` set, or removed when `size` is not 2 or
 *  more. Only a per-child entry takes one. */
export function withPageSize(q: QueryEntry, size: number | undefined): QueryEntry {
  if (!isEachChild(q)) return q;
  const next: DriverEachChildQuery = { ...q };
  if (size !== undefined && Number.isInteger(size) && size >= 2) {
    next.page_size = size;
  } else {
    delete next.page_size;
  }
  return next;
}

/** The entry's `when_state` gate, or `undefined` when it has none. */
export function queryWhenState(q: QueryEntry): DriverWhenState | undefined {
  if (isEachChild(q) || isGated(q) || isOscItem(q)) {
    const gate = q.when_state;
    if (gate && typeof gate === "object" && Object.keys(gate).length > 0) {
      return gate;
    }
  }
  return undefined;
}

/** An entry with its `when_state` gate set, or removed when `gate` is empty.
 *  A bare string takes the `{send}` form to carry one, and a `{send}` entry
 *  left with nothing but its send goes back to a string. */
export function withWhenState(
  q: QueryEntry,
  gate: DriverWhenState | undefined,
): QueryEntry {
  const has = gate !== undefined && Object.keys(gate).length > 0;
  if (typeof q === "string") {
    return has ? { send: q, when_state: gate } : q;
  }
  if (isEachChild(q) || isGated(q) || isOscItem(q)) {
    const next = { ...q } as Record<string, unknown>;
    if (has) {
      next.when_state = gate;
    } else {
      delete next.when_state;
    }
    if (
      !has &&
      isGated(q) &&
      Object.keys(next).length === 1 &&
      typeof next.send === "string"
    ) {
      return next.send;
    }
    return next as QueryEntry;
  }
  return q;
}

/** Keys an edit of the send, scope, gate or pairing must not lose: what
 *  `buildQueryEntry` does not take as a part is copied from the entry being
 *  replaced, where the new shape can hold it. */
export function carryQueryExtras(prev: QueryEntry, next: QueryEntry): QueryEntry {
  return withWhenState(
    withPageSize(next, queryPageSize(prev)),
    queryWhenState(prev),
  );
}

/** A device-level state variable a `when_state` gate can name, with what the
 *  value editor needs: its type and, for an enum, its values. */
export interface StateGateVariable {
  name: string;
  label: string;
  type: string;
  values: string[];
}

/** The device-level state variables a `when_state` gate can read. */
export function stateGateVariables(draft: DriverDefinition): StateGateVariable[] {
  return Object.entries(draft.state_variables ?? {}).map(([name, def]) => ({
    name,
    label: def?.label || name,
    type: def?.type || "string",
    // A hand-written file may list {value, label} options.
    values: Array.isArray(def?.values)
      ? (def.values as unknown[]).map((v) =>
          typeof v === "object" && v !== null && "value" in v
            ? String((v as { value: unknown }).value)
            : String(v),
        )
      : [],
  }));
}

/** The value a new condition on this variable starts at. */
export function firstGateValue(v: StateGateVariable): string | number | boolean {
  if (v.type === "boolean") return false;
  if (v.type === "enum") return v.values[0] ?? "";
  if (v.type === "integer" || v.type === "number" || v.type === "float") return 0;
  return "";
}

/** The value a gate stores for what was picked: one value as itself, several
 *  as a list. */
export function gateValue(
  picked: (string | number | boolean)[],
): string | number | boolean | (string | number | boolean)[] {
  return picked.length === 1 ? picked[0] : picked;
}

/** Text typed for a number or text variable, as the values it lists:
 *  separated by commas, numbers parsed for a number variable (an entry that
 *  is not one is dropped). */
export function parseGateValues(
  text: string,
  type: string,
): (string | number)[] {
  const parts = text
    .split(",")
    .map((t) => t.trim())
    .filter((t) => t !== "");
  if (type === "integer" || type === "number" || type === "float") {
    return parts
      .map((t) => Number(t))
      .filter((n) => Number.isFinite(n) && (type !== "integer" || Number.isInteger(n)));
  }
  return parts;
}

/** The `<child type>.<variable>` pairs a query sent once can report: the
 *  child variables a `child_list` rule spreads from one reply. */
export function listReadOptions(draft: DriverDefinition): string[] {
  const out: string[] = [];
  for (const resp of draft.responses ?? []) {
    const list = resp.child_list;
    if (!list || typeof list.state !== "object" || list.state === null) continue;
    for (const prop of Object.keys(list.state)) {
      const name = `${list.type}.${prop}`;
      if (!out.includes(name)) out.push(name);
    }
  }
  return out;
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
