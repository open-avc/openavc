/**
 * What the Theme Studio's Elements section offers, per element type: each
 * control, its label and hint, and where its value lives, a theme variable
 * (`kind: "var"`) or the type's element default (`kind: "default"`). A value
 * has one home, so no two controls edit the same thing, and a foundational
 * token (the Theme section's) is never repeated here. The order the sections
 * are drawn in, and their names, are here too, and `hasElementSection` is
 * which types a control clicked in the preview can open.
 */

/** Where a control's value lives: a theme variable, or the element type's default. */
export type ControlSource = { kind: "var"; key: string } | { kind: "default"; key: string };

export interface ElementControl {
  label: string;
  type: "color" | "number" | "text";
  source: ControlSource;
  hint?: string;
}

// Button-derivative types (page_nav, camera_preset, keypad) inherit the
// button_* CSS variables by default, but built-in themes ship per-type
// element_defaults that override them — so each gets its own section here to
// author those overrides. Clearing a derivative's color reverts it to the
// button/CSS default (the element_defaults key is deleted).

const STANDARD_BORDER_CONTROLS: ElementControl[] = [
  { label: "Border Color", type: "color", source: { kind: "default", key: "border_color" } },
  { label: "Border Width (px)", type: "number", source: { kind: "default", key: "border_width" } },
];

const BOX_SHADOW_CONTROL: ElementControl = {
  label: "Box Shadow",
  type: "text",
  source: { kind: "default", key: "box_shadow" },
  hint: "CSS syntax, e.g. '0 2px 4px rgba(0,0,0,0.3)' or 'none'",
};

