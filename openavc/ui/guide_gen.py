"""Render the per-control facts into the guide a remote authoring client fetches.

Produces ``openavc/ui/panel_authoring_guide.md`` from ``RULES`` in
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

Run:  python -m openavc.ui.guide_gen
A test compares the committed file against a fresh render, so editing the rules
without regenerating (or hand-editing the artifact) fails CI.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openavc.ui.control_minimums import (
    REFERENCE_HEIGHT_PX,
    REFERENCE_WIDTH_PX,
    REM_BASE_PX,
    RULES,
    minimum_box,
    minimum_percent,
)
from openavc.ui.page_review import (
    HONORED_PROPERTIES,
    buried_master_findings,
    custom_page_findings,
    HONORED_SHOW_SLOTS,
    INERT_WITHOUT,
    MATRIX_CONFIG_KEYS,
    RENDERED_TYPES,
    STATE_LABEL_TYPES,
    STRUCTURAL_PROPERTIES,
    TOUCH_MIN_MM,
    TOUCH_MIN_PX,
    TOUCH_PX_PER_INCH,
    TOUCHABLE_TYPES,
)

ARTIFACT = "openavc/ui/panel_authoring_guide.md"

#: Reading order for the `show` slots, so the table is not alphabetical for the
#: sake of it. Anything new falls in behind these rather than disappearing.
_SLOT_ORDER = ("value", "look", "items")


def _px(value: float) -> str:
    """A pixel number without a trailing .0 on whole numbers."""
    return f"{value:.0f}" if float(value).is_integer() else f"{value:g}"


def _rem(px: float) -> str:
    """A pixel size as the rem the author actually writes.

    The trap this exists for: the guide used to quote these defaults in pixels
    only, so copying `44` produced 44 rem -- a 616px floor and a list row told to
    take 81% of the page.
    """
    return f"{px / REM_BASE_PX:.2f}"


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
     Rendered from openavc/ui/control_minimums.py and openavc/ui/page_review.py.
     Regenerate with:  python -m openavc.ui.guide_gen
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

TYPES_INTRO = """\

## The element types

%(names)s

That is the whole set. `type` is a free-form string in the file, so anything else
is accepted by the loader, saved, given a placement -- and then dropped by the
renderer, which has no case for it and draws nothing. There is no error and no
gap on screen where it would have been, so this is worth checking before
inventing a name: there is no `knob`, no `toggle`, no `meter`.

`plugin` is the one that gets missed. It draws an element an installed plugin
defines, and needs `plugin_id` naming the plugin plus `plugin_type` naming the
element that plugin declares -- both of them, or it draws its unconfigured
placeholder. Do not guess either value.
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
that is content, not a floor. Only `label` draws it: a status LED renders
`show.look` and nothing else, so binding `show.value` neither puts text on
screen nor widens this floor.

| status_led | Smallest box | Of a full page |
|---|---|---|
"""

SCALED_INTRO = """\

## Controls whose floor depends on a size you set

These have no single floor, because the part that does not shrink is one you can
set. The floor is a formula; the numbers in the last column are what the default
produces. Work the formula out with your own value if you set one.

**The size you write is in `rem`, not pixels** -- px / %(rem)s, like every other
measurement on an element. The Default column gives both forms: copy the **rem**
number. Writing the pixel number instead means that many rem, which is %(rem)s
times too big and produces a floor larger than the page. The formulas are in
pixels because that is what the control is measured in, so multiply your
authored value by %(rem)s before working one out.

| Type | Floor | Authored by | Default | Of a full page at the default |
|---|---|---|---|---|
"""

COUNTED_INTRO = """\

## The matrix, whose floor is a function of the grid you asked for

A matrix has no single floor. A crosspoint grid is one column per `sources` entry
by one row per `destinations` entry, of a cell that does not shrink below the
finger rule, so its floor is a line rather than a point -- and the three
`matrix_style` values are not the same line. A list matrix is one dropdown per
destination, so its width does not move with the source count at all. A tiles
matrix is one card per destination and no sources on the wall at all, laid out
`ceil(destinations / rows)` across by `rows = floor(sqrt(destinations))` down.

