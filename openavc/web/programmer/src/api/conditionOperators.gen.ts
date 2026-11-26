// GENERATED FILE - DO NOT EDIT.
// Rendered from the condition operator lists
// (openavc/core/condition_eval.py OPERATORS, OPERATOR_ALIASES,
// VALUELESS_OPERATORS, ANY_CHANGE, PLACES).
// Regenerate with:  python -m openavc.core.condition_gen
// A test compares this file against a fresh render, so hand edits fail CI.
//
// The operators a condition may use, by their own names in the order an
// editor lists them; every other spelling the runtime takes, mapped to the
// operator it names (anything else, a name in another case included, is no
// operator); the operators that read no value; a State Change trigger's own
// operator for any change; and each place a condition sits, with whether it
// takes that operator. Where it does, an operator left out, blank or null
// reads as it; elsewhere one left out reads as `eq` (conditionEval.ts
// writtenOperator).

export const OPERATORS = ["eq", "ne", "gt", "lt", "gte", "lte", "truthy", "falsy"] as const;

/** An operator by its own name. */
export type Operator = (typeof OPERATORS)[number];

export const OPERATOR_ALIASES = {
  "equals": "eq",
  "not_equals": "ne",
  "==": "eq",
  "!=": "ne",
  ">": "gt",
  "<": "lt",
  ">=": "gte",
  "<=": "lte",
  "equal": "eq",
  "not_equal": "ne",
  "greater_than": "gt",
  "less_than": "lt",
  "greater_or_equal": "gte",
  "less_or_equal": "lte"
} as const satisfies Readonly<Record<string, Operator>>;

export const VALUELESS_OPERATORS = ["truthy", "falsy"] as const satisfies readonly Operator[];

export const ANY_CHANGE = "any";

export const PLACES = {
  "skip_if": {
    "takes_any_change": false
  },
  "conditional": {
    "takes_any_change": false
  },
  "wait_until": {
    "takes_any_change": false
  },
  "guard": {
    "takes_any_change": false
  },
  "state_trigger": {
    "takes_any_change": true
  }
} as const satisfies Readonly<Record<string, { readonly takes_any_change: boolean }>>;

/** A place a condition sits. */
export type ConditionPlace = keyof typeof PLACES;
