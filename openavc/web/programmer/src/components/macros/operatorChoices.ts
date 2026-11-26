/**
 * An operator picker's options and the one it shows, for every picker that
 * edits a condition's operator: a macro step's condition and a control's
 * visibility (ConditionEditor), a State Change trigger's own operator and a
 * trigger's guards (TriggerEditor).
 *
 * The operators are the runtime's (conditionOperators.gen.ts, from
 * core/condition_eval.py), in its order; each picker brings only its own
 * words for them. The picker shows what the project holds, read as the
 * runtime reads it where the condition sits (`writtenOperator`): a State
 * Change trigger saved with a blank operator shows as any change, another
 * spelling of an operator (">=", "greater_than") shows as that operator with
 * its spelling, and a word nobody knows shows as written, marked not valid,
 * rather than as the first choice. Nothing is rewritten until the author picks.
 */

import { ANY_CHANGE, OPERATORS, PLACES, type ConditionPlace, type Operator } from "../../api/conditionOperators.gen";
import { knownOperator, writtenOperator } from "../../api/conditionEval";

/** A picker's words for each operator, and for `any` where its place takes it. */
export type OperatorWords = Readonly<Record<Operator, string>> & { readonly [ANY_CHANGE]?: string };

export interface OperatorChoice {
  readonly value: string;
  readonly label: string;
}

export interface OperatorPicker {
  /** The value the picker shows: the operator the project holds, as written. */
  readonly value: string;
  readonly choices: readonly OperatorChoice[];
}

/** The picker for a condition whose operator is `stored`, at `where` (none
 *  for a condition that does not take `any`). */
export function operatorPicker(stored: unknown, words: OperatorWords, where?: ConditionPlace): OperatorPicker {
  const ownAny = where !== undefined && PLACES[where].takes_any_change;
  const choices: OperatorChoice[] = [
    ...(ownAny ? [{ value: ANY_CHANGE, label: words[ANY_CHANGE] ?? ANY_CHANGE }] : []),
    ...OPERATORS.map((op) => ({ value: op, label: words[op] })),
  ];
  const written = writtenOperator(stored, where);
  const value = written === null || written === undefined ? "" : String(written);
  if (choices.some((choice) => choice.value === value)) return { value, choices };
  const named = knownOperator(value);
  if (named === null) {
    choices.push({ value, label: value === "" ? "(no operator, not valid)" : `${value} (not valid)` });
  } else {
    const at = choices.findIndex((choice) => choice.value === named);
    choices.splice(at + 1, 0, { value, label: `${words[named]} (${value})` });
  }
  return { value, choices };
}
