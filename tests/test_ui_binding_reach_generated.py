"""The generated binding-reach table must match the reviewer's.

``server/ui/page_review.py`` holds the tables; ``python -m openavc.ui.review_gen``
renders them into the Programmer IDE's ``uiBindingReach.gen.ts``. This re-renders
and compares byte-for-byte, so editing a table without regenerating -- or
hand-editing the artifact -- fails CI instead of letting the AI door and the
Builder quietly disagree about which bindings a type actually draws.

The Python side of the same tables is itself re-derived from ``panel.js`` by
``tests/test_ui_page_review_mirrors.py``, so the chain runs renderer -> Python ->
TypeScript with a test at each link and no hand-written copy anywhere in it.

Same arrangement as tests/test_ui_minimums_generated.py.
"""

from __future__ import annotations

from pathlib import Path

from openavc.ui.page_review import HONORED_SHOW_SLOTS, STATE_LABEL_TYPES
from openavc.ui.review_gen import ARTIFACT, render

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_committed_artifact_matches_the_tables() -> None:
    path = REPO_ROOT / ARTIFACT
    assert path.is_file(), f"{ARTIFACT} is missing -- run 'python -m openavc.ui.review_gen'"
    committed = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == render(), (
        f"{ARTIFACT} does not match the tables -- regenerate with "
        f"'python -m openavc.ui.review_gen' (never edit it by hand)"
    )


def test_every_element_type_reaches_the_artifact() -> None:
    """A type added to the reviewer must appear, not silently stay Python-only.

    A type missing from the Builder's copy is not a crash: the lookup misses,
    the check says nothing, and that element's inert bindings go unreported on
    the one surface a human is looking at.
    """
    rendered = render()
    for el_type in HONORED_SHOW_SLOTS:
        assert f'"{el_type}"' in rendered, (
            f"{el_type} is in HONORED_SHOW_SLOTS but not in the generated table"
        )


def test_the_state_label_types_are_a_subset_of_the_types_that_read_look() -> None:
    """Drawing state TEXT is a narrower thing than reading the look binding.

    Four types read ``show.look``; two of them have somewhere to put a string.
    A type listed here that does not read ``look`` at all would be a table
    describing something the renderer cannot do.
    """
    for el_type in STATE_LABEL_TYPES:
        assert "look" in HONORED_SHOW_SLOTS[el_type], (
            f"{el_type} is said to draw state labels but does not read show.look"
        )