export const ELEMENT_CONTROLS: Record<string, ElementControl[]> = {
  button: [
    // Button colors live as CSS variables, so they're the base for camera
    // presets and keypad keys (which use the same vars) and the border for
    // page nav — each can override its own in its section below. Active button
    // bg derives from Accent (no separate token); active text uses Button Text.
    { label: "Background", type: "color", source: { kind: "var", key: "button_bg" }, hint: "Base for camera presets and keypad keys (override per-type below)" },
    { label: "Text", type: "color", source: { kind: "var", key: "button_text" }, hint: "Also the active button text, and camera-preset / keypad-key text" },
    { label: "Border Color", type: "color", source: { kind: "var", key: "button_border" }, hint: "Base border for page nav, camera presets, and keypad keys (override per-type below)" },
    { label: "Border Width (px)", type: "number", source: { kind: "default", key: "border_width" } },
    BOX_SHADOW_CONTROL,
  ],
  page_nav: [
    { label: "Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "Page nav is transparent by default; set a color to fill it" },
    { label: "Text", type: "color", source: { kind: "default", key: "text_color" }, hint: "Defaults to Accent" },
    ...STANDARD_BORDER_CONTROLS,
    BOX_SHADOW_CONTROL,
  ],
  camera_preset: [
    { label: "Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "Defaults to Button Background" },
    { label: "Text", type: "color", source: { kind: "default", key: "text_color" }, hint: "Defaults to Button Text" },
    ...STANDARD_BORDER_CONTROLS,
    BOX_SHADOW_CONTROL,
  ],
  keypad: [
    { label: "Container Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "The number keys themselves use the Button colors" },
    { label: "Text Color", type: "color", source: { kind: "default", key: "text_color" }, hint: "Label and entry-display text" },
    ...STANDARD_BORDER_CONTROLS,
  ],
  label: [
    { label: "Background", type: "color", source: { kind: "default", key: "bg_color" } },
    { label: "Text Color", type: "color", source: { kind: "default", key: "text_color" }, hint: "Overrides Page Text just for labels" },
    ...STANDARD_BORDER_CONTROLS,
  ],
  slider: [
    { label: "Wrapper Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "Track uses Surface, fill uses Accent" },
    { label: "Label Color", type: "color", source: { kind: "default", key: "text_color" } },
    { label: "Thumb Size (px)", type: "number", source: { kind: "default", key: "thumb_size" }, hint: "Larger = easier to grab on touch (44 is standard)" },
    ...STANDARD_BORDER_CONTROLS,
  ],
  fader: [
    { label: "Wrapper Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "Track uses Surface, handle uses Accent" },
    { label: "Label Color", type: "color", source: { kind: "default", key: "text_color" } },
    ...STANDARD_BORDER_CONTROLS,
  ],
  status_led: [
    { label: "Active Color", type: "color", source: { kind: "default", key: "bg_color" }, hint: "Color shown when bound state is true" },
    { label: "Label Color", type: "color", source: { kind: "default", key: "text_color" } },
    { label: "Border Color", type: "color", source: { kind: "default", key: "border_color" } },
  ],
  gauge: [
    { label: "Gauge Color", type: "color", source: { kind: "default", key: "gauge_color" }, hint: "Filled portion" },
    { label: "Gauge Background", type: "color", source: { kind: "default", key: "gauge_bg_color" }, hint: "Empty portion" },
    { label: "Text Color", type: "color", source: { kind: "default", key: "text_color" } },
    { label: "Background", type: "color", source: { kind: "default", key: "bg_color" } },
    ...STANDARD_BORDER_CONTROLS,
  ],
  level_meter: [
    { label: "Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "Zone colors come from Success / Warning / Danger" },
    ...STANDARD_BORDER_CONTROLS,
    { label: "Green Threshold (dB)", type: "number", source: { kind: "default", key: "green_to" }, hint: "Levels below are green; default −12" },
    { label: "Yellow Threshold (dB)", type: "number", source: { kind: "default", key: "yellow_to" }, hint: "Above is red; default −3" },
  ],
  list: [
    { label: "Item Background", type: "color", source: { kind: "default", key: "item_bg" } },
    { label: "Selected Item", type: "color", source: { kind: "default", key: "item_active_bg" }, hint: "Highlighted row" },
    { label: "Text Color", type: "color", source: { kind: "default", key: "text_color" } },
    { label: "Wrapper Background", type: "color", source: { kind: "default", key: "bg_color" } },
    ...STANDARD_BORDER_CONTROLS,
  ],
  matrix: [
    { label: "Active Route", type: "color", source: { kind: "default", key: "crosspoint_active_color" }, hint: "Crosspoint dot when route is connected" },
    { label: "Inactive Route", type: "color", source: { kind: "default", key: "crosspoint_inactive_color" } },
    { label: "Background", type: "color", source: { kind: "default", key: "bg_color" } },
    ...STANDARD_BORDER_CONTROLS,
  ],
  select: [
    { label: "Wrapper Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "The dropdown itself uses Surface" },
    { label: "Label Color", type: "color", source: { kind: "default", key: "text_color" } },
    ...STANDARD_BORDER_CONTROLS,
  ],
  text_input: [
    { label: "Wrapper Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "The input itself uses Surface" },
    { label: "Label Color", type: "color", source: { kind: "default", key: "text_color" } },
    ...STANDARD_BORDER_CONTROLS,
  ],
  image: [
    { label: "Background", type: "color", source: { kind: "default", key: "bg_color" } },
    ...STANDARD_BORDER_CONTROLS,
  ],
  group: [
    { label: "Background", type: "color", source: { kind: "default", key: "bg_color" } },
    { label: "Title Color", type: "color", source: { kind: "default", key: "text_color" }, hint: "Group label text" },
    ...STANDARD_BORDER_CONTROLS,
  ],
  clock: [
    { label: "Background", type: "color", source: { kind: "default", key: "bg_color" } },
    { label: "Text Color", type: "color", source: { kind: "default", key: "text_color" } },
    ...STANDARD_BORDER_CONTROLS,
  ],
};

// Order in which element sections render. Buttons first (most common) with
// their derivatives grouped right after, then other interactive elements, then
// displays, then layout/utility.
export const ELEMENT_ORDER = [
  "button", "page_nav", "camera_preset", "keypad",
  "label", "slider", "fader", "select", "text_input",
  "status_led", "gauge", "level_meter",
  "list", "matrix", "group",
  "image", "clock",
];

export const ELEMENT_TYPE_LABELS: Record<string, string> = {
  button: "Button",
  label: "Label",
  slider: "Slider",
  page_nav: "Page Nav",
  select: "Select",
  text_input: "Text Input",
  status_led: "Status LED",
  image: "Image",
  camera_preset: "Camera Preset",
  gauge: "Gauge",
  level_meter: "Level Meter",
  fader: "Fader",
  group: "Group",
  clock: "Clock",
  keypad: "Keypad",
  list: "List",
  matrix: "Matrix",
};

/** Whether an element type has a section of its own. */
export function hasElementSection(type: string): boolean {
  return Boolean(type && ELEMENT_CONTROLS[type]);
}
