"""Render the per-control facts into the guide a remote authoring client fetches.

Produces ``server/ui/panel_authoring_guide.md`` from ``RULES`` in
``control_minimums.py`` plus the finger rule and the binding-reach table in
``page_review.py``. Third renderer over the same measured numbers, after
``minimums_gen`` (the Builder's types) and ``review_gen`` (the binding table) --
so a floor exists once, in Python, measured against a real browser and pinned by
``tests/e2e/test_control_minimums.py``.

Why a committed markdown file rather than a copy where it is read
----------------------------------------------------------------
The consumer is the cloud's authoring assistant, which lives in another
repository and cannot import this one. It already fetches the driver and plugin
guides over ``raw.githubusercontent.com``; this artifact is fetched the same way,
so the numbers cross the repository boundary without anyone keeping a second copy
of a measured fact in sync by hand.

**That makes the path part of the contract.** The fetch is by URL, so moving or
renaming this artifact breaks it silently -- the caller gets a 404 and simply
authors without the numbers, which is the failure this file exists to prevent.
Change ``ARTIFACT`` only together with the URL that reads it.

What the guide deliberately does NOT say
---------------------------------------
A floor for a type that has none. Two thirds of the element types have no fixed
internals at all, and their limit is their content -- a caption, an image,
whatever a plugin draws -- which depends on the string and the theme and is
unbounded. Publishing a number for those would invent a limit the renderer does
not have, and every layout under it would be rejected for nothing. The guide
lists them by name as having no floor, which is a fact, and says why.

Run:  python -m server.ui.guide_gen
A test compares the committed file against a fresh render, so editing the rules
without regenerating (or hand-editing the artifact) fails CI.
"""

from __future__ import annotations

from pathlib import Path

from server.ui.control_minimums import (
    REFERENCE_HEIGHT_PX,
    REFERENCE_WIDTH_PX,
    RULES,
    minimum_box,
    minimum_percent,
)
from server.ui.page_review import (
    HONORED_SHOW_SLOTS,
    STATE_LABEL_TYPES,
    TOUCH_MIN_MM,
    TOUCH_MIN_PX,
    TOUCH_PX_PER_INCH,
    TOUCHABLE_TYPES,
)

ARTIFACT = "server/ui/panel_authoring_guide.md"

#: Reading order for the `show` slots, so the table is not alphabetical for the
#: sake of it. Anything new falls in behind these rather than disappearing.
_SLOT_ORDER = ("value", "look", "items")


def _px(value: float) -> str:
    """A pixel number without a trailing .0 on whole numbers."""
    return f"{value:.0f}" if float(value).is_integer() else f"{value:g}"


def _pct(value: float) -> str:
    """A percentage the way the write-time review prints it, so the two agree."""
    return f"{value:.2f}".rstrip("0").rstrip(".") or "0"


def _pct_pair(element: dict) -> str:
    pair = minimum_percent(element)
    assert pair is not None  # only called for types that have a rule
    return f"{_pct(pair[0])}% x {_pct(pair[1])}%"


def _box(element: dict) -> str:
    box = minimum_box(element)
    assert box is not None
    return f"{_px(box.width_px)} x {_px(box.height_px)}"


def _internal(i, mark_origin: bool = True) -> str:
    """One fixed part, named with whichever dimensions it actually holds."""
    if i.width_px and i.height_px:
        size = f"{_px(i.width_px)} x {_px(i.height_px)}"
    elif i.width_px:
        size = f"{_px(i.width_px)} wide"
    else:
        size = f"{_px(i.height_px)} tall"
    tail = " (font-driven)" if mark_origin and i.origin == "font-driven" else ""
    return f"{i.part} {size}{tail}"


def _internals(element: dict) -> str:
    box = minimum_box(element)
    assert box is not None
    return ", ".join(_internal(i) for i in box.internals)


HEADER = """\
<!-- GENERATED FILE - DO NOT EDIT.
     Rendered from server/ui/control_minimums.py and server/ui/page_review.py.
     Regenerate with:  python -m server.ui.guide_gen
     A test compares this file against a fresh render, so hand edits fail CI. -->

# Panel control minimums

An element's box is a percentage of its parent. A percentage can be arbitrarily
small; several controls cannot. A status LED's dot is %(dot)spx and does not shrink
with the box: give one 2%% of a %(ref_w)dpx page and it has %(two_pct)spx, which holds the dot
and not the %(captioned)spx a captioned LED needs, so the caption is drawn with negative
room. None of that is visible in the percentages, which is what this file is for.

Below is the smallest box each control still draws in. Every pixel number is at
the **%(ref_w)d x %(ref_h)d reference screen**, the same frame the write-time review and
the UI Builder both measure against.

## Percent and pixels

    px      = percent / 100 * parent_px
    percent = px / parent_px * 100

The parent is the page -- %(ref_w)d x %(ref_h)d -- unless the element names a `parent`
container, in which case it is **that container's box**, and this is where the
arithmetic usually goes wrong. A container %(cw)d%% x %(ch)d%% of the page is %(cw_px)d x %(ch_px)d px, so a
status LED inside it needs %(dot_in_container)s of the container to hold the same %(dot)spx dot --
not the %(dot_of_page)s it would need of the page.
"""

