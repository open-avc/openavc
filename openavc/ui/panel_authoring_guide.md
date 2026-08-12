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

`button`, `camera_preset`, `clock`, `custom`, `fader`, `gauge`, `group`, `image`, `keypad`, `label`, `level_meter`, `list`, `matrix`, `page_nav`, `plugin`, `select`, `slider`, `status_led` and `text_input`

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

`button`, `camera_preset`, `clock`, `custom`, `gauge`, `group`, `image`, `label`, `page_nav` and `plugin`

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
| custom | nothing |
| fader | `show.value` |
| gauge | `show.value` |
| group | nothing |
| image | nothing |
| keypad | nothing |
| label | `show.value`, `show.look` |
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
(`states[].label`) is drawn by `button`, `camera_preset` and `label` and by nothing else, so a
`states[].label` on any other type never appears on screen. A label that should
read ONLINE / OFFLINE needs its text in `show.value`.

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
| button | `button_image`, `display_mode`, `frameless`, `image_blend_mode`, `image_fit`, `image_opacity`, `label` (+ the shared icon set) |
| camera_preset | `button_image`, `display_mode`, `frameless`, `image_blend_mode`, `image_fit`, `image_opacity`, `label`, `preset_number` (+ the shared icon set) |
| clock | `clock_mode`, `duration_minutes`, `format`, `start_key`, `target_time`, `timezone` |
| custom | `custom_config`, `custom_file` |
| fader | `display_decimals`, `label`, `max`, `min`, `orientation`, `output_max`, `output_min`, `response`, `response_db_range`, `scale_to_full`, `send_on_release`, `send_throttle_ms`, `step`, `unit` |
| gauge | `arc_angle`, `display_decimals`, `label`, `max`, `min`, `unit`, `zones` |
| group | `label`, `label_position` |
| image | `label`, `object_fit`, `src` |
| keypad | `auto_send`, `auto_send_delay_ms`, `digits`, `keypad_style`, `label`, `show_display` |
| label | `display_decimals`, `text` (+ the shared icon set) |
| level_meter | `label`, `max`, `min`, `orientation` |
| list | `item_height`, `items`, `label`, `list_style`, `options` |
| matrix | `label`, `matrix_config`, `matrix_style` |
| page_nav | `label`, `target_page` (+ the shared icon set) |
| plugin | `plugin_config`, `plugin_id`, `plugin_type` |
| select | `label`, `options` |
| slider | `display_decimals`, `label`, `max`, `min`, `orientation`, `output_max`, `output_min`, `response`, `response_db_range`, `scale_to_full`, `send_on_release`, `send_throttle_ms`, `step`, `thumb_size`, `unit` |
| status_led | `label` |
| text_input | `label`, `placeholder` |

Fields common to every element are not in the table because no renderer owns
them: `aspect_lock`, `bindings`, `css_class`, `hidden`, `id`, `locked`, `pages`, `parent`, `placement`, `placements`, `style` and `type`.

`hidden` is the one to read twice. On a page element it is **per-layout**
(`layouts[].hidden`), and setting it on the element does nothing. A master
element belongs to no layout, so there it is an element property and works.

Four types draw an empty box when one particular thing is missing, and a write
warns about each: a `custom` needs `custom_file`; a `image` needs `src`; a `label` needs `text` or a `show.value` binding; a `page_nav` needs `target_page`; a `select` needs `options` or a `show.items` binding.

## The matrix, which is configured entirely inside `matrix_config`

A matrix is the one control whose settings do not live on the element. They live
in `matrix_config`, which is a free-form object at every layer -- no schema, no
defaults published anywhere else -- so an invented key is stored and ignored in
exactly the same way a correct one is stored and used.

| Key | What it does |
|---|---|
| `input_count` / `output_count` | Grid size. **Default 4 each.** An 8x8 switcher that omits these silently draws half of itself. |
| `route_key_pattern` | **The one that lights the crosspoints.** No default. |
| `input_labels` / `output_labels` | Column and row captions. Default `In 1`..`In N` / `Out 1`..`Out N`. |
| `input_key_pattern` / `output_key_pattern` | Captions driven from live state instead, same `*` substitution. |
| `audio_route_key_pattern` | Audio routes, which also drives the per-output A!=V badge. |
| `audio_follow_video` | Send the audio route alongside the video one. Needs a `do.audio_route` binding. |
| `show_lock` | Per-output lock buttons. **Defaults on.** Client-side only -- locking sends nothing, it just stops that row being changed on this panel. |
| `show_mute` | Per-output mute buttons. Drawn only when there is also a `do.mute_route` binding. |
| `presets` | `[{name, macro}]`. A preset bar above the grid; each button runs its macro. |

`route_key_pattern` is worth its own paragraph, because getting it wrong
produces a control that looks finished. It is the state key of one output's
routed input with the **output number replaced by `*`**, 1-based:

    "route_key_pattern": "device.<device id>.output.*.input"

The panel substitutes 1..`output_count` and reads each key to decide which
crosspoint in that row is lit. Without it, no state binding is registered at all:
the grid draws, clicking still routes correctly (the command carries
`$input`/`$output` from the touch, not from config), and **no crosspoint ever
changes colour** for the life of the panel.

Routing itself is a `do` binding, not config: `do.route` with `$input` and
`$output`, plus `do.audio_route`, `do.mute_route` and `do.audio_mute_route`
(`$output`, `$mute`) if the device supports them.

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

## After a write

A UI write returns any of the above it finds, in pixels and in the percentage of
that element's own container to write instead. Those are warnings, not failures:
the write landed, and resolving them is part of the same job.