| Type and `matrix_style` | Floor |
|---|---|
%(formulas)s

`cell` is %(cell)spx unless `style.cell_size` authors another size, in which case the
slope moves with it. **That value is in rem** -- px / %(rem)s, like every other style
measurement -- and the formulas are in pixels, so multiply before using one.

Then add, for each of these the matrix actually has:

| Part | Adds |
|---|---|
%(conditionals)s

Worked, for a matrix with a label:

| Grid | %(style_names)s |
%(example_rule)s
%(examples)s

The tiles column ignores the source count in those rows, because a tile wall has
no source axis: `4x4`, `8x8` and `16x16` are four, eight and sixteen destinations.

What this holds is every crosspoint (or every tile), drawn at the finger rule and
visible without scrolling, plus enough room to read the destination names and the
column numbers.

What it does **not** hold is the *whole* of a name. The destination column keeps a
declared 80px and grows to the longest name when there is room; past that the name
ellipsises. The source legend is the same bargain turned sideways: one row tall
whatever the sources are called, scrolling if there are more than fit. A tile's two
names ellipsise inside it. A floor that held any name anyone typed would be a floor
whose value is whatever they typed, and nothing in this file sizes text.

%(notes)s
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

PROPERTIES_INTRO = """\

## What each type reads off the element

Every property below is settable on every element type -- the file format
declares them flat and optional, and the loader keeps whatever it is given. Each
renderer then reads a handful. A property this table does not list for a type is
stored, round-trips through a read perfectly, and **does nothing**: no error, no
log, nothing on screen.

Two of these are worth knowing before you author anything.

**`label` is drawn by nearly every type, and not by `label`.** A `label` element
draws its `text`. Setting `label` on one is the commonest way to get a blank
element, and it looks correct in every read-back.

**`show.value` overrides `text`** when both are set, so a label that reflects
state does not need a static string as well.

Icons are shared: `icon`, `icon_position`, `icon_size` and `icon_color` are read
for the types listed below, and each one is taken from `style` first and the
element second.

| Type | Reads |
|---|---|
"""

PROPERTIES_TAIL = """\

Fields common to every element are not in the table because no renderer owns
them: %(structural)s.

`hidden` is the one to read twice. On a page element it is **per-layout**
(`layouts[].hidden`), and setting it on the element does nothing. A master
element belongs to no layout, so there it is an element property and works.

Four types draw an empty box when one particular thing is missing, and a write
warns about each: %(inert)s.
"""

