/**
 * The state keys a control, a macro step and a trigger read, each with the
 * words the Variables view's Used By list says it in: what Delete Unused
 * counts as a use. A condition reads its own key and the key its value names
 * (`$var.wanted`, `conditionKeys`); a value its operator does not read names
 * nothing.
 */

import type { MacroStep, TriggerConfig } from "../../api/types";
import { conditionKeys, readsNoValue, referencedKey } from "../../api/conditionEval";
import { ANY_CHANGE } from "../../api/conditionOperators.gen";
import type { ViewId } from "../../components/layout/Sidebar";
import type { FocusTarget } from "../../store/navigationStore";

/** A control's `show` bindings, by slot. */
type ShowBindings = Record<string, any>;

/** The keys a control's `visible_when` reads: each condition's own key and
 *  the key its value names (`conditionKeys`), under `all`, under `any` and
 *  beside them, each once. */
function visibilityKeys(show: ShowBindings): string[] {
  const vw = show.visible_when;
  if (!vw || typeof vw !== "object") return [];
  const conditions = [...(Array.isArray(vw.all) ? vw.all : []), ...(Array.isArray(vw.any) ? vw.any : []), vw];
  const keys = conditions.flatMap((c: unknown) => (c && typeof c === "object" ? conditionKeys(c) : []));
  return [...new Set(keys)];
}

/** The state keys a control shows from, each with where it is said: its
 *  value source, its appearance, then every key its visibility reads. */
export function shownKeys(show: ShowBindings): [string, string][] {
  const found: [string, string][] = [];
  for (const [slot, detail] of [["value", "Value source"], ["look", "Appearance binding"]] as const) {
    const key = show[slot]?.key;
    if (typeof key === "string" && key) found.push([key, detail]);
  }
  return [...found, ...visibilityKeys(show).map((key): [string, string] => [key, "Visibility condition"])];
}

/** The keys a step's conditions compare with (a value that names a key,
 *  `$var.wanted`; `referencedKey`), each with where it is said: its Skip If,
 *  then a Conditional's or a Wait Until's own condition. */
export function comparedWith(step: MacroStep): [string, string][] {
  const own = step.action === "conditional" ? "Conditional condition" : "Wait Until condition";
  return [
    ...namedKey(step.skip_if, "Skip-if guard: compared with"),
    ...namedKey(step.condition, `${own}: compared with`),
  ];
}

/** The key a condition's value names, with where it is said; none for a
 *  value written as itself or one its operator does not read. */
function namedKey(
  cond: { operator?: string | null; value?: unknown } | undefined,
  where: string,
  place?: "state_trigger",
): [string, string][] {
  if (!cond || readsNoValue(cond.operator ?? "eq", place)) return [];
  const named = referencedKey(cond.value);
  return named ? [[named, where]] : [];
}

/** The keys a trigger's guards, and a State Change trigger's own value,
 *  compare with, each with where it is said. */
function triggerComparedWith(trigger: TriggerConfig): [string, string][] {
  const guards = (trigger.conditions ?? []).flatMap((cond) => (
    namedKey(cond, `Trigger "${trigger.id}": guard condition: compared with`)
  ));
  if (trigger.type !== "state_change") return guards;
  const own = { operator: trigger.state_operator ?? ANY_CHANGE, value: trigger.state_value };
  return [...guards, ...namedKey(own, `Trigger "${trigger.id}": compared with on a state change`, "state_trigger")];
}

/** The keys one trigger reads, each with where it is said: the key it
 *  watches (`watched` names it), each guard's key, and each key a guard's or
 *  its own value names. */
export function triggerKeys(trigger: TriggerConfig, watched: string): [string, string][] {
  const found: [string, string][] = trigger.state_key ? [[trigger.state_key, `Trigger "${trigger.id}": ${watched}`]] : [];
  for (const cond of trigger.conditions ?? []) {
    if (cond.key) found.push([cond.key, `Trigger "${trigger.id}": guard condition`]);
  }
  return [...found, ...triggerComparedWith(trigger)];
}

/** A master element as the Used By list names it, and where a click on it goes. */
export function masterLabel(mel: { id: string; label?: string; type: string }): string {
  return `Master Elements → ${mel.label || mel.type} (${mel.id})`;
}

export function masterNav(mel: { id: string }): { view: ViewId; focus: FocusTarget } {
  return { view: "ui-builder", focus: { type: "element", id: mel.id, detail: "master" } };
}
