"""Render the condition operator lists into the Programmer.

The operators a condition may use, their other spellings, the ones that read
no value, a State Change trigger's own ``any`` and the places a condition sits
are written once, in ``openavc/core/condition_eval.py`` (``OPERATORS``,
``OPERATOR_ALIASES``, ``VALUELESS_OPERATORS``, ``ANY_CHANGE``, ``PLACES``).
This writes them to ``openavc/web/programmer/src/api/conditionOperators.gen.ts``,
which the operator pickers (a macro step's condition, a control's visibility,
a trigger's own operator and its guards), the trigger summaries and the
Programmer's copy of the comparison (``conditionEval.ts``) read, so a project
holding another spelling of an operator shows it as that operator, and an
operator the runtime gains reaches every picker at the next render.

Data only. Run:  python -m openavc.core.condition_gen
A test compares the committed file against a fresh render, so a list changed
without rendering again, or the file edited by hand, fails CI.
"""

from __future__ import annotations

import json
from typing import Any

from openavc.core.condition_eval import (
    ANY_CHANGE,
    OPERATOR_ALIASES,
    OPERATORS,
    PLACES,
    VALUELESS_OPERATORS,
)
from openavc.utils.generated import write_artifacts

ARTIFACT = "openavc/web/programmer/src/api/conditionOperators.gen.ts"

#: The committed files.
ARTIFACTS = (ARTIFACT,)

BANNER = """\
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
"""

BODY = """
export const OPERATORS = %(operators)s as const;

/** An operator by its own name. */
export type Operator = (typeof OPERATORS)[number];

export const OPERATOR_ALIASES = %(aliases)s as const satisfies Readonly<Record<string, Operator>>;

export const VALUELESS_OPERATORS = %(valueless)s as const satisfies readonly Operator[];

export const ANY_CHANGE = %(any_change)s;

export const PLACES = %(places)s as const satisfies Readonly<Record<string, { readonly takes_any_change: boolean }>>;

/** A place a condition sits. */
export type ConditionPlace = keyof typeof PLACES;
"""


def _ts(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def places() -> dict[str, dict[str, bool]]:
    """Each place as the Programmer reads it."""
    return {where: {"takes_any_change": place.takes_any_change} for where, place in PLACES.items()}


def render(artifact: str = ARTIFACT) -> str:
    return BANNER + BODY % {
        "operators": json.dumps(list(OPERATORS)),
        "aliases": _ts(OPERATOR_ALIASES),
        "valueless": json.dumps(list(VALUELESS_OPERATORS)),
        "any_change": json.dumps(ANY_CHANGE),
        "places": _ts(places()),
    }


def main() -> int:
    return write_artifacts(ARTIFACTS, render)


if __name__ == "__main__":
    raise SystemExit(main())
