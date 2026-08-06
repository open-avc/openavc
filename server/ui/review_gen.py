"""Render the binding-reach tables into the Programmer IDE's types.

Produces ``web/programmer/src/api/uiBindingReach.gen.ts`` from
``HONORED_SHOW_SLOTS`` and ``STATE_LABEL_TYPES`` in ``page_review.py``. Data
only -- the rows, not the arithmetic over them -- so the Builder and the AI door
answer "does this element type's renderer read this binding" from one table.

Generated rather than hand-mirrored on purpose. The table is not a design
decision anyone can re-derive by reading it: it is what each render function in
``web/panel/panel.js`` actually looks at, eighteen types wide, and
``tests/test_ui_page_review_mirrors.py`` re-derives the Python side from the
renderer itself. A hand-written TypeScript copy would be a third version of a
fact that already has two, and the failure it produces is silent -- a confident
warning about a binding that works, or silence about one that does nothing.

Run:  python -m server.ui.review_gen
A test compares the committed file against a fresh render, so editing the
tables without regenerating (or hand-editing the artifact) fails CI.
"""

from __future__ import annotations

import json
from pathlib import Path

from server.ui.page_review import HONORED_SHOW_SLOTS, STATE_LABEL_TYPES

ARTIFACT = "web/programmer/src/api/uiBindingReach.gen.ts"

BANNER = """\
// GENERATED FILE - DO NOT EDIT.
// Rendered from the binding-reach tables (server/ui/page_review.py).
// Regenerate with:  python -m server.ui.review_gen
// A test compares this file against a fresh render, so hand edits fail CI.
//
// `show` accepts the same four slots for every element type, and each render
// function in web/panel/panel.js reads only some of them. A slot the renderer
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
