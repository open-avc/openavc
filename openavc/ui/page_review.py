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
survives it, two controls on top of each other, one hanging out of its
container, one with no box at all, one too small for a finger, a ``style``
measurement bigger than the element carrying it (those are rem), a range wider
than the device it drives, a ``type`` the panel has no renderer for, and a
binding the panel does not read for that element type.

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

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from openavc.ui.control_minimums import (
    REFERENCE_HEIGHT_PX,
    REFERENCE_WIDTH_PX,
    REM_BASE_PX,
    minimum_box,
    minimum_percent,
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
    "fader": frozenset({"value"}),
    "gauge": frozenset({"value"}),
    "group": frozenset(),
    "image": frozenset(),
    "keypad": frozenset(),
    "label": frozenset({"value"}),
    "level_meter": frozenset({"value"}),
    "list": frozenset({"items", "value"}),
    "matrix": frozenset(),
    "page_nav": frozenset(),
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
#: reads ``look`` takes only colour from it -- a status LED tints its dot, a
#: select styles its options -- so a ``states[].label`` on those never appears.
STATE_LABEL_TYPES = frozenset({"button", "camera_preset"})


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
        "show_display",
    }),
    "label": frozenset({
        "display_decimals", "icon", "icon_color", "icon_position", "icon_size",
        "text",
    }),
    "level_meter": frozenset({"label", "max", "min", "orientation"}),
    "list": frozenset({"item_height", "items", "label", "list_style", "options"}),
    "matrix": frozenset({"label", "matrix_config", "matrix_style"}),
    "page_nav": frozenset({
        "icon", "icon_color", "icon_position", "icon_size", "label", "target_page",
    }),
    "plugin": frozenset({"plugin_config", "plugin_id", "plugin_type"}),
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


#: The root every chain ends at. It is the one box no remedy can grow.
PAGE_CONTAINER = Container("the page", REFERENCE_WIDTH_PX, REFERENCE_HEIGHT_PX)


def _mapping(element: Any) -> Mapping[str, Any]:
    """A dict view of an element, whether it arrived as a model or a dict."""
    if isinstance(element, Mapping):
        return element
    dump = getattr(element, "model_dump", None)
    return dump() if callable(dump) else dict(vars(element))


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