MATRIX_INTRO = """\

## The matrix, which is configured entirely inside `matrix_config`

A matrix is the one control whose settings do not live on the element. They live
in `matrix_config`, which is a free-form object at every layer -- no schema, no
defaults published anywhere else -- so an invented key is stored and ignored in
exactly the same way a correct one is stored and used.

**A matrix is two lists**: the `sources` you can pick from, and the
`destinations` you can send them to. Everything else is a way of drawing them.

| Key | What it does |
|---|---|
| `sources` | What can be routed. A list of entries, or a generator standing for one. **No default** -- a matrix that omits it draws nothing to route from. |
| `destinations` | What can be routed to. Same two forms, same absence of a default. |
| `audio_follow_video` | Send the audio route alongside the video one. Needs a `do.audio_route` binding. |
| `show_lock` | Per-destination lock buttons. **Defaults off.** Give each destination a `lock_key` under `var.` and the lock is a variable every panel reads; without one it is this panel's own memory and is forgotten when the page redraws. |
| `show_mute` | Per-destination mute buttons. Drawn only when there is also a `do.mute_route` binding. |
| `presets` | `[{name, macro}]`. A preset bar above the grid; each button runs its macro. |

An entry on either axis carries a `value` -- whatever the device reports and
accepts, which need not be a number -- and a `label`. A **destination** also
carries its own `route_key`, and this is the field worth a paragraph, because
getting it wrong produces a control that looks finished:

    "destinations": [
      {"value": 1, "label": "Main LCD", "route_key": "device.mx.output.1.input"},
      {"value": 2, "label": "Confidence", "route_key": "device.mx.output.2.input"}
    ]

The panel reads each destination's own key to decide which crosspoint in that row
is lit. A destination without one still routes -- the command carries
`$input`/`$output` from the touch, not from config -- and **never changes colour**
for the life of the panel.

Because the key is per destination rather than one pattern for all of them, one
matrix can span several devices, skip the ports nobody patched, use string ids,
and cover one **routing plane** of a device that has several (the plane is part
of the key, so a decoder routing video and USB independently is two elements).
Optional per entry: `label_key` for a live name from state, `audio_route_key` on
a destination for the audio route (whose source is then named beside the video
one wherever the two differ), `lock_key` on a destination for the variable
backing its lock, and `route` on a destination for an action list that overrides
`do.route` for that row alone.

A **source** may also carry a `report_value`. `value` is what gets SENT and
`report_value` is what gets MATCHED, and they are the same thing on almost every
device, which is why one value is normally enough. Where they differ they must be
said separately or the source can never light: a mixer input selected by sending
`"0"` may report back `"Mic"`, and `"0"` on its own is what every renderer reads
as "nothing is routed".

A destination whose reported source matches no entry does not draw as an unrouted
one. It says what the device reported, because "routed to something not on this
list" and "routed to nothing" are different facts about the room -- the first
usually means a port was left out of the list or patched at the rack since.

Writing every entry out is tedious for a frame whose ports run 1..N, so an axis
may instead be a **generator**: `from` holds a `count` (or explicit `values`),
plus `labels`, `label_key`, `route_key`, `audio_route_key` and `lock_key` as
patterns where `*` becomes the entry's value. `exclude` drops entries and `overrides` edits them
by value.

    "destinations": {
      "from": {"count": 8, "route_key": "device.mx.output.*.input"},
      "exclude": [7, 8],
      "overrides": {"1": {"label": "Main LCD"}}
    }

In `crosspoint` style the columns are **numbered** and the source names are read
out in a legend under the grid, so a long source name costs nothing; a
destination name is a row caption and ellipsises to fit its column. In `tiles`
style there is one card per destination naming what is routed to it in large
type, and the sources are not on the wall at all -- a tap opens them as a chooser
over the panel, so a tile wall's floor does not move with the source count.

Routing itself is a `do` binding, not config: `do.route` with `$input` and
`$output` (which carry the source's and destination's own `value`), plus
`do.audio_route`, `do.mute_route` and `do.audio_mute_route` (`$output`, `$mute`)
if the device supports them.
"""

COMPARISON_INTRO = """\

## How a bound value is compared

Three rules, and they are not the same one. All three coerce to string first, so
a boolean state matches a `"true"` map key and an integer `1` matches `"1"` --
but only two of them ignore case.

| Where | Rule |
|---|---|
| A button's `toggle_value` against `toggle_key` | string, **case-insensitive** |
| `show.value` with a `condition.equals` | string, **case-insensitive** |
| `show.look.map` keys (a status LED's colours) | string, **case-sensitive** |
| A `select` option's `value` against `show.value` | string, exact |

So `{"true": "#4CAF50"}` lights an LED bound to a boolean, and `{"True": ...}`
does not -- while a toggle would have matched both. Write map keys in the
device's own casing, and prefer lowercase `"true"` / `"false"` for booleans.
"""

CUSTOM_PAGE_INTRO = """\

## A page that draws its own markup

A page can carry `render_mode: "custom"` and a `custom_file` naming a page in the
project's `ui/` folder. The panel hands that page the whole screen in one
sandboxed frame and **draws none of the page's own elements**, so a page that
looks empty in the file may be the busiest screen in the project. Master
elements still draw over the frame, so a nav bar that appears on every page is
on this one too.

Two things follow for a write:

- **Adding controls to such a page does nothing visible.** They are saved, they
  are positioned, and they are not drawn. The review answers: *%(kept)s*
- **A page set to custom that names no file still draws its controls**, which is
  the one case where adding one is not wasted. The review answers: *%(fileless)s*

`render_mode`, `custom_file`, `custom_config` and `grant` are yours to set, on a
page you add and on one that already exists. What you cannot do is put the file
there: the `ui/` folder is written by the person building the panel, so point a
page only at a file that is already in it. Naming one that is not there draws an
empty screen, and the review says so.

Leave `render_mode` alone unless you are asked to change it. A page switched to
custom stops drawing every control on it, which is rarely what somebody asking
for a change to one control meant.
"""

