"""Render the binding-reach tables into the Programmer IDE's types.

Produces ``openavc/web/programmer/src/api/uiBindingReach.gen.ts`` from
``HONORED_SHOW_SLOTS`` and ``STATE_LABEL_TYPES`` in ``page_review.py``. Data
only -- the rows, not the arithmetic over them -- so the Builder and the AI door
answer "does this element type's renderer read this binding" from one table.

Generated rather than hand-mirrored on purpose. The table is not a design
decision anyone can re-derive by reading it: it is what each render function in
``openavc/web/panel/panel.js`` actually looks at, eighteen types wide, and
``tests/test_ui_page_review_mirrors.py`` re-derives the Python side from the
renderer itself. A hand-written TypeScript copy would be a third version of a
fact that already has two, and the failure it produces is silent -- a confident
warning about a binding that works, or silence about one that does nothing.

Run:  python -m openavc.ui.review_gen
A test compares the committed file against a fresh render, so editing the
tables without regenerating (or hand-editing the artifact) fails CI.
"""

from __future__ import annotations

import json
from pathlib import Path

from openavc.ui.page_references import NAVIGATION_SENTINELS
from openavc.ui.page_review import (
    HONORED_PROPERTIES,
    HONORED_SHOW_SLOTS,
    MATRIX_CONFIG_KEYS,
    STATE_LABEL_TYPES,
    STRUCTURAL_PROPERTIES,
)

ARTIFACT = "openavc/web/programmer/src/api/uiBindingReach.gen.ts"

BANNER = """\
// GENERATED FILE - DO NOT EDIT.
// Rendered from the binding-reach tables (openavc/ui/page_review.py).
// Regenerate with:  python -m openavc.ui.review_gen
// A test compares this file against a fresh render, so hand edits fail CI.
//
// `show` accepts the same four slots for every element type, and each render
// function in openavc/web/panel/panel.js reads only some of them. A slot the renderer
// never looks at is silently inert: the element draws, the state key resolves,
// and the thing the author asked for simply never happens. That is how a label
// ended up carrying ONLINE / OFFLINE state text the panel has no code to draw.
//
// `visible_when` is absent on purpose -- it is registered for every element
// from the page tree, so it is honored everywhere and is never worth a warning.

"""

TYPES = """\
/** Which `show` slots each element type's renderer actually reads. */
export const HONORED_SHOW_SLOTS: Record<string, string[]> =
%(honored)s;

/**
 * Types whose `look` binding renders per-state TEXT.
 *
 * Everything else that reads `look` takes only colour from it -- a status LED
 * tints its dot, a select styles its options -- so a `states[].label` on those
 * never appears anywhere.
 */
export const STATE_LABEL_TYPES: string[] = %(state_labels)s;

/** The slots worth naming in a message, in the order a reader expects them. */
export const REVIEWED_SHOW_SLOTS = ["value", "look", "items"] as const;

/**
 * Which element PROPERTIES each type's renderer actually reads.
 *
 * The same problem as the slot table, one level out. Every optional field on
 * UIElement is settable on every type and the loader keeps all of them; each
 * renderer reads a handful. `label` is the sharp case -- nearly every renderer
 * draws it, and the `label` element is the one that does not (it draws `text`).
 */
export const HONORED_PROPERTIES: Record<string, string[]> =
%(properties)s;

/**
 * Fields that belong to every element and are read by something other than a
 * renderer, so no per-type table can vouch for them.
 *
 * `hidden` is here for the master-element case: on a page element it is
 * per-layout, but a master belongs to no layout and the panel reads it off the
 * element, so warning about it would fire on the correct spelling.
 */
export const STRUCTURAL_PROPERTIES: string[] = %(structural)s;

/**
 * Every key the matrix renderer reads out of `matrix_config`.
 *
 * That property is a bare dict at every layer -- no schema, no validation -- so
 * this table is the only thing that knows the shape. `route_key_pattern` is the
 * one with no default: without it no crosspoint ever lights.
 */
export const MATRIX_CONFIG_KEYS: string[] = %(matrix_keys)s;

/**
 * Navigation targets that are not page ids and never will be.
 *
 * The panel resolves both itself: `$back` dismisses an open overlay or pops the
 * page history, `$dismiss` closes an overlay and nothing else. A validator that
 * does not know them reports the documented spelling as a dangling page, and
 * believing it means hardcoding a page id into a confirm dialog's Cancel
 * button -- which makes the dialog single-use.
 */
export const NAVIGATION_SENTINELS = new Set(%(sentinels)s);
"""


def render() -> str:
    honored = json.dumps(
        {name: sorted(slots) for name, slots in HONORED_SHOW_SLOTS.items()},
        indent=2,
        ensure_ascii=False,
    )
    return BANNER + TYPES % {
        "honored": honored,
        "state_labels": json.dumps(sorted(STATE_LABEL_TYPES), ensure_ascii=False),
        "properties": json.dumps(
            {name: sorted(props) for name, props in HONORED_PROPERTIES.items()},
            indent=2,
            ensure_ascii=False,
        ),
        "structural": json.dumps(sorted(STRUCTURAL_PROPERTIES), ensure_ascii=False),
        "matrix_keys": json.dumps(sorted(MATRIX_CONFIG_KEYS), ensure_ascii=False),
        "sentinels": json.dumps(sorted(NAVIGATION_SENTINELS), ensure_ascii=False),
    }


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / ARTIFACT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {ARTIFACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
