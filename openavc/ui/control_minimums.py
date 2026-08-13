"""The smallest box each control can be drawn in, and what breaks below it.

A panel element's geometry is a percentage of its parent. A percentage box can
be arbitrarily small, but several controls contain parts that are a fixed
number of pixels and do not shrink -- a status LED's dot is 20px whatever the
box says. Give one 3% of a 1280px page and the dot has 38px to live in with
8px of gap; give it 2% and the caption has negative room. Nothing anywhere
recorded that, so nothing could warn about it.

This module records it once. `minimum_box` answers "how small can this get",
`fixed_internals` answers "and what is it that doesn't fit", so a warning can
name the part rather than just assert a number.

Where the numbers come from
---------------------------
Measured, not derived. Each one is the smallest box in which the named parts
still hold their size, found by binary search against a real browser rendering
the real ``panel.js`` with the real stylesheets. Arithmetic from the CSS was
tried first and is not trustworthy on its own: padding, gaps and flex
behaviour compose in ways that do not add up by hand, and three of the numbers
below live in ``panel.js`` rather than in any stylesheet.

``tests/e2e/test_control_minimums.py`` re-measures every entry and fails if a
box no longer holds, and also fails if one pixel LESS still holds -- which is
what keeps these tight rather than merely safe as the panel styling changes.
Without that second half they would rot into round numbers nobody trusts.

When two machines disagree
--------------------------
Several of these floors are text-driven: a keypad key is as wide as the glyph
on it needs, and a glyph is not the same width in every font stack. So the same
control measures differently on macOS, on Linux and on the CI runner, and there
is no single true answer to record.

**The number here is the largest of the machines it has been measured on.** A
panel is drawn on whatever the room has -- an iPad as readily as the Linux box
the browser suite happens to run on -- and one project opens on all of them, so
a floor that is only true on the machine that recorded it is not a floor. Being
two pixels generous makes the review refuse a layout that would have rendered,
and it says which control and by how much; being two pixels short draws a broken
control on the device it was short for and says nothing at all. So where they
disagree the larger wins.

What stops "largest" from sliding into "add ten and stop thinking" is
``TIGHTNESS_SLACK_PX`` in the e2e test: it tolerates the disagreement that has
actually been measured between machines and fails on anything wider. That is
also why raising one of these numbers does not need every platform re-measured
by hand -- the suite's other half will say so on whichever machine runs it.

What is deliberately NOT here
-----------------------------
Intrinsic content -- how wide a caption is, whether a button's label wraps.
That depends on the string and the theme's font, it is unbounded, and no
minimum box can express it. A control too small for its TEXT degrades (the
caption drops, the scale hides); a control too small for its FIXED INTERNALS
is simply broken. Only the second is a minimum.

Two kinds of number live here, and the difference matters when one changes:

  * declared    the size is a constant in the stylesheet or the renderer:
                the 20px dot, the 44px fader handle, the 28px scale column,
                the 2px meter segment. These move only when someone edits
                that constant.
  * font-driven the size falls out of the theme's font size plus padding:
                a keypad key, a select's native control. There is no declared
                floor for these, so the value below is what the DEFAULT theme
                produces. They are still here -- a keypad is the second-largest
                trap on the list and leaving it out would be worse -- but a
                theme with a larger font moves them, and the test is what
                catches that rather than a promise in a comment.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# The panel's rem base: `style` measurements are px / 14 (project format 0.8.0).
REM_BASE_PX = 14.0

# The screen every percentage is reasoned about against. Matches TOUCH_REFERENCE
# in the Builder (openavc/web/programmer/src/components/ui-builder/uiBuilderHelpers.ts),
# measured against real panel diagonals rather than chosen.
REFERENCE_WIDTH_PX = 1280
REFERENCE_HEIGHT_PX = 800


@dataclass(frozen=True)
class FixedInternal:
    """A part of a control that does not shrink when the box does."""

    part: str
    """The rendered class, so a warning can say which piece is being crushed."""

    width_px: float | None
    height_px: float | None
    origin: str
    """`declared` or `font-driven` -- see the module docstring."""

    source: str
    """Where the number actually lives, so the next reader can go check it."""


@dataclass(frozen=True)
class MinimumBox:
    width_px: float
    height_px: float
    internals: tuple[FixedInternal, ...]

    def starves(self, width_px: float, height_px: float) -> list[str]:
        """Why this box is too small. Empty when it fits.

        Reported per axis with the numbers in it, because "too small" on its
        own is not something an author or a model can act on.
        """
        reasons: list[str] = []
        if width_px + 0.5 < self.width_px:
            reasons.append(
                f"{width_px:.0f}px wide, needs {self.width_px:.0f}px"
                f"{_blame(self.internals, 'width')}"
            )
        if height_px + 0.5 < self.height_px:
            reasons.append(
                f"{height_px:.0f}px tall, needs {self.height_px:.0f}px"
                f"{_blame(self.internals, 'height')}"
            )
        return reasons


def _blame(internals: tuple[FixedInternal, ...], axis: str) -> str:
    parts = [
        f"{i.part} is {getattr(i, f'{axis}_px'):.0f}px"
        for i in internals
        if getattr(i, f"{axis}_px")
    ]
    return f" ({', '.join(parts)})" if parts else ""


# --- The internals, one entry per part that does not shrink -----------------

_DOT = FixedInternal("led-dot", 20, 20, "declared", "panel-elements.css .led-dot 1.4286rem")
_HANDLE = FixedInternal("fader-handle", 44, 44, "declared", "panel-elements.css .fader-handle 3.1429rem")
_SCALE = FixedInternal("fader-scale", 28, None, "declared", "panel-elements.css .fader-scale 2rem")
_SEGMENT = FixedInternal("meter-segment", None, 2, "declared", "panel-elements.css .meter-segment min-height")
_KEY = FixedInternal("keypad-key", None, 36, "font-driven", "panel-elements.css .keypad-key font-size 1.2857rem + padding")
_CONTROL = FixedInternal("native control", None, 30, "font-driven", "panel-elements.css select/input padding + inherited font")


def _has_caption(element: Mapping[str, Any]) -> bool:
    """Whether this element draws text beside its control.

    Only ``label`` does. The single type with a caption bonus is ``status_led``,
    and ``panel.js`` builds its ``.led-label`` under ``if (element.label)`` and
    from nothing else -- which is the same thing ``HONORED_SHOW_SLOTS`` already
    records (a status LED renders ``show.look``, not ``show.value``) and what
    Phase 7 confirmed by execution in a real browser.

    This used to count a bound ``show.value`` too, on the reasoning that the
    caption "is rendered either way". It is not: the review would widen the
    floor by 9px to hold text the renderer never draws, while separately warning
    that the very same binding is inert. It also honoured ``show.text``, which
    is not a slot in the binding model at all (``value``, ``look``, ``items``,
    ``visible_when``) -- a guard on a field the schema cannot express.
    """
    return bool((element.get("label") or "").strip())


@dataclass(frozen=True)
class RepeatedInternal:
    """A part drawn once per item the element's config asks for.

    The matrix is the type this exists for: a crosspoint grid is
    ``input_count`` columns by ``output_count`` rows of a cell that does not
    shrink, so its floor is a line rather than a point. Measured slope is
    exactly ``size_px + gap_px`` on both axes across 2, 3, 4, 5, 6, 8, 12 and
    16, and it stays exact when the cell is authored to another size.
    """

    part: str
    size_px: float
    gap_px: float
    """The gap after each item. Slope is size + gap, not size."""

    count_key: str
    count_in: str
    """The element property holding the count map (``matrix_config``)."""

    default_count: int
    axis: str
    """``width`` or ``height``."""

    size_property: str = ""
    """A key in ``element.style`` (in rem) that overrides ``size_px``."""

    origin: str = "declared"
    source: str = ""


#: Every condition a ConditionalPart may name. A closed set on purpose: each one
#: is mirrored in TypeScript (``partIsPresent`` in uiBuilderHelpers.ts), so it is
#: four cases in two languages rather than an expression language in either.
CONDITIONS = ("label", "presets", "lock_column", "mute_column")


@dataclass(frozen=True)
class ConditionalPart:
    """Chrome that is there or not, depending on how the element is configured.

    Not an internal being crushed -- a part that either takes room or does not,
    so it moves the floor without ever being what a starvation warning blames.
    The lock column is the one that matters most: it is on by default and costs
    a whole cell's width, so a floor that ignored it overstated every matrix
    that turns it off by 45px.
    """

    part: str
    axis: str
    size_px: float
    when: str
    """One of ``CONDITIONS``."""

    origin: str = "declared"
    source: str = ""


@dataclass(frozen=True)
class ScalingInternal:
    """An internal whose size is authored rather than fixed.

    Three of these exist. Each defaults to 44px and is overridable per element
    (and, for the thumb, per theme), so the floor is a function rather than a
    number. The coefficients are measured: slope was exactly 1.0 on both axes
    for the slider and on height only for the list, across 44 / 66 / 88.
    """

    part: str
    property: str
    default_px: float
    width_coefficient: float
    height_coefficient: float
    source: str
    from_theme: bool = False


@dataclass(frozen=True)
class MinimumRule:
    """How one type's floor is computed. The single source both sides read."""

    base_width_px: float
    base_height_px: float
    internals: tuple[FixedInternal, ...] = ()
    scales_with: ScalingInternal | None = None
    caption_width_bonus_px: float = 0.0
    repeated: tuple[RepeatedInternal, ...] = ()
    conditionals: tuple[ConditionalPart, ...] = ()
    style_property: str = ""
    """An element property that picks a different rule -- ``matrix_style``.

    The rule carrying this IS the default style, so a matrix that says nothing
    gets the crosspoint numbers, exactly as ``renderMatrix`` does. ``styles``
    holds only the alternatives.
    """

    styles: Mapping[str, MinimumRule] = field(default_factory=dict)
    style_default: str = ""
    """What the style this rule IS is called, for anything that has to name it."""

    note: str = ""