MASTER_INTRO = """\

## The elements that are on every page and are not in the page

Master elements live in `ui.master_elements`, not in `page.elements`, and each
one draws on every page its `pages` field names (`"*"` means all of them). A
page's own controls are drawn **after** them and therefore **on top of them**, so
a control laid over a master hides it and takes the finger that was meant for it.

That is worth more care than an ordinary collision. A master is usually the
navigation -- on a page that draws its own markup it is the only way off the page
-- and nothing on the page itself says it is there, so the page looks fine in
isolation.

Reading a page before writing to it will not show you them. Read the master
elements too, and keep controls out of their boxes. A master's box is a
percentage of the **viewport**, keyed by orientation, not of anything on the
page. The review answers: *%(buried)s*
"""

WRITE_TAIL = """\

## After a write

A UI write returns any of the above it finds, in pixels and in the percentage of
that element's own container to write instead. Those are warnings, not failures:
the write landed, and resolving them is part of the same job.
"""


def _custom_page_section() -> str:
    """The custom-page rules, quoting the review's own sentences.

    Rendered rather than written out, for the same reason every other table
    here is: a hand copy of what the review says drifts from what it says, and
    this reader acts on the sentence.
    """
    kept = custom_page_findings(SimpleNamespace(
        id="lobby", render_mode="custom", custom_file="room_map/index.html",
        elements=[object(), object()],
    ))
    fileless = custom_page_findings(SimpleNamespace(
        id="lobby", render_mode="custom", custom_file=None, elements=[object()],
    ))
    return CUSTOM_PAGE_INTRO % {
        "kept": kept[0].message,
        "fileless": fileless[0].message,
    }


def _master_section() -> str:
    """The master-element rule, quoting the review's own sentence.

    Same reason as the custom-page section above: the reader acts on the
    sentence, so a hand copy of it here would be a second version to drift.
    """
    buried = buried_master_findings(
        {"video": {"x": 3.984375, "y": 0, "w": 48.75, "h": 20}},
        {"video": {"id": "video", "type": "image"}},
        {"video": None},
        [{"id": "nav_bar", "type": "button", "pages": "*",
          "placements": {"landscape": {"x": 0, "y": 0, "w": 15.859375, "h": 7.875}}}],
        "lobby",
        "landscape",
        lambda _el_id: True,
    )
    return MASTER_INTRO % {"buried": buried[0].message}


def _variants(name: str, rule) -> list[tuple[str, dict, object]]:
    """Every rule a type can resolve to, with an element that selects it.

    One entry for a normal type; for the matrix, one per ``matrix_style``, with
    the rule in the table being the default style rather than a separate row.
    """
    if not rule.style_property:
        return [(name, {"type": name}, rule)]
    out = [(f"{name} ({rule.style_default})", {"type": name}, rule)]
    out += [
        (f"{name} ({style})", {"type": name, rule.style_property: style}, sub)
        for style, sub in rule.styles.items()
    ]
    return out


#: What a repeated part's count is CALLED in a formula, per layout. A linear
#: part counts its own list; a tile wall's two axes both count `destinations`,
#: so saying so twice would read as a square wall of them.
_COUNT_NAME = {"grid_columns": "columns", "grid_rows": "rows"}


