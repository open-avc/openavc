"""What a page will actually look like, said in numbers, before anyone sees it.

The AI writes a panel blind. It gets no screenshot, no canvas, and until now no
answer to the only question that matters after "is this JSON valid": *will this
draw right*. It sized a status light at 3% of the page because 3% sounded small,
and 3% of 1280 is 38px of which 20 is a dot that does not shrink -- so fourteen
status lights shipped with their captions cut to nothing and every tool call
came back ``created``.

This module is the answer to that question. It takes a page, folds its geometry
down through ``page_geometry``, and reports -- in reference pixels and in the
percentages the caller actually writes -- everything that will render wrong: a
control smaller than its own fixed internals, one drawn so small nothing
survives it, two controls on top of each other, one drawn over a master element
that the panel draws underneath it, one hanging out of its container, one with
no box at all, one too small for a finger, a ``style`` measurement bigger than
the element carrying it (those are rem), a range wider than the device it
drives, a ``type`` the panel has no renderer for, a binding the panel does not
read for that element type, and a ``do`` action the runtime has no branch for.

What a binding POINTS AT -- a macro, a page, a device, a command -- is the
neighbouring question, and ``page_references`` answers it. It is separate
because those checks need the project and the driver registry, and because the
Builder already answers them somewhere else (``validateProject``) rather than in
the mirror of this module.

Everything here is a WARNING. Nothing rejects.
--------------------------------------------
That is a deliberate constraint, not an oversight. A rejection throws away the
batch and costs a whole round trip; a warning returned in the same tool result
is read during a turn the caller is already having. So this module never raises
and never refuses -- the write lands, and the findings ride back with it. The
one class that does reject (a container inside itself, a dangling ``inherits``)
rejects at the door in ``ui_tools`` and did so before this existed, because
there is no valid reading of it to warn about.

Every finding carries the arithmetic
------------------------------------
"Leave a little air between boxes" is unverifiable and gets guessed. "28px wide,
needs 29px, so give it at least 2.24% of its container" is checkable and gets
obeyed -- that is the whole lesson of the panel that started this. The rem rule
was anchored to real pixels and came back perfect; geometry was anchored to
nothing and came back broken. So a finding here always names the element, its
actual size in pixels, the size it needs, and what to write instead.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from math import isfinite
from operator import gt, lt
from typing import Any

from openavc.core.macro_validation import BUILTIN_STEP_ACTIONS
from openavc.core.ui_events import DISPATCHED_ACTIONS
from openavc.ui.control_minimums import (
    PIXEL_SLACK,
    REFERENCE_HEIGHT_PX,
    REFERENCE_WIDTH_PX,
    REM_BASE_PX,
    minimum_box,
    minimum_percent,
    reference_box,
)
from openavc.ui.matrix_model import (
    MATRIX_CONFIG_KEYS,
    PANEL_WRITABLE_PREFIXES,
    resolve_axis,
    route_matches,
    source_report_value,
)
from openavc.ui.page_geometry import (
    PAGE_BOX,
    absolute_placements,
    layout_chain,
    primary_layout,
    resolved_placements,
)

# --- The finger rule -------------------------------------------------------
#
# Mirrored from the Builder (TOUCH_REFERENCE / TOUCH_MIN_MM in
# uiBuilderHelpers.ts), which is where they were measured against real panel
# diagonals: 1280x800 in the field is a 10.1" panel, so ~149ppi rather than the
# ~100 an earlier version assumed. Millimetres because the question is physical
# -- whether a thumb can hit the thing does not depend on pixel density.
# tests/test_ui_page_review_mirrors.py reads the TypeScript and fails if these
# drift apart.
TOUCH_PX_PER_INCH = 149.0
TOUCH_MIN_MM = 9.0
TOUCH_MIN_PX = TOUCH_MIN_MM / 25.4 * TOUCH_PX_PER_INCH

#: Element types a finger actually has to hit. A label being small is fine.
#: The same set as the Builder's TOUCHABLE_TYPES, so the two agree.
#:
#: ``fader`` and ``slider`` are here because dragging is touch -- you grab the
#: handle with the same thumb. In practice neither can reach this check without
#: already having failed the contents floor (a fader that holds its handle and
#: scale is 72x100, a slider 68x81, and every one of those exceeds the 53px
#: finger minimum), so what they buy is consistency rather than coverage: the
#: rule is "a control you touch has a physical minimum", and leaving the two
#: draggable controls out made it read like a rule about buttons.
TOUCHABLE_TYPES = frozenset({
    "button", "page_nav", "camera_preset", "select", "text_input", "keypad", "list",
    "fader", "slider",
})


# --- What the panel reads out of `show`, per type --------------------------
#
# Read off the renderer, not off the schema: `show` accepts four slots for every
# element type, and each render function in openavc/web/panel/panel.js registers a
# binding for only some of them. A slot the renderer never looks at is silently
# inert -- the element draws, nothing errors, and the thing the author asked for
# simply never happens. That is how a label ended up carrying ONLINE / OFFLINE
# state text the panel has no code to draw.
#
# `visible_when` is absent from this table on purpose: it is registered for
# every element from the page tree (registerVisibleWhen), so it is honored
# everywhere and is never worth a warning.
#
# tests/test_ui_page_review_mirrors.py re-derives this from panel.js and fails
# if a render function starts or stops reading a slot.
HONORED_SHOW_SLOTS: dict[str, frozenset[str]] = {
    "button": frozenset({"look"}),
    "camera_preset": frozenset({"look"}),
    "clock": frozenset({"value"}),
    "custom": frozenset(),
    "fader": frozenset({"value"}),
    "gauge": frozenset({"value"}),
    "group": frozenset({"look"}),
    "image": frozenset({"look"}),
    "keypad": frozenset(),
    "label": frozenset({"look", "value"}),
    "level_meter": frozenset({"value"}),
    "list": frozenset({"items", "value"}),
    "matrix": frozenset(),
    "page_nav": frozenset({"look"}),
    "plugin": frozenset(),
    "select": frozenset({"look", "value"}),
    "slider": frozenset({"value"}),
    "status_led": frozenset({"look"}),
    "text_input": frozenset({"value"}),
}

#: Every type the panel has a renderer for.
#:
#: Derived from the table above rather than written out again:
#: ``tests/test_ui_page_review_mirrors.py`` re-derives that table's keys from
#: ``renderElement``'s dispatch, so this IS the renderer's own set, and a type
#: added to the panel without a row above fails that test before it reaches here.
RENDERED_TYPES = frozenset(HONORED_SHOW_SLOTS)

#: Types whose ``look`` binding renders per-state TEXT. Everything else that
#: reads ``look`` takes colour from it and no words -- a status LED tints its
#: dot, a select styles its options, an image swaps its picture -- so a
#: ``states[].label`` on those never appears.
#:
#: ``label`` differs from the others in what happens when a state names no
#: label: a button or a page nav falls back to its own ``label`` field, while a
#: label keeps whatever text it already had (its ``text``, or a bound
#: ``show.value``) and takes only colour from that state.
STATE_LABEL_TYPES = frozenset({"button", "camera_preset", "label", "page_nav"})

#: Types whose ``look`` binding renders a per-state ICON, which is a smaller set
#: than the one above.
#:
#: A state's appearance is merged into the element's style and applied, and an
#: icon is not a style property -- it is content, and something has to rebuild
#: the icon+text layout for it to appear. The button's evaluator does
#: (``evaluateFeedback`` calls ``renderElementContent``), and so does the page
#: nav's; the label's (``evaluateLabelLook``) deliberately does colour and words
#: and nothing else.
#:
#: So a label draws its OWN ``icon`` -- ``renderLabel`` builds that at render
#: time -- and ignores one named by a state. That difference is invisible from
#: the project file, which is why it is written down here.
STATE_ICON_TYPES = frozenset({"button", "camera_preset", "page_nav"})

#: Types whose ``look`` binding renders a per-state PICTURE (a state's
#: ``button_image``).
#:
#: One field name on every type, so a state's picture is spelled the same way
#: wherever it is drawn: a button and a camera preset show it as their artwork,
#: an image shows it in place of its own ``src``, a group (Container) draws it
#: as its frame's background. Everything else that reads ``look`` has nowhere to
#: put a picture, and one named there is stored and never seen.
STATE_IMAGE_TYPES = frozenset({"button", "camera_preset", "group", "image"})


# --- What the panel reads off the element itself, per type -----------------
#
# The same problem as HONORED_SHOW_SLOTS, one level out. ``UIElement`` allows
# extras and declares ~60 optional fields flat, so EVERY property is settable on
# EVERY type and the loader keeps all of them. Each renderer then reads a
# handful. A property the renderer does not read for that type is stored,
# round-trips perfectly, and does nothing.
#
# Two shapes of that failure, both seen in one AI-authored panel:
#
#   * A property that exists for another type. ``label`` is the sharp one --
#     nearly every renderer draws it, and the ``label`` element is the one that
#     does NOT (it draws ``text``). So the most natural guess on the most common
#     element type is the one that silently draws nothing.
#   * A property that exists nowhere. ``segments`` on a level_meter, which is
#     really ``style.meter_segments``, and whose default of 20 makes the wrong
#     write look right.
#
# Derived rather than transcribed. tests/test_ui_page_review_mirrors.py walks
# each render function, follows the helpers it hands the element to and the
# evaluators it registers bindings for, and fails when this disagrees.
#
# ``grant`` on the two frame types is the one worth naming, because getting it
# wrong here is worse than silence: the panel reads it in the shared frame
# renderer both of them hand the element to, and it is the whole of what a
# custom control or a plugin panel can see and send. Calling it unread told a
# reader their control's device access was being dropped -- an invitation to
# delete the one field holding the grant.
HONORED_PROPERTIES: dict[str, frozenset[str]] = {
    "button": frozenset({
        "button_image", "display_mode", "frameless", "icon", "icon_color",
        "icon_position", "icon_size", "image_blend_mode", "image_fit",
        "image_opacity", "label",
    }),
    "camera_preset": frozenset({
        "button_image", "display_mode", "frameless", "icon", "icon_color",
        "icon_position", "icon_size", "image_blend_mode", "image_fit",
        "image_opacity", "label", "preset_number",
    }),
    "clock": frozenset({
        "clock_mode", "duration_minutes", "format", "start_key", "target_time",
        "timezone",
    }),
    "custom": frozenset({"custom_config", "custom_file", "grant"}),
    "fader": frozenset({
        "display_decimals", "label", "max", "min", "orientation", "output_max",
        "output_min", "response", "response_db_range", "scale_to_full",
        "send_on_release", "send_throttle_ms", "step", "unit",
    }),
    "gauge": frozenset({
        "arc_angle", "display_decimals", "label", "max", "min", "unit", "zones",
    }),
    "group": frozenset({"label", "label_position"}),
    "image": frozenset({"label", "object_fit", "src"}),
    "keypad": frozenset({
        "auto_send", "auto_send_delay_ms", "digits", "keypad_style", "label",
        "mask_display", "show_display",
    }),
    "label": frozenset({
        "display_decimals", "icon", "icon_color", "icon_position", "icon_size",
        "text",
    }),
    "level_meter": frozenset({"label", "max", "min", "orientation"}),
    "list": frozenset({"item_height", "items", "label", "list_style", "options"}),
    "matrix": frozenset({"label", "matrix_config", "matrix_style"}),
    "page_nav": frozenset({
        "highlight_current", "icon", "icon_color", "icon_position", "icon_size", "label",
        "target_page",
    }),
    "plugin": frozenset({"grant", "plugin_config", "plugin_id", "plugin_type"}),
    "select": frozenset({"label", "options"}),
    "slider": frozenset({
        "display_decimals", "label", "max", "min", "orientation", "output_max",
        "output_min", "response", "response_db_range", "scale_to_full",
        "send_on_release", "send_throttle_ms", "step", "thumb_size", "unit",
    }),
    "status_led": frozenset({"label"}),
    "text_input": frozenset({"label", "placeholder"}),
}

#: Fields that belong to every element and are read by something other than a
#: renderer, so no per-type table can vouch for them.
#:
#: ``parent`` and ``aspect_lock`` are consumed by the layout engine, ``style``
#: and ``bindings`` by machinery shared across all types, ``css_class`` by the
#: project stylesheet, ``locked`` by the Builder alone. ``hidden`` is here for
#: the master-element case only -- on a page element it is per-layout
#: (``layout.hidden``) and an element-level one really is inert, but masters
#: belong to no layout and the panel reads ``mEl.hidden`` directly, so warning
#: about it would fire on the correct spelling.
STRUCTURAL_PROPERTIES = frozenset({
    "id", "type", "style", "bindings", "parent", "aspect_lock", "css_class",
    "locked", "hidden", "placement", "placements", "pages",
})

#: The slots worth naming in a message, in the order a reader expects them.
_SLOTS = ("value", "look", "items")

#: What an author sees when a slot is bound to nothing, per slot. Said out loud
#: because "the binding is inert" describes the code and not the screen, and the
#: screen is where this gets noticed -- a label that went blank mid-edit reads as
#: a control that vanished, not as a binding waiting for a key.
_UNBOUND_CONSEQUENCE = {
    "value": "The control shows what an unset value looks like: blank text, or the low end of a range.",
    "look": "Its appearance stays on whatever an unmatched state gives it.",
    "items": "The list comes up empty.",
}

# --- Ranges ----------------------------------------------------------------
#
# Mirrors the Builder's "Match driver range" affordance (DeviceValuePicker):
# same element types, same fields, same precedence. The Builder offers it to a
# human who can see the control; here it happens and is reported, because the
# caller cannot see anything.
RANGE_TYPES = frozenset({"slider", "fader", "gauge", "level_meter"})
STEP_TYPES = frozenset({"slider", "fader"})
UNIT_TYPES = frozenset({"slider", "fader", "gauge"})
#: ...and of those, the two that SEND what they read. A gauge or a meter scaled
#: past what the device reports is a needle that never reaches the end of its
#: sweep -- untidy, and a legitimate choice for a scale shared across channels.
#: A fader scaled past it hands the device a value it refuses.
COMMANDING_TYPES = frozenset({"slider", "fader"})

#: An overlap smaller than this on either axis is a rounding artefact, not a
#: collision. Percentages are stored to 4 decimal places, which at the reference
#: is ~0.0001px, so anything at or above a whole pixel is real.
_OVERLAP_MIN_PX = 1.0
#: ...and it has to be a visible share of the smaller box, so two controls that
#: graze a corner do not read like one sitting on top of another.
_OVERLAP_MIN_SHARE = 1.0
#: How many colliding neighbours a collapsed overlap finding names before it
#: counts the rest. Every one of them is still counted in the total -- the
#: sentence says "and N more" rather than quietly stopping.
_OVERLAP_NAMED = 3
#: Percentages are stored to four decimals, so a box is out of its parent only
#: past this. Mirrors BOUNDS_EPSILON in the Builder.
_BOUNDS_EPSILON = 0.0001


@dataclass(frozen=True)
class Finding:
    """One thing that will not draw the way it was written.

    ``kind`` groups them for a caller that wants to count or filter; ``message``
    is the whole finding in one self-contained sentence, because the consumer
    that matters reads prose and acts on it. ``key`` is what makes a finding the
    same finding across two arrangements of one page -- an element can collide
    with three different neighbours, and all three are worth saying once.
    """

    element_id: str
    kind: str
    message: str
    key: tuple = ()

    def __post_init__(self) -> None:
        if not self.key:
            object.__setattr__(self, "key", (self.kind, self.element_id))


@dataclass(frozen=True)
class Adjustment:
    """A field filled in from what the device declares, and said out loud."""

    element_id: str
    field: str
    value: Any
    message: str


@dataclass(frozen=True)
class Container:
    """One box in an element's ancestry, as a remedy would have to name it."""

    label: str
    """How it reads mid-sentence: ``'grp_strip'``, or ``the page`` for the root."""

    width_px: float
    height_px: float


