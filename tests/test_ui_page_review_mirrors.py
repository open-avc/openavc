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
    STATE_ICON_TYPES,
    STATE_LABEL_TYPES,
    STRUCTURAL_PROPERTIES,
    TOUCH_MIN_MM,
    TOUCH_PX_PER_INCH,
    TOUCHABLE_TYPES,
)
from openavc.ui.control_minimums import (
    PORTRAIT_HEIGHT_PX,
    PORTRAIT_WIDTH_PX,
    REFERENCE_HEIGHT_PX,
    REFERENCE_WIDTH_PX,
    RULES,
    reference_box,
)

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


def test_only_the_state_text_types_can_draw_a_state_label(
    panel_method_bodies: dict[str, str], renderer_by_type: dict[str, str],
) -> None:
    """A look binding is honored for colour far more widely than for text.

    A status LED registers its ``show.look`` as a ``color`` binding and a select
    as a ``select_look`` one, and neither has anywhere to put a string, so a
    ``states[].label`` on those never appears. The types that CAN draw state
    text are the ones whose ``show.look`` registration lands on an evaluator
    that writes text.

    Derived that way rather than by naming ``feedback``, because there are now
    two such evaluators -- the button's, which also re-applies its chrome and
    swaps its image, and the label's, which does colour and words and nothing
    else. Naming them would mean this test needs editing to keep passing every
    time one is added, which is the opposite of what it is for.

    Keyed on the LOOK binding specifically: ``evaluateUiOverrides`` also writes
    a label, from the ``ui.<id>.label`` state key rather than from a state's
    appearance, and that is a different mechanism this table says nothing about.
    """
    look_binding_type = {}
    for el_type, fn_name in renderer_by_type.items():
        for block in _PUSH_BLOCK.findall(panel_method_bodies[fn_name]):
            if "bindings.show.look" not in block:
                continue
            if (m := re.search(r"type:\s*'([\w_]+)'", block)) is not None:
                look_binding_type[el_type] = m.group(1)
    assert look_binding_type, "no renderer registers a show.look binding"

    evaluator_for = dict(_EVALUATOR.findall("\n".join(panel_method_bodies.values())))
    derived = frozenset(
        el_type for el_type, bind_type in look_binding_type.items()
        if "_setLabelText" in panel_method_bodies.get(evaluator_for.get(bind_type, ""), "")
    )
    assert derived == STATE_LABEL_TYPES


def test_only_the_state_icon_types_can_draw_a_state_icon(
    panel_method_bodies: dict[str, str], renderer_by_type: dict[str, str],
) -> None:
    """Narrower again than the text one, and for a structural reason.

    A state's appearance is merged into the element's style and applied, and an
    icon is not style -- it is content. It appears only where the evaluator
    goes on to rebuild the icon+text layout, which is ``renderElementContent``.
    The button's evaluator does; the label's does colour and words and stops,
    so an icon picked per state on a label is written to the project and never
    drawn.

    Derived the same way as the text table, by what the evaluator behind each
    look binding actually calls, so adding a third evaluator does not quietly
    leave this table wrong.
    """
    look_binding_type = {}
    for el_type, fn_name in renderer_by_type.items():
        for block in _PUSH_BLOCK.findall(panel_method_bodies[fn_name]):
            if "bindings.show.look" not in block:
                continue
            if (m := re.search(r"type:\s*'([\w_]+)'", block)) is not None:
                look_binding_type[el_type] = m.group(1)
    assert look_binding_type, "no renderer registers a show.look binding"

    evaluator_for = dict(_EVALUATOR.findall("\n".join(panel_method_bodies.values())))
    derived = frozenset(
        el_type for el_type, bind_type in look_binding_type.items()
        if "renderElementContent" in panel_method_bodies.get(evaluator_for.get(bind_type, ""), "")
    )
    assert derived == STATE_ICON_TYPES


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
_CALL_HEAD = re.compile(r"this\.(\w+)\(")
_TAKES_ELEMENT = re.compile(r"\belement(Def)?\b")
#: `?.` is not optional here -- `elementDef?.display_decimals` is how the
#: helpers actually spell it, and a parse that misses it under-reports.
_PROP_READ = re.compile(r"\b(?:element|elementDef)\??\.([a-z_][a-z0-9_]*)")
#: Inside an evaluator `element` is the DOM node and `elementDef` is the
#: definition, so reading both there collects `value`, `_dragging` and `class`.
_PROP_READ_DEF = re.compile(r"\belementDef\??\.([a-z_][a-z0-9_]*)")
_REGISTERED_BINDING = re.compile(r"type:\s*'([\w_]+)'")
#: One ``this.bindings.push({ ... })`` call, so a registration's binding type
#: can be read together with the slot it was registered from.
_PUSH_BLOCK = re.compile(r"this\.bindings\.push\(\{.*?\}\);", re.S)
_EVALUATOR = re.compile(r"case '([\w_]+)':\s*this\.(evaluate\w+)\(b\);", re.S)