def _axis_formula(rule, axis: str) -> str:
    """One axis of a counted rule, as the sum an author can work out."""
    base = "width" if axis == "width" else "height"
    terms = [_px(getattr(rule, f"base_{base}_px"))]
    for repeated in rule.repeated:
        if repeated.axis != axis:
            continue
        count = _COUNT_NAME.get(repeated.layout, repeated.count_key)
        if repeated.size_property:
            terms.append(f"{count} x (cell + {_px(repeated.gap_px)})")
        else:
            terms.append(f"{count} x {_px(repeated.size_px + repeated.gap_px)}")
    return " + ".join(terms)


def _counted_formula_rows(counted) -> str:
    return "\n".join(
        f"| {label} | "
        f"{_axis_formula(rule, 'width')} wide, "
        f"{_axis_formula(rule, 'height')} tall |"
        for label, _element, rule in counted
    )


def _counted_conditional_rows(counted) -> str:
    """Every conditional part, once, with what it costs in each style."""
    styles = [label.split("(")[1].rstrip(")") for label, _e, _r in counted]
    order: list[tuple[str, str]] = []
    costs: dict[tuple[str, str], dict[str, float]] = {}
    for style, (_label, _element, rule) in zip(styles, counted, strict=True):
        for part in rule.conditionals:
            key = (part.when, part.axis)
            if key not in costs:
                costs[key] = {}
                order.append(key)
            costs[key][style] = part.size_px
    described = {
        "label": "a `label`",
        "presets": "`presets`",
        "lock_column": "the lock column (`show_lock`, off unless asked for)",
        "mute_column": "the mute column (`show_mute` plus a `do.mute_route` binding)",
    }
    rows = []
    for when, axis in order:
        per_style = costs[(when, axis)]
        sizes = set(per_style.values())
        cost = (
            f"{_px(sizes.pop())}px"
            if len(sizes) == 1
            else ", ".join(
                f"{_px(per_style[s])}px in `{s}`" for s in styles if s in per_style
            )
        )
        rows.append(f"| {described[when]} | {cost}, on the {axis} |")
    return "\n".join(rows)


def _counted_example_rows(counted) -> str:
    """The worked examples, from a matrix in the form the renderer is handed.

    Two resolved LISTS, never the counts. These were still being written as
    ``{input_count, output_count}`` after project format 0.10.0 deleted that
    shape, so every row resolved to an empty axis and the table published one
    floor for 4x4, 8x8 and 16x16 alike -- 140x86 for all three, in the artifact
    the cloud AI fetches to decide whether a layout fits.
    """
    rows = []
    for n in (4, 8, 16):
        boxes = []
        for _label, element, _rule in counted:
            grid = dict(element)
            grid["label"] = "Routing"
            grid["matrix_config"] = {
                "sources": [{"value": i} for i in range(1, n + 1)],
                "destinations": [{"value": i} for i in range(1, n + 1)],
            }
            boxes.append(f"{_box(grid)} px")
        rows.append(f"| {n}x{n} | {' | '.join(boxes)} |")
    return "\n".join(rows)


def _counted_section() -> str:
    counted = [
        variant
        for name, rule in RULES.items()
        if rule.repeated or rule.styles
        for variant in _variants(name, rule)
    ]
    cell = next(
        r.size_px for _l, _e, rule in counted for r in rule.repeated if r.size_property
    )
    notes = "\n".join(
        f"**{label}** -- {rule.note}\n" for label, _e, rule in counted if rule.note
    )
    return COUNTED_INTRO % {
        "formulas": _counted_formula_rows(counted),
        "conditionals": _counted_conditional_rows(counted),
        "examples": _counted_example_rows(counted),
        "style_names": " | ".join(
            label.split("(")[1].rstrip(")") for label, _e, _r in counted
        ),
        "example_rule": "|" + "---|" * (len(counted) + 1),
        "cell": _px(cell),
        "rem": _px(REM_BASE_PX),
        "notes": notes,
    }


