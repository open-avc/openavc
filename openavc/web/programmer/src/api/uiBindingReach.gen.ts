// GENERATED FILE - DO NOT EDIT.
// Rendered from the binding-reach tables (openavc/ui/page_review.py).
// Regenerate with:  python -m openavc.ui.review_gen
// A test compares this file against a fresh render, so hand edits fail CI.
//
// `show` accepts the same four slots for every element type, and each render
// function in openavc/web/panel/panel.js reads only some of them. A slot the renderer
// never looks at is silently inert: the element draws, the state key resolves,
// and the thing the author asked for simply never happens. That is how a label
// ended up carrying ONLINE / OFFLINE state text the panel has no code to draw.
//
// `visible_when` is absent on purpose -- it is registered for every element
// from the page tree, so it is honored everywhere and is never worth a warning.

/** Which `show` slots each element type's renderer actually reads. */
export const HONORED_SHOW_SLOTS: Record<string, string[]> =
{
  "button": [
    "look"
  ],
  "camera_preset": [
    "look"
  ],
  "clock": [
    "value"
  ],
  "custom": [],
  "fader": [
    "value"
  ],
  "gauge": [
    "value"
  ],
  "group": [],
  "image": [],
  "keypad": [],
  "label": [
    "look",
    "value"
  ],
  "level_meter": [
    "value"
  ],
  "list": [
    "items",
    "value"
  ],
  "matrix": [],
  "page_nav": [],
  "plugin": [],
  "select": [
    "look",
    "value"
  ],
  "slider": [
    "value"
  ],
  "status_led": [
    "look"
  ],
  "text_input": [
    "value"
  ]
};

/**
 * Types whose `look` binding renders per-state TEXT.
 *
 * Everything else that reads `look` takes only colour from it -- a status LED
 * tints its dot, a select styles its options -- so a `states[].label` on those
 * never appears anywhere.
 */
export const STATE_LABEL_TYPES: string[] = ["button", "camera_preset", "label"];

/** The slots worth naming in a message, in the order a reader expects them. */
export const REVIEWED_SHOW_SLOTS = ["value", "look", "items"] as const;

/**
 * Which element PROPERTIES each type's renderer actually reads.
 *
 * The same problem as the slot table, one level out. Every optional field on
 * UIElement is settable on every type and the loader keeps all of them; each
 * renderer reads a handful. `label` is the sharp case -- nearly every renderer
 * draws it, and the `label` element is the one that does not (it draws `text`).
 */
export const HONORED_PROPERTIES: Record<string, string[]> =
{
  "button": [
    "button_image",
    "display_mode",
    "frameless",
    "icon",
    "icon_color",
    "icon_position",
    "icon_size",
    "image_blend_mode",
    "image_fit",
    "image_opacity",
    "label"
  ],
  "camera_preset": [
    "button_image",
    "display_mode",
    "frameless",
    "icon",
    "icon_color",
    "icon_position",
    "icon_size",
    "image_blend_mode",
    "image_fit",
    "image_opacity",
    "label",
    "preset_number"
  ],
  "clock": [
    "clock_mode",
    "duration_minutes",
    "format",
    "start_key",
    "target_time",
    "timezone"
  ],
  "custom": [
    "custom_config",
    "custom_file",
    "grant"
  ],
  "fader": [
    "display_decimals",
    "label",
    "max",
    "min",
    "orientation",
    "output_max",
    "output_min",
    "response",
    "response_db_range",
    "scale_to_full",
    "send_on_release",
    "send_throttle_ms",
    "step",
    "unit"
  ],
  "gauge": [
    "arc_angle",
    "display_decimals",
    "label",
    "max",
    "min",
    "unit",
    "zones"
  ],
  "group": [
    "label",
    "label_position"
  ],
  "image": [
    "label",
    "object_fit",
    "src"
  ],
  "keypad": [
    "auto_send",
    "auto_send_delay_ms",
    "digits",
    "keypad_style",
    "label",
    "show_display"
  ],
  "label": [
    "display_decimals",
    "icon",
    "icon_color",
    "icon_position",
    "icon_size",
    "text"
  ],
  "level_meter": [
    "label",
    "max",
    "min",
    "orientation"
  ],
  "list": [
    "item_height",
    "items",
    "label",
    "list_style",
    "options"
  ],
  "matrix": [
    "label",
    "matrix_config",
    "matrix_style"
  ],
  "page_nav": [
    "icon",
    "icon_color",
    "icon_position",
    "icon_size",
    "label",
    "target_page"
  ],
  "plugin": [
    "grant",
    "plugin_config",
    "plugin_id",
    "plugin_type"
  ],
  "select": [
    "label",
    "options"
  ],
  "slider": [
    "display_decimals",
    "label",
    "max",
    "min",
    "orientation",
    "output_max",
    "output_min",
    "response",
    "response_db_range",
    "scale_to_full",
    "send_on_release",
    "send_throttle_ms",
    "step",
    "thumb_size",
    "unit"
  ],
  "status_led": [
    "label"
  ],
  "text_input": [
    "label",
    "placeholder"
  ]
};

/**
 * Fields that belong to every element and are read by something other than a
 * renderer, so no per-type table can vouch for them.
 *
 * `hidden` is here for the master-element case: on a page element it is
 * per-layout, but a master belongs to no layout and the panel reads it off the
 * element, so warning about it would fire on the correct spelling.
 */
export const STRUCTURAL_PROPERTIES: string[] = ["aspect_lock", "bindings", "css_class", "hidden", "id", "locked", "pages", "parent", "placement", "placements", "style", "type"];

/**
 * Every key the matrix renderer reads out of `matrix_config`.
 *
 * That property is a bare dict at every layer -- no schema, no validation -- so
 * this table is the only thing that knows the shape. `route_key_pattern` is the
 * one with no default: without it no crosspoint ever lights.
 */
export const MATRIX_CONFIG_KEYS: string[] = ["audio_follow_video", "destinations", "presets", "show_lock", "show_mute", "sources"];

/**
 * Navigation targets that are not page ids and never will be.
 *
 * The panel resolves both itself: `$back` dismisses an open overlay or pops the
 * page history, `$dismiss` closes an overlay and nothing else. A validator that
 * does not know them reports the documented spelling as a dangling page, and
 * believing it means hardcoding a page id into a confirm dialog's Cancel
 * button -- which makes the dialog single-use.
 */
export const NAVIGATION_SENTINELS = new Set(["$back", "$dismiss"]);