FIXED_INTRO = """\

## Controls with a fixed floor

`Of a full page` is the floor as a percentage of a %(ref_w)d x %(ref_h)d parent. Divide by
the container instead when the element sits in one.

| Type | Smallest box | Of a full page | What does not shrink |
|---|---|---|---|
"""

CAPTION_INTRO = """\

## A status LED's floor changes when it draws a caption

The dot is the same either way. A caption adds the gap plus a sliver of text, so
the box has to widen before any of the caption is legible -- how much more than
that is content, not a floor. A bound `show.value` counts as a caption: it
renders the same way, and an empty string today is a device name at runtime.

| status_led | Smallest box | Of a full page |
|---|---|---|
"""

SCALED_INTRO = """\

## Controls whose floor depends on a size you set

These have no single floor, because the part that does not shrink is one you can
set. The floor is a formula; the numbers in the last column are what the default
produces. Work the formula out with your own value if you set one.

| Type | Floor | Authored by | Default | Of a full page at the default |
|---|---|---|---|---|
"""

NO_FLOOR_INTRO = """\

## Types with no floor at all

%(names)s

These have **no fixed internals**: nothing inside them keeps a size when the box
shrinks. What limits them is their content -- a caption, an icon, an image,
whatever a plugin draws -- which depends on the string and on the theme's font,
is unbounded, and is therefore not a minimum box. There is no number to check
for these types, and inventing one would reject layouts that draw correctly.

That is not permission to make them tiny. It means the check is your judgement,
plus the finger rule below where the type is one you touch.
"""

FONT_DRIVEN_INTRO = """\

## Some numbers above are the theme's, not a declared size

%(rows)s

None of these has a declared floor anywhere. They fall out of the theme's font
size plus padding, so the value recorded above is what the **default theme**
produces and a theme with larger type moves it. They are in the tables anyway: a
keypad crushed under its own keys is a worse outcome than a floor that can move.
"""

NOTES_INTRO = """\

## Per-type notes

Where a floor is not what the shape of the control suggests.

"""

TOUCH_INTRO = """\

## The finger rule

A control a finger has to hit needs %(mm)s mm of it. At the reference screen's
%(ppi)d ppi that is **%(px)dpx**, or %(w_pct)s of the page's width and %(h_pct)s of its height.

Applies to: %(types)s

A fader and a slider are on that list because dragging is touch -- you grab the
handle with the same thumb. In practice neither reaches this check without
already having failed its own floor above.
"""

BINDINGS_INTRO = """\

## What each type's `show` bindings actually render

`show` accepts the same slots for every element type, and each type's renderer
reads only some of them. A slot the renderer never looks at is **silently
inert**: the element draws, the state key resolves, nothing errors, and the thing
you asked for never happens. Check this table before binding.

| Type | Renders |
|---|---|
"""

BINDINGS_TAIL = """\

`show.visible_when` is absent from the table because it is honored for every
element type, from the page tree rather than from the renderer.

`show.look` carries per-state **colour** wherever it is read. Per-state **text**
(`states[].label`) is drawn by %(state_label_types)s and by nothing else, so a
`states[].label` on any other type never appears on screen. A label that should
read ONLINE / OFFLINE needs its text in `show.value`.
"""

WRITE_TAIL = """\

## After a write

A UI write returns any of the above it finds, in pixels and in the percentage of
that element's own container to write instead. Those are warnings, not failures:
the write landed, and resolving them is part of the same job.
"""


def _fixed_rows() -> str:
    rows = []
    for name, rule in RULES.items():
        if rule.scales_with or rule.caption_width_bonus_px:
            continue
        element = {"type": name}
        rows.append(
            f"| {name} | {_box(element)} px | {_pct_pair(element)} | {_internals(element)} |"
        )
    return "\n".join(rows) + "\n"


def _caption_rows() -> str:
    rows = []
    for name, rule in RULES.items():
        if not rule.caption_width_bonus_px:
            continue
        bare = {"type": name}
        labelled = {"type": name, "label": "Zone 1"}
        rows.append(f"| no caption | {_box(bare)} px | {_pct_pair(bare)} |")
        rows.append(
            f"| caption or bound text | {_box(labelled)} px | {_pct_pair(labelled)} |"
        )
    return "\n".join(rows) + "\n"


