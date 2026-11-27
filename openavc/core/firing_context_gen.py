"""Render the firing context's field table into the IDE.

What each producer of a macro run (a trigger by its type, a control) puts in
the run's firing context, and what each field holds, is written once:
``FIRING_CONTEXTS`` in ``openavc/core/value_resolver.py``. This writes it to
``openavc/web/programmer/src/api/firingContext.gen.ts``, which the state-key
picker's trigger group and the trigger editor read, so a field a producer
gains reaches both at the next render.

Data only. Run:  python -m openavc.core.firing_context_gen
A test compares the committed file against a fresh render, so a field changed
without rendering again, or the file edited by hand, fails CI.
"""

from __future__ import annotations

import json
from typing import Any

from openavc.core.value_resolver import FIRING_CONTEXTS
from openavc.utils.generated import write_artifacts

ARTIFACT = "openavc/web/programmer/src/api/firingContext.gen.ts"

#: The committed files.
ARTIFACTS = (ARTIFACT,)

BANNER = """\
// GENERATED FILE - DO NOT EDIT.
// Rendered from the firing context's field table
// (openavc/core/value_resolver.py FIRING_CONTEXTS).
// Regenerate with:  python -m openavc.core.firing_context_gen
// A test compares this file against a fresh render, so hand edits fail CI.
//
// What each producer of a macro run puts in the run's firing context, which a
// step reads as `$trigger.<field>` and a condition as the key
// `trigger.<field>`. Keyed by the producer: a trigger's own type, and `press`
// for a control that runs the macro. `name` is what ran the macro, `fields`
// what the macro can read, and `carries_payload` that the context also holds
// every field of the event's own payload, under the names its sender gave
// them. Fire now in the trigger list hands the fields of its trigger's type
// that the trigger alone can supply. A macro run by a Run Macro step reads the
// context of the macro that ran it; a run started any other way has none.
"""

BODY = """
/** One field of a firing context, read as `trigger.<name>`. */
export interface ContextField {
  readonly name: string;
  readonly description: string;
}

/** What one producer of a macro run puts in the run's firing context. */
export interface FiringContext {
  readonly name: string;
  readonly fields: readonly ContextField[];
  readonly carries_payload: boolean;
}

export const FIRING_CONTEXTS = %(table)s as const satisfies Readonly<Record<string, FiringContext>>;
"""


def firing_contexts() -> dict[str, Any]:
    """The table as the IDE reads it."""
    return {
        producer: {
            "name": context.name,
            "fields": [field._asdict() for field in context.fields],
            "carries_payload": context.carries_payload,
        }
        for producer, context in FIRING_CONTEXTS.items()
    }


def render(artifact: str = ARTIFACT) -> str:
    table = json.dumps(firing_contexts(), indent=2, ensure_ascii=False)
    return BANNER + BODY % {"table": table}


def main() -> int:
    return write_artifacts(ARTIFACTS, render)


if __name__ == "__main__":
    raise SystemExit(main())
