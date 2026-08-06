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
from dataclasses import dataclass
from typing import Any

# The panel's rem base: `style` measurements are px / 14 (project format 0.8.0).
REM_BASE_PX = 14.0

# The screen every percentage is reasoned about against. Matches TOUCH_REFERENCE
# in the Builder (web/programmer/src/components/ui-builder/uiBuilderHelpers.ts),
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
    note: str = ""


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
    "matrix": MinimumRule(
        277, 234,
        (FixedInternal("matrix-cell", 44, 44, "declared", "panel.js:2294 cell_size"),),
        note="Constant, NOT a function of the crosspoint count: 2x2, 3x3 and "
             "4x4 all floor here, because .matrix-scroll scrolls the grid "
             "internally once it runs out of room.",
    ),
    "level_meter": MinimumRule(13, 80, (_SEGMENT,)),
    "keypad": MinimumRule(84, 221, (_KEY,)),
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


def minimum_box(
    element: Mapping[str, Any],
    theme: Mapping[str, Any] | None = None,
) -> MinimumBox | None:
    """The smallest box this element can be drawn in, or None when unbounded.

    None means the type has no fixed internals at all -- a button, a label, an
    image. Those are limited by their text, which is not a minimum box.
    """
    rule = RULES.get(str(element.get("type")))
    if rule is None:
        return None

    width = rule.base_width_px
    height = rule.base_height_px
    internals = rule.internals

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
