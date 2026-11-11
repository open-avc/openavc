/** The Driver Builder's rules for changing a driver's commands table: what a
 *  new command starts as, what a rename carries with it, and how a renamed
 *  parameter's placeholder follows it through its command's own strings.
 *
 *  No React. The command list and the parameter editor ask these. A command
 *  that names another one by id (the command that wakes the device, a
 *  read-back query) keeps pointing at it through a rename because the field
 *  is listed in `NAMES_A_COMMAND`; a new field that names a command is one
 *  entry there. */

import type { DriverCommandDef } from "../../api/types";
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