# --- The matrix, whose floor is the one that is a line rather than a point ---
#
# It used to be recorded as a constant 278x236 for every matrix ever authored,
# with a note saying the constant was deliberate: .matrix-scroll scrolls, so a
# 16x16 in a 278px box "held" in the sense that nothing was crushed -- it just
# showed a corner of itself. That reading made the floor useless for the thing
# a floor is for. An 8x8 needs 432x429 and a 16x16 needs 792x789, and the
# editor silently accepted either in a quarter of the room.
#
# Both styles measure exactly linear in their counts (2, 3, 4, 5, 6, 8, 12, 16
# each, plus the off-diagonal 8x2, 2x8 and 16x4 to prove the axes are
# independent), and stay exact when the cell is authored to another size.
#
# What the floor does NOT hold, on purpose: the destination names. They are a
# flex column that ellipsises to its padding, so at the floor a crosspoint grid
# is numbered columns and nameless rows. Text is content -- unbounded and
# theme-dependent -- and this module has never claimed to size it. What the
# floor DOES hold is every crosspoint, visible without scrolling, which is what
# nothing anywhere could say before.
_MATRIX_LIST = MinimumRule(
    148, 9,
    (FixedInternal("matrix-list-row", None, 28, "font-driven",
                   "panel-elements.css .matrix-list-select padding + inherited font"),),
    repeated=(
        RepeatedInternal(
            "matrix-list-row", 28, 6, "output_count", "matrix_config", 4, "height",
            origin="font-driven",
            source="panel-elements.css .matrix-list gap 0.4286rem + row height",
        ),
    ),
    conditionals=(
        ConditionalPart("matrix-label", "height", 23, "label", "font-driven",
                        "panel-elements.css .panel-matrix gap + .matrix-label line box"),
        ConditionalPart("matrix-presets", "height", 36, "presets", "font-driven",
                        "panel-elements.css .matrix-presets padding + .matrix-preset-btn"),
        ConditionalPart("matrix-lock-btn", "width", 32, "lock_column", "font-driven",
                        "panel-elements.css .matrix-lock-btn + .matrix-list-row gap"),
        ConditionalPart("matrix-mute-btn", "width", 28, "mute_column", "font-driven",
                        "panel-elements.css .matrix-mute-btn + .matrix-list-row gap"),
    ),
    note="A list matrix is one dropdown per destination, so its width does not "
         "move with the input count at all -- sixteen sources are sixteen "
         "options, not sixteen columns. Recording the crosspoint floor for both "
         "styles is what the old constant did, and it told a 16-input list it "
         "needed 792px when it needs 180. The lock and mute buttons differ in "
         "width here because they are glyphs rather than grid tracks, and an "
         "unlock glyph is wider than an M.",
)

