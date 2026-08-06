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
control smaller than its own fixed internals, two controls on top of each other,
one hanging out of its container, one with no box at all, one too small for a
finger, a range wider than the device it drives, and a binding the panel does
not read for that element type.

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

from server.ui.control_minimums import (
    REFERENCE_HEIGHT_PX,
    REFERENCE_WIDTH_PX,
    minimum_box,
    minimum_percent,
)
from server.ui.page_geometry import (
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
# element type, and each render function in web/panel/panel.js registers a
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

#: Types whose ``look`` binding renders per-state TEXT. Everything else that
#: reads ``look`` takes only colour from it -- a status LED tints its dot, a
#: select styles its options -- so a ``states[].label`` on those never appears.
STATE_LABEL_TYPES = frozenset({"button", "camera_preset"})

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
    if x < -0.0001:
        sides.append(f"{-x / 100 * parent_px[0]:.0f}px past the left (x {_pct(x)}%)")
    if y < -0.0001:
        sides.append(f"{-y / 100 * parent_px[1]:.0f}px past the top (y {_pct(y)}%)")
    if x + w > 100.0001:
        sides.append(
            f"{(x + w - 100) / 100 * parent_px[0]:.0f}px past the right "
            f"(x {_pct(x)}% + w {_pct(w)}% = {_pct(x + w)}%)"
        )
    if y + h > 100.0001:
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


def overlap_finding(
    a_id: str, a_type: str, a_box: Mapping[str, float],
    b_id: str, b_type: str, b_box: Mapping[str, float],
    parent_name: str,
) -> Finding | None:
    """Two controls in the same space sitting on top of each other.

    Checked between siblings only. Boxes under different containers can
    legitimately overlap (a group laid over another is a design), and a
    container always contains its own children -- neither is a defect, and
    flagging them would bury the case that is.
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
    return Finding(
        a_id,
        "overlap",
        f"{a_id} ({a_type}) and {b_id} ({b_type}) overlap by {ox_px:.0f}x{oy_px:.0f}px "
        f"({share:.0f}% of the smaller one) inside {parent_name}.",
        key=("overlap", a_id, b_id),
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
        findings.extend(binding_findings(dump))
        key = bound_state_key(dump)
        if key and declared_range:
            fills, range_findings = range_review(dump, declared_range(key), key)
            findings.extend(range_findings)
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
            touch_finding(dump, box_px),
        ]
        if own.get(el_id) is not None:
            candidates.append(overhang_finding(
                dump, own[el_id], parent_name(el_id), parent_box_px(el_id),
            ))
        findings.extend(f for f in candidates if f)

    # Siblings, in the space they share.
    by_parent: dict[str | None, list[str]] = {}
    for el_id in dumps:
        if el_id in hidden or absolute.get(el_id) is None:
            continue
        by_parent.setdefault(parent_of(el_id), []).append(el_id)
    for parent_id, kids in by_parent.items():
        kids.sort()
        for i, a_id in enumerate(kids):
            for b_id in kids[i + 1:]:
                if not (in_scope(a_id) or in_scope(b_id)):
                    continue
                finding = overlap_finding(
                    a_id, str(dumps[a_id].get("type", "?")), absolute[a_id],
                    b_id, str(dumps[b_id].get("type", "?")), absolute[b_id],
                    f"'{parent_id}'" if parent_id else "the page",
                )
                if finding:
                    findings.append(finding)
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
    findings = binding_findings(dump)
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
            touch_finding(dump, box_px),
        ):
            if finding and finding.key not in seen:
                seen.add(finding.key)
                findings.append(_with_where(finding, f" in the {orientation} arrangement"))
    return findings