def _scaled_rows() -> str:
    rows = []
    for name, rule in RULES.items():
        scale = rule.scales_with
        if not scale:
            continue
        width = (
            f"{_px(rule.base_width_px)} + {scale.property}"
            if scale.width_coefficient
            else _px(rule.base_width_px)
        )
        height = (
            f"{_px(rule.base_height_px)} + {scale.property}"
            if scale.height_coefficient
            else _px(rule.base_height_px)
        )
        authored = f"`{scale.property}` on the element"
        if scale.from_theme:
            authored += " or the theme"
        element = {"type": name}
        rows.append(
            f"| {name} | {width} wide, {height} tall | {authored} | "
            f"{scale.property} {_px(scale.default_px)}px, so {_box(element)} px | "
            f"{_pct_pair(element)} |"
        )
    return "\n".join(rows) + "\n"


def _font_driven_rows() -> str:
    seen: dict[str, list[str]] = {}
    for name in RULES:
        box = minimum_box({"type": name})
        assert box is not None
        for i in box.internals:
            if i.origin == "font-driven":
                seen.setdefault(_internal(i, mark_origin=False), []).append(name)
    return "\n".join(
        f"- **{part}** -- {', '.join(types)}" for part, types in seen.items()
    )


def _note_rows() -> str:
    """The notes on the rules, which say things no table column can.

    ``status_led`` is skipped because its note IS the caption section's prose --
    printing it there and here would read as the same paragraph twice.
    """
    return "\n".join(
        f"- **{name}** -- {rule.note}"
        for name, rule in RULES.items()
        if rule.note and not rule.caption_width_bonus_px
    ) + "\n"


def _binding_rows() -> str:
    slots_seen = set().union(*HONORED_SHOW_SLOTS.values())
    order = [s for s in _SLOT_ORDER if s in slots_seen] + sorted(
        slots_seen - set(_SLOT_ORDER)
    )
    rows = []
    for name in sorted(HONORED_SHOW_SLOTS):
        honored = HONORED_SHOW_SLOTS[name]
        reads = ", ".join(f"`show.{s}`" for s in order if s in honored) or "nothing"
        rows.append(f"| {name} | {reads} |")
    return "\n".join(rows) + "\n"


def _and_list(names) -> str:
    names = list(names)
    if len(names) == 1:
        return f"`{names[0]}`"
    return ", ".join(f"`{n}`" for n in names[:-1]) + f" and `{names[-1]}`"


def render() -> str:
    dot = minimum_box({"type": "status_led"})
    assert dot is not None
    # A container to do the arithmetic in front of the reader, rather than
    # asserting that the parent matters and leaving them to work it out.
    container_w_pct, container_h_pct = 40, 30
    container_w = REFERENCE_WIDTH_PX * container_w_pct / 100
    container_h = REFERENCE_HEIGHT_PX * container_h_pct / 100
    in_container = minimum_percent({"type": "status_led"}, container_w, container_h)
    of_page = minimum_percent({"type": "status_led"})
    assert in_container is not None and of_page is not None

    captioned = minimum_box({"type": "status_led", "label": "Zone 1"})
    assert captioned is not None

    header = HEADER % {
        "dot": _px(dot.width_px),
        "captioned": _px(captioned.width_px),
        "ref_w": REFERENCE_WIDTH_PX,
        "ref_h": REFERENCE_HEIGHT_PX,
        "two_pct": _px(REFERENCE_WIDTH_PX * 0.02),
        "cw": container_w_pct,
        "ch": container_h_pct,
        "cw_px": container_w,
        "ch_px": container_h,
        "dot_in_container": f"{_pct(in_container[0])}%",
        "dot_of_page": f"{_pct(of_page[0])}%",
    }

    no_floor = sorted(set(HONORED_SHOW_SLOTS) - set(RULES))

    return "".join([
        header,
        FIXED_INTRO % {"ref_w": REFERENCE_WIDTH_PX, "ref_h": REFERENCE_HEIGHT_PX},
        _fixed_rows(),
        CAPTION_INTRO,
        _caption_rows(),
        SCALED_INTRO,
        _scaled_rows(),
        NOTES_INTRO,
        _note_rows(),
        NO_FLOOR_INTRO % {"names": _and_list(no_floor)},
        FONT_DRIVEN_INTRO % {"rows": _font_driven_rows()},
        TOUCH_INTRO % {
            "mm": _px(TOUCH_MIN_MM),
            "ppi": round(TOUCH_PX_PER_INCH),
            "px": round(TOUCH_MIN_PX),
            "w_pct": f"{_pct(TOUCH_MIN_PX / REFERENCE_WIDTH_PX * 100)}%",
            "h_pct": f"{_pct(TOUCH_MIN_PX / REFERENCE_HEIGHT_PX * 100)}%",
            "types": _and_list(sorted(TOUCHABLE_TYPES)),
        },
        BINDINGS_INTRO,
        _binding_rows(),
        BINDINGS_TAIL % {"state_label_types": _and_list(sorted(STATE_LABEL_TYPES))},
        WRITE_TAIL,
    ])


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / ARTIFACT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {ARTIFACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