def _fixed_rows() -> str:
    rows = []
    for name, rule in RULES.items():
        if rule.scales_with or rule.caption_width_bonus_px or rule.repeated:
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
        # "or bound text" until the caption rule was corrected: a bound
        # show.value neither draws nor widens, which is what the prose above
        # now says, and this row was still contradicting it.
        rows.append(f"| caption | {_box(labelled)} px | {_pct_pair(labelled)} |")
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
            f"{scale.property} `{_rem(scale.default_px)}` rem "
            f"(renders {_px(scale.default_px)}px), so {_box(element)} px | "
            f"{_pct_pair(element)} |"
        )
    return "\n".join(rows) + "\n"


def _font_driven_rows() -> str:
    seen: dict[str, list[str]] = {}
    for name, rule in RULES.items():
        for label, element, _sub in _variants(name, rule):
            box = minimum_box(element)
            assert box is not None
            for i in box.internals:
                if i.origin == "font-driven":
                    seen.setdefault(_internal(i, mark_origin=False), []).append(label)
    return "\n".join(
        f"- **{part}** -- {', '.join(types)}" for part, types in seen.items()
    )


def _note_rows() -> str:
    """The notes on the rules, which say things no table column can.

    ``status_led`` is skipped because its note IS the caption section's prose --
    printing it there and here would read as the same paragraph twice. The
    matrix's is skipped for the same reason: it belongs to the section that
    publishes its formula.
    """
    return "\n".join(
        f"- **{name}** -- {rule.note}"
        for name, rule in RULES.items()
        if rule.note and not rule.caption_width_bonus_px and not rule.repeated
    ) + "\n"


def _inert_list() -> str:
    """The four "or it draws nothing" rules, in one clause each."""
    return "; ".join(
        f"a `{name}` needs `{prop}`"
        + (f" or a `show.{slot}` binding" if slot else "")
        for name, (prop, slot, _) in sorted(INERT_WITHOUT.items())
    )


def _matrix_section() -> str:
    """The matrix keys, whose prose is written rather than rendered.

    Every other table here is generated from the tables it describes, because a
    hand copy drifts. This one cannot be: what a key DOES is a sentence, and
    ``MATRIX_CONFIG_KEYS`` holds only names. So the completeness of the copy is
    checked instead -- a key added to the renderer that nobody documented stops
    the generator rather than shipping a guide that is quietly missing it.
    """
    section = MATRIX_INTRO
    missing = sorted(k for k in MATRIX_CONFIG_KEYS if f"`{k}`" not in section)
    if missing:
        raise AssertionError(
            f"matrix_config keys documented nowhere in the guide: {', '.join(missing)}. "
            f"Add a row for each to MATRIX_INTRO in guide_gen.py."
        )
    return section


def _property_rows() -> str:
    """Per-type properties, with the shared icon set collapsed out of the way.

    Naming `icon`, `icon_position`, `icon_size` and `icon_color` on all twelve
    types that draw one turns a scannable table into a wall, and the fact worth
    carrying is per-type: which properties are this control's own.
    """
    icons = {"icon", "icon_position", "icon_size", "icon_color"}
    rows = []
    for name in sorted(HONORED_PROPERTIES):
        props = HONORED_PROPERTIES[name]
        own = sorted(props - icons)
        reads = ", ".join(f"`{p}`" for p in own) or "nothing of its own"
        if icons <= props:
            reads += " (+ the shared icon set)"
        rows.append(f"| {name} | {reads} |")
    return "\n".join(rows) + "\n"


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
        TYPES_INTRO % {"names": _and_list(sorted(RENDERED_TYPES))},
        FIXED_INTRO % {"ref_w": REFERENCE_WIDTH_PX, "ref_h": REFERENCE_HEIGHT_PX},
        _fixed_rows(),
        CAPTION_INTRO,
        _caption_rows(),
        SCALED_INTRO % {"rem": _px(REM_BASE_PX)},
        _scaled_rows(),
        _counted_section(),
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
        PROPERTIES_INTRO,
        _property_rows(),
        PROPERTIES_TAIL % {
            "structural": _and_list(sorted(STRUCTURAL_PROPERTIES)),
            "inert": _inert_list(),
        },
        _matrix_section(),
        COMPARISON_INTRO,
        _custom_page_section(),
        _master_section(),
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
