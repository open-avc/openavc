"""The reviewer's two borrowed tables, checked against where they were borrowed from.

``page_review`` answers two questions it does not own the facts for:

* *does this element type's renderer read this binding* -- which lives in
  ``openavc/web/panel/panel.js``, in whichever render function the type dispatches to.
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

from openavc.ui.page_review import (
    HONORED_PROPERTIES,
    HONORED_SHOW_SLOTS,
    MATRIX_CONFIG_KEYS,
    MINIMUM_VISIBLE_PX,
    STATE_LABEL_TYPES,
    STRUCTURAL_PROPERTIES,
    TOUCH_MIN_MM,
    TOUCH_PX_PER_INCH,
    TOUCHABLE_TYPES,
)
from openavc.ui.control_minimums import REFERENCE_HEIGHT_PX, REFERENCE_WIDTH_PX, RULES

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
PANEL_JS = OPENAVC_ROOT / "openavc" / "web" / "panel" / "panel.js"
BUILDER_HELPERS = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "ui-builder"
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


# --- The property table ----------------------------------------------------
#
# Harder to derive than the slot table, because a render function is not where
# the reading stops. It hands the element to shared helpers
# (`renderElementContent` draws the icon for a dozen types) and it registers
# bindings whose evaluators run later against `elementDef` -- which is how a
# label's `display_decimals` is read two hops from `renderLabel`. Both hops are
# followed here, or the table would call correct properties unread and the
# warning would fire on working panels.

#: A call that hands the element (or its definition) to another method. The
#: argument test is what keeps the walk from wandering: a helper that reads
#: element properties has to be given the element, and one that is not given it
#: cannot read any.
_CALL = re.compile(r"this\.(\w+)\(([^;\n]*)\)")
_TAKES_ELEMENT = re.compile(r"\belement(Def)?\b")
#: `?.` is not optional here -- `elementDef?.display_decimals` is how the
#: helpers actually spell it, and a parse that misses it under-reports.
_PROP_READ = re.compile(r"\b(?:element|elementDef)\??\.([a-z_][a-z0-9_]*)")
#: Inside an evaluator `element` is the DOM node and `elementDef` is the
#: definition, so reading both there collects `value`, `_dragging` and `class`.
_PROP_READ_DEF = re.compile(r"\belementDef\??\.([a-z_][a-z0-9_]*)")
_REGISTERED_BINDING = re.compile(r"type:\s*'([\w_]+)'")
_EVALUATOR = re.compile(r"case '([\w_]+)':\s*this\.(evaluate\w+)\(b\);", re.S)


def _reachable(entry: str, bodies: dict[str, str], seen: set[str] | None = None) -> set[str]:
    seen = set() if seen is None else seen
    if entry in seen or entry not in bodies:
        return seen
    seen.add(entry)
    for callee, args in _CALL.findall(bodies[entry]):
        if callee in bodies and _TAKES_ELEMENT.search(args):
            _reachable(callee, bodies, seen)
    return seen


def test_every_type_reads_exactly_the_properties_the_table_says(
    panel_method_bodies: dict[str, str], renderer_by_type: dict[str, str],
) -> None:
    """The property table, re-derived from the renderer it describes.

    The failure this guards is the one that produced it. `label` is settable on
    every element type and drawn by nearly every renderer -- except the `label`
    element, which draws `text`. Nothing anywhere said so, so an AI-authored
    panel put four header strings on `label` masters, they rendered blank, and
    the workaround cost three variables, a macro and two triggers.

    Drift in either direction is a defect, and they fail differently: a
    property the table calls unread that IS read produces a confident warning
    about working authoring, which teaches the caller to ignore the field;
    one the table calls read that is NOT leaves the original silence in place.
    """
    evaluator_for = dict(_EVALUATOR.findall("\n".join(panel_method_bodies.values())))
    assert len(evaluator_for) > 10, "evaluator dispatch scan found almost nothing"

    derived: dict[str, frozenset[str]] = {}
    for el_type, fn_name in renderer_by_type.items():
        methods = _reachable(fn_name, panel_method_bodies)
        for bind_type in _REGISTERED_BINDING.findall(panel_method_bodies[fn_name]):
            if (evaluator := evaluator_for.get(bind_type)) is not None:
                methods |= _reachable(evaluator, panel_method_bodies)
        found: set[str] = set()
        for method in methods:
            pattern = _PROP_READ_DEF if method.startswith("evaluate") else _PROP_READ
            found |= set(pattern.findall(panel_method_bodies[method]))
        derived[el_type] = frozenset(found - STRUCTURAL_PROPERTIES)

    assert derived == HONORED_PROPERTIES


def test_the_label_element_is_the_one_that_does_not_draw_label(
    panel_method_bodies: dict[str, str], renderer_by_type: dict[str, str],
) -> None:
    """Guard the guard: the asymmetry that motivates the whole table.

    A derivation test passes just as happily against a table that says every
    type reads everything. This pins the specific shape worth catching, so a
    parse that quietly went permissive fails here instead of going green.
    """
    assert "label" not in HONORED_PROPERTIES["label"]
    assert "text" in HONORED_PROPERTIES["label"]
    # ...and it really is the odd one out, so the warning is worth writing.
    draws_label = {t for t, props in HONORED_PROPERTIES.items() if "label" in props}
    assert len(draws_label) > 10, "if most types stopped drawing `label`, revisit the message"


def test_the_matrix_config_keys_match_the_renderer(
    panel_method_bodies: dict[str, str], renderer_by_type: dict[str, str],
) -> None:
    """`matrix_config` has no schema anywhere, so this table is the only one.

    Every other element property is declared on `UIElement`; this one is a bare
    `dict[str, Any]`, which is how an 8x8 switcher authored with `inputs[]` /
    `outputs[]` stored clean and drew as an unbound 4x4.
    """
    body = panel_method_bodies[renderer_by_type["matrix"]]
    derived = set(re.findall(r"\bconfig\??\.([a-z_][a-z0-9_]*)", body))
    derived |= set(re.findall(r"matrix_config\??\.([a-z_][a-z0-9_]*)", body))
    assert derived == set(MATRIX_CONFIG_KEYS)


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
