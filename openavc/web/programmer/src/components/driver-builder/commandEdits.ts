/** The Driver Builder's rules for changing a driver's commands table: what a
 *  new command starts as, what a rename carries with it, and how a renamed
 *  parameter's placeholder follows it through its command's own strings.
 *
 *  No React. The command list and the parameter editor ask these. Everything
 *  that names a command by id keeps pointing at it through a rename: a field
 *  of another command (the command that wakes the device, a read-back query)
 *  because it is listed in `NAMES_A_COMMAND`, and the rest of the draft (a
 *  button, a start-up or poll line, a follow-up query, the routing command, a
 *  push registration) because its section is listed in
 *  `SECTIONS_NAMING_A_COMMAND`. A new field that names a command is one entry
 *  in one of the two. */

import type { DriverActionDef, DriverCommandDef, DriverDefinition } from "../../api/types";
import type { RenameResult } from "./IdRenameInput";

type Commands = Record<string, DriverCommandDef>;

/** How a command's fields write it: a partial, a field set to undefined removed. */
export type CommandUpdate = (partial: Partial<DriverCommandDef>) => void;

/** What a command starts as on the driver's transport, so its card opens on
 *  the fields that transport sends with. */
function seed(transport: string | undefined): DriverCommandDef {
  if (transport === "http") {
    return { label: "New Command", send: "", method: "GET", path: "/", params: {} };
  }
  if (transport === "osc") {
    return { label: "New Command", send: "", address: "/", args: [], params: {} };
  }
  return { label: "New Command", send: "", params: {} };
}

/** A new command's id, numbered past the ones taken, and what it starts as. */
export function newCommand(
  commands: Commands,
  transport: string | undefined,
): [string, DriverCommandDef] {
  let counter = Object.keys(commands).length + 1;
  let name = `command_${counter}`;
  while (name in commands) {
    counter++;
    name = `command_${counter}`;
  }
  return [name, seed(transport)];
}

/** The fields of a command that name another command by its id: one id, or
 *  (`ids`) one id or a list of them. */
const NAMES_A_COMMAND: Record<"sleeps_device" | "read_back", "id" | "ids"> = {
  sleeps_device: "id",
  read_back: "ids",
};

/** A command id renamed: the id it had and the id it has now. */
interface Rename {
  from: string;
  to: string;
}

function renamedIn(value: unknown, kind: "id" | "ids", { from, to }: Rename): unknown {
  if (value === from) return to;
  if (kind === "ids" && Array.isArray(value) && value.includes(from)) {
    return value.map((q) => (q === from ? to : q));
  }
  return value;
}

/** `cmd` with every reference to the renamed command pointed at its new id;
 *  the same object when it names no such command. */
function followRename(cmd: DriverCommandDef, rename: Rename): DriverCommandDef {
  let moved = cmd;
  for (const [field, kind] of Object.entries(NAMES_A_COMMAND)) {
    const key = field as keyof typeof NAMES_A_COMMAND;
    const next = renamedIn(moved[key], kind, rename);
    if (next !== moved[key]) moved = { ...moved, [key]: next };
  }
  return moved;
}

/** Command `from` renamed `to`, in its place in the table, with the commands
 *  that name it following it. A refusal carries its reason; a rename to the
 *  same id is accepted with nothing to write. */
export function commandsRenamed(
  commands: Commands,
  from: string,
  to: string,
): RenameResult & { commands?: Commands } {
  if (!to) return { ok: false, reason: "ID can't be empty." };
  if (to === from) return { ok: true };
  if (to in commands) return { ok: false, reason: `"${to}" already exists.` };
  const next: Commands = {};
  for (const [k, v] of Object.entries(commands)) {
    next[k === from ? to : k] = followRename(v, { from, to });
  }
  return { ok: true, commands: next };
}

/** `list` with `each` applied to every item; the same list when none changed. */
function eachRenamed<T>(list: T[] | undefined, each: (item: T) => T): T[] | undefined {
  if (!Array.isArray(list)) return list;
  const next = list.map(each);
  return next.some((item, i) => item !== list[i]) ? next : list;
}

/** `obj` with the fields of `next` that differ from its own; the same object
 *  when none do. */
function withChanged<T extends object>(obj: T, next: Partial<T>): T {
  const own = obj as Record<string, unknown>;
  const changed = Object.entries(next).filter(([k, v]) => v !== own[k]);
  return changed.length ? { ...obj, ...Object.fromEntries(changed) } : obj;
}

/** A mapping entry's `send` line; undefined for a bare line. */
function sendOf(entry: unknown): unknown {
  return entry instanceof Object ? (entry as { send?: unknown }).send : undefined;
}

/** A start-up, poll or follow-up entry: a bare line, or a mapping whose
 *  `send` is the line. The runtime sends a line that names a declared
 *  command as that command, so the entry follows the rename. */
function queryEntryRenamed<T>(entry: T, { from, to }: Rename): T {
  if (entry === from) return to as T;
  return sendOf(entry) === from ? ({ ...(entry as object), send: to } as T) : entry;
}

/** An action that sends a command: its `command`, or its own id when it
 *  names none (the command the runtime sends then), pointed at the new id. */
function actionRenamed(action: DriverActionDef, { from, to }: Rename): DriverActionDef {
  if ((action.kind ?? "command") !== "command") return action;
  return (action.command || action.id) === from ? { ...action, command: to } : action;
}