def _box_px(box: Mapping[str, float]) -> tuple[float, float]:
    return (
        box["w"] / 100 * REFERENCE_WIDTH_PX,
        box["h"] / 100 * REFERENCE_HEIGHT_PX,
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
    blocked = []
    if box_px[0] + 0.5 < box.width_px and box.width_px > parent_px[0] + 0.5:
        blocked.append(("width", box.width_px, box_px[0]))
    if box_px[1] + 0.5 < box.height_px and box.height_px > parent_px[1] + 0.5:
        blocked.append(("height", box.height_px, box_px[1]))
    return blocked


def _grown(container: Container, axis: str) -> float:
    return container.width_px if axis == "width" else container.height_px


def container_remedy(
    ancestors: tuple[Container, ...], blocked: list[tuple[str, float, float]],
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
    chain = (*ancestors, PAGE_CONTAINER)
    for index, ancestor in enumerate(chain[:-1]):
        parent = chain[index + 1]
        needs: dict[str, float] = {}
        for axis, wants, has in blocked:
            grown = _grown(ancestor, axis) * wants / has
            if grown > _grown(parent, axis) + 0.5:
                break
            needs[axis] = grown
        else:
            return ancestor, parent, needs
    return None


def _container_clause(
    ancestors: tuple[Container, ...], blocked: list[tuple[str, float, float]],
) -> str:
    """The remedy for axes no percentage of the parent can reach."""
    axes = [axis for axis, _, _ in blocked]
    remedy = container_remedy(ancestors, blocked)
    if remedy is None:
        # Nothing in the ancestry has room to grow, so the page itself is the
        # binding constraint. Said as the ceiling rather than as a percentage,
        # because there is no percentage that helps.
        limits = []
        for axis, wants, has in blocked:
            outer = _grown(ancestors[-1], axis) if ancestors else has
            page = _grown(PAGE_CONTAINER, axis)
            ceiling = has * page / outer if outer > 0 else page
            word = "widest" if axis == "width" else "tallest"
            limits.append(
                f" No placement fits: the {word} this can be drawn on a "
                f"{page:.0f}px page is {ceiling:.0f}px, and it needs {wants:.0f}px."
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
        f"{'w' if axis == 'width' else 'h'} at least "
        f"{_pct(needs[axis] / _grown(parent, axis) * 100)}%"
        for axis in axes
    )
    return (
        f" {holder.label} is {size}, too small to hold it at any {word}: "
        f"give {holder.label} {gives} of {parent.label}."
    )


def starvation_finding(
    element: Mapping[str, Any],
    box_px: tuple[float, float],
    parent_px: tuple[float, float],
    parent_name: str,
    theme: Mapping[str, Any] | None = None,
    ancestors: tuple[Container, ...] = (),
) -> Finding | None:
    """A control smaller than the parts inside it that do not shrink.

    The dominant defect class, and the one nothing could see before the
    minimums were measured: the box is a percentage and can be any size, while
    the dot, the handle, the thumb and the key grid are pixels and are not
    negotiable. Below the floor the control still draws -- it just draws with
    its contents cut off, which reads as a styling bug rather than a sizing one.

    The remedy is a percentage of the element's own parent, EXCEPT when the
    parent is itself too small to hold the floor. Then no percentage exists and
    ``ancestors`` is what turns the finding back into something actionable, by
    naming the box that has to grow instead.
    """
    box = minimum_box(element, theme)
    if box is None:
        return None
    width_px, height_px = box_px
    reasons = box.starves(width_px, height_px)
    if not reasons:
        return None

    el_id = str(element.get("id", "?"))
    el_type = str(element.get("type", "?"))
    blocked = _blocked_axes(box, box_px, parent_px)
    stuck = {axis for axis, _, _ in blocked}
    percent = minimum_percent(element, parent_px[0], parent_px[1], theme)
    fixes = []
    if percent:
        need_w, need_h = percent
        if width_px + 0.5 < box.width_px and "width" not in stuck:
            fixes.append(f"w at least {_pct(need_w)}%")
        if height_px + 0.5 < box.height_px and "height" not in stuck:
            fixes.append(f"h at least {_pct(need_h)}%")
    fix = f" Give it {' and '.join(fixes)} of {parent_name}." if fixes else ""
    fix += _container_clause(ancestors, blocked) if blocked else ""
    return Finding(
        el_id,
        "too_small_for_contents",
        f"{el_id} ({el_type}) is {width_px:.0f}x{height_px:.0f}px at the "
        f"{REFERENCE_WIDTH_PX}x{REFERENCE_HEIGHT_PX} reference, too small for what it "
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
    box_px: tuple[float, float],
    parent_px: tuple[float, float],
    parent_name: str,
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
    if minimum_box(element, theme) is not None:
        return None
    width_px, height_px = box_px
    if width_px >= MINIMUM_VISIBLE_PX and height_px >= MINIMUM_VISIBLE_PX:
        return None

    el_id = str(element.get("id", "?"))
    el_type = str(element.get("type", "?"))
    fixes = []
    if width_px < MINIMUM_VISIBLE_PX and parent_px[0] > 0:
        fixes.append(f"w at least {_pct(MINIMUM_VISIBLE_PX / parent_px[0] * 100)}%")
    if height_px < MINIMUM_VISIBLE_PX and parent_px[1] > 0:
        fixes.append(f"h at least {_pct(MINIMUM_VISIBLE_PX / parent_px[1] * 100)}%")
    fix = f" Give it {' and '.join(fixes)} of {parent_name}." if fixes else ""
    return Finding(
        el_id,
        "too_small_to_draw",
        f"{el_id} ({el_type}) is {width_px:.0f}x{height_px:.0f}px at the "
        f"{REFERENCE_WIDTH_PX}x{REFERENCE_HEIGHT_PX} reference, which is not small, it is "
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
    narrow = width_px < TOUCH_MIN_PX
    short = height_px < TOUCH_MIN_PX
    if not (narrow or short):
        return None
    axis = "width and height" if (narrow and short) else ("width" if narrow else "height")
    el_id = str(element.get("id", "?"))
    return Finding(
        el_id,
        "small_touch_target",
        f"{el_id} ({el_type}) is about {width_px:.0f}x{height_px:.0f}px, roughly "
        f"{_to_mm(width_px):.1f}x{_to_mm(height_px):.1f}mm on a 10-inch panel -- under the "
        f"{_num(TOUCH_MIN_MM)}mm comfortable touch minimum on {axis} ({TOUCH_MIN_PX:.0f}px).",
    )


def _style_measure(style: Mapping[str, Any], *names: str) -> tuple[str, float] | None:
    """The first of these fields that is set, as ``(name, value)``.

    Ordered the way the panel resolves them: a specific axis wins over the
    shorthand, so the message names the field the author would actually edit.
    """
    for name in names:
        value = style.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value > 0:
            return name, float(value)
    return None


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
    reasons: list[str] = []
    # Keyed by field, because `padding` is one number governing both axes: when
    # it breaks the box on each, it still gets one ceiling, the tighter one.
    limits: dict[str, float] = {}

    def note(reason: str, field: str, limit_px: float) -> None:
        reasons.append(reason)
        room = limit_px / REM_BASE_PX
        limits[field] = min(limits.get(field, room), room)

    font = _style_measure(style, "font_size")
    if font and height_px > 0 and font[1] * REM_BASE_PX > height_px + 0.5:
        note(
            f"font_size {_num(font[1])} draws {font[1] * REM_BASE_PX:.0f}px of text in a box "
            f"{height_px:.0f}px tall",
            font[0], height_px,
        )

    down = _style_measure(style, "padding_vertical", "padding")
    if down and height_px > 0 and 2 * down[1] * REM_BASE_PX >= height_px:
        note(
            f"{down[0]} {_num(down[1])} leaves {down[1] * REM_BASE_PX:.0f}px above and below in a "
            f"box {height_px:.0f}px tall",
            down[0], height_px / 2,
        )

    across = _style_measure(style, "padding_horizontal", "padding")
    if across and width_px > 0 and 2 * across[1] * REM_BASE_PX >= width_px:
        note(
            f"{across[0]} {_num(across[1])} leaves {across[1] * REM_BASE_PX:.0f}px each side in a "
            f"box {width_px:.0f}px wide",
            across[0], width_px / 2,
        )

    edge = _style_measure(style, "border_width")
    smallest = min(width_px, height_px)
    if edge and smallest > 0 and 2 * edge[1] * REM_BASE_PX >= smallest:
        note(
            f"border_width {_num(edge[1])} draws a {edge[1] * REM_BASE_PX:.0f}px border on a box "
            f"{width_px:.0f}x{height_px:.0f}px",
            edge[0], smallest / 2,
        )

    gap = _style_measure(style, "letter_spacing")
    if gap and width_px > 0 and gap[1] * REM_BASE_PX > width_px:
        note(
            f"letter_spacing {_num(gap[1])} puts {gap[1] * REM_BASE_PX:.0f}px between letters in a "
            f"box {width_px:.0f}px wide",
            gap[0], width_px,
        )

    if not reasons:
        return None
    el_id = str(element.get("id", "?"))
    fixes = _joined([f"{field} at most {_pct(room)}" for field, room in limits.items()])
    return Finding(
        el_id,
        "style_too_large",
        f"{el_id} ({element.get('type', '?')}) is {width_px:.0f}x{height_px:.0f}px and its style "
        f"asks for more room than that: {'; '.join(reasons)}. style measurements are rem, not "
        f"pixels -- px / {_num(REM_BASE_PX)} -- so write {fixes}.",
    )


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
    a_box: Mapping[str, float], b_box: Mapping[str, float],
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
    ox_px = ox / 100 * REFERENCE_WIDTH_PX
    oy_px = oy / 100 * REFERENCE_HEIGHT_PX
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
        counts: dict[str, int] = {}
        for a_id, b_id, *_ in remaining:
            counts[a_id] = counts.get(a_id, 0) + 1
            counts[b_id] = counts.get(b_id, 0) + 1
        owner = min(counts, key=lambda el_id: (-counts[el_id], el_id))
        mine = [p for p in remaining if owner in (p[0], p[1])]
        remaining = [p for p in remaining if owner not in (p[0], p[1])]
        partners = sorted(
            (b_id if a_id == owner else a_id, ox, oy, share)
            for a_id, b_id, ox, oy, share in mine
        )
        findings.append(_overlap_message(owner, partners, types, parent_name))
    return findings


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

_EQ = frozenset({"eq", "equals", "=="})
_NE = frozenset({"ne", "not_equals", "!="})
#: Range operators, mapped to whether the bound they set is inclusive.
_LOWER = {"gt": False, ">": False, "gte": True, ">=": True}
_UPPER = {"lt": False, "<": False, "lte": True, "<=": True}

#: What both surfaces can compare without disagreeing. A list or an object as a
#: condition value is not something either can reason about, and Python and
#: JavaScript do not even agree on whether an empty one is truthy.
_SCALARS = (str, int, float, bool)


def _same_value(left: Any, right: Any) -> bool:
    """Two authored literals, compared the way both surfaces can agree on.

    Not ``==``: Python says ``1 == True`` and JavaScript says ``"1" == 1``, and
    they disagree about which. Same type family and same value, or different.
    """
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    return left is None and right is None


def _number(value: Any) -> float | None:
    """A condition value as a number, or None when it is not one.

    A numeric string is deliberately not one. The panel compares it with
    JavaScript's coercing ``>``, which has no Python equivalent worth mirroring,
    and undecidable here only costs a warning nobody needed suppressed.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


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
        if not isinstance(value, _SCALARS) and value is not None:
            return True
        return bool(value) if op == "truthy" else not value
    if op in _LOWER or op in _UPPER:
        left, right = _number(value), _number(target)
        if left is None or right is None:
            return True
        if op in _LOWER:
            return left >= right if _LOWER[op] else left > right
        return left <= right if _UPPER[op] else left < right
    return True


def _one_way(op_a: str, val_a: Any, op_b: str, val_b: Any) -> bool:
    """Whether condition ``a`` rules out ``b``, read in one direction only."""
    if op_a in _EQ:
        # `key == val_a` pins the key to one value, so the other condition is
        # either satisfied by that value or contradicts it outright.
        return not _holds(val_a, op_b, val_b)
    if op_a == "truthy" and op_b == "falsy":
        return True
    if op_a in _LOWER and op_b in _UPPER:
        low, high = _number(val_a), _number(val_b)
        if low is None or high is None:
            return False
        closed = _LOWER[op_a] and _UPPER[op_b]
        return high < low or (high == low and not closed)
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
    satisfiable, and would suppress every overlap on the page.

    An unreadable leaf inside ``all:`` is dropped, which only widens the set: if
    the wider condition is still exclusive, so is the real one. Inside ``any:``
    the same drop would NARROW it, which could prove an exclusivity that is not
    there, so the whole block goes undecidable instead.
    """
    bindings = element.get("bindings")
    show = bindings.get("show") if isinstance(bindings, Mapping) else None
    when = show.get("visible_when") if isinstance(show, Mapping) else None
    if not isinstance(when, Mapping):
        return None

    any_of, all_of = when.get("any"), when.get("all")
    if isinstance(any_of, list):
        leaves = [_leaf(c) for c in any_of]
        if not leaves or any(leaf is None for leaf in leaves):
            return None
        return [[leaf] for leaf in leaves if leaf]
    if isinstance(all_of, list):
        kept = [leaf for c in all_of if (leaf := _leaf(c))]
        return [kept] if kept else None
    leaf = _leaf(when)
    return [[leaf]] if leaf else None


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


def property_findings(element: Mapping[str, Any]) -> list[Finding]:
    """Properties this element type's renderer never reads.

    The element-level twin of ``binding_findings``, and it fires on the same
    silence: the field is declared on ``UIElement`` for some other type (or on
    no type at all), the loader stores it, the write returns created, and the
    renderer has no line that looks at it.

    Two readings, and the message distinguishes them, because the repair is
    different. A property some OTHER type reads is usually the right idea on the
    wrong element -- ``label`` on a ``label``, which wants ``text``. A property
    NO type reads was invented, and the only useful answer is the list of what
    this type does read.

    Anything falsy is ignored. A caller that dumps a full Pydantic model hands
    over sixty keys of ``None``, and warning that an unset field is unread would
    bury the two that were actually set.
    """
    el_type = str(element.get("type", ""))
    honored = HONORED_PROPERTIES.get(el_type)
    if honored is None:  # a type this module has never heard of; say nothing
        return []

    el_id = str(element.get("id", "?"))
    findings: list[Finding] = []
    for name, value in element.items():
        if name in honored or name in STRUCTURAL_PROPERTIES:
            continue
        # An unset field is not an authoring decision. `0` and `False` are.
        if value is None or value == "" or value == [] or value == {}:
            continue
        elsewhere = sorted(
            other for other, props in HONORED_PROPERTIES.items() if name in props
        )
        if el_type == "matrix" and name in MATRIX_CONFIG_KEYS:
            # The right idea one level too high. Saying "nothing reads it" here
            # would be true of the top level and useless, because the matrix
            # does read it -- from inside matrix_config.
            why = f"'{name}' belongs inside matrix_config, not on the element."
        elif elsewhere:
            where = _joined([f"`{t}`" for t in elsewhere])
            why = f"'{name}' is read by {where}, not by a {el_type}."
            if name == "label" and "text" in honored:
                why += " A label element draws `text`."
        else:
            why = f"No element type reads '{name}'."
        findings.append(Finding(
            el_id,
            "property_not_rendered",
            f"{el_id} ({el_type}) sets '{name}', which a {el_type} does not render. "
            f"{why} A {el_type} reads: {', '.join(sorted(honored))}.",
            key=("property_not_rendered", el_id, name),
        ))
    return findings


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
}


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
    if element.get(prop):
        return []
    bindings = element.get("bindings")
    show = bindings.get("show") if isinstance(bindings, Mapping) else None
    if slot and isinstance(show, Mapping) and show.get(slot):
        return []
    el_id = str(element.get("id", "?"))
    instead = f", and no show.{slot} to supply one" if slot else ""
    return [Finding(
        el_id,
        "nothing_to_draw",
        f"{el_id} ({el_type}) has no {prop}{instead}, so it draws an empty box. "
        f"Set {prop} to {remedy}.",
    )]


#: Every key ``renderMatrix`` reads out of ``matrix_config``.
#:
#: The element declares this as a bare ``dict[str, Any]``, so unlike every other
#: property it has no schema at any layer -- the loader takes whatever shape
#: arrives and the renderer reads the keys it knows. An 8x8 switcher authored
#: with ``inputs: [...]`` / ``outputs: [{state_key: ...}]`` stores perfectly and
#: draws as a 4x4 grid of crosspoints that can never light.
MATRIX_CONFIG_KEYS = frozenset({
    "input_count", "output_count", "input_labels", "output_labels",
    "input_key_pattern", "output_key_pattern", "route_key_pattern",
    "audio_route_key_pattern", "audio_follow_video", "show_lock", "show_mute",
    "presets",
})

#: What a matrix draws when nothing says otherwise. Small enough that a real
#: switcher is nearly always bigger, which is what makes the silence expensive.
MATRIX_DEFAULT_COUNT = 4


def matrix_findings(element: Mapping[str, Any]) -> list[Finding]:
    """A matrix that will draw, and route, and never show what is routed.

    ``route_key_pattern`` is the one key with no default and no fallback:
    ``renderMatrix`` guards the whole state binding on it, so without it
    ``evaluateMatrixRoutes`` is never registered and every crosspoint keeps its
    inactive colour for the life of the panel. Clicking still routes -- the
    command carries ``$input``/``$output`` from the event, not from config -- so
    the control is half-alive, and a bench test that only asks "does it switch"
    passes.

    That is the finding worth the most here. The other two are the ones that
    make it hard to notice: a config full of keys nothing reads, and a grid
    silently drawn at 4x4.
    """
    if str(element.get("type", "")) != "matrix":
        return []
    config = element.get("matrix_config")
    el_id = str(element.get("id", "?"))
    findings: list[Finding] = []

    if not isinstance(config, Mapping) or not config:
        return [Finding(
            el_id,
            "matrix_not_configured",
            f"{el_id} (matrix) has no matrix_config, so it draws an unbound "
            f"{MATRIX_DEFAULT_COUNT}x{MATRIX_DEFAULT_COUNT} grid. Set input_count, "
            f"output_count and route_key_pattern at least.",
        )]

    unread = sorted(k for k in config if k not in MATRIX_CONFIG_KEYS)
    if unread:
        findings.append(Finding(
            el_id,
            "matrix_config_unread",
            f"{el_id} (matrix) sets matrix_config {_joined([repr(k) for k in unread])}, "
            f"which the matrix renderer does not read. The keys it reads are: "
            f"{', '.join(sorted(MATRIX_CONFIG_KEYS))}.",
            key=("matrix_config_unread", el_id, *unread),
        ))

    if not config.get("route_key_pattern"):
        findings.append(Finding(
            el_id,
            "matrix_no_route_feedback",
            f"{el_id} (matrix) has no route_key_pattern, so no crosspoint will ever "
            f"light up -- the grid draws and routes, but never shows which input is "
            f"selected. Set it to the state key of an output's routed input with the "
            f"output number replaced by '*', e.g. 'device.<id>.output.*.input'.",
        ))

    for axis in ("input", "output"):
        if not config.get(f"{axis}_count"):
            findings.append(Finding(
                el_id,
                "matrix_default_size",
                f"{el_id} (matrix) does not set {axis}_count, so it draws "
                f"{MATRIX_DEFAULT_COUNT} {axis}s. State the real count.",
                key=("matrix_default_size", el_id, axis),
            ))
    return findings


def binding_findings(element: Mapping[str, Any]) -> list[Finding]:
    """Bindings this element type's renderer never reads.

    Nothing rejects these and nothing logs them at runtime: the element draws,
    the state key resolves, and the binding simply has no code path behind it
    for this type. From the outside it is indistinguishable from a value that
    never changes.
    """
    el_type = str(element.get("type", ""))
    honored = HONORED_SHOW_SLOTS.get(el_type)
    if honored is None:  # a type this module has never heard of; say nothing
        return []
    bindings = element.get("bindings")
    show = bindings.get("show") if isinstance(bindings, Mapping) else None
    if not isinstance(show, Mapping):
        return []

    el_id = str(element.get("id", "?"))
    findings: list[Finding] = []
    reads = (
        ", ".join(f"show.{slot}" for slot in _SLOTS if slot in honored)
        or "nothing from show but show.visible_when"
    )
    for slot in _SLOTS:
        if not show.get(slot) or slot in honored:
            continue
        extra = ""
        if slot == "look" and "value" in honored:
            extra = (
                " Put whatever depended on it into show.value (a condition with "
                "text_true / text_false), or use a button."
            )
        findings.append(Finding(
            el_id,
            "binding_not_rendered",
            f"{el_id} ({el_type}) declares show.{slot}, which a {el_type} does not render "
            f"-- it reads {reads}. That binding has no effect.{extra}",
            key=("binding_not_rendered", el_id, slot),
        ))

    # A look binding can be honored for its colour while its state labels are
    # not: a status LED tints its dot, a select styles its options, and neither
    # has anywhere to put text.
    look = show.get("look")
    if (
        "look" in honored
        and el_type not in STATE_LABEL_TYPES
        and isinstance(look, Mapping)
        and isinstance(look.get("states"), Mapping)
    ):
        labelled = sorted(
            str(name) for name, spec in look["states"].items()
            if isinstance(spec, Mapping) and spec.get("label") is not None
        )
        if labelled:
            findings.append(Finding(
                el_id,
                "binding_not_rendered",
                f"{el_id} ({el_type}): show.look.states sets a label for "
                f"{', '.join(labelled)}, but a {el_type} takes only colour from show.look, "
                f"so that text never appears. Use the element's own label, or a label "
                f"element bound to the same key.",
                key=("binding_not_rendered", el_id, "look.states.label"),
            ))
    return findings


# --- Range findings and the one auto-fill ----------------------------------


def bound_state_key(element: Mapping[str, Any]) -> str | None:
    """The state key a control's value reads, when it reads one."""
    bindings = element.get("bindings")
    show = bindings.get("show") if isinstance(bindings, Mapping) else None
    value = show.get("value") if isinstance(show, Mapping) else None
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
      reported.
    * **narrower than the device** -- left alone. A volume-limited install is
      real authoring, and overriding it would undo a deliberate choice to
      protect something.
    * **wider than the device** -- warned, for the two types that send what they
      read. The control can command a value the device refuses, which shows up
      as a dead end of travel.
    """
    if not declared or str(element.get("type", "")) not in RANGE_TYPES:
        return [], []

    el_id = str(element.get("id", "?"))
    el_type = str(element.get("type", ""))
    fills: list[Adjustment] = []
    findings: list[Finding] = []

    wanted = {"min", "max"}
    if el_type in STEP_TYPES:
        wanted.add("step")
    if el_type in UNIT_TYPES:
        wanted.add("unit")

    for field in ("min", "max", "step", "unit"):
        if field not in wanted or element.get(field) is not None:
            continue
        value = declared.get(field)
        expected = str if field == "unit" else (int, float)
        if value is None or isinstance(value, bool) or not isinstance(value, expected):
            continue
        fills.append(Adjustment(
            el_id, field, value,
            f"{el_id}: {field} was not set, so it was filled in as {_num(value)} from what "
            f"the driver declares for {state_key}.",
        ))

    if el_type not in COMMANDING_TYPES:
        return fills, findings

    low, high = declared.get("min"), declared.get("max")
    el_low, el_high = element.get("min"), element.get("max")
    if isinstance(low, (int, float)) and isinstance(el_low, (int, float)) and el_low < low:
        findings.append(Finding(
            el_id, "range_wider_than_device",
            f"{el_id}: min {_num(el_low)} is below the {_num(low)} the driver declares for "
            f"{state_key}. The bottom of its travel commands a value the device refuses.",
            key=("range_wider_than_device", el_id, "min"),
        ))
    if isinstance(high, (int, float)) and isinstance(el_high, (int, float)) and el_high > high:
        findings.append(Finding(
            el_id, "range_wider_than_device",
            f"{el_id}: max {_num(el_high)} is above the {_num(high)} the driver declares for "
            f"{state_key}. The top of its travel commands a value the device refuses.",
            key=("range_wider_than_device", el_id, "max"),
        ))
    return fills, findings


# --- The whole page --------------------------------------------------------


def review_page(
    page: Any,
    *,
    touched: set[str] | None = None,
    theme: Mapping[str, Any] | None = None,
    declared_range: Callable[[str], Mapping[str, Any] | None] | None = None,
    set_field: Callable[[Any, str, Any], None] = setattr,
) -> tuple[list[Finding], list[Adjustment]]:
    """Review everything one write touched, across every arrangement.

    ``touched`` is the point of the whole signature: a write that adds five
    controls to a page of sixty answers for its five. Reporting the other
    fifty-five would bury them, and would re-report the same pre-existing
    problems on every subsequent call until the caller learned to ignore the
    field entirely. A finding is kept when it involves something this write
    touched -- for an overlap, when either side does.

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

    # Bindings and ranges are properties of the element itself, so they are
    # answered once rather than once per arrangement.
    for el_id, dump in dumps.items():
        if not in_scope(el_id):
            continue
        type_finding = element_type_finding(dump)
        if type_finding:
            findings.append(type_finding)
        findings.extend(binding_findings(dump))
        findings.extend(property_findings(dump))
        findings.extend(content_findings(dump))
        findings.extend(matrix_findings(dump))
        key = bound_state_key(dump)
        if key and declared_range:
            declared = declared_range(key)
            fills, range_findings = range_review(dump, declared, key)
            findings.extend(range_findings)
            rounding = precision_review(dump, declared, key)
            if rounding:
                findings.append(rounding)
            for fill in fills:
                set_field(elements[el_id], fill.field, fill.value)
                dump[fill.field] = fill.value
                adjustments.append(fill)

    if not page.layouts:
        return findings, adjustments

    primary_id = primary_layout(page).id
    seen: set[tuple] = set()
    # Primary first: it is the arrangement every variant inherits from, so its
    # phrasing ("...") beats the variant's ("... in the portrait arrangement").
    ordered = sorted(page.layouts, key=lambda lay: lay.id != primary_id)
    for layout in ordered:
        hidden: set[str] = set()
        for ancestor in layout_chain(page, layout.id):
            hidden |= set(getattr(ancestor, "hidden", None) or ())
        where = "" if layout.id == primary_id else f" in the '{layout.id}' arrangement"
        for finding in _layout_findings(
            page,
            dumps,
            absolute_placements(page, layout.id),
            resolved_placements(page, layout.id),
            hidden,
            in_scope,
            theme,
            is_primary=layout.id == primary_id,
        ):
            if finding.key in seen:
                continue
            seen.add(finding.key)
            findings.append(_with_where(finding, where))

    return findings, adjustments


def _layout_findings(
    page: Any,
    dumps: dict[str, dict[str, Any]],
    absolute: dict,
    own: dict,
    hidden: set[str],
    in_scope: Callable[[str], bool],
    theme: Mapping[str, Any] | None,
    is_primary: bool,
) -> list[Finding]:
    """Every geometry finding for one arrangement."""
    findings: list[Finding] = []
    elements = {el.id: el for el in page.elements}

    def parent_of(el_id: str) -> str | None:
        named = getattr(elements.get(el_id), "parent", None)
        return named if named and named in elements and named != el_id else None

    def parent_box_px(el_id: str) -> tuple[float, float]:
        parent_id = parent_of(el_id)
        box = (absolute.get(parent_id) if parent_id else PAGE_BOX) or PAGE_BOX
        return _box_px(box)

    def parent_name(el_id: str) -> str:
        parent_id = parent_of(el_id)
        return f"its container '{parent_id}'" if parent_id else "the page"

    def ancestors_of(el_id: str) -> tuple[Container, ...]:
        """Every container above this element, innermost first.

        Only reached when a floor is larger than the immediate parent, and only
        as far as the boxes actually resolve -- a container the arrangement
        never positions ends the chain rather than guessing a size for it.
        """
        chain: list[Container] = []
        seen = {el_id}
        cursor = parent_of(el_id)
        while cursor and cursor not in seen:
            seen.add(cursor)
            box = absolute.get(cursor)
            if box is None:
                break
            chain.append(Container(f"'{cursor}'", *_box_px(box)))
            cursor = parent_of(cursor)
        return tuple(chain)

    for el_id, dump in dumps.items():
        if not in_scope(el_id) or el_id in hidden:
            continue
        box = absolute.get(el_id)
        if box is None:
            # A variant legitimately carries no delta for an element -- it
            # inherits one. Only the primary having no box means it has none.
            if is_primary:
                findings.append(no_box_finding(dump, parent_name(el_id)))
            continue
        box_px = _box_px(box)
        candidates = [
            starvation_finding(
                dump, box_px, parent_box_px(el_id), parent_name(el_id), theme,
                ancestors_of(el_id),
            ),
            degenerate_finding(
                dump, box_px, parent_box_px(el_id), parent_name(el_id), theme,
            ),
            touch_finding(dump, box_px),
            style_finding(dump, box_px),
        ]
        if own.get(el_id) is not None:
            candidates.append(overhang_finding(
                dump, own[el_id], parent_name(el_id), parent_box_px(el_id),
            ))
        findings.extend(f for f in candidates if f)

    # Siblings, in the space they share.
    #
    # An element that also hangs out of its container is NOT excused here, which
    # was tried and was wrong: two boxes can overlap in the middle of a
    # container while one of them separately runs off the edge, and fixing the
    # overflow does not touch the collision. Narrow a box from 80% to 70% and it
    # sits inside its parent while still lying on the neighbour it started at.
    # Reporting both is right; the volume that suppression was aimed at is what
    # `overlap_findings` collapses.
    types = {el_id: str(dump.get("type", "?")) for el_id, dump in dumps.items()}
    by_parent: dict[str | None, list[str]] = {}
    for el_id in dumps:
        if el_id in hidden or absolute.get(el_id) is None:
            continue
        by_parent.setdefault(parent_of(el_id), []).append(el_id)
    for parent_id, kids in by_parent.items():
        kids.sort()
        pairs = []
        for i, a_id in enumerate(kids):
            for b_id in kids[i + 1:]:
                if not (in_scope(a_id) or in_scope(b_id)):
                    continue
                if mutually_exclusive(dumps[a_id], dumps[b_id]):
                    continue
                extent = overlap_extent(absolute[a_id], absolute[b_id])
                if extent:
                    pairs.append((a_id, b_id, *extent))
        findings.extend(overlap_findings(
            pairs, types, f"'{parent_id}'" if parent_id else "the page",
        ))
    return findings


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
    viewport, keyed by orientation -- so it has no siblings to collide with and
    no container to hang out of. What is left is whether it holds its own
    contents, whether a finger can hit it, and whether its bindings are read.
    """
    dump = _mapping(element)
    type_finding = element_type_finding(dump)
    findings = (
        ([type_finding] if type_finding else [])
        + binding_findings(dump)
        + property_findings(dump)
        + content_findings(dump)
        + matrix_findings(dump)
    )
    placements = dump.get("placements")
    if not isinstance(placements, Mapping):
        return findings
    seen: set[tuple] = set()
    for orientation, raw in placements.items():
        box = _mapping(raw)
        try:
            box_px = (
                float(box.get("w") or 100.0) / 100 * REFERENCE_WIDTH_PX,
                float(box.get("h") or 100.0) / 100 * REFERENCE_HEIGHT_PX,
            )
        except (TypeError, ValueError):
            continue
        for finding in (
            starvation_finding(
                dump, box_px, (REFERENCE_WIDTH_PX, REFERENCE_HEIGHT_PX), "the page", theme,
            ),
            degenerate_finding(
                dump, box_px, (REFERENCE_WIDTH_PX, REFERENCE_HEIGHT_PX), "the page", theme,
            ),
            touch_finding(dump, box_px),
            style_finding(dump, box_px),
        ):
            if finding and finding.key not in seen:
                seen.add(finding.key)
                findings.append(_with_where(finding, f" in the {orientation} arrangement"))
    return findings