@dataclass(frozen=True)
class Seat:
    """Where one box is drawn, in reference pixels: what every per-box check measures.

    Its own size, the container it has to fit (and how a sentence names that
    container), the containers above that one, innermost first, and which way
    round the screen is.
    """

    box_px: tuple[float, float]
    parent_px: tuple[float, float]
    parent_name: str
    ancestors: tuple[Container, ...] = ()
    orientation: str = "landscape"


def page_container(orientation: str = "landscape") -> Container:
    """The root every chain ends at. It is the one box no remedy can grow.

    A function rather than a constant because a portrait arrangement is drawn
    on the same screen turned, and measuring one against 1280x800 was the whole
    of the defect: 60% too generous on width, too strict on height, and wrong in the
    direction that ACCEPTS -- so nothing was flagged anywhere.
    """
    width, height = reference_box(orientation)
    return Container("the page", width, height)


def _mapping(element: Any) -> Mapping[str, Any]:
    """A dict view of an element, whether it arrived as a model or a dict."""
    if isinstance(element, Mapping):
        return element
    dump = getattr(element, "model_dump", None)
    return dump() if callable(dump) else dict(vars(element))


def _show(element: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """An element's ``show`` bindings, when it carries a readable set of them.

    Every check that reads a slot (``value``, ``look``, ``items``,
    ``visible_when``) starts here, so a malformed ``bindings`` or ``show`` reads
    as no bindings everywhere alike.
    """
    bindings = element.get("bindings")
    show = bindings.get("show") if isinstance(bindings, Mapping) else None
    return show if isinstance(show, Mapping) else None


def _pct(value: float) -> str:
    """A percentage the way it would be written back into a placement."""
    return f"{value:.2f}".rstrip("0").rstrip(".") or "0"


def _num(value: Any) -> str:
    """A declared bound, without a trailing .0 on whole numbers."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _to_mm(px: float) -> float:
    return px / TOUCH_PX_PER_INCH * 25.4


def _article(word: str) -> str:
    """``a`` or ``an``, so a type name reads as a sentence rather than a token."""
    return "an" if word[:1].lower() in "aeiou" else "a"


def _joined(parts: list[str]) -> str:
    """A list read out loud: one, one and another, or one, another and a third."""
    if len(parts) < 3:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + f" and {parts[-1]}"


def _box_px(
    box: Mapping[str, float],
    reference: tuple[float, float] = (REFERENCE_WIDTH_PX, REFERENCE_HEIGHT_PX),
) -> tuple[float, float]:
    return (
        box["w"] / 100 * reference[0],
        box["h"] / 100 * reference[1],
    )


# --- Geometry findings -----------------------------------------------------


def _blocked_axes(
    box: Any, box_px: tuple[float, float], parent_px: tuple[float, float],
) -> list[tuple[str, float, float]]:
    """The starved axes whose floor is larger than the container itself.

    ``(axis, needs_px, has_px)`` per axis. These are the ones with no percentage
    to write: 100% of the parent is already under the floor, so every remedy
    phrased against the parent is a number above 100 -- unreachable, and read as
    nonsense by anyone who tries it. The container is the fault.
    """
    parent = dict(zip(("width", "height"), parent_px))
    return [
        (axis, needs, has)
        for axis, needs, has in box.short_axes(*box_px)
        if needs > parent[axis] + PIXEL_SLACK
    ]


def _grown(container: Container, axis: str) -> float:
    return container.width_px if axis == "width" else container.height_px


def _at_least(axis: str, percent: float) -> str:
    """One axis of a remedy: ``w at least 7.25%``."""
    return f"{'w' if axis == 'width' else 'h'} at least {_pct(percent)}%"


def _give_it(percents: Mapping[str, float], parent_name: str) -> str:
    """The remedy as shares of the element's own parent, width first; empty with none."""
    if not percents:
        return ""
    shares = " and ".join(_at_least(axis, percent) for axis, percent in percents.items())
    return f" Give it {shares} of {parent_name}."


def container_remedy(
    ancestors: tuple[Container, ...],
    blocked: list[tuple[str, float, float]],
    page: Container | None = None,
) -> tuple[Container, Container, dict[str, float]] | None:
    """The nearest ancestor that can be grown until every starved axis fits.

    Percentages cascade, so growing one box scales everything beneath it: a
    fader at 90% of a 51px strip reaches 72px the moment the strip reaches 80px,
    and no other edit is needed. That is what makes a single number actionable
    here where ``w at least 140.62%`` was not.

    Walks outward because a container is only growable up to its own parent --
    when the immediate one is already pinned, the fix is one level further out,
    and it still lands on the element underneath. Returns the box to grow, the
    box its percentage is measured against, and the pixels it needs per axis.
    """
    if any(has <= 0 for _, _, has in blocked):
        return None  # a zero box has no share to scale; that is its own finding
    chain = (*ancestors, page if page is not None else page_container())
    for index, ancestor in enumerate(chain[:-1]):
        parent = chain[index + 1]
        needs: dict[str, float] = {}
        for axis, wants, has in blocked:
            grown = _grown(ancestor, axis) * wants / has
            if grown > _grown(parent, axis) + PIXEL_SLACK:
                break
            needs[axis] = grown
        else:
            return ancestor, parent, needs
    return None


def _container_clause(
    ancestors: tuple[Container, ...],
    blocked: list[tuple[str, float, float]],
    page: Container | None = None,
) -> str:
    """The remedy for axes no percentage of the parent can reach."""
    page = page if page is not None else page_container()
    axes = [axis for axis, _, _ in blocked]
    remedy = container_remedy(ancestors, blocked, page)
    if remedy is None:
        # Nothing in the ancestry has room to grow, so the page itself is the
        # binding constraint. Said as the ceiling rather than as a percentage,
        # because there is no percentage that helps.
        limits = []
        for axis, wants, has in blocked:
            outer = _grown(ancestors[-1], axis) if ancestors else has
            page_px = _grown(page, axis)
            ceiling = has * page_px / outer if outer > 0 else page_px
            word = "widest" if axis == "width" else "tallest"
            limits.append(
                f" No placement fits: the {word} this can be drawn on a "
                f"{page_px:.0f}px page is {ceiling:.0f}px, and it needs {wants:.0f}px."
            )
        return "".join(limits)

    holder, parent, needs = remedy
    if len(axes) == 2:
        size = f"{holder.width_px:.0f}x{holder.height_px:.0f}px"
        word = "size"
    elif axes[0] == "width":
        size = f"{holder.width_px:.0f}px wide"
        word = "width"
    else:
        size = f"{holder.height_px:.0f}px tall"
        word = "height"
    gives = " and ".join(
        _at_least(axis, needs[axis] / _grown(parent, axis) * 100) for axis in axes
    )
    return (
        f" {holder.label} is {size}, too small to hold it at any {word}: "
        f"give {holder.label} {gives} of {parent.label}."
    )


def _shares_within_reach(
    short: list[tuple[str, float, float]],
    blocked: list[tuple[str, float, float]],
    percent: tuple[float, float] | None,
) -> dict[str, float]:
    """The share of its parent each short axis needs, for the axes a share can still reach.

    ``percent`` is the floor as shares of the parent, width then height. A
    blocked axis is left out: its share would be over 100%, and the container
    clause names the box that has to grow instead.
    """
    if not percent:
        return {}
    stuck = {axis for axis, _, _ in blocked}
    need = dict(zip(("width", "height"), percent))
    return {axis: need[axis] for axis, _, _ in short if axis not in stuck}


def starvation_finding(
    element: Mapping[str, Any],
    seat: Seat,
    theme: Mapping[str, Any] | None = None,
) -> Finding | None:
    """A control smaller than the parts inside it that do not shrink.

    The dominant defect class, and the one nothing could see before the
    minimums were measured: the box is a percentage and can be any size, while
    the dot, the handle, the thumb and the key grid are pixels and are not
    negotiable. Below the floor the control still draws -- it just draws with
    its contents cut off, which reads as a styling bug rather than a sizing one.

    The remedy is a percentage of the element's own parent, EXCEPT when the
    parent is itself too small to hold the floor. Then no percentage exists and
    the seat's ``ancestors`` are what turn the finding back into something
    actionable, by naming the box that has to grow instead.
    """
    box = minimum_box(element, theme, seat.orientation)
    if box is None:
        return None
    width_px, height_px = seat.box_px
    reasons = box.starves(width_px, height_px)
    if not reasons:
        return None

    el_id = str(element.get("id", "?"))
    el_type = str(element.get("type", "?"))
    blocked = _blocked_axes(box, seat.box_px, seat.parent_px)
    percent = minimum_percent(element, *seat.parent_px, theme, seat.orientation)
    page = page_container(seat.orientation)
    short = box.short_axes(width_px, height_px)
    fix = _give_it(_shares_within_reach(short, blocked, percent), seat.parent_name)
    fix += _container_clause(seat.ancestors, blocked, page) if blocked else ""
    return Finding(
        el_id,
        "too_small_for_contents",
        f"{el_id} ({el_type}) is {width_px:.0f}x{height_px:.0f}px at the "
        f"{page.width_px:.0f}x{page.height_px:.0f} reference, too small for what it "
        f"draws: {'; '.join(reasons)}.{fix}",
    )


#: Below this on either axis an element is not small, it is absent.
#:
#: Deliberately NOT a per-type floor. Two thirds of the element types publish no
#: floor at all, because what limits them is their content -- a caption, an
#: image, whatever a plugin draws -- which is unbounded and theme dependent, and
#: inventing a curve for that would reject layouts that render correctly. That
#: reasoning stands. It just does not cover 6x4px, which is not a judgement call.
#:
#: 10 sits under every measured floor in ``control_minimums`` (the lowest is a
#: level meter at 13px wide), so this can never contradict one, and a test pins
#: that relationship rather than leaving it to whoever edits the table next.
MINIMUM_VISIBLE_PX = 10.0


def degenerate_finding(
    element: Mapping[str, Any],
    seat: Seat,
    theme: Mapping[str, Any] | None = None,
) -> Finding | None:
    """A box too small to draw anything, on a type with no floor to breach.

    The gap this closes: a gauge and a clock at 0.5% x 0.5% of the page came back
    completely clean. They have no fixed internals, so the starvation check has
    nothing to measure them against, and 6x4px sailed through a review whose
    whole purpose is catching controls too small to work.

    Runs only where ``minimum_box`` has no opinion, so it never speaks over a
    measured floor and never becomes one.
    """
    if minimum_box(element, theme, seat.orientation) is not None:
        return None
    width_px, height_px = seat.box_px
    if width_px >= MINIMUM_VISIBLE_PX and height_px >= MINIMUM_VISIBLE_PX:
        return None

    el_id = str(element.get("id", "?"))
    el_type = str(element.get("type", "?"))
    shares = {
        axis: MINIMUM_VISIBLE_PX / parent * 100
        for axis, drawn, parent in zip(("width", "height"), seat.box_px, seat.parent_px)
        if drawn < MINIMUM_VISIBLE_PX and parent > 0
    }
    page = page_container(seat.orientation)
    fix = _give_it(shares, seat.parent_name)
    return Finding(
        el_id,
        "too_small_to_draw",
        f"{el_id} ({el_type}) is {width_px:.0f}x{height_px:.0f}px at the "
        f"{page.width_px:.0f}x{page.height_px:.0f} reference, which is not small, it is "
        f"invisible. {_article(el_type).capitalize()} {el_type} has no fixed floor -- what limits it is its content -- so "
        f"nothing else here measures it.{fix}",
    )


def touch_finding(
    element: Mapping[str, Any], box_px: tuple[float, float],
) -> Finding | None:
    """A control a finger will struggle to hit. Physical, not pixel.

    A separate question from starvation, with a separate answer: a select holds
    everything it draws at 44px tall and is still under the comfortable touch
    minimum, which is exactly what shipped.
    """
    el_type = str(element.get("type", "?"))
    if el_type not in TOUCHABLE_TYPES:
        return None
    width_px, height_px = box_px
    if width_px <= 0 or height_px <= 0:
        # A box with no size is degenerate, not uncomfortable. Reporting
        # "roughly 43.6x0.0mm on a 10-inch panel -- under the 9mm comfortable
        # touch minimum" invites someone to make it a little bigger, when the
        # thing is not on screen at all. The degenerate check has the sentence
        # for it, and one fix ends both.
        return None
    short = _under_touch(box_px)
    if not short:
        return None
    el_id = str(element.get("id", "?"))
    return Finding(
        el_id,
        "small_touch_target",
        f"{el_id} ({el_type}) is about {width_px:.0f}x{height_px:.0f}px, roughly "
        f"{_to_mm(width_px):.1f}x{_to_mm(height_px):.1f}mm on a 10-inch panel -- under the "
        f"{_num(TOUCH_MIN_MM)}mm comfortable touch minimum on {_joined(short)} "
        f"({TOUCH_MIN_PX:.0f}px).",
    )


def _under_touch(box_px: tuple[float, float]) -> list[str]:
    """The axes a finger has too little of, width first: each needs ``TOUCH_MIN_PX``.

    The Builder's twin is ``underTouch``.
    """
    return [axis for axis, size in zip(("width", "height"), box_px) if size < TOUCH_MIN_PX]


@dataclass(frozen=True)
class _StyleMeasure:
    """One style field as the panel draws it: the field, and its rem, each ``REM_BASE_PX``."""

    field: str
    rem: float

    @property
    def px(self) -> float:
        return self.rem * REM_BASE_PX


def _style_measure(style: Mapping[str, Any], *names: str) -> _StyleMeasure | None:
    """The first of these fields that is set.

    Ordered the way the panel resolves them: a specific axis wins over the
    shorthand, so the message names the field the author would actually edit.
    """
    for name in names:
        value = style.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value > 0:
            return _StyleMeasure(name, float(value))
    return None


def _past_one_side(measure: _StyleMeasure, side_px: float, slack: float = 0) -> float | None:
    """The room a side gives a measurement drawn once across it, when it is longer.

    Text against the box's height, the gap between two letters against its
    width: the room is the side itself, and ``slack`` is how far past it still
    draws alike. None when it fits. A side with no size is not measured --
    nothing is drawn there to outgrow. The Builder's twin is ``pastOneSide``.
    """
    return side_px if side_px > 0 and measure.px > side_px + slack else None


def _meets_in_middle(measure: _StyleMeasure, side_px: float) -> float | None:
    """The room each edge has for a measurement drawn on both edges of a side, when the two meet.

    Padding on two opposite sides, a border across the box's shorter side:
    each edge has half the side. None when they leave a gap between them. A
    side with no size is not measured. The Builder's twin is ``meetsInMiddle``.
    """
    return side_px / 2 if side_px > 0 and 2 * measure.rem * REM_BASE_PX >= side_px else None


#: One style measurement the box cannot hold: the words for it, the field to
#: change, and the most room the box gives that field, in pixels.
_StyleBreach = tuple[str, str, float]


def _font_breach(style: Mapping[str, Any], width_px: float, height_px: float) -> _StyleBreach | None:
    """Text taller than the box, past half a pixel of rounding."""
    font = _style_measure(style, "font_size")
    if font is None:
        return None
    room = _past_one_side(font, height_px, PIXEL_SLACK)
    if room is None:
        return None
    return (
        f"font_size {_num(font.rem)} draws {font.px:.0f}px of text in a box "
        f"{height_px:.0f}px tall",
        font.field, room,
    )


@dataclass(frozen=True)
class _PaddingAxis:
    """Padding on one axis: the fields that set it, and how a sentence names it.

    ``names`` is in the panel's order, the axis's own field before the
    shorthand, so a finding names the field the author would actually edit.
    """

    names: tuple[str, str]
    where: str
    extent: str


_PADDING_DOWN = _PaddingAxis(("padding_vertical", "padding"), "above and below", "tall")
_PADDING_ACROSS = _PaddingAxis(("padding_horizontal", "padding"), "each side", "wide")


def _padding_breach(
    style: Mapping[str, Any], axis: _PaddingAxis, size_px: float,
) -> _StyleBreach | None:
    """Padding on two opposite sides that meets in the middle of the box."""
    pad = _style_measure(style, *axis.names)
    if pad is None:
        return None
    room = _meets_in_middle(pad, size_px)
    if room is None:
        return None
    return (
        f"{pad.field} {_num(pad.rem)} leaves {pad.px:.0f}px {axis.where} in a "
        f"box {size_px:.0f}px {axis.extent}",
        pad.field, room,
    )


def _padding_down(style: Mapping[str, Any], width_px: float, height_px: float) -> _StyleBreach | None:
    return _padding_breach(style, _PADDING_DOWN, height_px)


def _padding_across(style: Mapping[str, Any], width_px: float, height_px: float) -> _StyleBreach | None:
    return _padding_breach(style, _PADDING_ACROSS, width_px)


def _border_breach(style: Mapping[str, Any], width_px: float, height_px: float) -> _StyleBreach | None:
    """A border that meets in the middle of the box's shorter side."""
    edge = _style_measure(style, "border_width")
    smallest = min(width_px, height_px)
    if edge is None:
        return None
    room = _meets_in_middle(edge, smallest)
    if room is None:
        return None
    return (
        f"border_width {_num(edge.rem)} draws a {edge.px:.0f}px border on a box "
        f"{width_px:.0f}x{height_px:.0f}px",
        edge.field, room,
    )


def _letter_breach(style: Mapping[str, Any], width_px: float, height_px: float) -> _StyleBreach | None:
    """A gap between two letters wider than the box."""
    gap = _style_measure(style, "letter_spacing")
    if gap is None:
        return None
    room = _past_one_side(gap, width_px)
    if room is None:
        return None
    return (
        f"letter_spacing {_num(gap.rem)} puts {gap.px:.0f}px between letters in a "
        f"box {width_px:.0f}px wide",
        gap.field, room,
    )


#: Every style measurement that can outgrow its box, in the order a finding reads
#: them out. A new one is a function asking ``_past_one_side`` or
#: ``_meets_in_middle`` for its room and an entry here, and the same in
#: ``STYLE_BREACHES``, its twin in uiBuilderHelpers.ts.
_STYLE_BREACHES: tuple[Callable[[Mapping[str, Any], float, float], _StyleBreach | None], ...] = (
    _font_breach,
    _padding_down,
    _padding_across,
    _border_breach,
    _letter_breach,
)


def _style_ceilings(breaches: list[_StyleBreach]) -> dict[str, float]:
    """The most each field may be, in rem.

    Keyed by field, because ``padding`` is one number governing both axes: when
    it breaks the box on each, it still gets one ceiling, the tighter one.
    """
    limits: dict[str, float] = {}
    for _, field, limit_px in breaches:
        room = limit_px / REM_BASE_PX
        limits[field] = min(limits.get(field, room), room)
    return limits


def style_finding(
    element: Mapping[str, Any], box_px: tuple[float, float],
) -> Finding | None:
    """A ``style`` measurement that renders bigger than the element it is on.

    ``style`` measurements are **rem** -- px / 14, since project format 0.8.0 --
    and the number an author reaches for is the pixel one. ``font_size: 24`` is
    24 rem, which is 336px of text, and on a 32px label it is a third of a metre
    of type overflowing a box the height of a line. Nothing caught it: the write
    lands, the panel draws it, and it is only wrong to look at.

    Only measurements that cannot fit are reported, which is what makes this a
    fact rather than a style opinion: 24 rem is perfectly reasonable on a box
    tall enough to hold it, and this says nothing at all about that one.

    ``border_radius`` and ``margin`` are deliberately absent. CSS clamps a radius
    to half the box, so an oversized one draws a legal pill rather than a defect;
    and a margin sits outside a box the layout has already positioned by
    percentage, so it has no size of its own to exceed.
    """
    style = element.get("style")
    if not isinstance(style, Mapping):
        return None
    width_px, height_px = box_px
    breaches = [
        breach for check in _STYLE_BREACHES if (breach := check(style, width_px, height_px))
    ]
    if not breaches:
        return None
    el_id = str(element.get("id", "?"))
    reasons = "; ".join(reason for reason, _, _ in breaches)
    fixes = _joined([
        f"{field} at most {_pct(room)}" for field, room in _style_ceilings(breaches).items()
    ])
    return Finding(
        el_id,
        "style_too_large",
        f"{el_id} ({element.get('type', '?')}) is {width_px:.0f}x{height_px:.0f}px and its style "
        f"asks for more room than that: {reasons}. style measurements are rem, not "
        f"pixels -- px / {_num(REM_BASE_PX)} -- so write {fixes}.",
    )


def box_findings(
    element: Mapping[str, Any], seat: Seat, theme: Mapping[str, Any] | None = None,
) -> list[Finding]:
    """Every check one box gets, wherever it is drawn, in the order they report.

    A page's arrangement and a master's own orientation-keyed boxes both run
    exactly these; only an element on a page also has a container to hang out of.
    """
    candidates = (
        starvation_finding(element, seat, theme),
        degenerate_finding(element, seat, theme),
        touch_finding(element, seat.box_px),
        style_finding(element, seat.box_px),
    )
    return [finding for finding in candidates if finding]


def overhang_finding(
    element: Mapping[str, Any],
    placement: Any,
    parent_name: str,
    parent_px: tuple[float, float],
) -> Finding | None:
    """A box that runs off the edge of whatever holds it.

    Containers do not clip -- the panel sets ``overflow: visible`` on any
    element with children -- so this draws rather than disappearing. It lands on
    top of whatever sits beside the container, which is worse than being cut
    off, because it looks intentional.
    """
    def _f(name: str, fallback: float) -> float:
        value = getattr(placement, name, None)
        return float(value) if value is not None else fallback

    x, y = _f("x", 0.0), _f("y", 0.0)
    w, h = _f("w", 100.0), _f("h", 100.0)

    sides = []
    if x < -_BOUNDS_EPSILON:
        sides.append(f"{-x / 100 * parent_px[0]:.0f}px past the left (x {_pct(x)}%)")
    if y < -_BOUNDS_EPSILON:
        sides.append(f"{-y / 100 * parent_px[1]:.0f}px past the top (y {_pct(y)}%)")
    if x + w > 100 + _BOUNDS_EPSILON:
        sides.append(
            f"{(x + w - 100) / 100 * parent_px[0]:.0f}px past the right "
            f"(x {_pct(x)}% + w {_pct(w)}% = {_pct(x + w)}%)"
        )
    if y + h > 100 + _BOUNDS_EPSILON:
        sides.append(
            f"{(y + h - 100) / 100 * parent_px[1]:.0f}px past the bottom "
            f"(y {_pct(y)}% + h {_pct(h)}% = {_pct(y + h)}%)"
        )
    if not sides:
        return None
    el_id = str(element.get("id", "?"))
    return Finding(
        el_id,
        "outside_its_container",
        f"{el_id} extends beyond {parent_name}: {', and '.join(sides)}.",
    )


def overlap_extent(
    a_box: Mapping[str, float],
    b_box: Mapping[str, float],
    reference: tuple[float, float] = (REFERENCE_WIDTH_PX, REFERENCE_HEIGHT_PX),
) -> tuple[float, float, float] | None:
    """How much two boxes share: pixels on each axis, and share of the smaller.

    None when they do not really collide -- no intersection, an intersection
    under a pixel on either axis (percentages are stored to four decimals, so
    that is rounding), or too small a share of the smaller box to read as one
    control sitting on another rather than two grazing a corner.

    Checked between siblings only, by the caller. Boxes under different
    containers can legitimately overlap (a group laid over another is a design),
    and a container always contains its own children.
    """
    ox = min(a_box["x"] + a_box["w"], b_box["x"] + b_box["w"]) - max(a_box["x"], b_box["x"])
    oy = min(a_box["y"] + a_box["h"], b_box["y"] + b_box["h"]) - max(a_box["y"], b_box["y"])
    if ox <= 0 or oy <= 0:
        return None
    ox_px = ox / 100 * reference[0]
    oy_px = oy / 100 * reference[1]
    if ox_px < _OVERLAP_MIN_PX or oy_px < _OVERLAP_MIN_PX:
        return None
    smaller = min(a_box["w"] * a_box["h"], b_box["w"] * b_box["h"])
    share = 100.0 * (ox * oy) / smaller if smaller else 100.0
    if share < _OVERLAP_MIN_SHARE:
        return None
    return ox_px, oy_px, share


def overlap_findings(
    pairs: list[tuple[str, str, float, float, float]],
    types: Mapping[str, str],
    parent_name: str,
) -> list[Finding]:
    """One finding per element rather than one per colliding pair.

    A single oversized box collides with everything beneath it, and reporting
    each collision on its own line produced 23 warnings out of 56 on one page --
    enough to push the sizing failure that caused all of them out of reading
    range. The reader then deletes the offender just to see the next round,
    which is what actually happened.

    So the pairs are attributed to whichever element is in most of them and that
    element answers for the lot in one sentence. Greedy and re-counted each
    round, so the worst offender is named first and nothing is reported twice.

    A lone collision keeps the pairwise sentence and all of its arithmetic. Two
    boxes on top of each other is the common case and is worth stating in full;
    the collapse is for the case where stating it in full is the problem.
    """
    findings: list[Finding] = []
    remaining = list(pairs)
    while remaining:
        owner = _overlap_owner(remaining)
        mine = [p for p in remaining if owner in (p[0], p[1])]
        remaining = [p for p in remaining if owner not in (p[0], p[1])]
        partners = sorted(
            (b_id if a_id == owner else a_id, ox, oy, share)
            for a_id, b_id, ox, oy, share in mine
        )
        findings.append(_overlap_message(owner, partners, types, parent_name))
    return findings


def _overlap_owner(pairs: list[tuple[str, str, float, float, float]]) -> str:
    """Who answers for a set of colliding pairs: whichever element is in most of them.

    The earlier id on a tie. Called again on what is left after each answer, so
    the worst offender is named first. The Builder's twin is ``overlapOwner``.
    """
    counts: dict[str, int] = {}
    for a_id, b_id, *_ in pairs:
        counts[a_id] = counts.get(a_id, 0) + 1
        counts[b_id] = counts.get(b_id, 0) + 1
    return min(counts, key=lambda el_id: (-counts[el_id], el_id))


def _overlap_message(
    owner: str,
    partners: list[tuple[str, float, float, float]],
    types: Mapping[str, str],
    parent_name: str,
) -> Finding:
    owner_type = types.get(owner, "?")
    key = ("overlap", owner, *(p_id for p_id, _, _, _ in partners))
    if len(partners) == 1:
        other, ox, oy, share = partners[0]
        return Finding(
            owner,
            "overlap",
            f"{owner} ({owner_type}) and {other} ({types.get(other, '?')}) overlap by "
            f"{ox:.0f}x{oy:.0f}px ({share:.0f}% of the smaller one) inside {parent_name}.",
            key=key,
        )
    named = ", ".join(
        f"{p_id} by {ox:.0f}x{oy:.0f}px"
        for p_id, ox, oy, _ in partners[:_OVERLAP_NAMED]
    )
    rest = len(partners) - _OVERLAP_NAMED
    tail = f", and {rest} more." if rest > 0 else "."
    return Finding(
        owner,
        "overlap",
        f"{owner} ({owner_type}) overlaps {len(partners)} elements inside "
        f"{parent_name}: {named}{tail}",
        key=key,
    )


# --- Deliberately stacked elements -----------------------------------------
#
# Two boxes in the same place are usually a mistake and sometimes the entire
# design: a tab strip is N panels at identical coordinates, each gated by a
# `visible_when` on the same key. Warning about those fires on every page that
# uses the pattern, and a checker that cries wolf on correct work teaches people
# to stop reading it -- which costs more than the collisions it does catch.
#
# So an overlap is suppressed only when the two conditions PROVABLY cannot both
# hold. Everything this cannot prove still warns: a missed collision between two
# conditionally-shown elements costs one warning, and a false one costs the
# credibility of every warning printed beside it.

#: The operator spellings the panel's ``_evalConditionOp`` understands, by what
#: they mean. A spelling missing here reads as undecidable, which only costs a
#: warning; tests/test_ui_page_review_mirrors.py holds these to the panel's
#: and the Builder's sets to these.
_EQ = frozenset({"eq", "equals", "=="})
_NE = frozenset({"ne", "not_equals", "!="})
#: Range operators, mapped to whether the bound they set is inclusive.
_LOWER = {"gt": False, ">": False, "gte": True, ">=": True}
_UPPER = {"lt": False, "<": False, "lte": True, "<=": True}

def _literal_kind(value: Any) -> str | None:
    """Which kind of authored literal this is, where both surfaces agree; None where they do not.

    ``null``, ``bool``, ``number`` or ``text``. A list or an object as a
    condition value is not something either surface can reason about, and
    Python and JavaScript do not even agree on whether an empty one is truthy. A
    boolean is never a number here, though Python counts it as one. Equality,
    truthiness and the bounds below all ask this first.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "text"
    return None


def _same_value(left: Any, right: Any) -> bool:
    """Two authored literals, compared the way both surfaces can agree on.

    Not ``==``: Python says ``1 == True`` and JavaScript says ``"1" == 1``, and
    they disagree about which. Same kind and same value, or different.
    """
    kind = _literal_kind(left)
    if kind is None or kind != _literal_kind(right):
        return False
    if kind == "number":
        return float(left) == float(right)
    return left == right


def _number(value: Any) -> float | None:
    """A condition value as a number, or None when it is not one.

    A numeric string is deliberately not one. The panel compares it with
    JavaScript's coercing ``>``, which has no Python equivalent worth mirroring,
    and undecidable here only costs a warning nobody needed suppressed.
    """
    return float(value) if _literal_kind(value) == "number" else None


def _holds(value: Any, op: str, target: Any) -> bool:
    """Whether a key pinned to ``value`` satisfies ``op target``.

    True for anything undecidable, so an unreadable pair yields no proof of
    exclusivity rather than a false one.
    """
    if op in _EQ:
        return _same_value(value, target)
    if op in _NE:
        return not _same_value(value, target)
    if op in ("truthy", "falsy"):
        return _truth_holds(value, op)
    if op in _LOWER or op in _UPPER:
        return _bound_holds(value, op, target)
    return True


def _truth_holds(value: Any, op: str) -> bool:
    """Whether a key pinned to ``value`` is ``truthy`` or ``falsy``, as ``op`` asks.

    True (undecidable) for a value the two surfaces would not read alike.
    """
    if _literal_kind(value) is None:
        return True
    return bool(value) if op == "truthy" else not value


def _bound_holds(value: Any, op: str, target: Any) -> bool:
    """Whether a key pinned to ``value`` is on the near side of the bound ``op target``.

    True (undecidable) unless both are numbers.
    """
    left, right = _number(value), _number(target)
    if left is None or right is None:
        return True
    if op in _LOWER:
        return left >= right if _LOWER[op] else left > right
    return left <= right if _UPPER[op] else left < right


def _no_room_between(lower_op: str, lower: Any, upper_op: str, upper: Any) -> bool:
    """Whether a lower bound and an upper bound leave no number that meets both.

    False (no proof) unless both bounds are numbers.
    """
    low, high = _number(lower), _number(upper)
    if low is None or high is None:
        return False
    closed = _LOWER[lower_op] and _UPPER[upper_op]
    return high < low or (high == low and not closed)


def _one_way(op_a: str, val_a: Any, op_b: str, val_b: Any) -> bool:
    """Whether condition ``a`` rules out ``b``, read in one direction only."""
    if op_a in _EQ:
        # `key == val_a` pins the key to one value, so the other condition is
        # either satisfied by that value or contradicts it outright.
        return not _holds(val_a, op_b, val_b)
    if op_a == "truthy":
        return op_b == "falsy"
    if op_a in _LOWER and op_b in _UPPER:
        return _no_room_between(op_a, val_a, op_b, val_b)
    return False


def _leaf(condition: Any) -> tuple[str, str, Any] | None:
    """One condition as ``(key, operator, value)``, or None if unreadable."""
    if not isinstance(condition, Mapping):
        return None
    key = condition.get("key")
    if not isinstance(key, str) or not key:
        return None
    return key, str(condition.get("operator") or "eq").lower(), condition.get("value")


def _branches(element: Mapping[str, Any]) -> list[list[tuple[str, str, Any]]] | None:
    """A ``visible_when`` as branches of ANDed leaves: visible if any branch is.

    A bare condition and an ``all:`` block are one branch; an ``any:`` block is
    one branch per condition. None means there is nothing to reason about, which
    is not the same as an empty list -- an empty list of branches is never
    satisfiable, and would suppress every overlap on the page. An unreadable
    condition is dropped from ``all:`` and makes ``any:`` undecidable; each
    block's reader says why.
    """
    show = _show(element)
    when = show.get("visible_when") if show is not None else None
    if not isinstance(when, Mapping):
        return None

    any_of, all_of = when.get("any"), when.get("all")
    if isinstance(any_of, list):
        return _any_branches(any_of)
    if isinstance(all_of, list):
        return _all_branch(all_of)
    leaf = _leaf(when)
    return [[leaf]] if leaf else None


def _any_branches(conditions: list[Any]) -> list[list[tuple[str, str, Any]]] | None:
    """An ``any:`` block, one branch per condition; None if any one is unreadable.

    Dropping an unreadable condition here would NARROW the set, which could
    prove an exclusivity that is not there.
    """
    leaves = [_leaf(c) for c in conditions]
    if not leaves or any(leaf is None for leaf in leaves):
        return None
    return [[leaf] for leaf in leaves if leaf]


def _all_branch(conditions: list[Any]) -> list[list[tuple[str, str, Any]]] | None:
    """An ``all:`` block as one branch of its readable conditions; None when none is.

    Dropping an unreadable condition only widens the set: if the wider
    condition is still exclusive, so is the real one.
    """
    kept = [leaf for c in conditions if (leaf := _leaf(c))]
    return [kept] if kept else None


def mutually_exclusive(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Whether two elements can never be on screen at the same time.

    Proof, not inference: every way ``a`` can be visible has to contradict every
    way ``b`` can be. Two conditions contradict when they name the same key and
    no single value satisfies both -- different ``eq`` values, an ``eq`` against
    its own ``ne``, ``truthy`` against ``falsy``, or two bounds that leave no
    room between them.
    """
    a_branches, b_branches = _branches(a), _branches(b)
    if not a_branches or not b_branches:
        return False
    return all(
        any(
            _one_way(a_op, a_val, b_op, b_val) or _one_way(b_op, b_val, a_op, a_val)
            for a_key, a_op, a_val in a_leaves
            for b_key, b_op, b_val in b_leaves
            if a_key == b_key
        )
        for a_leaves in a_branches
        for b_leaves in b_branches
    )


# --- A page's controls against the masters underneath them -----------------
#
# Masters are appended to the page surface BEFORE the page's own elements
# (panel.js `renderPage`), and every child of `.panel-page` shares one z-index,
# so the later sibling wins: a control laid over a master hides it and takes the
# touch. That is the whole failure. A master nav bar is how somebody gets off a
# page -- decision 15 of the custom-UI plan makes it the ONLY way off a page
# that draws its own markup -- so burying one is not a cosmetic collision.
#
# It was invisible on both surfaces: the overlap check collides `page.elements`
# with each other, and masters are not among them.
#
# A custom page is the exception and needs no test here. There the frame is
# appended FIRST and the masters paint over it, and `review_page` has already
# returned by the time this runs (its controls are not drawn at all, which is
# the one thing it says about such a page).


def _stored_master_box(placements: Mapping[str, Any], orientation: str) -> Any:
    """Which of a master's stored boxes is drawn on a page arranged this way.

    The panel's ``_masterPlacement``: this orientation's own box, then
    landscape, then portrait, then whatever is there. A master has to be valid
    on every page it appears on, so it carries its own orientation-keyed boxes
    rather than borrowing a layout's -- and the panel falls back rather than
    drawing nothing, so a master with only a landscape box still appears on a
    portrait screen. The Builder's twin is ``storedMasterBox``.

    One difference from the panel: past the named orientations the panel takes
    the first stored value and this the first that is set, so a box stored as
    null draws nothing there and is skipped here. The project model refuses a
    null box, so no loaded project reaches it.
    """
    return (
        placements.get(orientation)
        or placements.get("landscape")
        or placements.get("portrait")
        or next((box for box in placements.values() if box), None)
    )


def _drawn_box(raw: Any) -> dict[str, float] | None:
    """A stored box as the panel draws it, or None when it is not numbers.

    The same defaults ``_placeElement`` applies to a missing coordinate. A
    hand-edited file can carry a NaN, which compares False against every bound
    and would otherwise report an overlap measured in "nan px".
    """
    box = _mapping(raw)
    try:
        out = {
            "x": float(box.get("x") or 0.0),
            "y": float(box.get("y") or 0.0),
            "w": float(box.get("w") or 100.0),
            "h": float(box.get("h") or 100.0),
        }
    except (TypeError, ValueError):
        return None
    return out if all(isfinite(value) for value in out.values()) else None


def master_box(master: Mapping[str, Any], orientation: str) -> dict[str, float] | None:
    """The box a master draws in on a page arranged this way."""
    placements = master.get("placements")
    if not isinstance(placements, Mapping):
        return None
    raw = _stored_master_box(placements, orientation)
    return None if raw is None else _drawn_box(raw)


def master_draws_on(pages: Any, page_id: str) -> bool:
    """Whether a master appears on this page. The panel's own test, exactly."""
    return pages == "*" or (isinstance(pages, list) and page_id in pages)


def _master_scope(pages: Any) -> str:
    """How widely a master draws, said the way it matters to the reader.

    The point of the clause is that a master is shared, so moving it is not a
    local fix -- whatever it is doing here, it is doing somewhere else too.
    """
    if isinstance(pages, list):
        return f"{len(pages)} page" + ("" if len(pages) == 1 else "s")
    return "every page"


@dataclass(frozen=True)
class DrawnPage:
    """A page's controls as one arrangement draws them, which is what a master
    underneath them is checked against.

    ``boxes`` are absolute -- page percentages with container nesting already
    flattened -- because a master's box is a percentage of the viewport and a
    nested control's is a percentage of its container, so the two are not
    comparable until one of them has been folded down. They hold only the
    controls drawn in this arrangement; ``elements`` and ``parents`` hold every
    element on the page, by id. The Builder's twin is ``DrawnPage``.
    """

    page_id: str
    orientation: str
    boxes: Mapping[str, Mapping[str, float]]
    elements: Mapping[str, Mapping[str, Any]]
    parents: Mapping[str, str | None]


@dataclass(frozen=True)
class _MasterHere:
    """One master as it draws on one page: its box, its size, the screen it is on."""

    master: Mapping[str, Any]
    page_id: str
    box: dict[str, float]
    size_px: tuple[float, float]
    reference: tuple[float, float]


def _master_here(master: Any, drawn: DrawnPage) -> _MasterHere | None:
    """A master as it draws on this page, or None when it draws nothing here.

    Hidden, listed for other pages only, with no box, or with no size: each is
    a master the page's controls cannot be drawn over.
    """
    m = _mapping(master)
    if m.get("hidden") or not master_draws_on(m.get("pages", "*"), drawn.page_id):
        return None
    box = master_box(m, drawn.orientation)
    if box is None:
        return None
    reference = reference_box(drawn.orientation)
    size_px = _box_px(box, reference)
    if size_px[0] <= 0 or size_px[1] <= 0:
        return None
    return _MasterHere(m, drawn.page_id, box, size_px, reference)


def _covered_by(here: _MasterHere, drawn: DrawnPage) -> dict[str, tuple[float, float]]:
    """Each drawn control lying on this master, with the pixels it covers per axis.

    A control that can never be on screen with the master is not lying on it.
    """
    covered: dict[str, tuple[float, float]] = {}
    for el_id, box in drawn.boxes.items():
        if mutually_exclusive(drawn.elements[el_id], here.master):
            continue
        extent = overlap_extent(box, here.box, here.reference)
        if extent:
            covered[el_id] = (extent[0], extent[1])
    return covered


def _covers_master_finding(
    here: _MasterHere, el_id: str, element: Mapping[str, Any], overlap_px: tuple[float, float],
) -> Finding:
    m = here.master
    m_id = str(m.get("id", "?"))
    ox_px, oy_px = overlap_px
    share = 100.0 * (ox_px * oy_px) / (here.size_px[0] * here.size_px[1])
    return Finding(
        el_id,
        "covers_master",
        # The coverage goes LAST because a variant arrangement appends
        # " in the 'portrait' arrangement" to whatever the sentence ends
        # with, and geometry is the half that is per-arrangement -- a
        # master's page list is not.
        f"{el_id} ({element.get('type', '?')}) is drawn over the master element "
        f"{m_id} ({m.get('type', '?')}), which draws on "
        f"{_master_scope(m.get('pages', '*'))} and sits behind a page's own controls. "
        f"Move {el_id} off it, or stop {m_id} drawing on {here.page_id}. "
        f"{el_id} covers {ox_px:.0f}x{oy_px:.0f}px of {m_id}, {share:.0f}% of it.",
        key=("covers_master", el_id, m_id),
    )


def buried_master_findings(
    drawn: DrawnPage, masters: Sequence[Any], in_scope: Callable[[str], bool],
) -> list[Finding]:
    """Every control on this page that is drawn over a master element.

    Reported on the CONTROL rather than on the master: the control is what
    moved, it is the thing being edited, and the master has no page to be
    warned about it on. A control inside a container that is itself burying the
    master says the same thing twice, so only the container answers -- moving
    it is the fix, and the child has done nothing wrong.
    """
    findings: list[Finding] = []
    for master in masters:
        here = _master_here(master, drawn)
        if here is None:
            continue
        covered = _covered_by(here, drawn)
        findings.extend(
            _covers_master_finding(here, el_id, drawn.elements[el_id], covered[el_id])
            for el_id in sorted(covered)
            if in_scope(el_id) and not _covered_ancestor(el_id, covered, drawn.parents)
        )
    return findings


def _covered_ancestor(
    el_id: str, covered: Mapping[str, Any], parents: Mapping[str, str | None],
) -> bool:
    """Whether a container above this element is burying the same master."""
    seen = {el_id}
    cursor = parents.get(el_id)
    while cursor and cursor not in seen:
        if cursor in covered:
            return True
        seen.add(cursor)
        cursor = parents.get(cursor)
    return False


def custom_page_findings(page: Any) -> list[Finding]:
    """What a page that draws its own markup is doing with the controls on it.

    Two ways this goes wrong, and neither has any geometry to look at:

    A page set to draw its own markup keeps every control that was ever placed
    on it -- deliberately, so switching back restores the page rather than
    asking somebody to rebuild it. But the panel draws none of them, so every
    other finding about them would be advice about pixels nobody will see, and
    a caller reading a clean review would conclude the controls are fine.

    And a page set to draw its own markup that names no file draws its controls
    after all, which looks from the file like the switch did nothing. That is
    the same shape as an image with no ``src``.
    """
    if getattr(page, "render_mode", "elements") != "custom":
        return []
    page_id = str(getattr(page, "id", "?"))
    custom_file = getattr(page, "custom_file", None)
    count = len(getattr(page, "elements", None) or ())
    if not custom_file:
        return [Finding(
            page_id,
            "custom_page_without_a_file",
            f"{page_id} is set to show a page you wrote but names no file, so it still "
            f"draws its controls. Choose a file in the project's ui/ folder, or set the "
            f"page back to controls.",
        )]
    if not count:
        return []
    controls = "control" if count == 1 else "controls"
    them = "it" if count == 1 else "them"
    return [Finding(
        page_id,
        "custom_page_elements_not_drawn",
        f"{page_id} shows {custom_file}, so the {count} {controls} on it {'is' if count == 1 else 'are'} "
        f"not drawn. Move {them} to another page, or set the page back to controls to show "
        f"{them} again.",
    )]


def no_box_finding(element: Mapping[str, Any], parent_name: str) -> Finding:
    """An element the primary arrangement never positions.

    The renderer's fallback for a missing placement is ``{0, 0, 100, 100}`` --
    it fills its parent and covers everything already there. Nothing in the
    file says so, which makes this the one geometry defect with no geometry to
    look at.
    """
    el_id = str(element.get("id", "?"))
    return Finding(
        el_id,
        "no_placement",
        f"{el_id} ({element.get('type', '?')}) has no placement, so it fills "
        f"{parent_name} edge to edge and covers whatever is already there. Give it "
        f"{{x, y, w, h}}.",
    )


# --- Binding findings ------------------------------------------------------


def element_type_finding(element: Mapping[str, Any]) -> Finding | None:
    """A type the panel has no renderer for, which draws nothing at all.

    ``UIElement.type`` is a free-form ``str`` and every layer downstream treats
    it as one: the loader accepts anything, this module used to return early on
    a type it did not know, and ``renderElement``'s switch falls through to a
    ``console.warn`` and returns null. So the element has an id, a placement and
    bindings, the write comes back created, and it is simply absent from the
    screen -- a defect with a file entry and no symptom.

    The message lists the whole set on purpose. This is the only place an author
    who guessed is told what the alternatives are, and one of them (``plugin``)
    is in neither the Builder's palette nor the authoring prompt, so naming it
    here is what makes it reachable at all.
    """
    el_type = str(element.get("type", ""))
    if el_type in RENDERED_TYPES:
        return None
    el_id = str(element.get("id", "?"))
    return Finding(
        el_id,
        "unknown_element_type",
        f"{el_id} has type '{el_type}', which the panel has no renderer for, so it draws "
        f"nothing at all. The types are: {', '.join(sorted(RENDERED_TYPES))}.",
    )


def _is_unset(value: Any) -> bool:
    """A value a full model dump carries for a field nobody set.

    Not an authoring decision, so never worth a finding. ``0`` and ``False``
    are decisions.
    """
    return value is None or value == "" or value == [] or value == {}


def _unread_reason(el_type: str, name: str, honored: frozenset[str]) -> str:
    """Why a property this type does not read is there, said as the repair.

    A property some OTHER type reads is usually the right idea on the wrong
    element -- ``label`` on a ``label``, which wants ``text``. A property NO
    type reads was invented, and the only useful answer is the list of what this
    type does read, which the finding gives after this.
    """
    if el_type == "matrix" and name in MATRIX_CONFIG_KEYS:
        # The right idea one level too high. Saying "nothing reads it" here
        # would be true of the top level and useless, because the matrix
        # does read it -- from inside matrix_config.
        return f"'{name}' belongs inside matrix_config, not on the element."
    elsewhere = sorted(
        other for other, props in HONORED_PROPERTIES.items() if name in props
    )
    if not elsewhere:
        return f"No element type reads '{name}'."
    where = _joined([f"`{t}`" for t in elsewhere])
    why = f"'{name}' is read by {where}, not by a {el_type}."
    if name == "label" and "text" in honored:
        why += " A label element draws `text`."
    return why


def property_findings(element: Mapping[str, Any]) -> list[Finding]:
    """Properties this element type's renderer never reads.

    The element-level twin of ``binding_findings``, and it fires on the same
    silence: the field is declared on ``UIElement`` for some other type (or on
    no type at all), the loader stores it, the write returns created, and the
    renderer has no line that looks at it. ``_unread_reason`` says which of
    those it is, because the repair is different.

    Anything unset is ignored. A caller that dumps a full Pydantic model hands
    over sixty keys of ``None``, and warning that an unset field is unread would
    bury the two that were actually set.
    """
    el_type = str(element.get("type", ""))
    honored = HONORED_PROPERTIES.get(el_type)
    if honored is None:  # a type this module has never heard of; say nothing
        return []

    el_id = str(element.get("id", "?"))
    reads = ", ".join(sorted(honored))
    return [
        Finding(
            el_id,
            "property_not_rendered",
            f"{el_id} ({el_type}) sets '{name}', which a {el_type} does not render. "
            f"{_unread_reason(el_type, name, honored)} A {el_type} reads: {reads}.",
            key=("property_not_rendered", el_id, name),
        )
        for name, value in element.items()
        if name not in honored and name not in STRUCTURAL_PROPERTIES and not _is_unset(value)
    ]


#: Controls that are inert without one particular thing, and what to set.
#:
#: Not "this field is required" -- almost nothing is, and a half-built page
#: mid-edit should not be scolded. These are the four where the element draws an
#: empty box and there is no reading under which that was the intent: an image
#: with no source, a nav button with nowhere to go, a label with no text and
#: nothing bound to put text in it, a select with no options to select.
#:
#: Each entry is (property, binding slot that can supply it instead, remedy).
#: The slot matters: a label bound to ``show.value`` needs no ``text``, and
#: warning about it would fire on the correct spelling.
INERT_WITHOUT: dict[str, tuple[str, str | None, str]] = {
    "image": ("src", None, "an asset ref ('assets://logo.png') or a URL"),
    "page_nav": ("target_page", None, "the page id it should open, or '$back'"),
    "label": ("text", "value", "the string to draw, or a show.value binding"),
    "select": ("options", "items", "[{label, value}, ...] for the list of choices"),
    "custom": ("custom_file", None, "a page in the project's ui/ folder, e.g. 'room_map/index.html'"),
}


def _look_states_naming(look: Mapping[str, Any], field: str) -> list[str]:
    """Which states of a ``show.look`` name ``field`` (an icon, a picture), in both spellings.

    The multi-state form carries one appearance per state; the binary form
    carries two, ``style_active`` and ``style_inactive``. Both are offered by
    the same editor, so both reach the file.
    """
    states = look.get("states")
    if isinstance(states, Mapping):
        return sorted(
            str(name) for name, spec in states.items()
            if isinstance(spec, Mapping) and spec.get(field)
        )
    return [
        half for half, side in (("active", "style_active"), ("inactive", "style_inactive"))
        if isinstance(look.get(side), Mapping) and look[side].get(field)
    ]


def _look_takes(el_type: str) -> str:
    """What a type draws from ``show.look``, in words, for a sentence that says so."""
    parts = ["color"]
    if el_type in STATE_LABEL_TYPES:
        parts.append("text")
    if el_type in STATE_ICON_TYPES:
        parts.append("an icon")
    if el_type in STATE_IMAGE_TYPES:
        parts.append("a picture")
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _a(el_type: str) -> str:
    """A type name with its article: "a button", "an image"."""
    return f"an {el_type}" if el_type[:1] in ("a", "e", "i", "o", "u") else f"a {el_type}"


#: Where to put per-state words instead, for a type that has no words of its
#: own to change. An image's ``label`` is its alt text, so "use the element's
#: own label" would send the author to a field that never appears.
_STATE_TEXT_REMEDY = {
    "image": "Place a label element bound to the same key over it.",
}


def look_supplies_text(look: Any) -> bool:
    """Whether a ``show.look`` puts TEXT on screen, rather than only colour.

    Two shapes do: a ``states`` map with a ``label`` on any entry, and the binary
    form's ``label_active`` / ``label_inactive``. A look carrying only colours
    (``style_active``, a ``map``, ``states`` entries with just a ``bg_color``)
    supplies none, which is the whole distinction -- the renderer only calls
    ``_setLabelText`` when one of those four fields is present.

    Only meaningful for a type in ``STATE_LABEL_TYPES``; the others take colour
    from a look and have nowhere to put text.
    """
    if not isinstance(look, Mapping):
        return False
    if look.get("label_active") is not None or look.get("label_inactive") is not None:
        return True
    states = look.get("states")
    if isinstance(states, Mapping):
        return any(
            isinstance(spec, Mapping) and spec.get("label") is not None
            for spec in states.values()
        )
    return False


def content_findings(element: Mapping[str, Any]) -> list[Finding]:
    """A control with nothing to draw, which draws an empty box and says nothing.

    The failure that named this: a logo placeholder was created as a master
    image and never given a `src`, because no tool could set one. It stored, it
    round-tripped, it took a placement, and it rendered as nothing at all --
    while the request that asked for it was reported as done.
    """
    el_type = str(element.get("type", ""))
    rule = INERT_WITHOUT.get(el_type)
    if rule is None:
        return []
    prop, slot, remedy = rule
    if _content_supplied(element, prop, slot):
        return []
    el_id = str(element.get("id", "?"))
    instead = f", and no show.{slot} to supply one" if slot else ""
    return [Finding(
        el_id,
        "nothing_to_draw",
        f"{el_id} ({el_type}) has no {prop}{instead}, so it draws an empty box. "
        f"Set {prop} to {remedy}.",
    )]


def _content_supplied(element: Mapping[str, Any], prop: str, slot: str | None) -> bool:
    """Whether anything gives this element what it draws.

    Three things can: the property itself; the ``show`` slot that stands in for
    it (a label bound to ``show.value`` needs no ``text``); and, for a type that
    draws a look's words (``STATE_LABEL_TYPES``), a look that has some. The
    renderer draws show.look's per-state text for those types and falls back to
    the matched default_state, so a state chip that carries its four captions in
    the look and no ``text`` at all draws correctly; warning about it taught
    authors to add a dead ``text`` to silence the check. Another source the panel
    learns to draw from is one more line here, and in ``contentSupplied``.
    """
    if element.get(prop):
        return True
    show = _show(element)
    if show is None:
        return False
    if slot and show.get(slot):
        return True
    return (
        str(element.get("type", "")) in STATE_LABEL_TYPES
        and look_supplies_text(show.get("look"))
    )


#: The keypad's defaults, as panel.js renderKeypad reads them (``digits ?? 4``,
#: ``auto_send_delay_ms ?? 1500``). The delay is also the review's threshold:
#: nobody has measured how long a person takes between two keys on a panel, so
#: the one number with a claim to it is the platform's own default.
#: tests/test_ui_page_review_mirrors.py holds both to renderKeypad and to the
#: Builder's copies, which its keypad properties also show.
KEYPAD_DIGITS_DEFAULT = 4
KEYPAD_SEND_DELAY_DEFAULT_MS = 1500


@dataclass(frozen=True)
class _Keypad:
    """How a keypad sends, with the panel's defaults filled in the way renderKeypad fills them."""

    el_id: str
    phone: bool
    """The Phone style, which has no Enter key."""
    auto_send: bool
    """Send without Enter."""
    digits: Any
    delay: Any
    """Send Delay, in ms."""

    @classmethod
    def of(cls, element: Mapping[str, Any]) -> _Keypad:
        digits = element.get("digits")
        delay = element.get("auto_send_delay_ms")
        return cls(
            el_id=str(element.get("id", "?")),
            phone=element.get("keypad_style") == "phone",
            auto_send=bool(element.get("auto_send")),
            digits=KEYPAD_DIGITS_DEFAULT if digits is None else digits,
            delay=KEYPAD_SEND_DELAY_DEFAULT_MS if delay is None else delay,
        )

    @property
    def pause_sends(self) -> bool:
        """Whether a pause after a key press sends what was typed.

        Only with Send without Enter on, and only with more than one digit to
        wait for: the first key of a one-digit entry is the whole entry.
        """
        if not self.auto_send or not isinstance(self.digits, int):
            return False
        return self.digits >= 2


def _never_sends(pad: _Keypad) -> Finding | None:
    """The Phone style has no Enter key, so with Send without Enter off nothing typed is sent."""
    if not pad.phone or pad.auto_send:
        return None
    return Finding(
        pad.el_id,
        "keypad_never_sends",
        f"{pad.el_id} (keypad) uses the Phone style, which has no Enter key, and "
        f"Send without Enter is off, so nothing typed on it is ever sent. Turn on "
        f"Send without Enter, or choose the Numeric style.",
    )


def _sends_early(pad: _Keypad) -> Finding | None:
    """A Send Delay under the default can send a code before the last digit is typed."""
    if not pad.pause_sends or not isinstance(pad.delay, (int, float)):
        return None
    if pad.delay >= KEYPAD_SEND_DELAY_DEFAULT_MS:
        return None
    return Finding(
        pad.el_id,
        "keypad_sends_early",
        f"{pad.el_id} (keypad) sends what has been typed {_num(pad.delay)} ms after the last "
        f"key press, shorter than the {KEYPAD_SEND_DELAY_DEFAULT_MS} ms default, so a "
        f"code can be sent before it is finished. Set Send Delay to "
        f"{KEYPAD_SEND_DELAY_DEFAULT_MS} ms or more; an entry of {pad.digits} digits is sent "
        f"as soon as the last one is pressed.",
    )


#: A keypad's warnings, in the order they are said. The two cannot both fire:
#: one needs Send without Enter off and the other needs it on.
_KEYPAD_CHECKS: tuple[Callable[[_Keypad], Finding | None], ...] = (_never_sends, _sends_early)


def keypad_findings(element: Mapping[str, Any]) -> list[Finding]:
    """A keypad that never sends, or sends a code before it is finished.

    With ``auto_send`` the panel sends at ``digits`` keys AND after
    ``auto_send_delay_ms`` with no key press, which is how the phone layout
    (no Enter key) sends an entry shorter than ``digits``. Two settings break
    that: the phone layout with ``auto_send`` off has no way to send at all,
    and a delay under the default can send after the first digit of a PIN --
    at 200 ms a four-digit PIN went out after its first key. With one digit
    the delay never runs (the first key is the whole entry), so it is silent.
    """
    if element.get("type") != "keypad":
        return []
    pad = _Keypad.of(element)
    return [found for check in _KEYPAD_CHECKS if (found := check(pad))]


#: Which direction each axis routes, for a sentence that has to say which one it
#: means without the reader counting the letters in "destinations".
_MATRIX_AXIS_DIRECTION = {"sources": "route from", "destinations": "route to"}

#: How many entries a matrix finding names before it counts the rest. Same
#: bargain as ``_OVERLAP_NAMED``: a 128-output frame must not produce a sentence
#: nobody reads, and nothing is quietly dropped -- the total is stated first.
_MATRIX_NAMED = 4


def _matrix_named(labels: list[str]) -> str:
    """A list of entry names, capped, with the remainder counted."""
    shown = ", ".join(labels[:_MATRIX_NAMED])
    rest = len(labels) - _MATRIX_NAMED
    return f"{shown} and {rest} more" if rest > 0 else shown


def _entry_name(entry: Mapping[str, Any]) -> str:
    """What a review sentence calls one matrix row: the name it was given, else its value.

    Not the panel's caption (``_entryLabel``), which can read a live name off
    the device: a review has no state to read, so a row the device names is
    called by its value here. The Builder's twin is ``matrixEntryName``.
    """
    return str(entry.get("label", entry.get("value")))


def _destinations(count: int) -> str:
    """``1 destination`` or ``3 destinations``."""
    return f"{count} destination{'' if count == 1 else 's'}"


def _unread_config_findings(el_id: str, config: Mapping[str, Any]) -> list[Finding]:
    """Keys in ``matrix_config`` the matrix renderer never reads."""
    unread = sorted(k for k in config if k not in MATRIX_CONFIG_KEYS)
    if not unread:
        return []
    return [Finding(
        el_id,
        "matrix_config_unread",
        f"{el_id} (matrix) sets matrix_config {_joined([repr(k) for k in unread])}, "
        f"which the matrix renderer does not read. The keys it reads are: "
        f"{', '.join(sorted(MATRIX_CONFIG_KEYS))}.",
        key=("matrix_config_unread", el_id, *unread),
    )]


def _axis_findings(el_id: str, config: Mapping[str, Any]) -> list[Finding]:
    """Each axis that resolves to nothing, or has two rows claiming one value."""
    findings: list[Finding] = []
    for axis in ("sources", "destinations"):
        entries = resolve_axis(config, axis)
        if not entries:
            findings.append(Finding(
                el_id,
                "matrix_default_size",
                f"{el_id} (matrix) resolves to no {axis}, so it draws nothing to "
                f"{_MATRIX_AXIS_DIRECTION[axis]}. List them under "
                f"matrix_config.{axis}, or generate them with {axis}.from.count.",
                key=("matrix_default_size", el_id, axis),
            ))
            continue
        collided = _colliding_entries(entries, axis)
        if collided:
            findings.append(Finding(
                el_id,
                "matrix_duplicate_values",
                f"{el_id} (matrix) has {len(collided)} of its {axis} claiming a value "
                f"another one already has ({_matrix_named(collided)}), and a routed "
                f"source is matched by value -- so they cannot be told apart. Give "
                f"each one the value its device actually reports.",
                key=("matrix_duplicate_values", el_id, axis, *collided),
            ))
    return findings


def _colliding_entries(entries: list[Mapping[str, Any]], axis: str) -> list[str]:
    """The rows claiming a value an earlier row already has, by name.

    Two entries claiming one value is new in 0.10.0 and is only possible
    because a value is now opaque and authored. It is not cosmetic: the
    crosspoint match lights every entry the device's report names, and a list
    dropdown can only hold one option per value, so the second entry is either
    a duplicate light or a row that cannot be selected.
    """
    seen: list[Any] = []
    collided: list[str] = []
    for entry in entries:
        # By what the DEVICE calls it, which is what a report is matched
        # against -- the same thing as the value on all but a couple of
        # drivers, and the only thing that can collide on those.
        value = source_report_value(entry) if axis == "sources" else entry.get("value")
        if any(route_matches(value, other) for other in seen):
            collided.append(_entry_name(entry))
        else:
            seen.append(value)
    return collided


def _unbacked_lock_findings(el_id: str, config: Mapping[str, Any]) -> list[Finding]:
    """Lock buttons drawn for destinations no variable backs.

    A lock only the panel that pressed it remembers is not a lock: it is gone
    the next time the page draws, the panel by the other door never hears
    about it, and the rack does not know either. Backing it with a variable is
    what makes it one, and `var.`/`plugin.` are the only prefixes a panel is
    allowed to write, so the check is the prefix rather than merely presence.
    """
    if config.get("show_lock") is not True:
        return []
    unbacked = [
        _entry_name(entry)
        for entry in resolve_axis(config, "destinations")
        if not str(entry.get("lock_key") or "").startswith(PANEL_WRITABLE_PREFIXES)
    ]
    if not unbacked:
        return []
    one = len(unbacked) == 1
    return [Finding(
        el_id,
        "matrix_lock_unbacked",
        f"{el_id} (matrix) draws lock buttons, but {_destinations(len(unbacked))} "
        f"({_matrix_named(unbacked)}) "
        f"{'has' if one else 'have'} no lock_key under "
        f"{' or '.join(repr(p) for p in PANEL_WRITABLE_PREFIXES)}, so locking "
        f"{'it' if one else 'them'} is this panel's own memory "
        f"-- forgotten when the page redraws, and invisible to every other "
        f"panel in the space. Give each one a variable, e.g. "
        f"'var.{el_id}_lock_1'.",
    )]


def _blind_destination_findings(el_id: str, config: Mapping[str, Any]) -> list[Finding]:
    """Destinations with no ``route_key``, which never show what is routed to them."""
    blind = [
        _entry_name(entry)
        for entry in resolve_axis(config, "destinations")
        if not entry.get("route_key")
    ]
    if not blind:
        return []
    return [Finding(
        el_id,
        "matrix_no_route_feedback",
        f"{el_id} (matrix) has {_destinations(len(blind))} with no route_key "
        f"({_matrix_named(blind)}), so those never show what is routed -- the "
        f"matrix routes, and the panel cannot say which source is selected. Give "
        f"each one the state key holding its routed source, e.g. "
        f"'device.<id>.output.1.input'.",
    )]


#: The checks a configured matrix gets, in the order they report. A new one is
#: an entry here and one in ``MATRIX_CHECKS``, its twin in uiBuilderHelpers.ts.
_MATRIX_CHECKS: tuple[Callable[[str, Mapping[str, Any]], list[Finding]], ...] = (
    _unread_config_findings,
    _axis_findings,
    _unbacked_lock_findings,
    _blind_destination_findings,
)


def matrix_findings(element: Mapping[str, Any]) -> list[Finding]:
    """A matrix that will draw, and route, and never show what is routed.

    ``route_key`` is what makes a destination visible rather than merely
    operable: without one the panel registers no state binding for that row, so
    it keeps its inactive colour for the life of the panel. Clicking still routes
    -- the command carries ``$input``/``$output`` from the event, not from config
    -- so the control is half-alive, and a bench test that only asks "does it
    switch" passes.

    It is per destination now rather than per element, which is the whole shape
    of project format 0.10.0: each destination owns its key, so half a matrix can
    be blind while the other half reports, and that is worth naming precisely.

    The others in ``_MATRIX_CHECKS`` are the ones that make it hard to notice: a
    config full of keys nothing reads, an axis that resolves to nothing, two
    entries that claim the same value, and a lock button backed by nothing.
    """
    if str(element.get("type", "")) != "matrix":
        return []
    config = element.get("matrix_config")
    el_id = str(element.get("id", "?"))
    if not isinstance(config, Mapping) or not config:
        return [Finding(
            el_id,
            "matrix_not_configured",
            f"{el_id} (matrix) has no matrix_config, so it draws an empty box. Set "
            f"sources and destinations -- each is a list of entries, or a generator "
            f"standing for one, e.g. destinations: "
            f"{{'from': {{'count': 8, 'route_key': 'device.<id>.output.*.input'}}}}.",
        )]
    return [finding for check in _MATRIX_CHECKS for finding in check(el_id, config)]


@dataclass(frozen=True)
class _Shown:
    """One element's ``show`` bindings, beside what its type's renderer reads."""

    el_id: str
    el_type: str
    honored: frozenset[str]
    show: Mapping[str, Any]

    @property
    def look(self) -> Mapping[str, Any] | None:
        """``show.look`` when this type reads one at all, else None."""
        look = self.show.get("look")
        return look if "look" in self.honored and isinstance(look, Mapping) else None

    def dropped(self, setting: str, noun: str, remedy: str) -> str:
        """The sentence for a per-state setting this type takes nothing of from show.look.

        ``setting`` continues ``show.look``: `` sets an icon for on, off``.
        """
        return (
            f"{self.el_id} ({self.el_type}): show.look{setting}, but {_a(self.el_type)} "
            f"takes only {_look_takes(self.el_type)} from show.look, so that {noun} never "
            f"appears. {remedy}"
        )


def _shown(element: Mapping[str, Any]) -> _Shown | None:
    el_type = str(element.get("type", ""))
    honored = HONORED_SHOW_SLOTS.get(el_type)
    if honored is None:  # a type this module has never heard of; say nothing
        return None
    show = _show(element)
    if show is None:
        return None
    return _Shown(str(element.get("id", "?")), el_type, honored, show)


def binding_findings(element: Mapping[str, Any]) -> list[Finding]:
    """Bindings this element type's renderer never reads.

    Nothing rejects these and nothing logs them at runtime: the element draws,
    the state key resolves, and the binding simply has no code path behind it
    for this type. From the outside it is indistinguishable from a value that
    never changes.
    """
    shown = _shown(element)
    if shown is None:
        return []
    return (
        _unread_slot_findings(shown)
        + _look_label_findings(shown)
        + _look_field_findings(shown)
        + _keyless_slot_findings(shown)
    )


def _unread_slot_findings(shown: _Shown) -> list[Finding]:
    """A whole show slot this type has no code path for."""
    reads = (
        ", ".join(f"show.{slot}" for slot in _SLOTS if slot in shown.honored)
        or "nothing from show but show.visible_when"
    )
    findings: list[Finding] = []
    for slot in _SLOTS:
        if not shown.show.get(slot) or slot in shown.honored:
            continue
        extra = ""
        if slot == "look" and "value" in shown.honored:
            extra = (
                " Put whatever depended on it into show.value (a condition with "
                "text_true / text_false), or use a button."
            )
        findings.append(Finding(
            shown.el_id,
            "binding_not_rendered",
            f"{shown.el_id} ({shown.el_type}) declares show.{slot}, which "
            f"{_a(shown.el_type)} does not render -- it reads {reads}. That binding has "
            f"no effect.{extra}",
            key=("binding_not_rendered", shown.el_id, slot),
        ))
    return findings


def _look_label_findings(shown: _Shown) -> list[Finding]:
    """Per-state words on a type that takes only colour from a look.

    A look binding can be honored for its colour while its state labels are
    not: a status LED tints its dot, a select styles its options, an image
    swaps its picture, and none of them has anywhere to put text. A label that
    is present counts even when it is empty: the renderer acts on the field.
    """
    look = shown.look
    if look is None or shown.el_type in STATE_LABEL_TYPES:
        return []
    states = look.get("states")
    if not isinstance(states, Mapping):
        return []
    labelled = sorted(
        str(name) for name, spec in states.items()
        if isinstance(spec, Mapping) and spec.get("label") is not None
    )
    if not labelled:
        return []
    remedy = _STATE_TEXT_REMEDY.get(
        shown.el_type,
        "Use the element's own label, or a label element bound to the same key.",
    )
    return [Finding(
        shown.el_id,
        "binding_not_rendered",
        shown.dropped(f".states sets a label for {', '.join(labelled)}", "text", remedy),
        key=("binding_not_rendered", shown.el_id, "look.states.label"),
    )]


@dataclass(frozen=True)
class _LookField:
    """A per-state look field only some types draw, and what to say when one is dropped."""

    field: str
    drawn_by: frozenset[str]
    named: str
    """How the sentence introduces one: ``an icon``."""
    noun: str
    """How it refers back to it: ``icon``."""
    remedy: Callable[[str], str]
    """What to do instead, from the element's type."""


def _icon_remedy(el_type: str) -> str:
    if "icon" in HONORED_PROPERTIES.get(el_type, frozenset()):
        return "Set the element's own icon -- it draws that one, it just cannot change it per state."
    return "Use a button, which does draw one."


#: Content a look can set per state, and the only types that draw it. A look can
#: be honored for its colour AND its text while a per-state icon is still
#: dropped, because an icon is content rather than style and only the button's
#: evaluator rebuilds it; a per-state picture is drawn only by the types in
#: STATE_IMAGE_TYPES, and a label, a status LED or a select stores one and never
#: shows it. A new per-state field the panel draws for some types is a row here,
#: beside its own STATE_* type set (which review_gen carries to the Builder) and
#: its word in ``_look_takes``.
_LOOK_FIELDS = (
    _LookField("icon", STATE_ICON_TYPES, "an icon", "icon", _icon_remedy),
    _LookField(
        "button_image", STATE_IMAGE_TYPES, "a picture (button_image)", "picture",
        lambda _el_type: "Use an image element bound to the same key.",
    ),
)


def _look_field_findings(shown: _Shown) -> list[Finding]:
    """Each per-state field in ``_LOOK_FIELDS`` set on a type that does not draw it."""
    look = shown.look
    if look is None:
        return []
    findings: list[Finding] = []
    for spec in _LOOK_FIELDS:
        if shown.el_type in spec.drawn_by:
            continue
        names = _look_states_naming(look, spec.field)
        if names:
            findings.append(Finding(
                shown.el_id,
                "binding_not_rendered",
                shown.dropped(
                    f" sets {spec.named} for {', '.join(names)}", spec.noun,
                    spec.remedy(shown.el_type),
                ),
                key=("binding_not_rendered", shown.el_id, f"look.{spec.field}"),
            ))
    return findings


def _names_its_source(slot: str, binding: Mapping[str, Any]) -> bool:
    """Whether a show binding names what it reads: its state key, or its key
    pattern for ``items``. A value shown from a macro's progress reads neither:
    its text comes from the macro."""
    if slot == "value" and binding.get("source") == "macro_progress":
        return True
    field = "key_pattern" if slot == "items" else "key"
    return bool(str(binding.get(field) or "").strip())


def _keyless_slot_findings(shown: _Shown) -> list[Finding]:
    """A binding the renderer DOES read, that names nothing to read.

    It is the half-finished state every one of these passes through -- the
    editor writes the binding when the source is chosen and the key a moment
    later -- and left that way it is worse than no binding at all, because every
    other check here treats a present binding as a supplied one and goes quiet.
    """
    findings: list[Finding] = []
    for slot in _SLOTS:
        binding = shown.show.get(slot)
        if slot not in shown.honored or not isinstance(binding, Mapping):
            continue
        if _names_its_source(slot, binding):
            continue
        named = "key pattern" if slot == "items" else "state key"
        findings.append(Finding(
            shown.el_id,
            "binding_without_key",
            f"{shown.el_id} ({shown.el_type}): show.{slot} has no {named}, so it reads "
            f"nothing and never updates. {_UNBOUND_CONSEQUENCE[slot]} Pick the {named} it "
            f"should read, or remove the binding.",
            key=("binding_without_key", shown.el_id, slot),
        ))
    return findings


# --- What a `do` action can be called --------------------------------------
#
# The other half of the same silence. `binding_findings` above answers whether
# this element type's renderer READS a show slot; this answers whether the
# runtime DISPATCHES a do action, which is a closed set living in
# core/ui_events.py (DISPATCHED_ACTIONS) rather than a per-type question.
#
# The second set is what makes the message worth reading. Five names --
# device.command, event.emit, macro, state.set, ui.navigate -- are both a
# binding action and a macro step, so the two vocabularies look like one and the
# ones that are macro steps ONLY (delay, conditional, wait_until, group.command,
# help.request, help.clear) are the natural wrong guess. Telling somebody `delay` does not
# exist would be false; telling them it is a macro step and how to reach one is
# the fix.

#: Macro steps that are not binding actions. Derived from the two closed sets so
#: a new step or a new action moves it without anyone remembering to.
MACRO_ONLY_ACTIONS = frozenset(BUILTIN_STEP_ACTIONS) - DISPATCHED_ACTIONS

#: Fields inside an action that hold another action. Both are on the FIRST
#: press action by convention (project format: `mode`), and both are dispatched
#: through the same chain, so a broken one is exactly as silent.
_NESTED_ACTION_FIELDS = ("off_action", "hold_action")


def _walk_action(path: str, action: Any) -> list[tuple[str, Mapping[str, Any]]]:
    """One action and everything nested inside it, each with its own path."""
    if not isinstance(action, Mapping):
        return []
    found = [(path, action)]
    for field in _NESTED_ACTION_FIELDS:
        found.extend(_walk_action(f"{path}.{field}", action.get(field)))
    if action.get("action") == "value_map":
        mapped = action.get("map")
        if isinstance(mapped, Mapping):
            # Sorted, not in authored order: the Builder's mirror reads the same
            # object out of JSON, and JavaScript hoists integer-like keys to the
            # front of an object regardless of how they were written. A select
            # mapping "1" and "2" to two commands would otherwise report its two
            # findings in a different order on each side, and the parity suite
            # compares them in order.
            for value in sorted(mapped, key=str):
                found.extend(_walk_action(f"{path}.map.{value}", mapped[value]))
    return found


def _action_entries(
    do: Mapping[str, Any],
) -> list[tuple[str, Mapping[str, Any]]]:
    """Every action dict under ``do``, with the path a reader can go and edit.

    Walks what the runtime walks: an interaction's list (or the single dict it
    accepts as a one-element list), the off_action / hold_action a press mode
    hangs off its first action, and the per-option actions a ``value_map``
    routes to. A nested one is not a lesser case -- an off_action is the half of
    a toggle that turns the room OFF.
    """
    found: list[tuple[str, Mapping[str, Any]]] = []
    for interaction, binding in do.items():
        entries = binding if isinstance(binding, list) else [binding]
        for entry in entries:
            found.extend(_walk_action(f"do.{interaction}", entry))
    return found


def _destination_route_entries(
    element: Mapping[str, Any],
) -> list[tuple[str, Mapping[str, Any]]]:
    """The same walk over a matrix row that overrides the element's do.route.

    A destination can carry its own action list, and the runtime runs it
    INSTEAD of ``do.route`` for that row (``ui_events._destination_route``) --
    which is how one matrix covers a frame's eight outputs plus a "Stream"
    destination that starts an encoder. It is a do binding living somewhere
    else, so it goes silent in exactly the same way, and it is hand-authored
    more often than the element's own default is.
    """
    if str(element.get("type", "")) != "matrix":
        return []
    config = element.get("matrix_config")
    if not isinstance(config, Mapping):
        return []
    found: list[tuple[str, Mapping[str, Any]]] = []
    for entry in resolve_axis(config, "destinations"):
        route = entry.get("route")
        if not isinstance(route, list):
            continue
        named = _entry_name(entry)
        for action in route:
            found.extend(
                _walk_action(f"do.route for '{named}'", action)
            )
    return found


def _element_actions(element: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    """Every action this element can run, each with the path a reader can go and edit.

    Its own ``do`` first, then each matrix row's override of ``do.route``: the
    two places a control keeps actions. ``do_action_findings`` judges them and
    ``do_action_dicts`` hands them out, so a new place an action lives is added
    here once for both. The Builder's twin is ``elementActions``.
    """
    bindings = element.get("bindings")
    do = bindings.get("do") if isinstance(bindings, Mapping) else None
    entries = _action_entries(do) if isinstance(do, Mapping) else []
    return entries + _destination_route_entries(element)


def do_action_dicts(element: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Every action dict this element can run, nesting included, paths dropped.

    The walk ``do_action_findings`` runs (``_element_actions``), for a caller
    that wants the actions rather than a verdict on them
    (``core/event_references`` asks which events a control can emit). It is
    public so that question is answered by the walk that goes where the runtime
    goes, rather than by another copy of the list of places an action nests --
    the copy that would be the one not updated the next time a place is added.
    """
    return [action for _path, action in _element_actions(element)]


#: The names a binding action can have, as a sentence lists them.
_BINDING_ACTIONS_SAID = ", ".join(sorted(DISPATCHED_ACTIONS))


def _why_nothing_runs(path: str, action: str) -> str:
    """What the action at ``path`` is, said after "has", when no branch of the runtime takes it.

    Three cases. The middle one is why the sentence is worth reading: a name
    that is a macro step is the natural wrong guess, and how to reach a macro
    step is the fix. The Builder's twin is ``whyNothingRuns``.
    """
    if not action:
        return f"a {path} entry with no action, so nothing runs when it fires."
    if action in MACRO_ONLY_ACTIONS:
        return (
            f"{path} action '{action}', which is a macro step and not a binding action, "
            f"so nothing runs when it fires. Put it in a macro and call that one with "
            f"action 'macro'."
        )
    return f"{path} action '{action}', which nothing dispatches, so nothing runs when it fires."


def do_action_findings(element: Mapping[str, Any]) -> list[Finding]:
    """Actions the runtime has no branch for, so the interaction does nothing.

    The chain in ``ui_events.execute_action`` ends with the names it knows
    (``DISPATCHED_ACTIONS``); anything else reaches no branch. Nothing rejected it on the way in and --
    until this shipped alongside the runtime's own warning -- nothing logged it
    either, so a control authored with a retired spelling drew perfectly, took
    the press, and did nothing at all.
    """
    el_id = str(element.get("id", "?"))
    el_type = str(element.get("type", ""))
    findings: list[Finding] = []
    for path, action_def in _element_actions(element):
        action = action_def.get("action")
        action = action if isinstance(action, str) else ""
        if action in DISPATCHED_ACTIONS:
            continue
        findings.append(Finding(
            el_id,
            "binding_not_dispatched",
            f"{el_id} ({el_type}) has {_why_nothing_runs(path, action)} A binding action is "
            f"one of: {_BINDING_ACTIONS_SAID}.",
            key=("binding_not_dispatched", el_id, path, action),
        ))
    return findings


# --- Range findings and the one auto-fill ----------------------------------


def bound_state_key(element: Mapping[str, Any]) -> str | None:
    """The state key a control's value reads, when it reads one."""
    show = _show(element)
    value = show.get("value") if show is not None else None
    if not isinstance(value, Mapping):
        return None
    key = value.get("key")
    return key if isinstance(key, str) and key else None


#: Types whose renderer prints a bound number RAW when no rounding is set.
#:
#: Only ``label``. Every other type that draws a number picks its own fallback
#: -- a fader uses 1, a slider derives one from its step -- so they cannot show
#: float64 noise. A label deliberately does not, because most labels are bound to
#: TEXT (device names, input modes, firmware versions) and reformatting "2.10"
#: into "2.1" would be wrong. That is the right call for the renderer and it is
#: what leaves this one case exposed.
_RAW_NUMBER_TYPES = frozenset({"label"})

#: What a driver calls a value that arrives at float64 width.
_FLOAT_DECLARED_TYPES = frozenset({"float", "number", "double", "real"})


def precision_review(
    element: Mapping[str, Any],
    declared: Mapping[str, Any] | None,
    state_key: str,
) -> Finding | None:
    """A label bound to a float with nothing telling it how to round.

    A float32 reading of 0.06 crosses the wire as 0.06000000238418579, and
    ``_labelValueText`` prints a number unchanged when ``display_decimals`` is
    absent. So the panel shows twenty characters of noise where a reading should
    be -- visible from across a room, and on first look.

    Only fires when the DRIVER says the value is a float. A label bound to text,
    or to a value nothing declares, is left alone: this is not an opinion about
    labels, it is the one case where the renderer's own default cannot help.
    """
    if not declared or str(element.get("type", "")) not in _RAW_NUMBER_TYPES:
        return None
    if element.get("display_decimals") is not None:
        return None
    kind = str(declared.get("type", "")).strip().lower()
    if kind not in _FLOAT_DECLARED_TYPES:
        return None
    el_id = str(element.get("id", "?"))
    unit = declared.get("unit")
    example = f"0.06 {unit}" if isinstance(unit, str) and unit else "0.06"
    return Finding(
        el_id, "unrounded_number",
        f"{el_id} (label) shows {state_key}, which the driver declares as a {kind}, and sets "
        f"no display_decimals. A label prints a number exactly as it arrives, so {example} "
        f"reads as 0.06000000238418579. Every other type that draws a number picks its own "
        f"rounding; a label cannot, because most are bound to text. Set display_decimals.",
        key=("unrounded_number", el_id),
    )


def _range_fields(el_type: str) -> tuple[str, ...]:
    """The parts of a range this type takes from the device, in the order they are filled."""
    return (
        "min", "max",
        *(("step",) if el_type in STEP_TYPES else ()),
        *(("unit",) if el_type in UNIT_TYPES else ()),
    )


def _declared_fill(declared: Mapping[str, Any], field: str) -> Any:
    """What the driver declares for this field, when it is the kind the field takes.

    A unit is words and every other bound a number, in ``_literal_kind``'s
    sense: a boolean is neither, so it fills nothing. None when there is nothing
    to fill with.
    """
    value = declared.get(field)
    wanted = "text" if field == "unit" else "number"
    return value if _literal_kind(value) == wanted else None


def _range_fills(
    element: Mapping[str, Any], declared: Mapping[str, Any], state_key: str,
) -> list[Adjustment]:
    """Each part of the range the author left out, filled from the declaration.

    Shares its type sets with the Builder's "Match driver range"
    (DeviceValuePicker), but not its precedence: that button overwrites what the
    author wrote, and this never does -- a value the author wrote, zero
    included, is kept, and each part is filled on its own.
    """
    el_id = str(element.get("id", "?"))
    fills: list[Adjustment] = []
    for field in _range_fields(str(element.get("type", ""))):
        if element.get(field) is not None:
            continue
        value = _declared_fill(declared, field)
        if value is None:
            continue
        fills.append(Adjustment(
            el_id, field, value,
            f"{el_id}: {field} was not set, so it was filled in as {_num(value)} from what "
            f"the driver declares for {state_key}.",
        ))
    return fills


#: The two ends of a control's travel, each with the comparison that puts it
#: past the device's own bound and the words for that end.
_TRAVEL_ENDS: tuple[tuple[str, Callable[[Any, Any], bool], str, str], ...] = (
    ("min", lt, "below", "bottom"),
    ("max", gt, "above", "top"),
)


def _wider_than_device(
    element: Mapping[str, Any], declared: Mapping[str, Any], state_key: str,
) -> list[Finding]:
    """Each end of the control's travel that commands a value the device refuses."""
    el_id = str(element.get("id", "?"))
    findings: list[Finding] = []
    for field, beyond, side, end in _TRAVEL_ENDS:
        device, written = declared.get(field), element.get(field)
        if (
            isinstance(device, (int, float))
            and isinstance(written, (int, float))
            and beyond(written, device)
        ):
            findings.append(Finding(
                el_id, "range_wider_than_device",
                f"{el_id}: {field} {_num(written)} is {side} the {_num(device)} the driver "
                f"declares for {state_key}. The {end} of its travel commands a value the "
                f"device refuses.",
                key=("range_wider_than_device", el_id, field),
            ))
    return findings


def range_review(
    element: Mapping[str, Any],
    declared: Mapping[str, Any] | None,
    state_key: str,
) -> tuple[list[Adjustment], list[Finding]]:
    """Reconcile a control's range against the range the driver declares.

    Three cases, deliberately not treated alike:

    * **absent** -- no intent was expressed, and the renderer's fallback is a
      guess that has already gone badly: an omitted ``max`` on a dB fader
      becomes 100, so the top of the throw commands +100dB. Filled in, and
      reported (``_range_fills``).
    * **narrower than the device** -- left alone. A volume-limited install is
      real authoring, and overriding it would undo a deliberate choice to
      protect something.
    * **wider than the device** -- warned, for the two types that send what they
      read (``_wider_than_device``). The control can command a value the device
      refuses, which shows up as a dead end of travel.
    """
    el_type = str(element.get("type", ""))
    if not declared or el_type not in RANGE_TYPES:
        return [], []
    fills = _range_fills(element, declared, state_key)
    if el_type not in COMMANDING_TYPES:
        return fills, []
    return fills, _wider_than_device(element, declared, state_key)


# --- One element, wherever it is drawn -------------------------------------

#: The checks an element gets on its own, before any box is measured, in the
#: order they report. A page and a master element both run this list, so a new
#: check is one entry here.
_ELEMENT_CHECKS: tuple[Callable[[Mapping[str, Any]], list[Finding]], ...] = (
    binding_findings,
    do_action_findings,
    property_findings,
    content_findings,
    keypad_findings,
    matrix_findings,
)


def _element_findings(element: Mapping[str, Any]) -> list[Finding]:
    """Its type first (an unknown one draws nothing), then each of ``_ELEMENT_CHECKS``."""
    type_finding = element_type_finding(element)
    findings = [type_finding] if type_finding else []
    for check in _ELEMENT_CHECKS:
        findings.extend(check(element))
    return findings


def _declared_range_findings(
    dump: Mapping[str, Any],
    declared_range: Callable[[str], Mapping[str, Any] | None] | None,
) -> tuple[list[Finding], list[Adjustment]]:
    """What the device declares about the state this element is bound to.

    The range findings and the rounding one, read before anything is filled,
    and the fills the declaration supplies. Nothing when the element reads no
    state or the caller has no declarations to ask.
    """
    key = bound_state_key(dump)
    if not (key and declared_range):
        return [], []
    declared = declared_range(key)
    fills, findings = range_review(dump, declared, key)
    rounding = precision_review(dump, declared, key)
    return [*findings, *([rounding] if rounding else [])], fills


def _filled(
    element: Any,
    dump: dict[str, Any],
    fills: list[Adjustment],
    set_field: Callable[[Any, str, Any], None],
) -> list[Adjustment]:
    """Each fill written into the element, and into the dump the rest of the review reads."""
    for fill in fills:
        set_field(element, fill.field, fill.value)
        dump[fill.field] = fill.value
    return list(fills)


# --- The whole page --------------------------------------------------------


def review_page(
    page: Any,
    *,
    touched: set[str] | None = None,
    theme: Mapping[str, Any] | None = None,
    declared_range: Callable[[str], Mapping[str, Any] | None] | None = None,
    set_field: Callable[[Any, str, Any], None] = setattr,
    masters: Sequence[Any] = (),
) -> tuple[list[Finding], list[Adjustment]]:
    """Review everything one write touched, across every arrangement.

    ``touched`` is the point of the whole signature: a write that adds five
    controls to a page of sixty answers for its five. Reporting the other
    fifty-five would bury them, and would re-report the same pre-existing
    problems on every subsequent call until the caller learned to ignore the
    field entirely. A finding is kept when it involves something this write
    touched -- for an overlap, when either side does.

    ``masters`` is the project's master elements, which the page does not carry
    and cannot reach: they live on ``ui.master_elements`` and draw underneath
    every page they are listed on. Passing them is what lets a control be told
    it is sitting on the nav bar. Omitted, that one check simply does not run.

    An ``Adjustment`` in the result always means a field WAS written -- the fill
    goes through ``set_field``, which a caller overrides only to reach an
    element that is not a plain object. Reporting a fill that did not land would
    be worse than not filling at all.
    """
    findings: list[Finding] = []
    adjustments: list[Adjustment] = []
    elements = {el.id: el for el in page.elements}
    dumps = {el_id: dict(_mapping(el)) for el_id, el in elements.items()}
    in_scope = (lambda el_id: True) if touched is None else (lambda el_id: el_id in touched)

    # A page drawing its own markup draws none of these, so everything below is
    # about pixels nobody will see. Said once, and nothing else. It is NOT
    # scoped by `touched`: writing a control onto such a page is exactly when
    # somebody needs to hear it.
    custom = custom_page_findings(page)
    if custom and custom[0].kind == "custom_page_elements_not_drawn":
        return custom, adjustments
    findings.extend(custom)

    # Bindings and ranges are properties of the element itself, so they are
    # answered once rather than once per arrangement.
    for el_id, dump in dumps.items():
        if not in_scope(el_id):
            continue
        findings.extend(_element_findings(dump))
        range_findings, fills = _declared_range_findings(dump, declared_range)
        findings.extend(range_findings)
        adjustments.extend(_filled(elements[el_id], dump, fills, set_field))

    if page.layouts:
        findings.extend(_arrangement_findings(
            page, dumps, _ReviewScope(in_scope, theme, masters),
        ))
    return findings, adjustments


@dataclass(frozen=True)
class _ReviewScope:
    """What one review was asked about: which elements, under which theme, over which masters."""

    in_scope: Callable[[str], bool]
    theme: Mapping[str, Any] | None
    masters: Sequence[Any]


@dataclass(frozen=True)
class _Arrangement:
    """One arrangement of a page as the panel resolves it.

    The Builder's twin is ``Arrangement`` in uiBuilderHelpers.ts, built by
    ``resolveArrangement``, with these methods as free functions there
    (``parentIn``, ``parentNameIn``, ``ancestorsIn``, ``seatIn``, ``isDrawn``).

    ``absolute`` is where each element is drawn, in page percentages with
    container nesting flattened; ``own`` is the box each one stores, a
    percentage of its container. Every percentage resolves against
    ``reference``, which is the whole of what portrait changes: the same
    screen, turned.
    """

    page: Any
    elements: Mapping[str, Any]
    absolute: Mapping[str, Any]
    own: Mapping[str, Any]
    hidden: frozenset[str]
    orientation: str
    is_primary: bool

    @classmethod
    def of(cls, page: Any, layout: Any, primary_id: str) -> _Arrangement:
        hidden: set[str] = set()
        for ancestor in layout_chain(page, layout.id):
            hidden |= set(getattr(ancestor, "hidden", None) or ())
        return cls(
            page=page,
            elements={el.id: el for el in page.elements},
            absolute=absolute_placements(page, layout.id),
            own=resolved_placements(page, layout.id),
            hidden=frozenset(hidden),
            orientation=str(getattr(layout, "orientation", "landscape") or "landscape"),
            is_primary=layout.id == primary_id,
        )

    @property
    def reference(self) -> tuple[float, float]:
        return reference_box(self.orientation)

    def is_drawn(self, el_id: str) -> bool:
        """Shown in this arrangement and given a box in it."""
        return el_id not in self.hidden and self.absolute.get(el_id) is not None

    def parent_of(self, el_id: str) -> str | None:
        named = getattr(self.elements.get(el_id), "parent", None)
        return named if named and named in self.elements and named != el_id else None

    def parent_box_px(self, el_id: str) -> tuple[float, float]:
        parent_id = self.parent_of(el_id)
        box = (self.absolute.get(parent_id) if parent_id else PAGE_BOX) or PAGE_BOX
        return _box_px(box, self.reference)

    def parent_name(self, el_id: str) -> str:
        parent_id = self.parent_of(el_id)
        return f"its container '{parent_id}'" if parent_id else "the page"

    def seat(self, el_id: str) -> Seat:
        """Where this element is drawn in this arrangement. It must have a box here."""
        return Seat(
            _box_px(self.absolute[el_id], self.reference),
            self.parent_box_px(el_id),
            self.parent_name(el_id),
            self.ancestors_of(el_id),
            self.orientation,
        )

    def ancestors_of(self, el_id: str) -> tuple[Container, ...]:
        """Every container above this element, innermost first.

        Only as far as the boxes actually resolve -- a container the
        arrangement never positions ends the chain rather than guessing a size
        for it.
        """
        chain: list[Container] = []
        seen = {el_id}
        cursor = self.parent_of(el_id)
        while cursor and cursor not in seen:
            seen.add(cursor)
            box = self.absolute.get(cursor)
            if box is None:
                break
            chain.append(Container(f"'{cursor}'", *_box_px(box, self.reference)))
            cursor = self.parent_of(cursor)
        return tuple(chain)


def _arrangement_findings(
    page: Any, dumps: dict[str, dict[str, Any]], scope: _ReviewScope,
) -> list[Finding]:
    """Every geometry finding, across every arrangement, each said once.

    Primary first: it is the arrangement every variant inherits from, so its
    phrasing ("...") beats the variant's ("... in the portrait arrangement").
    """
    primary_id = primary_layout(page).id
    findings: list[Finding] = []
    seen: set[tuple] = set()
    for layout in sorted(page.layouts, key=lambda lay: lay.id != primary_id):
        arrangement = _Arrangement.of(page, layout, primary_id)
        where = "" if arrangement.is_primary else f" in the '{layout.id}' arrangement"
        findings.extend(
            _with_where(finding, where)
            for finding in _unsaid(_layout_findings(arrangement, dumps, scope), seen)
        )
    return findings


def _unsaid(findings: list[Finding], said: set[tuple]) -> list[Finding]:
    """The findings whose ``key`` has not been said yet, each marked as said."""
    fresh: list[Finding] = []
    for finding in findings:
        if finding.key not in said:
            said.add(finding.key)
            fresh.append(finding)
    return fresh


def _layout_findings(
    arrangement: _Arrangement, dumps: dict[str, dict[str, Any]], scope: _ReviewScope,
) -> list[Finding]:
    """Every geometry finding for one arrangement: each box, then siblings, then masters."""
    return (
        _box_findings_in(arrangement, dumps, scope)
        + _sibling_overlaps(arrangement, dumps, scope)
        + _buried_masters_in(arrangement, dumps, scope)
    )


def _box_findings_in(
    arrangement: _Arrangement, dumps: dict[str, dict[str, Any]], scope: _ReviewScope,
) -> list[Finding]:
    findings: list[Finding] = []
    for el_id, dump in dumps.items():
        if not scope.in_scope(el_id) or el_id in arrangement.hidden:
            continue
        box = arrangement.absolute.get(el_id)
        if box is not None:
            findings.extend(_placed_box_findings(arrangement, el_id, dump, scope.theme))
        elif arrangement.is_primary:
            # A variant legitimately carries no delta for an element -- it
            # inherits one. Only the primary having no box means it has none.
            findings.append(no_box_finding(dump, arrangement.parent_name(el_id)))
    return findings


def _placed_box_findings(
    arrangement: _Arrangement,
    el_id: str,
    dump: Mapping[str, Any],
    theme: Mapping[str, Any] | None,
) -> list[Finding]:
    seat = arrangement.seat(el_id)
    findings = box_findings(dump, seat, theme)
    stored = arrangement.own.get(el_id)
    overhang = (
        overhang_finding(dump, stored, seat.parent_name, seat.parent_px)
        if stored is not None else None
    )
    return findings + ([overhang] if overhang else [])


def _sibling_overlaps(
    arrangement: _Arrangement, dumps: dict[str, dict[str, Any]], scope: _ReviewScope,
) -> list[Finding]:
    """Siblings, in the space they share.

    An element that also hangs out of its container is NOT excused here, which
    was tried and was wrong: two boxes can overlap in the middle of a
    container while one of them separately runs off the edge, and fixing the
    overflow does not touch the collision. Narrow a box from 80% to 70% and it
    sits inside its parent while still lying on the neighbour it started at.
    Reporting both is right; the volume that suppression was aimed at is what
    `overlap_findings` collapses.
    """
    types = {el_id: str(dump.get("type", "?")) for el_id, dump in dumps.items()}
    by_parent: dict[str | None, list[str]] = {}
    for el_id in dumps:
        if arrangement.is_drawn(el_id):
            by_parent.setdefault(arrangement.parent_of(el_id), []).append(el_id)
    findings: list[Finding] = []
    for parent_id, kids in by_parent.items():
        pairs = _colliding_pairs(arrangement, dumps, sorted(kids), scope.in_scope)
        findings.extend(overlap_findings(
            pairs, types, f"'{parent_id}'" if parent_id else "the page",
        ))
    return findings


def _colliding_pairs(
    arrangement: _Arrangement,
    dumps: dict[str, dict[str, Any]],
    kids: list[str],
    in_scope: Callable[[str], bool],
) -> list[tuple]:
    """Each pair of siblings whose boxes overlap, at least one of them in scope."""
    pairs = []
    for a_id, b_id in combinations(kids, 2):
        if not (in_scope(a_id) or in_scope(b_id)):
            continue
        if mutually_exclusive(dumps[a_id], dumps[b_id]):
            continue
        extent = overlap_extent(
            arrangement.absolute[a_id], arrangement.absolute[b_id], arrangement.reference,
        )
        if extent:
            pairs.append((a_id, b_id, *extent))
    return pairs


def _buried_masters_in(
    arrangement: _Arrangement, dumps: dict[str, dict[str, Any]], scope: _ReviewScope,
) -> list[Finding]:
    """...and the masters under all of them, which are nobody's sibling."""
    drawn = DrawnPage(
        page_id=str(getattr(arrangement.page, "id", "?")),
        orientation=arrangement.orientation,
        boxes={
            el_id: arrangement.absolute[el_id] for el_id in dumps if arrangement.is_drawn(el_id)
        },
        elements=dumps,
        parents={el_id: arrangement.parent_of(el_id) for el_id in dumps},
    )
    return buried_master_findings(drawn, scope.masters, scope.in_scope)


def _with_where(finding: Finding, where: str) -> Finding:
    if not where:
        return finding
    return Finding(
        finding.element_id,
        finding.kind,
        finding.message.rstrip(".") + f"{where}.",
        key=finding.key,
    )


def review_master_element(
    element: Any, theme: Mapping[str, Any] | None = None,
) -> list[Finding]:
    """The same checks a master element can be given.

    A master borrows no page's layout -- its box is a percentage of the
    viewport, keyed by orientation -- so it has no container to hang out of and
    no sibling in any layout to collide with. What is left is whether it holds
    its own contents, whether a finger can hit it, and whether its bindings are
    read.

    It CAN be collided with, by the controls on the pages it draws under, and
    that is answered on the page rather than here: the finding belongs on the
    control that moved (``buried_master_findings``), and a master reviewed on
    its own has no page in hand to look at.
    """
    dump = _mapping(element)
    findings = _element_findings(dump)
    placements = dump.get("placements")
    if not isinstance(placements, Mapping):
        return findings
    said: set[tuple] = set()
    for orientation, raw in placements.items():
        seat = _master_seat(orientation, raw)
        if seat is None:
            continue
        findings.extend(
            _with_where(finding, f" in the {orientation} arrangement")
            for finding in _unsaid(box_findings(dump, seat, theme), said)
        )
    return findings


def _master_seat(orientation: str, raw: Any) -> Seat | None:
    """A master's box for one orientation, or None when it is not a number.

    A master carries its own orientation-keyed boxes, so the key IS the way
    round this one is drawn -- the portrait box is measured against a portrait
    screen even on a project whose pages are all landscape. Its only container
    is the screen; a missing ``w`` or ``h`` is the whole of it.
    """
    reference = reference_box(orientation)
    box = _mapping(raw)
    try:
        box_px = (
            float(box.get("w") or 100.0) / 100 * reference[0],
            float(box.get("h") or 100.0) / 100 * reference[1],
        )
    except (TypeError, ValueError):
        return None
    return Seat(box_px, reference, "the page", (), orientation)
