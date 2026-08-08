<!-- GENERATED FILE - DO NOT EDIT.
     Rendered from openavc/ui/control_minimums.py and openavc/ui/page_review.py.
     Regenerate with:  python -m openavc.ui.guide_gen
     A test compares this file against a fresh render, so hand edits fail CI. -->

# Panel control minimums

An element's box is a percentage of its parent. A percentage can be arbitrarily
small; several controls cannot. A status LED's dot is 20px and does not shrink
with the box: give one 2% of a 1280px page and it has 25.6px, which holds the dot
and not the 29px a captioned LED needs, so the caption is drawn with negative
room. None of that is visible in the percentages, which is what this file is for.

Below is the smallest box each control still draws in. Every pixel number is at
the **1280 x 800 reference screen**, the same frame the write-time review and
the UI Builder both measure against.

## Percent and pixels

    px      = percent / 100 * parent_px
    percent = px / parent_px * 100

The parent is the page -- 1280 x 800 -- unless the element names a `parent`
container, in which case it is **that container's box**, and this is where the
arithmetic usually goes wrong. A container 40% x 30% of the page is 512 x 240 px, so a
status LED inside it needs 3.91% of the container to hold the same 20px dot --
not the 1.56% it would need of the page.

## The element types

`button`, `camera_preset`, `clock`, `fader`, `gauge`, `group`, `image`, `keypad`, `label`, `level_meter`, `list`, `matrix`, `page_nav`, `plugin`, `select`, `slider`, `status_led` and `text_input`

That is the whole set. `type` is a free-form string in the file, so anything else
is accepted by the loader, saved, given a placement -- and then dropped by the
renderer, which has no case for it and draws nothing. There is no error and no
gap on screen where it would have been, so this is worth checking before
inventing a name: there is no `knob`, no `toggle`, no `meter`.

`plugin` is the one that gets missed. It draws an element an installed plugin
defines, and needs `plugin_id` naming the plugin plus `plugin_type` naming the
element that plugin declares -- both of them, or it draws its unconfigured
placeholder. Do not guess either value.

## Controls with a fixed floor

`Of a full page` is the floor as a percentage of a 1280 x 800 parent. Divide by
the container instead when the element sits in one.

| Type | Smallest box | Of a full page | What does not shrink |
|---|---|---|---|
| fader | 72 x 100 px | 5.62% x 12.5% | fader-handle 44 x 44, fader-scale 28 wide |
| matrix | 278 x 236 px | 21.72% x 29.5% | matrix-cell 44 x 44 |
| level_meter | 13 x 80 px | 1.02% x 10% | meter-segment 2 tall |
| keypad | 84 x 221 px | 6.56% x 27.62% | keypad-key 36 tall (font-driven) |
| select | 44 x 51 px | 3.44% x 6.38% | native control 30 tall (font-driven) |
| text_input | 44 x 51 px | 3.44% x 6.38% | native control 30 tall (font-driven) |

## A status LED's floor changes when it draws a caption

The dot is the same either way. A caption adds the gap plus a sliver of text, so
the box has to widen before any of the caption is legible -- how much more than
that is content, not a floor. Only `label` draws it: a status LED renders
`show.look` and nothing else, so binding `show.value` neither puts text on
screen nor widens this floor.

| status_led | Smallest box | Of a full page |
|---|---|---|
| no caption | 20 x 20 px | 1.56% x 2.5% |
| caption | 29 x 20 px | 2.27% x 2.5% |

## Controls whose floor depends on a size you set

These have no single floor, because the part that does not shrink is one you can
set. The floor is a formula; the numbers in the last column are what the default
produces. Work the formula out with your own value if you set one.

**The size you write is in `rem`, not pixels** -- px / 14, like every other
measurement on an element. The Default column gives both forms: copy the **rem**
number. Writing the pixel number instead means that many rem, which is 14
times too big and produces a floor larger than the page. The formulas are in
pixels because that is what the control is measured in, so multiply your
authored value by 14 before working one out.

