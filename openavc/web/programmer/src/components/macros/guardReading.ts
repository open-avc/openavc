/**
 * What a trigger's guard reads before the trigger fires, for the trigger
 * editor's preview.
 *
 * A guard's `trigger.<field>` key, and a value that names one
 * (`$trigger.new_value`), are read from what fired the trigger, as the
 * trigger engine reads them (core/trigger_engine.py `_check_conditions`,
 * through value_resolver's `read_operand`). Neither has a value until the
 * trigger fires, so the preview judges only the guards on the state.
 */
import type { TriggerCondition } from "../../api/types";
import { referencedKey } from "../../api/conditionEval";

/** The namespace a guard reads what fired the trigger through
 *  (value_resolver's `TRIGGER_PREFIX`). */
const TRIGGER_PREFIX = "trigger.";

/** Whether `key` is a field of what fired the trigger (`trigger.new_value`). */
export function isFiredField(key: string | null | undefined): boolean {
  return typeof key === "string" && key.startsWith(TRIGGER_PREFIX);
}

/** Whether a guard reads what fired the trigger: its key, or the key its
 *  value names. */
export function readsWhatFired(cond: TriggerCondition): boolean {
  return isFiredField(cond.key) || isFiredField(referencedKey(cond.value));
}

/** What the preview says of a trigger's guards now: one on the state fails;
 *  all hold; those on the state hold and the rest read what fired it; or all
 *  read what fired it. */
export type GuardVerdict = "fails" | "fires" | "so_far" | "when_it_fires";

/** The preview's verdict on `conditions`, judging each guard on the state
 *  with `holds`. */
export function guardVerdict(
  conditions: TriggerCondition[],
  holds: (cond: TriggerCondition) => boolean,
): GuardVerdict {
  const onState = conditions.filter((cond) => !readsWhatFired(cond));
  if (!onState.every(holds)) return "fails";
  if (onState.length === conditions.length) return "fires";
  return onState.length > 0 ? "so_far" : "when_it_fires";
}
