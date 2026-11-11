/**
 * What the Theme Studio's Elements section offers, per element type: each
 * control, its label and hint, and where its value lives, a theme variable
 * (`kind: "var"`) or the type's element default (`kind: "default"`). A value
 * has one home, so no two controls edit the same thing, and a foundational
 * token (the Theme section's) is never repeated here. Each type is one entry
 * in `ELEMENT_SECTIONS`, in the order the sections are drawn, with the name its
 * section shows; `hasElementSection` is which types a control clicked in the
 * preview can open.
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

/** One element type's section: its type, the name it is drawn under, its controls. */
export interface ElementSection {
  type: string;
  label: string;
  controls: ElementControl[];
}

// In the order the sections are drawn: buttons first (the most common) with
// their derivatives right after, then the other interactive elements, then
// displays, then layout and utility.
export const ELEMENT_SECTIONS: readonly ElementSection[] = [
  {
    type: "button",
    label: "Button",
    controls: [
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
  },
  {
    type: "page_nav",
    label: "Page Nav",
    controls: [
      { label: "Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "Page nav is transparent by default; set a color to fill it" },
      { label: "Text", type: "color", source: { kind: "default", key: "text_color" }, hint: "Defaults to Accent" },
      ...STANDARD_BORDER_CONTROLS,
      BOX_SHADOW_CONTROL,
    ],
  },
  {
    type: "camera_preset",
    label: "Camera Preset",
    controls: [
      { label: "Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "Defaults to Button Background" },
      { label: "Text", type: "color", source: { kind: "default", key: "text_color" }, hint: "Defaults to Button Text" },
      ...STANDARD_BORDER_CONTROLS,
      BOX_SHADOW_CONTROL,
    ],
  },
  {
    type: "keypad",
    label: "Keypad",
    controls: [
      { label: "Container Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "The number keys themselves use the Button colors" },
      { label: "Text Color", type: "color", source: { kind: "default", key: "text_color" }, hint: "Label and entry-display text" },
      ...STANDARD_BORDER_CONTROLS,
    ],
  },
  {
    type: "label",
    label: "Label",
    controls: [
      { label: "Background", type: "color", source: { kind: "default", key: "bg_color" } },
      { label: "Text Color", type: "color", source: { kind: "default", key: "text_color" }, hint: "Overrides Page Text just for labels" },
      ...STANDARD_BORDER_CONTROLS,
    ],
  },
  {
    type: "slider",
    label: "Slider",
    controls: [
      { label: "Wrapper Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "Track uses Surface, fill uses Accent" },
      { label: "Label Color", type: "color", source: { kind: "default", key: "text_color" } },
      { label: "Thumb Size (px)", type: "number", source: { kind: "default", key: "thumb_size" }, hint: "Larger = easier to grab on touch (44 is standard)" },
      ...STANDARD_BORDER_CONTROLS,
    ],
  },
  {
    type: "fader",
    label: "Fader",
    controls: [
      { label: "Wrapper Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "Track uses Surface, handle uses Accent" },
      { label: "Label Color", type: "color", source: { kind: "default", key: "text_color" } },
      ...STANDARD_BORDER_CONTROLS,
    ],
  },
  {
    type: "select",
    label: "Select",
    controls: [
      { label: "Wrapper Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "The dropdown itself uses Surface" },
      { label: "Label Color", type: "color", source: { kind: "default", key: "text_color" } },
      ...STANDARD_BORDER_CONTROLS,
    ],
  },
  {
    type: "text_input",
    label: "Text Input",
    controls: [
      { label: "Wrapper Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "The input itself uses Surface" },
      { label: "Label Color", type: "color", source: { kind: "default", key: "text_color" } },
      ...STANDARD_BORDER_CONTROLS,
    ],
  },
  {
    type: "status_led",
    label: "Status LED",
    controls: [
      { label: "Active Color", type: "color", source: { kind: "default", key: "bg_color" }, hint: "Color shown when bound state is true" },
      { label: "Label Color", type: "color", source: { kind: "default", key: "text_color" } },
      { label: "Border Color", type: "color", source: { kind: "default", key: "border_color" } },
    ],
  },
  {
    type: "gauge",
    label: "Gauge",
    controls: [
      { label: "Gauge Color", type: "color", source: { kind: "default", key: "gauge_color" }, hint: "Filled portion" },
      { label: "Gauge Background", type: "color", source: { kind: "default", key: "gauge_bg_color" }, hint: "Empty portion" },
      { label: "Text Color", type: "color", source: { kind: "default", key: "text_color" } },
      { label: "Background", type: "color", source: { kind: "default", key: "bg_color" } },
      ...STANDARD_BORDER_CONTROLS,
    ],
  },
  {
    type: "level_meter",
    label: "Level Meter",
    controls: [
      { label: "Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "Zone colors come from Success / Warning / Danger" },
      ...STANDARD_BORDER_CONTROLS,
      { label: "Green Threshold (dB)", type: "number", source: { kind: "default", key: "green_to" }, hint: "Levels below are green; default −12" },
      { label: "Yellow Threshold (dB)", type: "number", source: { kind: "default", key: "yellow_to" }, hint: "Above is red; default −3" },
    ],
  },
  {
    type: "list",
    label: "List",
    controls: [
      { label: "Item Background", type: "color", source: { kind: "default", key: "item_bg" } },
      { label: "Selected Item", type: "color", source: { kind: "default", key: "item_active_bg" }, hint: "Highlighted row" },
      { label: "Text Color", type: "color", source: { kind: "default", key: "text_color" } },
      { label: "Wrapper Background", type: "color", source: { kind: "default", key: "bg_color" } },
      ...STANDARD_BORDER_CONTROLS,
    ],
  },
  {
    type: "matrix",
    label: "Matrix",
    controls: [
      { label: "Active Route", type: "color", source: { kind: "default", key: "crosspoint_active_color" }, hint: "Crosspoint dot when route is connected" },
      { label: "Inactive Route", type: "color", source: { kind: "default", key: "crosspoint_inactive_color" } },
      { label: "Background", type: "color", source: { kind: "default", key: "bg_color" } },
      ...STANDARD_BORDER_CONTROLS,
    ],
  },
  {
    type: "group",
    label: "Group",
    controls: [
      { label: "Background", type: "color", source: { kind: "default", key: "bg_color" } },
      { label: "Title Color", type: "color", source: { kind: "default", key: "text_color" }, hint: "Group label text" },
      ...STANDARD_BORDER_CONTROLS,
    ],
  },
  {
    type: "image",
    label: "Image",
    controls: [
      { label: "Background", type: "color", source: { kind: "default", key: "bg_color" } },
      ...STANDARD_BORDER_CONTROLS,
    ],
  },
  {
    type: "clock",
    label: "Clock",
    controls: [
      { label: "Background", type: "color", source: { kind: "default", key: "bg_color" } },
      { label: "Text Color", type: "color", source: { kind: "default", key: "text_color" } },
      ...STANDARD_BORDER_CONTROLS,
    ],
  },
];

/** The types in the order their sections are drawn (the arrow keys walk it). */
export const ELEMENT_ORDER = ELEMENT_SECTIONS.map((section) => section.type);

const SECTION_BY_TYPE: Record<string, ElementSection> = Object.fromEntries(
  ELEMENT_SECTIONS.map((section) => [section.type, section]),
);

/** Whether an element type has a section of its own (a plain lookup by key, as the click listener has always asked). */
export function hasElementSection(type: string): boolean {
  return Boolean(type && SECTION_BY_TYPE[type]);
}