| Type | Floor | Authored by | Default | Of a full page at the default |
|---|---|---|---|---|
| slider | 24 + thumb_size wide, 37 + thumb_size tall | `thumb_size` on the element or the theme | thumb_size `3.14` rem (renders 44px), so 68 x 81 px | 5.31% x 10.12% |
| list | 28 wide, 33 + item_height tall | `item_height` on the element | item_height `3.14` rem (renders 44px), so 28 x 77 px | 2.19% x 9.62% |

## Per-type notes

Where a floor is not what the shape of the control suggests.

- **list** -- Row height does not change how wide a list has to be.
- **matrix** -- Constant, NOT a function of the crosspoint count: 2x2, 3x3 and 4x4 all floor here, because .matrix-scroll scrolls the grid internally once it runs out of room. 278x236 rather than the 277x234 first recorded because both of those push a cell outside the box somewhere: these floors are text-driven and move a pixel or two with the font stack, so this is the largest of three machines (274..278 wide, 234..236 tall) rather than any one measurement. Where they disagree the larger wins -- a slightly generous floor rejects a layout that would have rendered, but a short one draws a broken control and says nothing.

## Types with no floor at all

`button`, `camera_preset`, `clock`, `gauge`, `group`, `image`, `label`, `page_nav` and `plugin`

These have **no fixed internals**: nothing inside them keeps a size when the box
shrinks. What limits them is their content -- a caption, an icon, an image,
whatever a plugin draws -- which depends on the string and on the theme's font,
is unbounded, and is therefore not a minimum box. There is no number to check
for these types, and inventing one would reject layouts that draw correctly.

That is not permission to make them tiny. It means the check is your judgement,
plus the finger rule below where the type is one you touch.

## Some numbers above are the theme's, not a declared size

- **keypad-key 36 tall** -- keypad
- **native control 30 tall** -- select, text_input

None of these has a declared floor anywhere. They fall out of the theme's font
size plus padding, so the value recorded above is what the **default theme**
produces and a theme with larger type moves it. They are in the tables anyway: a
keypad crushed under its own keys is a worse outcome than a floor that can move.

## The finger rule

A control a finger has to hit needs 9 mm of it. At the reference screen's
149 ppi that is **53px**, or 4.12% of the page's width and 6.6% of its height.

Applies to: `button`, `camera_preset`, `fader`, `keypad`, `list`, `page_nav`, `select`, `slider` and `text_input`

A fader and a slider are on that list because dragging is touch -- you grab the
handle with the same thumb. In practice neither reaches this check without
already having failed its own floor above.

## What each type's `show` bindings actually render

`show` accepts the same slots for every element type, and each type's renderer
reads only some of them. A slot the renderer never looks at is **silently
inert**: the element draws, the state key resolves, nothing errors, and the thing
you asked for never happens. Check this table before binding.

| Type | Renders |
|---|---|
| button | `show.look` |
| camera_preset | `show.look` |
| clock | `show.value` |
| fader | `show.value` |
| gauge | `show.value` |
| group | nothing |
| image | nothing |
| keypad | nothing |
| label | `show.value` |
| level_meter | `show.value` |
| list | `show.value`, `show.items` |
| matrix | nothing |
| page_nav | nothing |
| plugin | nothing |
| select | `show.value`, `show.look` |
| slider | `show.value` |
| status_led | `show.look` |
| text_input | `show.value` |

`show.visible_when` is absent from the table because it is honored for every
element type, from the page tree rather than from the renderer.

`show.look` carries per-state **colour** wherever it is read. Per-state **text**
(`states[].label`) is drawn by `button` and `camera_preset` and by nothing else, so a
`states[].label` on any other type never appears on screen. A label that should
read ONLINE / OFFLINE needs its text in `show.value`.

## After a write

A UI write returns any of the above it finds, in pixels and in the percentage of
that element's own container to write instead. Those are warnings, not failures:
the write landed, and resolving them is part of the same job.
