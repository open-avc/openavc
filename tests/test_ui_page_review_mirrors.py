"""The reviewer's two borrowed tables, checked against where they were borrowed from.

``page_review`` answers two questions it does not own the facts for:

* *does this element type's renderer read this binding* -- which lives in
  ``web/panel/panel.js``, in whichever render function the type dispatches to.
* *is this big enough for a finger* -- which lives in the Builder
  (``uiBuilderHelpers.ts``), measured against real panel diagonals.

Both are copies, and a copy of a fact drifts silently. These tests re-derive
each from its source and fail when they part company. The failure mode they
exist for is not a crash: a stale honored-slot table hands the AI a confident
warning about a binding that works fine, or stays silent about one that does
nothing, and either way the number that gets believed is the wrong one.

Reading JavaScript with a regex is imprecise, so both derivations are
deliberately coarse and fail loud rather than guessing. If a refactor makes the
parse wrong, the fix is to teach the parse, not to loosen the assertion.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from server.ui.page_review import (
    HONORED_SHOW_SLOTS,
    MINIMUM_VISIBLE_PX,
    STATE_LABEL_TYPES,
    TOUCH_MIN_MM,
    TOUCH_PX_PER_INCH,
    TOUCHABLE_TYPES,
)
from server.ui.control_minimums import REFERENCE_HEIGHT_PX, REFERENCE_WIDTH_PX, RULES

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
PANEL_JS = OPENAVC_ROOT / "web" / "panel" / "panel.js"
BUILDER_HELPERS = (
    OPENAVC_ROOT / "web" / "programmer" / "src" / "components" / "ui-builder"
    / "uiBuilderHelpers.ts"
)

# A class method at the top level of the panel renderer. Used only to find where
# one method's body ends -- without it, the last render function's body runs to
# the end of the file and picks up every `show.` in every evaluator.
_METHOD = re.compile(r"^    (?:async\s+)?([A-Za-z_$][\w$]*)\s*\(")
_SHOW_SLOT = re.compile(r"bindings\??\.\s*show\??\.\s*(\w+)")
_DISPATCH = re.compile(r"case '([\w_]+)':\s*return this\.(\w+)\(element\);")

# `visible_when` is registered for every element from the page tree rather than
# from any render function, so it is honored everywhere and is not in the table.
_UNIVERSAL_SLOTS = {"visible_when"}


@pytest.fixture(scope="module")
def panel_method_bodies() -> dict[str, str]:
    if not PANEL_JS.is_file():  # pragma: no cover - the repo always has it
        pytest.fail(f"panel renderer not found at {PANEL_JS}")
    lines = PANEL_JS.read_text(encoding="utf-8").split("\n")
    starts = [
        (i, match.group(1))
        for i, line in enumerate(lines)
        if (match := _METHOD.match(line))
    ]
    assert len(starts) > 50, "method scan found almost nothing -- the parse is wrong"
    starts.append((len(lines), "<eof>"))
    return {
        name: "\n".join(lines[start:end])
        for (start, name), (end, _) in zip(starts, starts[1:])
    }


@pytest.fixture(scope="module")
def renderer_by_type(panel_method_bodies: dict[str, str]) -> dict[str, str]:
    dispatch = dict(_DISPATCH.findall(panel_method_bodies["renderElement"]))
    assert len(dispatch) == len(HONORED_SHOW_SLOTS), (
        "the renderer dispatches "
        f"{sorted(dispatch)}, the review table covers {sorted(HONORED_SHOW_SLOTS)}"
    )
    return dispatch


def test_every_type_reads_exactly_what_the_table_says(
    panel_method_bodies: dict[str, str], renderer_by_type: dict[str, str],
) -> None:
    """The honored-slot table, re-derived from the renderer it describes.

    Both directions matter and they fail differently. A slot the table calls
    honored that the renderer never reads means a real defect goes unwarned --
    the label carrying ONLINE / OFFLINE state text that no code draws. A slot
    the table calls ignored that the renderer does read means a false warning,
    which is worse: it teaches the caller to distrust the whole field.
    """
    derived = {
        el_type: frozenset(
            set(_SHOW_SLOT.findall(panel_method_bodies[fn_name])) - _UNIVERSAL_SLOTS
        )
        for el_type, fn_name in renderer_by_type.items()
    }
    assert derived == HONORED_SHOW_SLOTS


def test_only_the_feedback_types_can_draw_a_state_label(
    panel_method_bodies: dict[str, str], renderer_by_type: dict[str, str],
) -> None:
    """A look binding is honored for colour far more widely than for text.

    ``evaluateFeedback`` is the only evaluator that calls ``_setLabelText`` off
    ``states[].label``; a status LED registers a ``color`` binding and a select
    a ``select_look`` one, and neither has anywhere to put a string. So the
    types that can render state text are exactly the ones registering
    ``type: 'feedback'``.
    """
    feedback = frozenset(
        el_type for el_type, fn_name in renderer_by_type.items()
        if "type: 'feedback'" in panel_method_bodies[fn_name]
    )
    assert feedback == STATE_LABEL_TYPES
    assert "_setLabelText" in panel_method_bodies["evaluateFeedback"]


@pytest.fixture(scope="module")
def builder_source() -> str:
    if not BUILDER_HELPERS.is_file():  # pragma: no cover - the repo always has it
        pytest.fail(f"builder helpers not found at {BUILDER_HELPERS}")
    return BUILDER_HELPERS.read_text(encoding="utf-8")


def test_the_finger_rule_matches_the_builders(builder_source: str) -> None:
    """Same millimetres, same density, so both surfaces reach the same verdict.

    These were re-measured in the Builder against real panel diagonals (a
    1280x800 panel in the field is 10.1 inches, not 15), and the old figure
    reported a control as half again more finger than it gets. A Python copy
    that missed that correction would be the same bug on the other side.
    """
    reference = re.search(
        r"TOUCH_REFERENCE\s*=\s*\{\s*width:\s*(\d+),\s*height:\s*(\d+),"
        r"\s*pxPerInch:\s*(\d+)\s*\}",
        builder_source,
    )
    assert reference, "could not find TOUCH_REFERENCE in the Builder helpers"
    assert (int(reference.group(1)), int(reference.group(2))) == (
        REFERENCE_WIDTH_PX, REFERENCE_HEIGHT_PX,
    )
    assert float(reference.group(3)) == TOUCH_PX_PER_INCH

    minimum = re.search(r"TOUCH_MIN_MM\s*=\s*([\d.]+)", builder_source)
    assert minimum, "could not find TOUCH_MIN_MM in the Builder helpers"
    assert float(minimum.group(1)) == TOUCH_MIN_MM


def test_the_same_types_are_treated_as_touchable(builder_source: str) -> None:
    block = re.search(
        r"const TOUCHABLE_TYPES = new Set\(\[(.*?)\]\)", builder_source, re.S,
    )
    assert block, "could not find TOUCHABLE_TYPES in the Builder helpers"
    assert frozenset(re.findall(r'"([\w_]+)"', block.group(1))) == TOUCHABLE_TYPES


def test_the_degenerate_threshold_matches_the_builders(builder_source: str) -> None:
    found = re.search(r"MINIMUM_VISIBLE_PX = ([\d.]+)", builder_source)
    assert found, "could not find MINIMUM_VISIBLE_PX in the Builder helpers"
    assert float(found.group(1)) == MINIMUM_VISIBLE_PX


def test_the_degenerate_threshold_stays_under_every_measured_floor() -> None:
    """The one relationship that keeps the two size checks from arguing.

    The degenerate check exists for the types with no floor, and it is written to
    be type-independent so it does not quietly become a floor of its own. If it
    ever rose above a measured one, an element could be told it is invisible at a
    size the browser has been observed drawing it whole -- the same inflated-floor
    failure the e2e minimums test exists to catch, arriving by a different door.
    """
    smallest = min(
        value
        for rule in RULES.values()
        for value in (rule.base_width_px, rule.base_height_px)
    )
    assert MINIMUM_VISIBLE_PX < smallest, (
        f"the degenerate threshold ({MINIMUM_VISIBLE_PX}px) has reached the smallest "
        f"measured floor ({smallest}px); one of them is now wrong"
    )