_MATRIX_CROSSPOINT = MinimumRule(
    # 63 of that height is the element's padding, the grid gaps and the 25px
    # column-number row (.matrix-input-header min-height 1.7857rem). That row
    # is declared rather than left to its line box because .matrix-header sets
    # `overflow: hidden`, which makes a grid track's min-content ZERO: without
    # it the row compressed to 8px of padding and cut the column numbers in
    # half at exactly the size this file calls the minimum.
    27, 63,
    (FixedInternal("matrix-cell", 44, 44, "declared", "panel.js MATRIX_CELL_MIN_PX"),),
    repeated=(
        RepeatedInternal(
            "matrix-cell", 44, 1, "input_count", "matrix_config", 4, "width",
            size_property="cell_size",
            source="panel.js MATRIX_CELL_MIN_PX + .matrix-grid gap 1px",
        ),
        RepeatedInternal(
            "matrix-cell", 44, 1, "output_count", "matrix_config", 4, "height",
            size_property="cell_size",
            source="panel.js MATRIX_CELL_MIN_PX + .matrix-grid gap 1px",
        ),
    ),
    conditionals=(
        ConditionalPart("matrix-label", "height", 23, "label", "font-driven",
                        "panel-elements.css .panel-matrix gap + .matrix-label line box"),
        ConditionalPart("matrix-presets", "height", 36, "presets", "font-driven",
                        "panel-elements.css .matrix-presets padding + .matrix-preset-btn"),
        ConditionalPart("lock column", "width", 45, "lock_column", "declared",
                        "panel.js renderMatrix extraColDefs + .matrix-grid gap"),
        ConditionalPart("mute column", "width", 45, "mute_column", "declared",
                        "panel.js renderMatrix extraColDefs + .matrix-grid gap"),
    ),
    style_property="matrix_style",
    styles={"list": _MATRIX_LIST},
    style_default="crosspoint",
    note="A function of the counts, which is the whole point of it: 27 + "
         "inputs x (cell + 1) wide, 46 + outputs x (cell + 1) tall, plus the "
         "lock and mute columns and the element's own label row. The cell is "
         "44 -- the touch floor it will not go below, whatever room it is "
         "given -- unless style.cell_size authors another size, in which case "
         "the slope moves with it and stays exact. The source legend is one "
         "strip that scrolls sideways rather than a block that wraps, so it "
         "costs one row rather than however many rows the source names take; "
         "the same is true of the preset bar.",
)

