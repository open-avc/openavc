import type {
  DriverActionDef,
  DriverVisibleWhen,
  DriverVisibleWhenCondition,
} from "../../api/types";
import { asksFirst, setsItsOwnConfirm } from "../shared/commandConfirm";

/**
 * Pure helpers for the Actions editor (ActionsEditor.tsx). Kept out of the
 * component so the quick-action conversion and visible_when handling can be
 * unit-tested without a DOM.
 */

/**
 * Fold a legacy `quick_actions` list into explicit `actions` entries: each
 * command id becomes `{ id, kind: "command" }`, appended in order after any
 * existing actions. Ids already covered by an explicit action are skipped —
 * the runtime gives explicit entries precedence, so converting them again
 * would only create duplicate-id errors.
 */
export function convertQuickActionsToActions(
  actions: DriverActionDef[] | undefined,
  quickActions: string[] | undefined,
): DriverActionDef[] {
  const existing = actions ?? [];
  const seen = new Set(existing.map((a) => a.id));
  const converted: DriverActionDef[] = [];
  for (const id of quickActions ?? []) {
    if (typeof id !== "string" || !id || seen.has(id)) continue;
    seen.add(id);
    converted.push({ id, kind: "command" });
  }
  return [...existing, ...converted];
}

/** The editing mode a visible_when block is in. "always" = no condition. */
export type VisibleWhenMode = "always" | "single" | "any" | "all";

export function visibleWhenMode(
  vw: DriverVisibleWhen | undefined,
): VisibleWhenMode {
  if (!vw || typeof vw !== "object") return "always";
  const rec = vw as Record<string, unknown>;
  // Mirror the runtime: the presence of an any/all key makes it a group
  // (any wins when both appear, matching the backend's check order).
  if ("any" in rec) return "any";
  if ("all" in rec) return "all";
  return "single";
}

/** The conditions list of a visible_when block, whatever its mode. */
export function visibleWhenConditions(
  vw: DriverVisibleWhen | undefined,
): DriverVisibleWhenCondition[] {
  const mode = visibleWhenMode(vw);
  if (mode === "always") return [];
  if (mode === "single") return [vw as DriverVisibleWhenCondition];
  const group = (vw as Record<string, unknown>)[mode];
  return Array.isArray(group) ? (group as DriverVisibleWhenCondition[]) : [];
}

/**
 * Coerce a condition-value text input to the primitive that lands in YAML.
 * State values are flat primitives, so a numeric-looking string almost always
 * means the number (ordering operators compare numerically) and true/false
 * mean the boolean. Anything else stays a string.
 */
export function coerceConditionValue(raw: string): string | number | boolean {
  const trimmed = raw.trim();
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  if (/^-?\d+$/.test(trimmed)) return parseInt(trimmed, 10);
  if (/^-?\d+\.\d+$/.test(trimmed)) return parseFloat(trimmed);
  return raw;
}

/**
 * Keys of `obj` outside the recognized set — carried across visible_when
 * restructures so hand-authored extras survive a round-trip through the
 * editor (the runtime tolerates unknown keys, so the editor must too).
 */
export function extraKeys(
  obj: Record<string, unknown>,
  known: readonly string[],
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (!known.includes(k)) out[k] = v;
  }
  return out;
}

/** What a button's "Ask before running" shows and writes. The button asks
 *  what the action sets for it, else what its command asks (only false turns
 *  that off), as the server resolves it. */
export interface ButtonConfirm {
  /** The tick: whether the button asks. */
  enabled: boolean;
  /** The action's confirm once the tick changes: with a command that asks,
   *  ticking goes back to its question and unticking writes false. */
  ticked: (checked: boolean) => boolean | undefined;
  /** The action's confirm once its sentence changes: blank goes back to the
   *  command's question, else to the plain one. */
  typed: (text: string) => string | boolean | undefined;
  placeholder: string;
}

export function buttonConfirm(
  own: unknown,
  commands: Record<string, { confirm?: unknown } | undefined>,
  command: string | undefined,
): ButtonConfirm {
  const commandAsks = commandAsksFirst(commands, command);
  return {
    enabled: setsItsOwnConfirm(own) ? asksFirst(own) : commandAsks,
    ticked: (checked) => tickedConfirm(checked, commandAsks),
    typed: (text) => typedConfirm(text, commandAsks),
    placeholder: commandAsks ? "Leave blank to ask what the command asks" : "Leave blank for the generic prompt",
  };
}

function commandAsksFirst(
  commands: Record<string, { confirm?: unknown } | undefined>,
  command: string | undefined,
): boolean {
  return command !== undefined && asksFirst(commands[command]?.confirm);
}

function tickedConfirm(checked: boolean, commandAsks: boolean): boolean | undefined {
  if (commandAsks) return checked ? undefined : false;
  return checked ? true : undefined;
}

function typedConfirm(text: string, commandAsks: boolean): string | boolean | undefined {
  if (text.trim()) return text;
  return commandAsks ? undefined : true;
}