def _calls(body: str) -> list[tuple[str, str]]:
    """Every ``this.method(...)`` call in a body, with its arguments.

    The argument list is read by balancing parentheses rather than by stopping
    at the end of the line, because the two iframe types hand the element to
    the frame renderer they share over a multi-line options object. A walk that
    stopped at the newline never reached it, so `grant` -- read there, and the
    whole of what a custom control or a plugin panel may see and send -- was
    derived as read by nothing and the review said so out loud.

    Only the positional part counts as handing the element over: `element`
    named inside a callback body is a closure, not an argument, and a helper
    that was not given the element cannot read its properties.
    """
    found: list[tuple[str, str]] = []
    for match in _CALL_HEAD.finditer(body):
        depth, i = 1, match.end()
        while i < len(body) and depth:
            if body[i] == "(":
                depth += 1
            elif body[i] == ")":
                depth -= 1
            i += 1
        args = body[match.end():i - 1]
        found.append((match.group(1), re.split(r"\{|=>", args, maxsplit=1)[0]))
    return found


def _reachable(entry: str, bodies: dict[str, str], seen: set[str] | None = None) -> set[str]:
    seen = set() if seen is None else seen
    if entry in seen or entry not in bodies:
        return seen
    seen.add(entry)
    for callee, args in _calls(bodies[entry]):
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


def test_the_two_frame_types_read_the_grant_they_were_placed_with() -> None:
    """Guard the guard, from the other side: a right property called unread.

    The walk above only ever reached a render function's own body and the
    helpers it named on one line, so it could not see the frame renderer that
    a custom control and a plugin panel share -- and the table said no element
    type reads `grant`. The review then told an author, in a sentence, that
    their control's device access was being dropped, which reads as an
    instruction to delete the one field that holds it. Nothing else in this
    module is load-bearing for what a page can reach into the room.
    """
    for el_type in ("custom", "plugin"):
        assert "grant" in HONORED_PROPERTIES[el_type], (
            f"a {el_type} element is placed with a grant and the panel reads it; "
            "calling it unread invites deleting it"
        )


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


def test_the_toggle_comparison_is_still_the_one_the_server_mirrors(
    panel_method_bodies: dict[str, str],
) -> None:
    """`resolve_press` is a copy of four lines of the button renderer.

    It has to be: the panel decides a toggle's branch in the browser and the
    server only ever sees the verdict, so anything trying to VERIFY a toggle has
    to reach the same verdict independently. The rule worth pinning is the
    comparison -- string, case-insensitive -- because it is what lets
    `toggle_value: true` match a driver's boolean True, which is how nearly
    every toggle in a real project is written. A change to strict equality here
    would break most of them, and quietly.
    """
    body = panel_method_bodies["renderButton"]
    normalized = re.sub(r"\s+", "", body)
    assert "String(stateValue).toLowerCase()===String(toggleValue).toLowerCase()" in normalized
    # ...and that a toggle with nothing to compare degrades to tap rather than
    # doing nothing, which resolve_press also mirrors.
    assert "mode==='toggle'&&!pressBinding.toggle_key" in normalized
    # The two events the branch chooses between.
    assert "ui.toggle_off" in body and "ui.press" in body


def test_resolve_press_agrees_with_that_rule() -> None:
    """The server side of the same comparison, exercised rather than read."""
    from openavc.core.ui_events import resolve_press

    class _El:
        bindings = {"do": {"press": [{
            "action": "macro", "macro": "m", "mode": "toggle",
            "toggle_key": "device.x.power", "toggle_value": True,
        }]}}

    class _State:
        def __init__(self, value): self.value = value
        def get(self, key, default=None): return self.value

    # A driver's boolean, a device that reports strings, and mixed case: all
    # three are the same toggle to the panel, so all three must be here.
    for reported in (True, "true", "True", "TRUE"):
        event, why = resolve_press(_El(), _State(reported))
        assert event == "toggle_off", f"{reported!r} should have read as on"
        assert why
    for reported in (False, "false", None, "off"):
        event, _ = resolve_press(_El(), _State(reported))
        assert event == "press", f"{reported!r} should have read as off"


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