# The whole table. Everything below is interpretation of these rows, and the
# generated TypeScript is a serialisation of them, so the two surfaces cannot
# hold different numbers.
RULES: dict[str, MinimumRule] = {
    "status_led": MinimumRule(
        20, 20, (_DOT,),
        caption_width_bonus_px=9,
        note="Dot is 20 and never shrinks. A caption adds an 8px gap plus a "
             "sliver of text, so a labelled LED needs 29 before any of the "
             "caption is legible; how much more is content, not a minimum.",
    ),
    "fader": MinimumRule(72, 100, (_HANDLE, _SCALE)),
    "slider": MinimumRule(
        24, 37,
        scales_with=ScalingInternal(
            "slider thumb", "thumb_size", 44.0, 1.0, 1.0,
            "panel.js:1696 / --thumb-size (a ::-webkit-slider-thumb pseudo-element)",
            from_theme=True,
        ),
    ),
    "list": MinimumRule(
        28, 33,
        scales_with=ScalingInternal(
            "list-item", "item_height", 44.0, 0.0, 1.0, "panel.js:2099 item_height",
        ),
        note="Row height does not change how wide a list has to be.",
    ),
    "matrix": _MATRIX_CROSSPOINT,
    "level_meter": MinimumRule(13, 80, (_SEGMENT,)),
    "keypad": MinimumRule(
        86, 221, (_KEY,),
        note="86 wide rather than the 84 first recorded. The enter key's glyph "
             "is wider than a digit, so the grid's three equal columns stop "
             "being equal -- that column takes the room it needs and the two "
             "digit columns divide what is left, which is what actually gets "
             "crushed. How much it needs depends on the font, so this is the "
             "widest of the machines measured: 84 is right where that glyph is "
             "narrow and two pixels short where it is not. A keypad can never "
             "floor below 84 on any machine, because that is where three equal "
             "columns reach 20px.",
    ),
    "select": MinimumRule(44, 51, (_CONTROL,)),
    "text_input": MinimumRule(44, 51, (_CONTROL,)),
}


def _scaled_px(
    scale: ScalingInternal,
    element: Mapping[str, Any],
    theme: Mapping[str, Any] | None,
) -> float:
    """Resolve an authored internal: element wins, then theme, then the default."""
    value = element.get(scale.property)
    if value is None and scale.from_theme and theme:
        value = theme.get(scale.property)
    return float(value) * REM_BASE_PX if value is not None else scale.default_px