type Sections = Required<
  Pick<DriverDefinition, "quick_actions" | "actions" | "on_connect" | "polling" | "responses" | "routing" | "push">
>;

/** The draft's sections outside the commands table that name a command, each
 *  rewritten for a rename: the same value when it names no such command. */
const SECTIONS_NAMING_A_COMMAND: { [K in keyof Sections]: (value: Sections[K], r: Rename) => Sections[K] } = {
  quick_actions: (ids, r) => eachRenamed(ids, (id) => (id === r.from ? r.to : id)) ?? ids,
  actions: (actions, r) => eachRenamed(actions, (a) => actionRenamed(a, r)) ?? actions,
  on_connect: (entries, r) => eachRenamed(entries, (e) => queryEntryRenamed(e, r)) ?? entries,
  polling: (polling, r) =>
    withChanged(polling, { queries: eachRenamed(polling.queries, (e) => queryEntryRenamed(e, r)) }),
  responses: (rules, r) =>
    eachRenamed(rules, (rule) =>
      withChanged(rule, { then_query: eachRenamed(rule.then_query, (e) => queryEntryRenamed(e, r)) }),
    ) ?? rules,
  routing: (routing, r) =>
    withChanged(routing, {
      command: routing.command === r.from ? r.to : routing.command,
      planes: eachRenamed(routing.planes, (plane) =>
        plane.command === r.from ? { ...plane, command: r.to } : plane,
      ),
    }),
  push: (push, r) => {
    const named = <T>(name: T): T => (name === r.from ? (r.to as T) : name);
    const register = Array.isArray(push.register)
      ? eachRenamed(push.register, (e) =>
          typeof e === "string" ? named(e) : e.command === r.from ? { ...e, command: r.to } : e,
        )
      : named(push.register);
    return withChanged(push, { register, unregister: named(push.unregister) });
  },
};

/** Command `from` renamed `to` across the whole draft: the commands table
 *  (`commandsRenamed`) and every section that names a command. `update` holds
 *  only what changed; a refusal carries its reason, and a rename to the same
 *  id is accepted with nothing to write. */
export function draftCommandRenamed(
  draft: DriverDefinition,
  from: string,
  to: string,
): RenameResult & { update?: Partial<DriverDefinition> } {
  const result = commandsRenamed(draft.commands, from, to);
  if (!result.commands) return { ok: result.ok, reason: result.reason };
  const update: Record<string, unknown> = { commands: result.commands };
  for (const [section, rewrite] of Object.entries(SECTIONS_NAMING_A_COMMAND)) {
    const value = draft[section as keyof Sections];
    if (value == null) continue;
    const next = (rewrite as (v: unknown, r: Rename) => unknown)(value, { from, to });
    if (next !== value) update[section] = next;
  }
  return { ok: true, update: update as Partial<DriverDefinition> };
}

/** A command's whole-string fields a parameter's placeholder can sit in. */
const TEXT_FIELDS = ["send", "path", "body", "address"] as const;
/** A command's maps whose string values can hold one. */
const MAP_FIELDS = ["headers", "query_params", "sets", "udp"] as const;

type Swap = (text: string) => string;

/** The text swapped; undefined when it is not text or names nothing to swap. */
function swapText(text: unknown, swap: Swap): string | undefined {
  return typeof text === "string" && swap(text) !== text ? swap(text) : undefined;
}

/** The map with each string value swapped; undefined when none changed. */
function swapValues<T>(map: Record<string, T> | undefined, swap: Swap): Record<string, T> | undefined {
  if (!map) return undefined;
  let changed = false;
  const next: Record<string, T> = {};
  for (const [k, v] of Object.entries(map)) {
    const nv = typeof v === "string" ? (swap(v) as T) : v;
    if (nv !== v) changed = true;
    next[k] = nv;
  }
  return changed ? next : undefined;
}

/** The OSC args with each string value swapped; undefined when none changed. */
function swapArgs(args: DriverCommandDef["args"], swap: Swap): DriverCommandDef["args"] {
  if (!args?.some((a) => typeof a.value === "string" && swap(a.value) !== a.value)) return undefined;
  return args.map((a) => (typeof a.value === "string" ? { ...a, value: swap(a.value) } : a));
}

/** The fields of `cmd` that name `{from}` (with or without a format spec),
 *  rewritten to name `{to}`; fields that do not name it are left out. A
 *  parameter belongs to its command, so only that command's own strings are
 *  touched: what it sends, and what its Sets State rows write. */
export function renamePlaceholder(
  cmd: DriverCommandDef,
  from: string,
  to: string,
): Partial<DriverCommandDef> {
  const token = new RegExp(`\\{${from}(:[^{}]*)?\\}`, "g");
  const swap: Swap = (text) => text.replace(token, (_m, spec?: string) => `{${to}${spec ?? ""}}`);
  const swapped: [string, unknown][] = [
    ...TEXT_FIELDS.map((f): [string, unknown] => [f, swapText(cmd[f], swap)]),
    ...MAP_FIELDS.map((f): [string, unknown] => [
      f,
      swapValues(cmd[f] as Record<string, unknown> | undefined, swap),
    ]),
    ["args", swapArgs(cmd.args, swap)],
  ];
  return Object.fromEntries(swapped.filter(([, v]) => v !== undefined)) as Partial<DriverCommandDef>;
}