def test_a_turned_panel_swaps_its_pixels_and_keeps_its_density(
    builder_source: str,
) -> None:
    """Portrait is the same glass on its side, on both surfaces.

    Two claims, and the second is the one worth a test. The pixels swap: a
    portrait arrangement is 800x1280 because it is the landscape panel turned,
    not a second measurement of a different product. The DENSITY does not:
    rotating a tablet does not change its DPI, so the 9mm finger rule means the
    same thing either way round and `pxPerInch` stays out of the swap. Keying
    the density on orientation is the plausible-looking edit this exists to
    stop -- it would quietly move the touch verdict on every portrait page.
    """
    assert (PORTRAIT_WIDTH_PX, PORTRAIT_HEIGHT_PX) == (
        REFERENCE_HEIGHT_PX, REFERENCE_WIDTH_PX,
    )
    assert reference_box("portrait") == (PORTRAIT_WIDTH_PX, PORTRAIT_HEIGHT_PX)
    assert reference_box("landscape") == (REFERENCE_WIDTH_PX, REFERENCE_HEIGHT_PX)
    # Anything that is not portrait is landscape, including the empty string a
    # layout with no orientation resolves to.
    assert reference_box("") == (REFERENCE_WIDTH_PX, REFERENCE_HEIGHT_PX)

    turned = re.search(
        r'return orientation === "portrait"\s*'
        r"\?\s*\{\s*width:\s*TOUCH_REFERENCE\.(\w+),\s*height:\s*TOUCH_REFERENCE\.(\w+)\s*\}\s*"
        r":\s*\{\s*width:\s*TOUCH_REFERENCE\.(\w+),\s*height:\s*TOUCH_REFERENCE\.(\w+)\s*\}",
        builder_source,
    )
    assert turned, "could not find referenceBox's swap in the Builder helpers"
    assert turned.groups() == ("height", "width", "width", "height"), (
        "the Builder's portrait box is not the landscape one turned"
    )
    assert "pxPerInch" not in turned.group(0), (
        "the density must not be keyed on orientation -- a rotated panel has "
        "the same DPI, and the finger rule is physical"
    )


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


def test_the_guide_states_the_off_values_the_led_renderer_uses(
    panel_method_bodies: dict[str, str],
) -> None:
    """The authoring guide names the values that leave a status LED unlit.

    Worth pinning because it is prose rather than a generated table, and because
    it documents the one thing about an LED that is NOT the colour map: the map
    picks the colour, the value alone decides whether the dot is drawn lit. An
    LED bound to a boolean whose healthy state is ``false`` therefore keeps its
    green and never lights, which cost a real panel a status row that read as
    dead. If ``evaluateColor`` ever changes which values count as off, this
    fails rather than leaving the guide quietly wrong.
    """
    from openavc.system_config import PACKAGE_DIR

    body = panel_method_bodies["evaluateColor"]
    guide = (PACKAGE_DIR / "ui" / "panel_authoring_guide.md").read_text(encoding="utf-8")

    # The literal strings the renderer treats as off, read out of the source.
    off_strings = re.findall(r"\[([^\]]*)\]\.includes\(value", body)
    assert off_strings, "evaluateColor no longer tests a list of off-like strings"
    literals = re.findall(r"'([^']*)'", off_strings[0])
    assert set(literals) == {"", "off", "false", "0", "no"}, literals

    # ...and the non-string values it also treats as off.
    normalized = re.sub(r"\s+", "", body)
    for token in ("value===null", "value===undefined", "value===false", "value===0"):
        assert token in normalized, f"evaluateColor no longer checks {token}"

    section = guide.partition("## A status LED lights on its value")[2]
    assert section, "the guide no longer documents the LED lit/unlit rule"
    for token in ("null", "undefined", "false", "0", '""', '"off"', '"false"', '"no"'):
        assert token in section, f"the guide's off-value list omits {token}"