def _config(element: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = element.get(name)
    return value if isinstance(value, Mapping) else {}


def _style_px(element: Mapping[str, Any], key: str) -> float | None:
    """An authored measurement out of ``element.style``, in px.

    Style measurements are rem (project format 0.8.0), so this is the one place
    that multiplies back up.
    """
    if not key:
        return None
    value = _config(element, "style").get(key)
    return float(value) * REM_BASE_PX if isinstance(value, int | float) else None


def part_is_present(when: str, element: Mapping[str, Any]) -> bool:
    """Whether a ConditionalPart is drawn. Mirrored in uiBuilderHelpers.ts.

    Each case is ``renderMatrix``'s own test, written the same way round: the
    lock column is on unless turned off, and the mute column additionally needs
    a mute_route binding, because without one the button sends a command the
    engine has no action for and ``renderMatrix`` declines to draw it.
    """
    config = _config(element, "matrix_config")
    if when == "label":
        return _has_caption(element)
    if when == "presets":
        return bool(config.get("presets"))
    if when == "lock_column":
        return config.get("show_lock") is not False
    if when == "mute_column":
        return (
            config.get("show_mute") is not False
            and bool(_config(_config(element, "bindings"), "do").get("mute_route"))
        )
    return False


def _count(repeated: RepeatedInternal, element: Mapping[str, Any]) -> int:
    value = _config(element, repeated.count_in).get(repeated.count_key)
    try:
        count = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return repeated.default_count
    return count if count > 0 else repeated.default_count


def rule_for(element: Mapping[str, Any]) -> MinimumRule | None:
    """The rule this element is measured against, style variant resolved."""
    rule = RULES.get(str(element.get("type")))
    if rule is None or not rule.style_property:
        return rule
    style = str(element.get(rule.style_property) or "")
    return rule.styles.get(style, rule)


def minimum_box(
    element: Mapping[str, Any],
    theme: Mapping[str, Any] | None = None,
) -> MinimumBox | None:
    """The smallest box this element can be drawn in, or None when unbounded.

    None means the type has no fixed internals at all -- a button, a label, an
    image. Those are limited by their text, which is not a minimum box.
    """
    rule = rule_for(element)
    if rule is None:
        return None

    width = rule.base_width_px
    height = rule.base_height_px
    internals = rule.internals

    if rule.repeated:
        # The resolved cell replaces the declared one, so a warning blames the
        # size this matrix actually draws rather than the default it does not.
        # A crosspoint declares the same part on both axes, so they are merged
        # into one internal rather than reported twice with a hole in each.
        resolved: dict[str, dict[str, Any]] = {}
        for repeated in rule.repeated:
            size = _style_px(element, repeated.size_property) or repeated.size_px
            span = _count(repeated, element) * (size + repeated.gap_px)
            if repeated.axis == "width":
                width += span
            else:
                height += span
            part = resolved.setdefault(
                repeated.part,
                {"width": None, "height": None,
                 "origin": repeated.origin, "source": repeated.source},
            )
            part[repeated.axis] = size
        internals = tuple(i for i in internals if i.part not in resolved) + tuple(
            FixedInternal(name, p["width"], p["height"], p["origin"], p["source"])
            for name, p in resolved.items()
        )

    for part in rule.conditionals:
        if not part_is_present(part.when, element):
            continue
        if part.axis == "width":
            width += part.size_px
        else:
            height += part.size_px

    if rule.scales_with:
        size = _scaled_px(rule.scales_with, element, theme)
        width += rule.scales_with.width_coefficient * size
        height += rule.scales_with.height_coefficient * size
        internals = internals + (
            FixedInternal(
                rule.scales_with.part,
                size if rule.scales_with.width_coefficient else None,
                size if rule.scales_with.height_coefficient else None,
                "declared",
                rule.scales_with.source,
            ),
        )

    if rule.caption_width_bonus_px and _has_caption(element):
        width += rule.caption_width_bonus_px

    return MinimumBox(width, height, internals)


def minimum_percent(
    element: Mapping[str, Any],
    parent_width_px: float = REFERENCE_WIDTH_PX,
    parent_height_px: float = REFERENCE_HEIGHT_PX,
    theme: Mapping[str, Any] | None = None,
) -> tuple[float, float] | None:
    """The same floor as a percentage of the box it sits in.

    This is the form the authoring surfaces need: geometry is percentages, so
    a minimum in pixels only becomes actionable once it is divided by whatever
    the element's parent actually is.
    """
    box = minimum_box(element, theme)
    if box is None:
        return None
    return (
        box.width_px / parent_width_px * 100.0,
        box.height_px / parent_height_px * 100.0,
    )


# Every type this module has an opinion about, for the surfaces that enumerate.
TYPES_WITH_MINIMUMS = tuple(RULES)
