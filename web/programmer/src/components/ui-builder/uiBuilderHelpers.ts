import type { UIPage, UIElement, Placement, Layout, SnapConfig, MasterElement, PageGroup, MacroConfig, MacroStep, VariableConfig, ScriptConfig, ProjectConfig } from "../../api/types";
import type { PluginExtension } from "../../api/pluginClient";
// Both generated, both from Python, because both are measured facts rather than
// design decisions: the per-control floors were binary-searched against a real
// browser, and the binding-reach table is read off the panel renderer itself.
// See server/ui/minimums_gen.py and server/ui/review_gen.py.
import {
  CONTROL_MINIMUMS,
  REM_BASE_PX,
  type ControlScalingInternal,
} from "../../api/uiMinimums.gen";
import {
  HONORED_SHOW_SLOTS,
  REVIEWED_SHOW_SLOTS,
  STATE_LABEL_TYPES,
} from "../../api/uiBindingReach.gen";

// --- Binding type definitions ---

export interface PressBinding {
  action: string;
  macro?: string;
  device?: string;
  command?: string;
  params?: Record<string, unknown>;
  key?: string;
  value?: unknown;
  page?: string;
  function?: string;
}

export interface FeedbackBinding {
  source: string;
  key: string;
  condition: { equals: unknown };
  style_active: Record<string, string>;
  style_inactive: Record<string, string>;
}

export interface TextBinding {
  source: string;
  key: string;
  format?: string;
}

export interface ColorBinding {
  source: string;
  key: string;
  map: Record<string, string>;
  default: string;
}

export interface ValueBinding {
  source: string;
  key: string;
}

// --- Element type palette definitions ---

export interface ElementTypeInfo {
  type: string;
  label: string;
  category: "controls" | "display" | "navigation" | "data";
  description: string;
}

export const ELEMENT_TYPES: ElementTypeInfo[] = [
  { type: "button", label: "Button", category: "controls", description: "Tap/hold to trigger actions, with multi-state feedback" },
  { type: "slider", label: "Slider", category: "controls", description: "Drag control for numeric values (volume, brightness), horizontal or vertical" },
  { type: "fader", label: "Fader", category: "controls", description: "Vertical mixing-console style fader with dB scale and meter" },
  { type: "select", label: "Select", category: "controls", description: "Dropdown to pick from a list of options" },
  { type: "text_input", label: "Text Input", category: "controls", description: "Text field for user input (names, IP addresses, codes)" },
  { type: "keypad", label: "Keypad", category: "controls", description: "Numeric keypad for entering digits (channel, PIN, speed dial)" },
  { type: "label", label: "Label", category: "display", description: "Static or dynamic text display" },
  { type: "status_led", label: "Status LED", category: "display", description: "Color indicator that changes based on device state" },
  { type: "image", label: "Image", category: "display", description: "Display an image or logo" },
  { type: "clock", label: "Clock", category: "display", description: "Live clock, date, countdown, or meeting timer" },
  { type: "group", label: "Container", category: "display", description: "A real container: elements dropped inside move, resize and hide with it" },
  { type: "gauge", label: "Gauge", category: "data", description: "Circular dial for displaying a single value (temperature, level)" },
  { type: "level_meter", label: "Level Meter", category: "data", description: "Segmented bar for audio levels (VU/PPM style)" },
  { type: "matrix", label: "Matrix", category: "data", description: "Audio/video routing crosspoint grid or dropdown list" },
  { type: "list", label: "List", category: "controls", description: "Scrollable list of items (sources, presets, zones)" },
  { type: "page_nav", label: "Page Nav", category: "navigation", description: "Button that navigates to another page or overlay" },
  { type: "camera_preset", label: "Camera Preset", category: "navigation", description: "Button to recall a PTZ camera preset position" },
];

// --- Binding capability descriptor per element type ---
//
// Every control's bindings are grouped into two buckets the integrator reasons
// about directly: SHOWS (what the control reflects from live state) and DOES
// (what happens when it's touched). This descriptor drives BindingProperties:
// which Value / Items / Appearance cards a control gets under SHOWS, and which
// interaction action-lists it gets under DOES. The "Visible when…" card is
// universal (every element can be conditionally shown) and is therefore not
// listed here — BindingProperties always renders it.

export type ValueEditorKind = "slider" | "text";
export type LookEditorKind = "feedback" | "color" | "select_feedback";
export type InteractionEditorKind = "actions" | "select_change";

export interface ValueCapability {
  /** Which editor draws the Value source. `slider` = a state-key picker (the
   *  control's numeric/selection value); `text` = the label text editor. */
  editor: ValueEditorKind;
  /** Heading for the Value card (e.g. "Selected item" for lists). */
  label?: string;
  /** The control can drive its value back out — shows the device-aware LINK
   *  (two-way) switch. False for read-only displays (gauge, level meter). */
  link?: boolean;
}

export interface DoesCapability {
  /** The `do.<interaction>` key this action list is stored under. */
  interaction: string;
  /** Heading shown above the action list. */
  label: string;
  editor: InteractionEditorKind;
}

export interface BindingCapability {
  /** SHOWS → Value card. */
  value?: ValueCapability;
  /** SHOWS → Items card (list row population). */
  items?: boolean;
  /** SHOWS → Appearance card (state-driven look). */
  look?: LookEditorKind;
  /** DOES → one action list per interaction. */
  does?: DoesCapability[];
  /** Buttons drive DOES through the unified ButtonBindingEditor (behavior mode +
   *  press/hold/release) rather than a plain action list. */
  buttonStyle?: boolean;
}

export const BINDING_CAPABILITIES: Record<string, BindingCapability> = {
  button: { look: "feedback", buttonStyle: true },
  camera_preset: {
    look: "feedback",
    does: [{ interaction: "press", label: "On press", editor: "actions" }],
  },
  label: { value: { editor: "text", label: "Text" } },
  slider: {
    value: { editor: "slider", link: true },
    does: [{ interaction: "change", label: "On change", editor: "actions" }],
  },
  fader: {
    value: { editor: "slider", link: true },
    does: [{ interaction: "change", label: "On change", editor: "actions" }],
  },
  select: {
    value: { editor: "slider", link: true },
    look: "select_feedback",
    does: [{ interaction: "change", label: "On change", editor: "select_change" }],
  },
  text_input: {
    value: { editor: "slider", link: true },
    does: [{ interaction: "change", label: "On change", editor: "actions" }],
  },
  status_led: { look: "color" },
  gauge: { value: { editor: "slider", link: false } },
  level_meter: { value: { editor: "slider", link: false } },
  keypad: {
    does: [{ interaction: "submit", label: "On submit", editor: "actions" }],
  },
  list: {
    value: { editor: "slider", label: "Selected item", link: true },
    items: true,
    does: [{ interaction: "select", label: "On row tap", editor: "actions" }],
  },
  matrix: {
    does: [
      { interaction: "route", label: "Video route", editor: "actions" },
      { interaction: "audio_route", label: "Audio route", editor: "actions" },
      { interaction: "mute_route", label: "Mute", editor: "actions" },
      { interaction: "audio_mute_route", label: "Audio mute", editor: "actions" },
    ],
  },
  // page_nav / image / group / clock / plugin: "Visible when…" only.
};

// --- Screen presets ---

export interface ScreenPreset {
  label: string;
  width: number;
  height: number;
}

export const SCREEN_PRESETS: ScreenPreset[] = [
  { label: '7" Tablet (1024x600)', width: 1024, height: 600 },
  { label: '10" Tablet (1280x800)', width: 1280, height: 800 },
  { label: "iPad (1024x768)", width: 1024, height: 768 },
  { label: "1080p (1920x1080)", width: 1920, height: 1080 },
];

// --- ID generation ---

function generateId(type: string, existingIds: Set<string>): string {
  let counter = 1;
  let id = `${type}_${counter}`;
  while (existingIds.has(id)) {
    counter++;
    id = `${type}_${counter}`;
  }
  return id;
}

// --- Create default element ---

/**
 * How big a freshly-dropped element is, as a percentage of the page.
 *
 * These are the old per-type cell spans read against the 12x8 grid that used to
 * BE the model, so a new panel starts out shaped exactly the way it always did.
 * They also land on whole snap increments at the default snap setting, which is
 * why a dropped control still lines up with its neighbours for free.
 */
export const DEFAULT_ELEMENT_SIZES: Record<string, { w: number; h: number }> = {
  button: { w: 25, h: 25 },
  label: { w: 25, h: 12.5 },
  status_led: { w: 16.6667, h: 12.5 },
  slider: { w: 33.3333, h: 12.5 },
  page_nav: { w: 16.6667, h: 12.5 },
  select: { w: 25, h: 12.5 },
  text_input: { w: 25, h: 12.5 },
  image: { w: 25, h: 37.5 },
  camera_preset: { w: 16.6667, h: 25 },
  gauge: { w: 25, h: 37.5 },
  level_meter: { w: 8.3333, h: 50 },
  fader: { w: 16.6667, h: 62.5 },
  group: { w: 50, h: 50 },
  clock: { w: 25, h: 12.5 },
  keypad: { w: 25, h: 62.5 },
  list: { w: 25, h: 50 },
  matrix: { w: 50, h: 62.5 },
};

/** Fallback size for an element type with no entry above (incl. plugins). */
export const FALLBACK_ELEMENT_SIZE = { w: 33.3333, h: 37.5 };

/**
 * The ratio a NEW element of this type is locked to.
 *
 * This lives here, in the authoring surface, and deliberately NOT in the panel
 * renderer. A renderer that locked every status LED to 1:1 would re-shape
 * elements in projects that were migrated rather than authored, against the
 * promise that a migrated panel looks exactly like it used to. So the runtime
 * honours a lock when it finds one and invents nothing; the palette is what
 * gives a new control its sensible shape.
 *
 * `image` is absent on purpose: its "intrinsic" ratio is not knowable when the
 * element is created with no picture in it. It gets its lock the moment a
 * source is chosen, measured from the file (see BasicProperties).
 */
export const DEFAULT_ASPECT_LOCKS: Record<string, number> = {
  status_led: 1.0,
  camera_preset: 1.0,
};

/** 16:9, for the plugin elements that show a picture (camera/video panels). */
export const VIDEO_ASPECT_LOCK = 16 / 9;

/** Plugin element types whose content is a video image and must not skew. */
const VIDEO_PLUGIN_TYPES = new Set(["video_stream", "video_panel", "camera_panel"]);

/** The default box for an element type, honouring a plugin's own declaration. */
export function defaultElementSize(
  type: string,
  panelElements: PluginExtension[] = [],
): { w: number; h: number } {
  if (type.startsWith("plugin:")) {
    const parts = type.split(":");
    const ext = panelElements.find(
      (e) => e.plugin_id === parts[1] && e.type === parts.slice(2).join(":"),
    );
    return {
      w: ext?.default_size?.w ?? FALLBACK_ELEMENT_SIZE.w,
      h: ext?.default_size?.h ?? FALLBACK_ELEMENT_SIZE.h,
    };
  }
  return DEFAULT_ELEMENT_SIZES[type] ?? FALLBACK_ELEMENT_SIZE;
}

/** The aspect lock a new element of this type is born with, or null. */
export function defaultAspectLock(type: string): number | null {
  if (type.startsWith("plugin:")) {
    const pluginType = type.split(":").slice(2).join(":");
    return VIDEO_PLUGIN_TYPES.has(pluginType) ? VIDEO_ASPECT_LOCK : null;
  }
  return DEFAULT_ASPECT_LOCKS[type] ?? null;
}

export function createDefaultElement(
  type: string,
  existingIds: Set<string>,
): UIElement {
  const id = generateId(type, existingIds);
  const aspect = defaultAspectLock(type);
  const base: UIElement = {
    id,
    type,
    parent: null,
    aspect_lock: aspect,
    style: {},
    bindings: {},
  };

  switch (type) {
    case "button":
      return { ...base, label: "Button" };
    case "label":
      return { ...base, text: "Label" };
    case "status_led":
      return { ...base, label: "Status" };
    case "slider":
      return {
        ...base,
        label: "Slider",
        min: 0,
        max: 100,
        step: 1,
        scale_to_full: true,
        response: "linear",
      };
    case "page_nav":
      return { ...base, label: "Next Page", target_page: "" };
    case "select":
      return {
        ...base,
        label: "Select",
        options: [
          { label: "Option 1", value: "option_1" },
          { label: "Option 2", value: "option_2" },
        ],
      };
    case "text_input":
      return { ...base, label: "Input", placeholder: "Type here..." };
    case "image":
      return { ...base, label: "" };
    case "camera_preset":
      return { ...base, label: "Preset", preset_number: 1 };
    case "gauge":
      return {
        ...base,
        label: "Gauge",
        min: 0,
        max: 100,
        unit: "%",
        arc_angle: 240,
        style: { gauge_width: 8, show_value: true, show_ticks: true, tick_count: 5 },
      };
    case "level_meter":
      return {
        ...base,
        label: "Level",
        min: -60,
        max: 0,
        orientation: "vertical",
        style: { meter_segments: 20, show_peak: true, peak_hold_ms: 1500 },
      };
    case "fader":
      return {
        ...base,
        label: "Fader",
        min: 0,
        max: 100,
        step: 1,
        unit: "%",
        scale_to_full: true,
        response: "linear",
        orientation: "vertical",
        style: { show_value: true, show_scale: true },
      };
    case "group":
      return { ...base, label: "Container", label_position: "top-left" };
    case "clock":
      return { ...base, clock_mode: "time" };
    case "keypad":
      return {
        ...base,
        label: "Keypad",
        digits: 4,
        auto_send: false,
        keypad_style: "numeric",
        show_display: true,
      };
    case "list":
      return {
        ...base,
        label: "Sources",
        list_style: "selectable",
        item_height: 44 / 14,
        items: [
          { label: "Item 1", value: "1" },
          { label: "Item 2", value: "2" },
          { label: "Item 3", value: "3" },
        ],
      };
    case "matrix":
      return {
        ...base,
        label: "Video Routing",
        matrix_config: {
          input_count: 4,
          output_count: 4,
          input_labels: ["Input 1", "Input 2", "Input 3", "Input 4"],
          output_labels: ["Output 1", "Output 2", "Output 3", "Output 4"],
          route_key_pattern: "",
        },
        matrix_style: "crosspoint",
        style: { cell_size: 44 / 14 },
      };
    default:
      // Plugin element: type is "plugin:<plugin_id>:<plugin_type>"
      if (type.startsWith("plugin:")) {
        const parts = type.split(":");
        const pluginId = parts[1];
        const pluginType = parts.slice(2).join(":");
        return {
          ...base,
          type: "plugin",
          label: pluginType,
          plugin_type: pluginType,
          plugin_id: pluginId,
          plugin_config: {},
        };
      }
      return { ...base, label: type };
  }
}

// --- Style units: the px the author types, the rem the panel stores ---
//
// Every stored measurement on an element is `rem`, because the panel's type
// scale is the panel's size (1rem = 1.75vmin) and that is what makes a control
// look the same on a 7" tablet and a 21" wall panel. But nobody designs in rem:
// an integrator asks for a 24px label, not a 1.7143rem one.
//
// So the editors keep speaking px and convert at the boundary, which is exactly
// what the 0.8.0 migration did to every existing project (px / 14). Get this
// wrong in one direction and a typed 24 renders at 336px; get it wrong in the
// other and every panel in the field silently shrinks by a factor of fourteen.

/** 1rem at the 1280x800 reference, where `1.75vmin` resolves to 14px.
 *
 *  Re-exported rather than declared here: the panel's rem base is one fact, and
 *  the measured control floors are expressed against it, so it comes from the
 *  same generated table they do. */
export { REM_BASE_PX };

/**
 * Style keys the panel renders as `rem`.
 *
 * Everything else in `style` is a colour, a keyword, a unitless multiplier
 * (`line_height`), a count (`meter_segments`, `tick_count`), a duration
 * (`peak_hold_ms`) or an SVG viewBox width (`gauge_width`) -- none of which are
 * lengths, and none of which convert.
 */
export const REM_STYLE_KEYS: ReadonlySet<string> = new Set([
  "font_size",
  "border_radius",
  "border_width",
  "padding",
  "padding_vertical",
  "padding_horizontal",
  "margin",
  "margin_vertical",
  "margin_horizontal",
  "letter_spacing",
  "cell_size",
]);

/** Top-level element fields the panel renders as `rem`. */
export const REM_ELEMENT_KEYS: ReadonlySet<string> = new Set([
  "icon_size",
  "item_height",
  "thumb_size",
]);

/** Theme variables that are measurements rather than colours. */
export const REM_THEME_VARIABLE_KEYS: ReadonlySet<string> = new Set(["border_radius"]);

/** True when this key's value is a length the panel will suffix with `rem`. */
export function isRemStyleKey(key: string): boolean {
  return REM_STYLE_KEYS.has(key) || REM_ELEMENT_KEYS.has(key);
}

/** A stored rem value as the px an author recognises, at the reference size. */
export function remToPx(rem: number | string | null | undefined): number | null {
  if (rem === "" || rem == null) return null;
  const n = typeof rem === "number" ? rem : Number(rem);
  if (!Number.isFinite(n)) return null;
  // Round to 2dp so a value that was authored in whole px comes back as whole
  // px rather than 23.999999999999996.
  return Math.round(n * REM_BASE_PX * 100) / 100;
}

/** The px an author typed, as the rem the panel stores. */
export function pxToRem(px: number | string | null | undefined): number | null {
  if (px === "" || px == null) return null;
  const n = typeof px === "number" ? px : Number(px);
  if (!Number.isFinite(n)) return null;
  return Math.round((n / REM_BASE_PX) * 10 ** GEOMETRY_PRECISION) / 10 ** GEOMETRY_PRECISION;
}

/** Read a style/element field for display: px if it is a length, else as-is. */
export function displayStyleValue(key: string, value: unknown): unknown {
  if (!isRemStyleKey(key) || typeof value !== "number") return value;
  return remToPx(value);
}

/** Write a style/element field from an editor: px in, rem out for lengths. */
export function storeStyleValue(key: string, value: unknown): unknown {
  if (!isRemStyleKey(key) || typeof value !== "number") return value;
  return pxToRem(value);
}

// --- Percentage geometry ---
//
// Geometry is a percentage of the parent box -- the page, or the container an
// element names as its parent. Free-form and floating point: there are no cells
// to land in and nothing clamps a control to a track. The grid that used to BE
// the model is now a ruler you can change or switch off without moving a thing.

/**
 * Percentages are stored to 4 decimal places, at every write path.
 *
 * Without a canonical rounding, px<->% round-trips jitter in the last digits
 * (12.500001 != 12.5), and that jitter dirties the save-reconcile diff and arms
 * phantom autosaves on a panel nobody touched.
 */
export const GEOMETRY_PRECISION = 4;

export function roundPct(value: number): number {
  const factor = 10 ** GEOMETRY_PRECISION;
  return Math.round(value * factor) / factor;
}

/** Round a whole box in one go. */
export function roundPlacement(p: Placement): Placement {
  return { x: roundPct(p.x), y: roundPct(p.y), w: roundPct(p.w), h: roundPct(p.h) };
}

/** The snap increment a page falls back to: the old 12x8 grid's spacing. */
export const SNAP_FALLBACK: SnapConfig = { enabled: true, x: 100 / 12, y: 100 / 8 };

/** Where a placement lands when the page has never heard of the element. */
export const DEFAULT_PLACEMENT: Placement = { x: 0, y: 0, w: 25, h: 12.5 };

/**
 * How close (in percent) an edge has to be before element-edge magnetism grabs
 * it. Roughly a fifth of a default snap cell, so the grid still wins in open
 * space and edges win when you are genuinely near one.
 */
export const MAGNET_TOLERANCE = 1.5;

/** The page's snap setting, with the old grid's spacing as the fallback. */
export function pageSnap(page: Pick<UIPage, "snap"> | undefined): SnapConfig {
  const s = page?.snap;
  if (!s) return { ...SNAP_FALLBACK };
  return {
    enabled: s.enabled !== false,
    x: typeof s.x === "number" && s.x > 0 ? s.x : SNAP_FALLBACK.x,
    y: typeof s.y === "number" && s.y > 0 ? s.y : SNAP_FALLBACK.y,
  };
}

/**
 * The layout the builder authors into: the page's primary.
 *
 * Layout variants (a portrait arrangement of the same controls) are authored
 * through a switcher of their own; every other edit path means "the layout I am
 * looking at", and until one is picked that is the primary.
 */
export function primaryLayout(page: UIPage | undefined): Layout | undefined {
  const layouts = page?.layouts ?? [];
  return layouts.find((l) => l.primary) ?? layouts[0];
}

/** A page's layout by id, or its primary when the id is unknown. */
export function layoutById(page: UIPage, layoutId?: string | null): Layout | undefined {
  if (layoutId) {
    const found = page.layouts?.find((l) => l.id === layoutId);
    if (found) return found;
  }
  return primaryLayout(page);
}

/**
 * The layouts that feed a chosen one, base first, so whatever the chosen layout
 * says for itself wins over what it inherits.
 *
 * The seen-set is a cycle guard -- a hand-edited project can point two layouts
 * at each other and the builder still has to draw something. Mirrors the panel
 * runtime's _selectLayout, which walks the same chain for the same reason.
 */
export function layoutChain(page: UIPage, layoutId?: string | null): Layout[] {
  const layouts = page.layouts ?? [];
  const chosen = layoutById(page, layoutId);
  if (!chosen) return [];
  const chain: Layout[] = [];
  const seen = new Set<string>();
  let cursor: Layout | undefined = chosen;
  while (cursor && !seen.has(cursor.id)) {
    seen.add(cursor.id);
    chain.unshift(cursor);
    cursor = cursor.inherits ? layouts.find((l) => l.id === cursor!.inherits) : undefined;
  }
  return chain;
}

/**
 * Fold an inherits chain down to one placement map. This is what makes a
 * variant deltas: it only stores what moved, and everything it stays quiet
 * about is answered by the layout it inherits from.
 */
export function resolvePlacements(
  page: UIPage,
  layoutId?: string | null,
): Record<string, Placement> {
  const placements: Record<string, Placement> = {};
  for (const layout of layoutChain(page, layoutId)) {
    Object.assign(placements, layout.placements ?? {});
  }
  return placements;
}

/** One element's box on a page, or a sane default when it has never been placed. */
export function getPlacement(
  page: UIPage,
  elementId: string,
  layoutId?: string | null,
): Placement {
  const p = resolvePlacements(page, layoutId)[elementId];
  return p ? { x: p.x, y: p.y, w: p.w, h: p.h } : { ...DEFAULT_PLACEMENT };
}

/** Write one element's box into a page's layout (rounded on the way in). */
export function withPlacement(
  page: UIPage,
  elementId: string,
  placement: Placement,
  layoutId?: string | null,
): UIPage {
  return withPlacements(page, { [elementId]: placement }, layoutId);
}

/** Write several boxes at once -- one store mutation for a multi-select drag. */
export function withPlacements(
  page: UIPage,
  placements: Record<string, Placement>,
  layoutId?: string | null,
): UIPage {
  const target = layoutById(page, layoutId);
  if (!target) return page;
  const rounded: Record<string, Placement> = {};
  for (const [id, p] of Object.entries(placements)) rounded[id] = roundPlacement(p);
  return {
    ...page,
    layouts: (page.layouts ?? []).map((l) =>
      l.id === target.id ? { ...l, placements: { ...l.placements, ...rounded } } : l,
    ),
  };
}

/** Drop an element's box from every layout -- used when the element is deleted. */
export function withoutPlacement(page: UIPage, elementId: string): UIPage {
  return {
    ...page,
    layouts: (page.layouts ?? []).map((l) => {
      if (!l.placements || !(elementId in l.placements)) {
        return l.hidden?.includes(elementId)
          ? { ...l, hidden: l.hidden.filter((id) => id !== elementId) }
          : l;
      }
      const next = { ...l.placements };
      delete next[elementId];
      return { ...l, placements: next, hidden: (l.hidden ?? []).filter((id) => id !== elementId) };
    }),
  };
}

// --- Layout variants ---
//
// A page holds one set of controls and one or more arrangements of them. The
// primary is the arrangement everything falls back to; a variant says only what
// moved. Everything below keeps that promise: a variant is written to sparsely,
// read through its inherits chain, and removable without touching the primary.

export type Orientation = "landscape" | "portrait";

export const ORIENTATIONS: Orientation[] = ["landscape", "portrait"];

/** The shape being authored: the active layout's orientation, or the primary's. */
export function layoutOrientation(
  page: UIPage | undefined,
  layoutId?: string | null,
): Orientation {
  if (!page) return "landscape";
  return layoutById(page, layoutId)?.orientation ?? "landscape";
}

/** Orientations this page has no arrangement for yet -- what "add" can offer. */
export function missingOrientations(page: UIPage | undefined): Orientation[] {
  const have = new Set((page?.layouts ?? []).map((l) => l.orientation));
  return ORIENTATIONS.filter((o) => !have.has(o));
}

/**
 * Add an arrangement for an orientation this page does not have one for.
 *
 * It starts as pure deltas: no placements at all, inheriting the primary. So the
 * moment it is created it looks exactly like the primary, and every control you
 * leave alone keeps following the primary forever. Only what you actually move
 * gets stored here.
 */
export function addLayout(page: UIPage, orientation: Orientation): UIPage {
  const layouts = page.layouts ?? [];
  const primary = primaryLayout(page);
  const taken = new Set(layouts.map((l) => l.id));
  let id: string = orientation;
  let counter = 1;
  while (taken.has(id)) {
    counter++;
    id = `${orientation}_${counter}`;
  }
  const variant: Layout = {
    id,
    orientation,
    primary: false,
    inherits: primary ? primary.id : null,
    placements: {},
    hidden: [],
  };
  return { ...page, layouts: [...layouts, variant] };
}

/**
 * Drop an arrangement. The primary is not removable -- it is what an unmatched
 * screen falls back to, so a page without one has no answer at all.
 *
 * Anything that inherited FROM the removed layout inherits what *it* inherited,
 * so a chain can never be left pointing at a layout that is gone.
 */
export function removeLayout(page: UIPage, layoutId: string): UIPage {
  const layouts = page.layouts ?? [];
  const target = layouts.find((l) => l.id === layoutId);
  if (!target || target.primary) return page;
  return {
    ...page,
    layouts: layouts
      .filter((l) => l.id !== layoutId)
      .map((l) => (l.inherits === layoutId ? { ...l, inherits: target.inherits ?? null } : l)),
  };
}

/**
 * Everything hidden in this arrangement, inherited hides included.
 *
 * Hiding accumulates down the chain the same way the panel runtime does it: a
 * variant can hide something the primary shows, but it cannot un-hide something
 * the primary hid -- you would go and un-hide it there. The properties panel
 * says which layout a hide came from so that is visible rather than mysterious.
 */
export function resolveHidden(page: UIPage, layoutId?: string | null): Set<string> {
  const hidden = new Set<string>();
  for (const layout of layoutChain(page, layoutId)) {
    for (const id of layout.hidden ?? []) hidden.add(id);
  }
  return hidden;
}

/** Hidden by THIS layout specifically, rather than by something it inherits. */
export function isHiddenInLayout(
  page: UIPage,
  elementId: string,
  layoutId?: string | null,
): boolean {
  return (layoutById(page, layoutId)?.hidden ?? []).includes(elementId);
}

/** Hide or show one element in one arrangement, leaving every other alone. */
export function withHidden(
  page: UIPage,
  elementId: string,
  hidden: boolean,
  layoutId?: string | null,
): UIPage {
  const target = layoutById(page, layoutId);
  if (!target) return page;
  return {
    ...page,
    layouts: (page.layouts ?? []).map((l) => {
      if (l.id !== target.id) return l;
      const current = l.hidden ?? [];
      const has = current.includes(elementId);
      if (hidden === has) return l;
      return {
        ...l,
        hidden: hidden ? [...current, elementId] : current.filter((id) => id !== elementId),
      };
    }),
  };
}

/**
 * A master element's box for an orientation.
 *
 * Masters carry their own orientation-keyed placements rather than living in any
 * page's layouts, because they render across pages that can be arranged
 * differently. Mirrors the panel runtime's _masterPlacement, fallbacks included.
 */
export function masterPlacement(
  master: Pick<MasterElement, "placements"> | undefined,
  orientation: Orientation,
): Placement | null {
  const p = master?.placements ?? {};
  return p[orientation] ?? p.landscape ?? p.portrait ?? Object.values(p)[0] ?? null;
}

/**
 * The canvas size for an arrangement.
 *
 * The screen presets are all landscape, and a portrait layout authored on a
 * landscape canvas is not something anyone can design in -- you would be placing
 * controls for a shape you cannot see. So the preset follows the layout: same
 * screen, turned. vmin is the shorter edge either way, so turning the canvas
 * does not resize a single glyph.
 */
export function presetForOrientation(
  preset: { width: number; height: number } | undefined,
  orientation: Orientation,
): { width: number; height: number } {
  const width = preset?.width ?? 1024;
  const height = preset?.height ?? 600;
  const long = Math.max(width, height);
  const short = Math.min(width, height);
  return orientation === "portrait"
    ? { width: short, height: long }
    : { width: long, height: short };
}

/**
 * Map a pointer coordinate to a percentage of the box it fell in.
 *
 * The page carries no padding any more (the grid's gutter died with the grid),
 * so this is the whole rect edge to edge -- the pointer lands exactly where the
 * pointer is.
 */
export function pointerToPercent(
  pointerPx: number,
  rectStart: number,
  rectLength: number,
): number {
  if (!(rectLength > 0)) return 0;
  return ((pointerPx - rectStart) / rectLength) * 100;
}

/** Round a value onto the nearest multiple of an increment. */
export function snapToIncrement(value: number, increment: number): number {
  if (!(increment > 0)) return value;
  return Math.round(value / increment) * increment;
}

// --- Magnetic snapping ---

/** What a gesture can be pulled toward, and what it takes to suspend it. */
export interface SnapContext {
  /** The page's snap increment. `enabled: false` leaves only edge magnetism. */
  snap: SnapConfig;
  /** Alt/Option held: suspend snapping entirely for this gesture, grid AND
   *  element-edge magnetism, so the control lands on the exact pointer. */
  bypass?: boolean;
  /** Sibling boxes to be magnetic against (same parent, moving ones excluded). */
  others?: Placement[];
}

/** A snapped gesture: the adjusted box, plus the guides worth drawing. */
export interface SnapOutcome {
  placement: Placement;
  /** Percent offsets where a vertical guide line should be drawn. */
  guidesX: number[];
  /** Percent offsets where a horizontal guide line should be drawn. */
  guidesY: number[];
}

/** Every line a moving edge can be attracted to on one axis: the page edges
 *  and centre, plus every sibling's edges and centre. Deliberately NOT the
 *  thirds — on an otherwise empty page they read as snapping to nothing. */
function magnetTargets(others: Placement[], axis: "x" | "y"): number[] {
  const targets = [0, 50, 100];
  for (const o of others) {
    const start = axis === "x" ? o.x : o.y;
    const size = axis === "x" ? o.w : o.h;
    targets.push(start, start + size / 2, start + size);
  }
  return targets;
}

/**
 * Pull one moving edge onto the nearest attractive line.
 *
 * Element edges, page edges, centre and thirds win when they are within
 * MAGNET_TOLERANCE, because being flush with a neighbour is what the author
 * meant. Failing that the snap increment takes it, which is a coarser pull with
 * no tolerance -- there is always a nearest increment.
 */
function snapAxis(
  edges: number[],
  increment: number,
  gridOn: boolean,
  targets: number[],
): { delta: number; guides: number[] } {
  let best: { delta: number; guide: number } | null = null;
  for (const edge of edges) {
    for (const target of targets) {
      const delta = target - edge;
      if (Math.abs(delta) > MAGNET_TOLERANCE) continue;
      if (!best || Math.abs(delta) < Math.abs(best.delta)) best = { delta, guide: target };
    }
  }
  if (best) {
    // Every edge that ends up on an attractive line gets a guide, so a flush
    // top AND a flush left both draw.
    const moved = edges.map((e) => e + best!.delta);
    const guides = targets.filter((t) => moved.some((m) => Math.abs(m - t) < 1e-6));
    return { delta: best.delta, guides: [...new Set(guides)] };
  }
  if (gridOn) {
    let gridBest: number | null = null;
    for (const edge of edges) {
      const delta = snapToIncrement(edge, increment) - edge;
      if (gridBest === null || Math.abs(delta) < Math.abs(gridBest)) gridBest = delta;
    }
    if (gridBest !== null) return { delta: gridBest, guides: [] };
  }
  return { delta: 0, guides: [] };
}

/**
 * Snap a box that is being MOVED: the size is fixed, so both edges and the
 * centre travel together and whichever of them is nearest something decides.
 */
export function snapMove(rect: Placement, ctx: SnapContext): SnapOutcome {
  if (ctx.bypass) return { placement: roundPlacement(rect), guidesX: [], guidesY: [] };
  const snap = ctx.snap;
  const others = ctx.others ?? [];
  const gridOn = snap.enabled !== false;

  const xr = snapAxis(
    [rect.x, rect.x + rect.w / 2, rect.x + rect.w],
    snap.x,
    gridOn,
    magnetTargets(others, "x"),
  );
  const yr = snapAxis(
    [rect.y, rect.y + rect.h / 2, rect.y + rect.h],
    snap.y,
    gridOn,
    magnetTargets(others, "y"),
  );

  return {
    placement: roundPlacement({ ...rect, x: rect.x + xr.delta, y: rect.y + yr.delta }),
    guidesX: xr.guides,
    guidesY: yr.guides,
  };
}

/**
 * Snap a box that is being RESIZED. Only the edges the handle actually drags
 * are attracted -- an east drag must not pull the west edge along with it.
 */
export function snapResize(
  rect: Placement,
  direction: string,
  ctx: SnapContext,
): SnapOutcome {
  if (ctx.bypass) return { placement: roundPlacement(rect), guidesX: [], guidesY: [] };
  const snap = ctx.snap;
  const others = ctx.others ?? [];
  const gridOn = snap.enabled !== false;
  const next = { ...rect };
  let guidesX: number[] = [];
  let guidesY: number[] = [];

  if (direction.includes("w")) {
    const r = snapAxis([next.x], snap.x, gridOn, magnetTargets(others, "x"));
    next.x += r.delta;
    next.w -= r.delta;
    guidesX = r.guides;
  } else if (direction.includes("e")) {
    const r = snapAxis([next.x + next.w], snap.x, gridOn, magnetTargets(others, "x"));
    next.w += r.delta;
    guidesX = r.guides;
  }
  if (direction.includes("n")) {
    const r = snapAxis([next.y], snap.y, gridOn, magnetTargets(others, "y"));
    next.y += r.delta;
    next.h -= r.delta;
    guidesY = r.guides;
  } else if (direction.includes("s")) {
    const r = snapAxis([next.y + next.h], snap.y, gridOn, magnetTargets(others, "y"));
    next.h += r.delta;
    guidesY = r.guides;
  }

  return { placement: roundPlacement(next), guidesX, guidesY };
}

/**
 * Where a palette drag sits right now, and lands when released: the new
 * element centred under the pointer, then snapped exactly like a move. The
 * live preview and the drop both call this, so what the guides show IS the
 * landing spot.
 */
export function paletteDragPlacement(
  centre: { x: number; y: number },
  size: { w: number; h: number },
  ctx: SnapContext,
): SnapOutcome {
  return snapMove(
    { x: centre.x - size.w / 2, y: centre.y - size.h / 2, w: size.w, h: size.h },
    ctx,
  );
}

// --- Auto-placement (the pointerless paths) ---

/** The smallest box a resize or an auto-place will produce, in percent. */
export const MIN_ELEMENT_SIZE = 1;

/** Each successive cascade step, in percent, when there is no grid to scan. */
const CASCADE_STEP = 2.5;

/** True when two boxes share any area at all. */
export function placementsOverlap(a: Placement, b: Placement): boolean {
  return (
    a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y
  );
}

/**
 * Where a control lands when nobody pointed at anything -- click-to-add from
 * the palette, duplicate, paste.
 *
 * With snap on this is the first free snap cell scanning row-major, which is
 * exactly what the old grid did for free, so click-to-add is visually unchanged
 * by the whole move to percentages. With snap off "first free cell" is
 * meaningless, so it falls back to the page centre plus a cascade -- the
 * down-right nudge every design tool uses for a paste.
 */
export function autoPlace(
  occupied: Placement[],
  size: { w: number; h: number },
  snap: SnapConfig,
  cascadeIndex = 0,
): Placement {
  const w = Math.max(MIN_ELEMENT_SIZE, size.w);
  const h = Math.max(MIN_ELEMENT_SIZE, size.h);

  if (snap.enabled !== false && snap.x > 0 && snap.y > 0) {
    const cols = Math.max(1, Math.floor(100 / snap.x));
    const rows = Math.max(1, Math.floor(100 / snap.y));
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const x = c * snap.x;
        const y = r * snap.y;
        if (x + w > 100 + 1e-6 || y + h > 100 + 1e-6) continue;
        const candidate = { x, y, w, h };
        if (!occupied.some((o) => placementsOverlap(candidate, o))) {
          return roundPlacement(candidate);
        }
      }
    }
  }

  // Nothing free (or no grid to scan): page centre, nudged down-right per add,
  // wrapped so a long cascade can't march an element off the page.
  const offset = (cascadeIndex * CASCADE_STEP) % 20;
  return roundPlacement({
    x: Math.max(0, Math.min(100 - w, 50 - w / 2 + offset)),
    y: Math.max(0, Math.min(100 - h, 50 - h / 2 + offset)),
    w,
    h,
  });
}

/** How many elements already sit at (near) this spot -- drives the cascade. */
export function cascadeIndexFor(occupied: Placement[]): number {
  return occupied.length;
}

/**
 * Which container a box dropped at page coordinates belongs to, and what its
 * coordinates are once it is in there. The palette's half of `dropTargetFor`:
 * same rule, answered as a box.
 */
export function resolveDropParent(
  page: UIPage,
  box: Placement,
  layoutId?: string | null,
  elementId = "",
): { parentId: string | null; relative: Placement } {
  const parentId = dropTargetFor(page, elementId, box, layoutId);
  // Page space, not stored percentages: a container inside another container is
  // stored as a fraction of ITS parent, so comparing that to a pointer's page
  // coordinate would test the wrong rectangle the moment containers nest.
  const base = parentId ? absolutePlacements(page, layoutId)[parentId] : null;
  if (!base || !(base.w > 0) || !(base.h > 0)) {
    return { parentId: null, relative: roundPlacement(box) };
  }
  return {
    parentId,
    relative: roundPlacement({
      x: ((box.x - base.x) / base.w) * 100,
      y: ((box.y - base.y) / base.h) * 100,
      w: (box.w / base.w) * 100,
      h: (box.h / base.h) * 100,
    }),
  };
}

// --- Page mutations (return new pages array) ---

export function addPage(
  pages: UIPage[],
  pageType: "page" | "overlay" | "sidebar" = "page",
): UIPage[] {
  const ids = new Set(pages.map((p) => p.id));
  const names = new Set(pages.map((p) => p.name));
  const prefix = pageType === "page" ? "page" : pageType;
  const label = pageType === "page" ? "Page" : pageType === "overlay" ? "Overlay" : "Sidebar";
  let counter = 1;
  let id = `${prefix}_${counter}`;
  while (ids.has(id)) {
    counter++;
    id = `${prefix}_${counter}`;
  }
  let nameCounter = counter;
  let name = `${label} ${nameCounter}`;
  while (names.has(name)) {
    nameCounter++;
    name = `${label} ${nameCounter}`;
  }

  const newPage: UIPage = {
    id,
    name,
    // An overlay is a smaller canvas, so it gets a coarser ruler -- the old
    // 4x4 overlay grid's spacing, the same way a page gets the old 12x8's.
    snap: pageType === "page"
      ? { enabled: true, ...{ x: 100 / 12, y: 100 / 8 } }
      : { enabled: true, x: 25, y: 25 },
    elements: [],
    layouts: [
      { id: "landscape", orientation: "landscape", primary: true, placements: {}, hidden: [] },
    ],
  };

  if (pageType === "overlay") {
    newPage.page_type = "overlay";
    newPage.overlay = {
      // Percentages of the viewport now, not raw px: 400x300 on the 1280x800
      // reference is what an overlay has always looked like.
      width: 31.25,
      height: 37.5,
      position: "center",
      backdrop: "dim",
      dismiss_on_backdrop: true,
      animation: "fade",
    };
  } else if (pageType === "sidebar") {
    newPage.page_type = "sidebar";
    newPage.overlay = {
      width: 25,
      side: "right",
      backdrop: "dim",
      dismiss_on_backdrop: true,
      animation: "slide-left",
    };
  }

  return [...pages, newPage];
}

/** The `do.<interaction>` keys that hold action lists. Authored as arrays of
 *  action objects; legacy projects may carry a single object. Matrix sends one
 *  ui.route event the server demuxes into the four route slots — all four are
 *  author-time interactions. */
const ACTION_SLOTS = [
  "press", "release", "hold", "change", "submit", "select",
  "route", "audio_route", "mute_route", "audio_mute_route",
] as const;

/**
 * Normalize one `do.<interaction>` entry to an array of action objects.
 * Interactions are authored as arrays (multiple actions per touch); legacy
 * projects may still carry a single action object — the panel runtime accepts
 * both. Pass the element's `do` map (`bindings.do`), not the whole bindings.
 */
export function slotActions(
  doMap: Record<string, unknown> | undefined,
  slot: string,
): Record<string, unknown>[] {
  const raw = doMap?.[slot];
  if (!raw || typeof raw !== "object") return [];
  if (Array.isArray(raw)) {
    return raw.filter((a) => a && typeof a === "object") as Record<string, unknown>[];
  }
  const obj = raw as Record<string, unknown>;
  return Object.keys(obj).length > 0 ? [obj] : [];
}

/** True when any action in the list issues a `device.command` — including the
 *  per-option actions inside a `value_map` (how a `select` drives a device:
 *  each chosen option maps to its own command). The engine and the AI validator
 *  treat a value_map's branches the same way, so the editor's "does this control
 *  reach the device?" check must look inside the map too, or a correct
 *  source-selector dropdown looks unwired. */
export function actionsCommandDevice(actions: Record<string, unknown>[]): boolean {
  const isDeviceCommand = (a: unknown) =>
    !!a && typeof a === "object" && (a as Record<string, unknown>).action === "device.command";
  return actions.some((a) => {
    if (isDeviceCommand(a)) return true;
    if (a.action === "value_map" && a.map && typeof a.map === "object") {
      return Object.values(a.map as Record<string, unknown>).some((mapped) => {
        const subs = Array.isArray(mapped) ? mapped : [mapped];
        return subs.some(isDeviceCommand);
      });
    }
    return false;
  });
}

/** Reference ids an action can dangle against. */
export interface BindingRefIds {
  deviceIds: ReadonlySet<string>;
  macroIds: ReadonlySet<string>;
  pageIds: ReadonlySet<string>;
}

/** First broken reference in an action — a device/macro/page id that no
 *  longer exists. A value_map runs the per-option action branches, so those
 *  are checked here too — AI tools and imports emit them, and a Broken badge
 *  that misses them lets a dead reference look fine. */
export function actionDanglingRef(
  a: Record<string, unknown>,
  ids: BindingRefIds,
): string | null {
  const act = a.action;
  if (act === "device.command" && a.device && !ids.deviceIds.has(a.device as string)) {
    return `Device "${a.device}" not found`;
  }
  if (act === "macro" && a.macro && !ids.macroIds.has(a.macro as string)) {
    return `Macro "${a.macro}" not found`;
  }
  if (act === "ui.navigate" && a.page && !ids.pageIds.has(a.page as string)) {
    return `Page "${a.page}" not found`;
  }
  if (act === "value_map" && a.map && typeof a.map === "object") {
    for (const branch of Object.values(a.map as Record<string, unknown>)) {
      const subs = Array.isArray(branch) ? branch : [branch];
      for (const sub of subs) {
        if (sub && typeof sub === "object") {
          const d = actionDanglingRef(sub as Record<string, unknown>, ids);
          if (d) return d;
        }
      }
    }
  }
  return null;
}

/** True when an action is missing the field it can't run without — the
 *  runtime silently skips these (e.g. script.call only fires when `function`
 *  is set), so the editor badges them Incomplete instead. Descends into a
 *  value_map's per-option branches like the runtime does. */
export function actionIncompleteCheck(a: Record<string, unknown>): boolean {
  const act = a.action;
  if (act === "device.command") return !a.device || !a.command;
  if (act === "macro") return !a.macro;
  if (act === "state.set") return !a.key;
  if (act === "ui.navigate") return !a.page;
  if (act === "script.call") return !a.function;
  if (act === "value_map") {
    const map = a.map as Record<string, unknown> | undefined;
    if (!map || Object.keys(map).length === 0) return true;
    return Object.values(map).some((branch) => {
      const subs = Array.isArray(branch) ? branch : [branch];
      return subs.some(
        (sub) => !!sub && typeof sub === "object" && actionIncompleteCheck(sub as Record<string, unknown>),
      );
    });
  }
  return !a.action;
}

/** Remove navigate actions targeting a deleted page from every `do.<interaction>`
 *  action list. */
function scrubNavigateActions(el: UIElement, pageId: string): UIElement {
  const bindings = el.bindings as Record<string, unknown> | undefined;
  const doMap = bindings?.do as Record<string, unknown> | undefined;
  if (!doMap) return el;
  const isDeadNavigate = (a: unknown) =>
    !!a && typeof a === "object" &&
    (a as Record<string, unknown>).action === "ui.navigate" &&
    (a as Record<string, unknown>).page === pageId;

  let changed = false;
  const nextDo: Record<string, unknown> = { ...doMap };
  for (const slot of ACTION_SLOTS) {
    const raw = nextDo[slot];
    if (!raw || typeof raw !== "object") continue;
    if (Array.isArray(raw)) {
      const filtered = raw.filter((a) => !isDeadNavigate(a));
      if (filtered.length !== raw.length) {
        changed = true;
        if (filtered.length > 0) nextDo[slot] = filtered;
        else delete nextDo[slot];
      }
    } else if (isDeadNavigate(raw)) {
      // Legacy single-object binding
      changed = true;
      delete nextDo[slot];
    }
  }
  return changed
    ? { ...el, bindings: { ...bindings, do: nextDo } as UIElement["bindings"] }
    : el;
}

export function removePage(pages: UIPage[], pageId: string): UIPage[] {
  // Filter out the page, then clean up dangling references to it
  return pages
    .filter((p) => p.id !== pageId)
    .map((p) => ({
      ...p,
      elements: p.elements.map((el) => {
        let updated = el;
        // Clear page_nav target_page if it pointed to the deleted page
        if (el.type === "page_nav" && el.target_page === pageId) {
          updated = { ...updated, target_page: "" };
        }
        // Drop navigate actions pointing at the deleted page from every
        // action slot (press/release/hold/change/submit), array or legacy
        // single-object shape alike
        updated = scrubNavigateActions(updated, pageId);
        return updated;
      }),
    }));
}

export function removePageAndScrubRefs(
  pages: UIPage[],
  pageId: string,
  masterElements: MasterElement[],
  macros: MacroConfig[],
): {
  pages: UIPage[];
  masterElements: MasterElement[];
  macros: MacroConfig[];
} {
  const newPages = removePage(pages, pageId);

  // Scrub master_elements.pages arrays that reference this page. Returns the
  // ORIGINAL array when nothing referenced the page — callers identity-check
  // the result to snapshot only the sections a delete actually changes.
  const newMasters = masterElements.map((m) => {
    if (m.pages === "*" || !Array.isArray(m.pages)) return m;
    const filtered = (m.pages as string[]).filter((pid) => pid !== pageId);
    if (filtered.length === m.pages.length) return m;
    return { ...m, pages: filtered.length > 0 ? filtered : "*" };
  });
  const mastersChanged = newMasters.some((m, i) => m !== masterElements[i]);

  // Scrub trigger conditions that match on the deleted page
  const newMacros = macros.map((macro) => {
    if (!macro.triggers) return macro;
    let changed = false;
    const newTriggers = macro.triggers.map((trigger) => {
      if (!trigger.conditions) return trigger;
      const filtered = trigger.conditions.filter(
        (c) => !(c.key === "system.current_page" && c.value === pageId),
      );
      if (filtered.length !== trigger.conditions.length) {
        changed = true;
        return { ...trigger, conditions: filtered };
      }
      return trigger;
    });
    return changed ? { ...macro, triggers: newTriggers } : macro;
  });
  const macrosChanged = newMacros.some((m, i) => m !== macros[i]);

  return {
    pages: newPages,
    masterElements: mastersChanged ? newMasters : masterElements,
    macros: macrosChanged ? newMacros : macros,
  };
}

// There is deliberately no successor to the old clampElementsToGrid. Changing
// the snap increment -- or switching it off -- moves nothing now, because the
// increment is a ruler rather than a container. That used to rewrite every
// element's coordinates to fit a shrinking grid, which is exactly the surprise
// percentage geometry exists to remove.

export function renamePage(
  pages: UIPage[],
  pageId: string,
  name: string,
): UIPage[] {
  return pages.map((p) => (p.id === pageId ? { ...p, name } : p));
}

// --- Element mutations (return new pages array) ---

/**
 * Add an element to a page.
 *
 * The box goes into the PRIMARY layout, whichever arrangement is being authored
 * at the time. A control exists in every layout the moment it exists at all, and
 * the primary is what the others inherit from -- write it into a variant instead
 * and the control would have no box anywhere else and fall back to the default
 * corner. Where the author actually dropped it is still honoured: the coordinate
 * handed in was measured against the layout on screen, and percentages of a
 * container mean the same thing in every arrangement of it.
 */
export function addElementToPage(
  pages: UIPage[],
  pageId: string,
  element: UIElement,
  placement?: Placement,
): UIPage[] {
  return pages.map((p) => {
    if (p.id !== pageId) return p;
    const withEl = { ...p, elements: [...p.elements, element] };
    return placement ? withPlacement(withEl, element.id, placement) : withEl;
  });
}

/**
 * Delete an element, its box in every layout, and -- if it was a container --
 * re-home whatever was living inside it.
 *
 * Children are kept rather than deleted with the parent: they are controls the
 * author wired up, and losing six of them to one wrong Delete is not a trade
 * anybody wants. They come back out to page level, keeping the position they
 * appeared to have, so nothing jumps.
 */
export function removeElementFromPage(
  pages: UIPage[],
  pageId: string,
  elementId: string,
): UIPage[] {
  return pages.map((p) => {
    if (p.id !== pageId) return p;
    const target = p.elements.find((e) => e.id === elementId);
    const children = p.elements.filter((e) => e.parent === elementId);

    let next: UIPage = { ...p, elements: p.elements.filter((e) => e.id !== elementId) };

    if (target && children.length) {
      const grandparent = target.parent ?? null;
      next = {
        ...next,
        elements: next.elements.map((e) =>
          e.parent === elementId ? { ...e, parent: grandparent } : e,
        ),
      };
      // Same conversion a reparent does, for the same reason: the children keep
      // the position they appeared to have, so nothing jumps when their
      // container goes away.
      next = reparentPlacements(p, next, children.map((c) => c.id), grandparent);
    }

    return withoutPlacement(next, elementId);
  });
}

export function updateElementInPage(
  pages: UIPage[],
  pageId: string,
  elementId: string,
  patch: Partial<UIElement>,
): UIPage[] {
  return pages.map((p) =>
    p.id === pageId
      ? {
          ...p,
          elements: p.elements.map((e) =>
            e.id === elementId ? { ...e, ...patch } : e,
          ),
        }
      : p,
  );
}

/** Move/resize one element by writing its box into the page's active layout. */
export function moveElementInPage(
  pages: UIPage[],
  pageId: string,
  elementId: string,
  placement: Placement,
  layoutId?: string | null,
): UIPage[] {
  return pages.map((p) =>
    p.id === pageId ? withPlacement(p, elementId, placement, layoutId) : p,
  );
}

/** Move/resize several elements in one mutation -- a multi-select drag. */
export function moveElementsInPage(
  pages: UIPage[],
  pageId: string,
  placements: Record<string, Placement>,
  layoutId?: string | null,
): UIPage[] {
  return pages.map((p) =>
    p.id === pageId ? withPlacements(p, placements, layoutId) : p,
  );
}

export function duplicateElementInPage(
  pages: UIPage[],
  pageId: string,
  elementId: string,
  reservedIds: string[] = [],
  layoutId?: string | null,
): UIPage[] {
  const page = pages.find((p) => p.id === pageId);
  if (!page) return pages;
  const element = page.elements.find((e) => e.id === elementId);
  if (!element) return pages;

  // Collect IDs from ALL pages to avoid cross-page collisions, plus any
  // reserved IDs (master_elements share the ui.<id> namespace) so a duplicate
  // can't be auto-named onto a master id.
  const existingIds = new Set(pages.flatMap((p) => p.elements.map((e) => e.id)));
  for (const id of reservedIds) existingIds.add(id);
  const newId = generateId(element.type, existingIds);

  // Place the copy down-right of the original, the nudge every design tool
  // uses, and pull it back inside the parent box if that would push it off.
  // Measured against the arrangement on screen, so the copy lands beside the
  // control the author is actually looking at; stored in the primary by
  // addElementToPage, because a copy is a new control and exists everywhere.
  const src = getPlacement(page, elementId, layoutId);
  const nudge = { x: src.x + 2.5, y: src.y + 2.5, w: src.w, h: src.h };
  if (nudge.x + nudge.w > 100) nudge.x = Math.max(0, 100 - nudge.w);
  if (nudge.y + nudge.h > 100) nudge.y = Math.max(0, 100 - nudge.h);

  // Rewrite self-referencing ui.<oldId>.* state keys (bindings, visibility)
  // to the duplicate's id — same machinery the rename path uses — so the
  // copy is wired to its own state, not the original's.
  const clone = JSON.parse(JSON.stringify(element)) as UIElement;
  const rewritten = rewriteElement(clone, element.id, newId);
  return addElementToPage(pages, pageId, rewritten, nudge);
}

export function reorderElement(
  pages: UIPage[],
  pageId: string,
  elementId: string,
  direction: "front" | "back",
): UIPage[] {
  return pages.map((p) => {
    if (p.id !== pageId) return p;
    const idx = p.elements.findIndex((e) => e.id === elementId);
    if (idx === -1) return p;
    const els = [...p.elements];
    const [el] = els.splice(idx, 1);
    if (direction === "front") {
      els.push(el);
    } else {
      els.unshift(el);
    }
    return { ...p, elements: els };
  });
}

// Swap two elements' positions in a page's element array (the array order IS
// the z-order). Callers pass the moving element and the neighbour it should
// trade places with — the OutlinePanel passes the visible neighbour UNDER THE
// SAME PARENT (`OutlineRow.prevSiblingId` / `nextSiblingId`), because z-order
// inside a container is position among its siblings and the row drawn above a
// child may belong to another container entirely. With a flat page and no
// search filter that reduces to a plain adjacent move, as it always was.
export function swapElementsInOrder(
  pages: UIPage[],
  pageId: string,
  elementId: string,
  neighborId: string,
): UIPage[] {
  return pages.map((p) => {
    if (p.id !== pageId) return p;
    const a = p.elements.findIndex((e) => e.id === elementId);
    const b = p.elements.findIndex((e) => e.id === neighborId);
    if (a === -1 || b === -1 || a === b) return p;
    const els = [...p.elements];
    [els[a], els[b]] = [els[b], els[a]];
    return { ...p, elements: els };
  });
}

// --- Page reordering ---

export function reorderPage(
  pages: UIPage[],
  pageId: string,
  direction: "left" | "right",
): UIPage[] {
  const idx = pages.findIndex((p) => p.id === pageId);
  if (idx === -1) return pages;
  const newIdx = direction === "left" ? idx - 1 : idx + 1;
  if (newIdx < 0 || newIdx >= pages.length) return pages;
  const result = [...pages];
  [result[idx], result[newIdx]] = [result[newIdx], result[idx]];
  return result;
}

// --- Page duplication ---

export function duplicatePage(
  pages: UIPage[],
  pageId: string,
  reservedIds: string[] = [],
): UIPage[] {
  const page = pages.find((p) => p.id === pageId);
  if (!page) return pages;

  const ids = new Set(pages.map((p) => p.id));
  const names = new Set(pages.map((p) => p.name));

  // Generate unique ID
  let newId = `${page.id}_copy`;
  let counter = 1;
  while (ids.has(newId)) {
    counter++;
    newId = `${page.id}_copy_${counter}`;
  }

  // Generate unique name
  let newName = `${page.name} (Copy)`;
  let nameCounter = 1;
  while (names.has(newName)) {
    nameCounter++;
    newName = `${page.name} (Copy ${nameCounter})`;
  }

  // Element ids must be unique across all pages AND the reserved ids
  // (master_elements share the ui.<id> namespace)
  const existingElementIds = new Set(
    pages.flatMap((p) => p.elements.map((e) => e.id)),
  );
  for (const id of reservedIds) existingElementIds.add(id);

  // First pass: assign every copied element its new id
  const idMap = new Map<string, string>();
  for (const el of page.elements) {
    let elId = `${el.type}_${newId}_1`;
    let c = 1;
    while (existingElementIds.has(elId)) {
      c++;
      elId = `${el.type}_${newId}_${c}`;
    }
    existingElementIds.add(elId);
    idMap.set(el.id, elId);
  }

  // Second pass: clone and rewrite ui.<id>.* references for EVERY old->new
  // pair, so both self-references and references to sibling elements on the
  // same page follow the copy instead of pointing back at the originals.
  const newElements = page.elements.map((el) => {
    let cloned = JSON.parse(JSON.stringify(el)) as UIElement;
    for (const [oldElId, newElId] of idMap) {
      cloned = rewriteElement(cloned, oldElId, newElId);
    }
    // The container hierarchy is keyed by element id too, and rewriteElement
    // only follows bindings -- remap it or every child dangles in the copy.
    if (cloned.parent && idMap.has(cloned.parent)) {
      cloned = { ...cloned, parent: idMap.get(cloned.parent)! };
    }
    return cloned;
  });

  // Geometry does not live on the elements: every arrangement keys its
  // placements and hidden list by element id, so the renames have to reach
  // them or the copy loses its whole layout to DEFAULT_PLACEMENT fallbacks.
  const clonedPage = JSON.parse(JSON.stringify(page)) as UIPage;
  const newLayouts = (clonedPage.layouts ?? []).map((l) => {
    const placements: Record<string, Placement> = {};
    for (const [elId, p] of Object.entries(l.placements ?? {})) {
      placements[idMap.get(elId) ?? elId] = p;
    }
    return {
      ...l,
      placements,
      hidden: (l.hidden ?? []).map((id) => idMap.get(id) ?? id),
    };
  });

  const newPage: UIPage = {
    ...clonedPage,
    id: newId,
    name: newName,
    elements: newElements,
    layouts: newLayouts,
  };

  // Insert after source page
  const idx = pages.findIndex((p) => p.id === pageId);
  const result = [...pages];
  result.splice(idx + 1, 0, newPage);
  return result;
}

// --- Alignment helpers ---

/**
 * Every element's box in PAGE percentages, with container nesting flattened.
 *
 * A child's stored percentages are of its container, so two elements under
 * different parents cannot be compared directly -- 20% wide means two different
 * widths on screen. The alignment tools all reason in this space and convert
 * back on the way out, because "line these up" means line them up where the eye
 * sees them, not where the numbers happen to agree.
 */
export function absolutePlacements(
  page: UIPage,
  layoutId?: string | null,
): Record<string, Placement> {
  const placements = resolvePlacements(page, layoutId);
  const byId = new Map(page.elements.map((e) => [e.id, e]));
  const out: Record<string, Placement> = {};

  const resolve = (id: string, seen: Set<string>): Placement | null => {
    const memo = out[id];
    if (memo) return memo;
    const p = placements[id];
    if (!p) return null;
    const named = byId.get(id)?.parent;
    // A parent that is missing, self-referential or already on the chain gets
    // treated as no parent -- a hand-edited project still has to draw.
    const parentId = named && named !== id && byId.has(named) && !seen.has(named) ? named : null;
    if (!parentId) {
      out[id] = { ...p };
      return out[id];
    }
    const base = resolve(parentId, new Set(seen).add(parentId));
    out[id] = base
      ? {
          x: base.x + (p.x / 100) * base.w,
          y: base.y + (p.y / 100) * base.h,
          w: (p.w / 100) * base.w,
          h: (p.h / 100) * base.h,
        }
      : { ...p };
    return out[id];
  };

  for (const el of page.elements) resolve(el.id, new Set([el.id]));
  return out;
}

/** The whole page, as a box. What a page-level element's percentages are of. */
const PAGE_BOX: Placement = { x: 0, y: 0, w: 100, h: 100 };

/** The page-space box an element's own percentages are measured against. */
function parentAbsoluteBox(
  page: UIPage,
  elementId: string,
  absolute: Record<string, Placement>,
): Placement {
  const parent = page.elements.find((e) => e.id === elementId)?.parent;
  const box = parent && parent !== elementId ? absolute[parent] : undefined;
  return box && box.w > 0 && box.h > 0 ? box : { ...PAGE_BOX };
}

/** Write page-space boxes back as percentages of whatever each element's own
 *  parent is. The inverse of absolutePlacements, and the only way an alignment
 *  computed across containers lands where it was computed. */
function withAbsolutePlacements(
  page: UIPage,
  absoluteBoxes: Record<string, Placement>,
  absolute: Record<string, Placement>,
  layoutId?: string | null,
): UIPage {
  const relative: Record<string, Placement> = {};
  for (const [id, box] of Object.entries(absoluteBoxes)) {
    const base = parentAbsoluteBox(page, id, absolute);
    relative[id] = {
      x: ((box.x - base.x) / base.w) * 100,
      y: ((box.y - base.y) / base.h) * 100,
      w: (box.w / base.w) * 100,
      h: (box.h / base.h) * 100,
    };
  }
  return withPlacements(page, relative, layoutId);
}

/**
 * Drop any element whose container is also selected.
 *
 * A container's children are percentages OF it, so moving or resizing the
 * container already carries them. Acting on both writes the same travel twice
 * and the child ends up somewhere nobody asked for. Easy to hit now that a
 * marquee can sweep a container and its contents in one gesture.
 */
/**
 * A locked element never moves, but it still counts.
 *
 * Aligning a row of buttons to the pinned frame behind them is a real thing an
 * integrator wants, so lock removes an element from what gets *written*, not
 * from the geometry the answer is measured against.
 */
function movableIds(page: UIPage, elementIds: Iterable<string>): Set<string> {
  const byId = new Map(page.elements.map((e) => [e.id, e]));
  const ids = new Set<string>();
  for (const id of elementIds) if (!byId.get(id)?.locked) ids.add(id);
  return ids;
}

// --- Containers: the tree, and moving things around in it ---

/** Everything hanging off an element, however deep. */
export function descendantIds(page: UIPage, elementId: string): Set<string> {
  const byParent = new Map<string, string[]>();
  for (const el of page.elements) {
    const parent = el.parent;
    if (!parent || parent === el.id) continue;
    const list = byParent.get(parent);
    if (list) list.push(el.id);
    else byParent.set(parent, [el.id]);
  }
  const out = new Set<string>();
  const walk = (id: string) => {
    for (const child of byParent.get(id) ?? []) {
      if (out.has(child)) continue;
      out.add(child);
      walk(child);
    }
  };
  walk(elementId);
  return out;
}

/**
 * Whether an element may be put inside a container.
 *
 * The tree makes cycles reachable for the first time: a container dropped into
 * itself, or into something already inside it, would parent a box to its own
 * descendant. The renderer survives that (it guards), but the page becomes a
 * thing nobody can draw or explain, so it is refused at the door instead.
 */
export function canReparent(
  page: UIPage,
  elementId: string,
  newParentId: string | null,
): boolean {
  if (!page.elements.some((e) => e.id === elementId)) return false;
  if (newParentId === null) return true;
  if (newParentId === elementId) return false;
  const parent = page.elements.find((e) => e.id === newParentId);
  if (!parent || parent.type !== "group") return false;
  return !descendantIds(page, elementId).has(newParentId);
}

/**
 * Re-express boxes against a different parent, in every layout that carries
 * one of their own.
 *
 * `parent` is a property of the element, not of a layout, so a reparent has to
 * be answered once per arrangement -- the same element sits at a different
 * page-space rect in a portrait layout, and so does its new container.
 */
function reparentPlacements(
  source: UIPage,
  target: UIPage,
  ids: string[],
  newParentId: string | null,
): UIPage {
  const primaryId = primaryLayout(source)?.id;
  let next = target;
  for (const layout of source.layouts ?? []) {
    const absolute = absolutePlacements(source, layout.id);
    const base = newParentId ? absolute[newParentId] : { ...PAGE_BOX };
    if (!base || !(base.w > 0) || !(base.h > 0)) continue;
    const own = layout.placements ?? {};
    const rewritten: Record<string, Placement> = {};
    for (const id of ids) {
      // A variant only gets a delta if it already had one; inventing entries
      // there would pin boxes that were happily inheriting.
      if (layout.id !== primaryId && !(id in own)) continue;
      const box = absolute[id];
      if (!box) continue;
      rewritten[id] = {
        x: ((box.x - base.x) / base.w) * 100,
        y: ((box.y - base.y) / base.h) * 100,
        w: (box.w / base.w) * 100,
        h: (box.h / base.h) * 100,
      };
    }
    if (Object.keys(rewritten).length) next = withPlacements(next, rewritten, layout.id);
  }
  return next;
}

/**
 * Sit an element next to its new siblings in the array.
 *
 * Array order is z-order among siblings, and something you just dropped into a
 * container belongs on top of what is already in there -- so it lands after the
 * last of them, or straight after the container itself when it is the first.
 */
function reseatAmongSiblings(
  elements: UIElement[],
  elementId: string,
  parentId: string | null,
): UIElement[] {
  const idx = elements.findIndex((e) => e.id === elementId);
  if (idx === -1) return elements;
  const rest = [...elements];
  const [moved] = rest.splice(idx, 1);
  let at = -1;
  for (let i = 0; i < rest.length; i++) {
    if ((rest[i].parent ?? null) === parentId) at = i;
  }
  if (at === -1 && parentId) at = rest.findIndex((e) => e.id === parentId);
  rest.splice(at + 1, 0, moved);
  return rest;
}

/**
 * Put an element inside a container (or back out to the page) without moving it
 * on screen.
 *
 * A child's percentages are of its container, so changing the parent and
 * nothing else teleports the element -- 20% of a quarter-page box is not 20% of
 * the page. The box is converted out to page space against the old parent and
 * back in against the new one, which is the whole job: the numbers change, the
 * pixels do not.
 */
export function reparentElement(
  pages: UIPage[],
  pageId: string,
  elementId: string,
  newParentId: string | null,
): UIPage[] {
  return pages.map((p) => {
    if (p.id !== pageId) return p;
    if (!canReparent(p, elementId, newParentId)) return p;
    const element = p.elements.find((e) => e.id === elementId);
    if (!element || (element.parent ?? null) === newParentId) return p;

    let next: UIPage = {
      ...p,
      elements: p.elements.map((e) =>
        e.id === elementId ? { ...e, parent: newParentId } : e,
      ),
    };
    next = reparentPlacements(p, next, [elementId], newParentId);
    return { ...next, elements: reseatAmongSiblings(next.elements, elementId, newParentId) };
  });
}

/** Containers on a page an element could move into -- never itself, never
 *  something already inside it. */
export function containerChoices(
  page: UIPage,
  elementId: string,
): { id: string; label: string }[] {
  const banned = descendantIds(page, elementId);
  banned.add(elementId);
  return page.elements
    .filter((e) => e.type === "group" && !banned.has(e.id))
    .map((e) => ({ id: e.id, label: e.label || e.id }));
}

/** One row of the Outline tree. */
export interface OutlineRow {
  id: string;
  /** Nesting depth, 0 for a page-level element. Drives the indent. */
  depth: number;
  hasChildren: boolean;
  collapsed: boolean;
  /** The visible neighbour under the SAME parent. Z-order inside a container is
   *  position among its siblings, so this is what the order buttons swap with —
   *  the row above might be a child of something else entirely. */
  prevSiblingId?: string;
  nextSiblingId?: string;
}

/**
 * Flatten a page's elements into the rows the Outline draws.
 *
 * Containers are real parents now, so the panel that lists them has to be a
 * tree or the hierarchy is invisible in the one place it should be obvious.
 * A search keeps any row that matches or has a match somewhere under it, and
 * ignores collapse -- a hit three levels down is useless if the containers
 * above it were filtered away.
 */
export function outlineRows(
  elements: UIElement[],
  opts: { collapsed?: Iterable<string>; matchIds?: ReadonlySet<string> | null } = {},
): OutlineRow[] {
  const collapsed = new Set(opts.collapsed ?? []);
  const match = opts.matchIds ?? null;
  const ids = new Set(elements.map((e) => e.id));
  const byParent = new Map<string | null, UIElement[]>();
  for (const el of elements) {
    const named = el.parent;
    const key = named && named !== el.id && ids.has(named) ? named : null;
    const list = byParent.get(key);
    if (list) list.push(el);
    else byParent.set(key, [el]);
  }

  const visible = new Set<string>();
  if (match) {
    const keep = (el: UIElement, seen: Set<string>): boolean => {
      let show = match.has(el.id);
      for (const child of byParent.get(el.id) ?? []) {
        if (seen.has(child.id)) continue;
        if (keep(child, new Set(seen).add(child.id))) show = true;
      }
      if (show) visible.add(el.id);
      return show;
    };
    for (const el of byParent.get(null) ?? []) keep(el, new Set([el.id]));
  }

  // Everything with a path down from a root, whether or not it is drawn today.
  // Kept apart from what gets emitted, so a folded container is not mistaken
  // for an unreachable one.
  const reachable = new Set<string>();
  const reach = (siblings: UIElement[], seen: Set<string>) => {
    for (const el of siblings) {
      if (reachable.has(el.id)) continue;
      reachable.add(el.id);
      reach((byParent.get(el.id) ?? []).filter((c) => !seen.has(c.id)), new Set([...seen, el.id]));
    }
  };
  reach(byParent.get(null) ?? [], new Set());

  const rows: OutlineRow[] = [];
  const emit = (siblings: UIElement[], depth: number, seen: Set<string>) => {
    const shown = match ? siblings.filter((el) => visible.has(el.id)) : siblings;
    shown.forEach((el, i) => {
      const kids = (byParent.get(el.id) ?? []).filter((c) => !seen.has(c.id));
      const shownKids = match ? kids.filter((c) => visible.has(c.id)) : kids;
      const isCollapsed = !match && collapsed.has(el.id) && shownKids.length > 0;
      rows.push({
        id: el.id,
        depth,
        hasChildren: shownKids.length > 0,
        collapsed: isCollapsed,
        prevSiblingId: shown[i - 1]?.id,
        nextSiblingId: shown[i + 1]?.id,
      });
      if (shownKids.length && !isCollapsed) emit(kids, depth + 1, new Set([...seen, el.id]));
    });
  };
  emit(byParent.get(null) ?? [], 0, new Set());

  // A hand-edited parent cycle leaves elements with no path down from a root.
  // The renderer drops them; the Outline is where you would go to fix that, so
  // it shows them at page level rather than pretending they aren't there.
  const stranded = elements.filter(
    (el) => !reachable.has(el.id) && (!match || match.has(el.id)),
  );
  stranded.forEach((el, i) => {
    rows.push({
      id: el.id,
      depth: 0,
      hasChildren: false,
      collapsed: false,
      prevSiblingId: stranded[i - 1]?.id,
      nextSiblingId: stranded[i + 1]?.id,
    });
  });
  return rows;
}

/** A parent-relative box, expressed in page space. */
export function toPageBox(relative: Placement, parentBox?: Placement | null): Placement {
  if (!parentBox || !(parentBox.w > 0) || !(parentBox.h > 0)) return { ...relative };
  return {
    x: parentBox.x + (relative.x / 100) * parentBox.w,
    y: parentBox.y + (relative.y / 100) * parentBox.h,
    w: (relative.w / 100) * parentBox.w,
    h: (relative.h / 100) * parentBox.h,
  };
}

/** Do two page-space boxes share any area at all? */
function boxesOverlap(a: Placement, b: Placement): boolean {
  return (
    a.x < b.x + b.w - BOUNDS_EPSILON &&
    a.x + a.w > b.x + BOUNDS_EPSILON &&
    a.y < b.y + b.h - BOUNDS_EPSILON &&
    a.y + a.h > b.y + BOUNDS_EPSILON
  );
}

/**
 * Which container a dragged element belongs to, given where it ended up.
 *
 * Dragging a control onto a container is how anyone expects to put it in one --
 * the palette drop has always worked that way, and a builder where a NEW button
 * joins the container but an EXISTING one dragged to the same spot does not is
 * a builder that contradicts itself.
 *
 * The rule, in order:
 *
 *  - Fully inside a container? It joins it. Innermost wins when they nest.
 *    Same rule as the palette drop and the 0.8.0 migration.
 *  - Otherwise, still touching the container it is already in? It stays there.
 *    Containers do not clip, and a control deliberately bled over its frame's
 *    edge is a design -- ejecting it the moment it crossed the line would make
 *    that impossible to author.
 *  - Otherwise it is out, at page level.
 */
export function dropTargetFor(
  page: UIPage,
  elementId: string,
  pageBox: Placement,
  layoutId?: string | null,
): string | null {
  // An id the page has never heard of is a brand-new element being dropped in
  // from the palette: nothing inside it, and nowhere it is already living.
  const element = page.elements.find((e) => e.id === elementId);
  const absolute = absolutePlacements(page, layoutId);
  const banned = descendantIds(page, elementId);
  banned.add(elementId);

  let best: { id: string; area: number } | null = null;
  for (const el of page.elements) {
    if (el.type !== "group" || banned.has(el.id)) continue;
    const c = absolute[el.id];
    if (!c) continue;
    const contains =
      pageBox.x >= c.x - BOUNDS_EPSILON &&
      pageBox.y >= c.y - BOUNDS_EPSILON &&
      pageBox.x + pageBox.w <= c.x + c.w + BOUNDS_EPSILON &&
      pageBox.y + pageBox.h <= c.y + c.h + BOUNDS_EPSILON;
    if (!contains) continue;
    const area = c.w * c.h;
    if (!best || area < best.area) best = { id: el.id, area };
  }
  if (best) return best.id;

  const current = element?.parent ?? null;
  if (current && !banned.has(current)) {
    const box = absolute[current];
    if (box && boxesOverlap(pageBox, box)) return current;
  }
  return null;
}

/**
 * Commit a finished canvas gesture: where everything landed, and what it
 * landed IN.
 *
 * The two halves have to happen together. Writing the boxes first and then
 * asking about containers is what lets the reparent measure from the position
 * the gesture actually produced, so the control does not move again on the way
 * into its new home.
 */
export function commitGesturePlacements(
  pages: UIPage[],
  pageId: string,
  placements: Record<string, Placement>,
  layoutId?: string | null,
): UIPage[] {
  return pages.map((p) => {
    if (p.id !== pageId) return p;
    let next = withPlacements(p, placements, layoutId);
    const absolute = absolutePlacements(next, layoutId);
    const adopted: { id: string; parent: string | null }[] = [];
    for (const id of Object.keys(placements)) {
      const element = next.elements.find((e) => e.id === id);
      const box = absolute[id];
      if (!element || !box) continue;
      const target = dropTargetFor(next, id, box, layoutId);
      if (target !== (element.parent ?? null)) adopted.push({ id, parent: target });
    }
    // Each conversion is pixel-preserving, so a container that moves in the
    // same gesture is still at the rect the next element measures against.
    for (const move of adopted) {
      next = reparentElement([next], pageId, move.id, move.parent)[0];
    }
    return next;
  });
}

/**
 * Where dropping onto an Outline row puts the dragged element.
 *
 * Drop on a container and you are putting it IN. Drop on anything else and you
 * are putting it WHERE THAT IS -- beside it, under the same parent -- which is
 * also how a child comes back out: drop it on any page-level element, or on the
 * page row itself. `undefined` means the drop is refused.
 */
export function outlineDropParent(
  page: UIPage,
  draggedId: string,
  targetId: string | null,
): string | null | undefined {
  if (targetId === null) return canReparent(page, draggedId, null) ? null : undefined;
  if (targetId === draggedId) return undefined;
  const target = page.elements.find((e) => e.id === targetId);
  if (!target) return undefined;
  if (descendantIds(page, draggedId).has(targetId)) return undefined;
  const parentId = target.type === "group" ? target.id : target.parent ?? null;
  return canReparent(page, draggedId, parentId) ? parentId : undefined;
}

export function topmostSelection(page: UIPage, elementIds: string[]): string[] {
  const selected = new Set(elementIds);
  const byId = new Map(page.elements.map((e) => [e.id, e]));
  return elementIds.filter((id) => {
    const seen = new Set<string>([id]);
    let cursor = byId.get(id)?.parent ?? null;
    while (cursor && byId.has(cursor) && !seen.has(cursor)) {
      if (selected.has(cursor)) return false;
      seen.add(cursor);
      cursor = byId.get(cursor)?.parent ?? null;
    }
    return true;
  });
}

export type AlignAction =
  | "align-left" | "align-center" | "align-right"
  | "align-top" | "align-middle" | "align-bottom";

export function alignElements(
  pages: UIPage[],
  pageId: string,
  elementIds: string[],
  action: AlignAction,
  layoutId?: string | null,
): UIPage[] {
  return pages.map((p) => {
    if (p.id !== pageId) return p;
    const ids = new Set(topmostSelection(p, elementIds));
    const targets = p.elements.filter((el) => ids.has(el.id));
    if (targets.length === 0) return p;

    const absolute = absolutePlacements(p, layoutId);
    const boxes = new Map(
      targets.map((el) => [el.id, absolute[el.id] ?? { ...DEFAULT_PLACEMENT }]),
    );

    // Several selected: align to the selection's own bounding box, which is
    // what "line these up with each other" means. One selected: align to the
    // page, which in this flattened space is the whole 0..100 box -- so a lone
    // element centres on the page rather than sitting still.
    let left = 0, right = 100, top = 0, bottom = 100;
    if (targets.length > 1) {
      const all = [...boxes.values()];
      left = Math.min(...all.map((b) => b.x));
      right = Math.max(...all.map((b) => b.x + b.w));
      top = Math.min(...all.map((b) => b.y));
      bottom = Math.max(...all.map((b) => b.y + b.h));
    }

    const movable = movableIds(p, ids);
    const moved: Record<string, Placement> = {};
    for (const el of targets) {
      if (!movable.has(el.id)) continue;
      const box = { ...boxes.get(el.id)! };
      switch (action) {
        case "align-left":
          box.x = left;
          break;
        case "align-center":
          box.x = left + (right - left - box.w) / 2;
          break;
        case "align-right":
          box.x = right - box.w;
          break;
        case "align-top":
          box.y = top;
          break;
        case "align-middle":
          box.y = top + (bottom - top - box.h) / 2;
          break;
        case "align-bottom":
          box.y = bottom - box.h;
          break;
      }
      moved[el.id] = box;
    }
    if (Object.keys(moved).length === 0) return p;
    return withAbsolutePlacements(p, moved, absolute, layoutId);
  });
}

export function alignElement(
  pages: UIPage[],
  pageId: string,
  elementId: string,
  action: AlignAction,
  layoutId?: string | null,
): UIPage[] {
  return alignElements(pages, pageId, [elementId], action, layoutId);
}

export type DistributeAxis = "horizontal" | "vertical";

/**
 * Even out the GAPS between elements, not their origins.
 *
 * Spacing origins evenly is the obvious implementation and the wrong one: with
 * a wide element next to a narrow one it leaves visibly uneven space, because
 * the eye measures the air between boxes, not the distance between their
 * top-left corners. So the outermost two hold still, their span is measured,
 * the elements' own sizes come off it, and what is left is split equally
 * between each adjacent pair.
 *
 * Overlapping elements produce a negative gap, which spreads the overlap evenly
 * rather than refusing -- the same thing every design tool does.
 */
export function distributeElements(
  pages: UIPage[],
  pageId: string,
  elementIds: string[],
  axis: DistributeAxis,
  layoutId?: string | null,
): UIPage[] {
  return pages.map((p) => {
    if (p.id !== pageId) return p;
    const ids = new Set(topmostSelection(p, elementIds));
    const targets = p.elements.filter((el) => ids.has(el.id));
    if (targets.length < 3) return p;

    const absolute = absolutePlacements(p, layoutId);
    const pos = axis === "horizontal" ? "x" : "y";
    const size = axis === "horizontal" ? "w" : "h";
    const boxes = new Map(
      targets.map((el) => [el.id, { ...(absolute[el.id] ?? DEFAULT_PLACEMENT) }]),
    );

    // Leading edge decides the order, with the far edge and then the id as
    // tie-breaks so two elements starting at the same coordinate still sort
    // the same way every time this runs.
    const sorted = [...targets].sort((a, b) => {
      const ba = boxes.get(a.id)!;
      const bb = boxes.get(b.id)!;
      return (
        ba[pos] - bb[pos] ||
        ba[pos] + ba[size] - (bb[pos] + bb[size]) ||
        (a.id < b.id ? -1 : 1)
      );
    });

    const first = boxes.get(sorted[0].id)!;
    const last = boxes.get(sorted[sorted.length - 1].id)!;
    const span = last[pos] + last[size] - first[pos];
    const occupied = sorted.reduce((sum, el) => sum + boxes.get(el.id)![size], 0);
    const gap = (span - occupied) / (sorted.length - 1);

    // A locked element in the middle holds its place, and the run carries on
    // from where it sits -- the gaps either side of it still even out.
    const movable = movableIds(p, ids);
    const moved: Record<string, Placement> = {};
    let cursor = first[pos] + first[size];
    for (let i = 1; i < sorted.length - 1; i++) {
      const box = { ...boxes.get(sorted[i].id)! };
      box[pos] = cursor + gap;
      cursor = box[pos] + box[size];
      if (movable.has(sorted[i].id)) moved[sorted[i].id] = box;
    }
    if (Object.keys(moved).length === 0) return p;
    return withAbsolutePlacements(p, moved, absolute, layoutId);
  });
}

export type MatchSizeAction = "match-width" | "match-height" | "match-both";

/**
 * Give every selected element the size of the first one selected.
 *
 * The first is the anchor because it is the one whose numbers the Properties
 * panel is showing -- "make these look like the one I'm editing". Sizes are
 * matched as they RENDER, so a control inside a half-width container ends up
 * the same width on screen as its page-level neighbour rather than the same
 * percentage of a different box.
 */
export function matchSizeElements(
  pages: UIPage[],
  pageId: string,
  elementIds: string[],
  action: MatchSizeAction,
  layoutId?: string | null,
): UIPage[] {
  return pages.map((p) => {
    if (p.id !== pageId) return p;
    const kept = topmostSelection(p, elementIds);
    if (kept.length < 2) return p;
    const anchorId = kept[0];

    const absolute = absolutePlacements(p, layoutId);
    const anchor = absolute[anchorId];
    if (!anchor) return p;

    const movable = movableIds(p, kept);
    const moved: Record<string, Placement> = {};
    for (const id of kept.slice(1)) {
      if (!movable.has(id)) continue;
      const box = absolute[id];
      if (!box) continue;
      const next = { ...box };
      if (action !== "match-height") next.w = anchor.w;
      if (action !== "match-width") next.h = anchor.h;
      // An aspect-locked element is centred inside its box by the renderer, so
      // resizing the box is still the right write -- the lock reshapes what is
      // drawn inside it, exactly as it does during a handle drag.
      moved[id] = next;
    }
    if (Object.keys(moved).length === 0) return p;
    return withAbsolutePlacements(p, moved, absolute, layoutId);
  });
}

/**
 * Which elements a marquee has touched, in page percentages.
 *
 * Touched, not enclosed: sweeping a band across a row of buttons should take
 * the whole row, which is the behaviour every design tool ships and the one an
 * integrator expects. Containers are hit like anything else -- and because a
 * marquee sweeping a page picks up a container AND its children, the callers
 * pass the result through topmostSelection before acting on it.
 */
export function elementsIntersectingRect(
  page: UIPage,
  rect: Placement,
  layoutId?: string | null,
): string[] {
  const absolute = absolutePlacements(page, layoutId);
  const left = Math.min(rect.x, rect.x + rect.w);
  const top = Math.min(rect.y, rect.y + rect.h);
  const right = Math.max(rect.x, rect.x + rect.w);
  const bottom = Math.max(rect.y, rect.y + rect.h);
  return page.elements
    .filter((el) => {
      const b = absolute[el.id];
      if (!b) return false;
      return b.x < right && b.x + b.w > left && b.y < bottom && b.y + b.h > top;
    })
    .map((el) => el.id);
}

/** Ids of every element on the page the author has pinned, plus any pinned
 *  master. One set, because the canvas and the outline both ask "can this
 *  move?" without caring which list the element came from. */
export function lockedIdsFor(
  page: UIPage | undefined,
  masterElements: MasterElement[] | undefined,
): Set<string> {
  const ids = new Set<string>();
  for (const el of page?.elements ?? []) if (el.locked) ids.add(el.id);
  for (const el of masterElements ?? []) if (el.locked) ids.add(el.id);
  return ids;
}

// --- Master element helpers ---

export function promoteToMaster(
  pages: UIPage[],
  masterElements: MasterElement[],
  pageId: string,
  elementId: string,
): { pages: UIPage[]; masterElements: MasterElement[] } {
  const page = pages.find(p => p.id === pageId);
  if (!page) return { pages, masterElements };
  const element = page.elements.find(e => e.id === elementId);
  if (!element) return { pages, masterElements };

  // A master's box is a percentage of the VIEWPORT, so it is valid on every
  // page whatever those pages are arranged like. The STORED placement is a
  // percentage of the element's parent -- for a container child that is the
  // container, not the page -- so convert through the flattened page space,
  // and do it per orientation: a portrait arrangement's position becomes the
  // master's portrait placement instead of being discarded.
  const primary = primaryLayout(page);
  const placements: Record<string, Placement> = {};
  for (const layout of page.layouts ?? []) {
    const key = layout.orientation ?? "landscape";
    if (key in placements && layout.id !== primary?.id) continue;
    const box = absolutePlacements(page, layout.id)[elementId];
    if (box) placements[key] = roundPlacement(box);
  }
  const primaryKey = primary?.orientation ?? "landscape";
  if (!(primaryKey in placements)) placements[primaryKey] = { ...DEFAULT_PLACEMENT };

  // Remove from page, dropping its box in every layout. If a whole container
  // is being promoted its children stay behind on the page, re-homed to the
  // container's own parent without moving -- a master cannot be anyone's
  // parent, and losing six wired controls to one promote is not a trade
  // anybody wants. Same conversion a container delete does.
  const newPages = removeElementFromPage(pages, pageId, elementId);

  // Masters and page elements share the ui.<id> namespace. If the promoted
  // id is already taken (possible in imported/hand-edited projects), rename
  // the promoted copy and rewrite its self-references.
  const taken = new Set<string>([
    ...masterElements.map((m) => m.id),
    ...newPages.flatMap((p) => p.elements.map((e) => e.id)),
  ]);
  let promoted: UIElement = element;
  if (taken.has(promoted.id)) {
    const newId = generateId(promoted.type, taken);
    promoted = rewriteElement(promoted, promoted.id, newId);
  }

  // Add to master elements with pages: "*". The parent link stays behind --
  // masters are placed against the viewport, never inside a page's container.
  const masterEl: MasterElement = {
    ...promoted,
    parent: null,
    pages: "*",
    placements,
    hidden: false,
  };
  return { pages: newPages, masterElements: [...masterElements, masterEl] };
}

export function demoteFromMaster(
  pages: UIPage[],
  masterElements: MasterElement[],
  masterElementId: string,
  targetPageId: string,
): { pages: UIPage[]; masterElements: MasterElement[] } {
  const masterEl = masterElements.find(m => m.id === masterElementId);
  if (!masterEl) return { pages, masterElements };

  // Remove from masters
  const newMasters = masterElements.filter(m => m.id !== masterElementId);

  // Strip the master-only fields; the box comes back as a page placement.
  const { pages: _pagesField, placements: masterPlacements, hidden: _hidden, ...elementFields } =
    masterEl;
  let demoted = elementFields as UIElement;
  const box =
    masterPlacements?.landscape ??
    masterPlacements?.portrait ??
    Object.values(masterPlacements ?? {})[0] ??
    { ...DEFAULT_PLACEMENT };

  // The destination shares the ui.<id> namespace with every page element and
  // the remaining masters. On collision (e.g. a page element was created with
  // this id while it lived as a master in an imported project), rename the
  // demoted copy and rewrite its self-references — two same-id elements would
  // break ui.<id> resolution at runtime.
  const taken = new Set<string>([
    ...newMasters.map((m) => m.id),
    ...pages.flatMap((p) => p.elements.map((e) => e.id)),
  ]);
  if (taken.has(demoted.id)) {
    const newId = generateId(demoted.type, taken);
    demoted = rewriteElement(demoted, demoted.id, newId);
  }

  const newPages = pages.map(p =>
    p.id === targetPageId
      ? withPlacement({ ...p, elements: [...p.elements, demoted] }, demoted.id, box)
      : p
  );

  return { pages: newPages, masterElements: newMasters };
}

export function updateMasterElement(
  masterElements: MasterElement[],
  elementId: string,
  patch: Partial<MasterElement>,
): MasterElement[] {
  return masterElements.map(m =>
    m.id === elementId ? { ...m, ...patch } : m
  );
}

export function removeMasterElement(
  masterElements: MasterElement[],
  elementId: string,
): MasterElement[] {
  return masterElements.filter(m => m.id !== elementId);
}

// --- Page group helpers ---

export function addPageGroup(pageGroups: PageGroup[], name: string): PageGroup[] {
  return [...pageGroups, { name, pages: [] }];
}

export function removePageGroup(pageGroups: PageGroup[], groupName: string): PageGroup[] {
  return pageGroups.filter(g => g.name !== groupName);
}

export function renamePageGroup(pageGroups: PageGroup[], oldName: string, newName: string): PageGroup[] {
  return pageGroups.map(g => g.name === oldName ? { ...g, name: newName } : g);
}

export function assignPageToGroup(pageGroups: PageGroup[], pageId: string, groupName: string | null): PageGroup[] {
  // Remove from all groups first
  let result = pageGroups.map(g => ({ ...g, pages: g.pages.filter(p => p !== pageId) }));
  // Add to target group
  if (groupName) {
    result = result.map(g => g.name === groupName ? { ...g, pages: [...g.pages, pageId] } : g);
  }
  return result;
}

// --- Element rename + reference rewriting ---

/**
 * Rewrite any string starting with `ui.<oldId>.` to `ui.<newId>.`.
 * Returns the input unchanged if it doesn't match.
 */
function rewriteStateKey(value: unknown, oldId: string, newId: string): unknown {
  if (typeof value !== "string") return value;
  const prefix = `ui.${oldId}.`;
  if (value.startsWith(prefix)) {
    return `ui.${newId}.` + value.slice(prefix.length);
  }
  return value;
}

/**
 * Recursively walk an arbitrary JSON-shaped value, rewriting any string
 * value found at a key named `key`, `state_key`, or `source_key` if it
 * starts with `ui.<oldId>.`. Returns a new value if anything changed,
 * else the original (preserving reference equality where possible).
 */
function rewriteRefsDeep(value: unknown, oldId: string, newId: string): unknown {
  if (Array.isArray(value)) {
    let changed = false;
    const next = value.map((v) => {
      const r = rewriteRefsDeep(v, oldId, newId);
      if (r !== v) changed = true;
      return r;
    });
    return changed ? next : value;
  }
  if (value && typeof value === "object") {
    let changed = false;
    const next: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      let nv: unknown = v;
      if ((k === "key" || k === "state_key" || k === "source_key") && typeof v === "string") {
        nv = rewriteStateKey(v, oldId, newId);
      } else {
        nv = rewriteRefsDeep(v, oldId, newId);
      }
      if (nv !== v) changed = true;
      next[k] = nv;
    }
    return changed ? next : value;
  }
  return value;
}

function rewriteElement(el: UIElement, oldId: string, newId: string): UIElement {
  const renamedSelf = el.id === oldId ? { ...el, id: newId } : el;
  const bindings = rewriteRefsDeep(renamedSelf.bindings, oldId, newId) as UIElement["bindings"];
  const next: UIElement = bindings === renamedSelf.bindings
    ? renamedSelf
    : { ...renamedSelf, bindings };
  // Walk visibility (and other top-level fields that may carry state keys)
  // — visibility lives at element[visibility] but isn't typed; treat as opaque.
  const elAsRecord = next as unknown as Record<string, unknown>;
  if (elAsRecord.visibility) {
    const newVis = rewriteRefsDeep(elAsRecord.visibility, oldId, newId);
    if (newVis !== elAsRecord.visibility) {
      return { ...next, visibility: newVis } as UIElement;
    }
  }
  return next;
}

function rewriteStep(step: MacroStep, oldId: string, newId: string): MacroStep {
  let next: MacroStep = step;
  if (step.key) {
    const k = rewriteStateKey(step.key, oldId, newId);
    if (k !== step.key) next = { ...next, key: k as string };
  }
  if (step.params) {
    const p = rewriteRefsDeep(step.params, oldId, newId) as Record<string, unknown>;
    if (p !== step.params) next = { ...next, params: p };
  }
  if (step.condition?.key) {
    const k = rewriteStateKey(step.condition.key, oldId, newId);
    if (k !== step.condition.key) {
      next = { ...next, condition: { ...step.condition, key: k as string } };
    }
  }
  if (step.skip_if?.key) {
    const k = rewriteStateKey(step.skip_if.key, oldId, newId);
    if (k !== step.skip_if.key) {
      next = { ...next, skip_if: { ...step.skip_if, key: k as string } };
    }
  }
  if (step.then_steps) {
    const t = step.then_steps.map((s) => rewriteStep(s, oldId, newId));
    if (t.some((s, i) => s !== step.then_steps![i])) next = { ...next, then_steps: t };
  }
  if (step.else_steps) {
    const e = step.else_steps.map((s) => rewriteStep(s, oldId, newId));
    if (e.some((s, i) => s !== step.else_steps![i])) next = { ...next, else_steps: e };
  }
  return next;
}

function rewriteMacro(macro: MacroConfig, oldId: string, newId: string): MacroConfig {
  let changed = false;
  const steps = macro.steps.map((s) => {
    const r = rewriteStep(s, oldId, newId);
    if (r !== s) changed = true;
    return r;
  });
  let triggers = macro.triggers;
  if (triggers) {
    const t = triggers.map((trig) => {
      let next = trig;
      if (trig.state_key) {
        const k = rewriteStateKey(trig.state_key, oldId, newId);
        if (k !== trig.state_key) next = { ...next, state_key: k as string };
      }
      if (trig.conditions) {
        const conds = trig.conditions.map((c) => {
          if (c.key) {
            const k = rewriteStateKey(c.key, oldId, newId);
            if (k !== c.key) return { ...c, key: k as string };
          }
          return c;
        });
        if (conds.some((c, i) => c !== trig.conditions![i])) next = { ...next, conditions: conds };
      }
      return next;
    });
    if (t.some((trig, i) => trig !== triggers![i])) {
      triggers = t;
      changed = true;
    }
  }
  return changed ? { ...macro, steps, triggers } : macro;
}

function rewriteVariable(v: VariableConfig, oldId: string, newId: string): VariableConfig {
  if (!v.source_key) return v;
  const k = rewriteStateKey(v.source_key, oldId, newId);
  return k === v.source_key ? v : { ...v, source_key: k as string };
}

// Map an array, returning the SAME reference when no item changed. The per-item
// rewriters already preserve reference equality for untouched items, so this
// lets renameElement hand back the original macros/variables/masters arrays when
// a rename didn't touch them — which is exactly what the undo-snapshot guard in
// UIBuilderView checks (result.macros !== project.macros) to keep the entry small.
function mapPreserve<T>(arr: T[], fn: (item: T) => T): T[] {
  let changed = false;
  const next = arr.map((item) => {
    const r = fn(item);
    if (r !== item) changed = true;
    return r;
  });
  return changed ? next : arr;
}

export interface RenameResult {
  pages: UIPage[];
  master_elements: MasterElement[];
  macros: MacroConfig[];
  variables: VariableConfig[];
  scriptsToReview: string[];  // script file names that mention `ui.<oldId>.`
}

/**
 * Validate a proposed element ID. Returns null if valid, else an error
 * message. Allowed chars: lowercase letters, digits, underscores. Must
 * start with a letter. Must not collide with any existing element ID
 * across all pages or master_elements (excluding the element being
 * renamed itself).
 */
export function validateElementId(
  newId: string,
  currentId: string,
  pages: UIPage[],
  masterElements: MasterElement[],
): string | null {
  if (!newId) return "ID cannot be empty.";
  if (newId === currentId) return null;
  if (!/^[a-z][a-z0-9_]*$/.test(newId)) {
    return "ID must start with a lowercase letter and contain only lowercase letters, digits, and underscores.";
  }
  const existing = new Set<string>();
  for (const p of pages) for (const el of p.elements) existing.add(el.id);
  for (const m of masterElements) existing.add(m.id);
  existing.delete(currentId);
  if (existing.has(newId)) return `An element with ID "${newId}" already exists.`;
  return null;
}

/**
 * Rename an element across the entire project, rewriting every reference
 * in element bindings, visibility conditions, master elements, macro steps,
 * trigger conditions, and variable source_keys. Scripts are NOT
 * auto-rewritten — their source code is returned in `scriptsToReview` so
 * the caller can warn the user.
 */
export function renameElement(
  pages: UIPage[],
  masterElements: MasterElement[],
  macros: MacroConfig[],
  variables: VariableConfig[],
  scripts: ScriptConfig[],
  oldId: string,
  newId: string,
): RenameResult {
  const newPages = mapPreserve(pages, (p) => {
    const elements = mapPreserve(p.elements, (el) => rewriteElement(el, oldId, newId));
    return elements === p.elements ? p : { ...p, elements };
  });
  const newMasters = mapPreserve(masterElements, (m) => rewriteElement(m as unknown as UIElement, oldId, newId) as MasterElement);
  const newMacros = mapPreserve(macros, (m) => rewriteMacro(m, oldId, newId));
  const newVars = mapPreserve(variables, (v) => rewriteVariable(v, oldId, newId));
  // Scripts: just list the ones that mention the old ID — caller warns.
  const scriptsToReview = scripts
    .filter((s) => s.file && s.id)
    .map((s) => s.file);
  // We can't actually grep script source from this client-side helper —
  // returning all script files keeps it simple; UIBuilderView will toast a
  // generic warning when scripts are present and the user can search.
  return {
    pages: newPages,
    master_elements: newMasters,
    macros: newMacros,
    variables: newVars,
    scriptsToReview,
  };
}

// --- Project validation ---

/** Percentages are floats; a hair over 100 is rounding, not a mistake.
 *
 *  Placements are stored to four decimal places, so this is exactly the
 *  rounding floor and nothing wider. It used to be 0.01, which quietly forgave
 *  a hundred times more than rounding can produce -- and the AI door does not
 *  forgive it, so the two surfaces disagreed about the same file. */
const BOUNDS_EPSILON = 0.0001;

/**
 * True when a box hangs outside its parent.
 *
 * Free positioning WARNS, it does not prevent -- containers don't clip, and a
 * control nudged past an edge stays visible. This is what puts the badge on it.
 */
export function isOutOfBounds(p: Placement): boolean {
  return (
    p.x < -BOUNDS_EPSILON ||
    p.y < -BOUNDS_EPSILON ||
    p.x + p.w > 100 + BOUNDS_EPSILON ||
    p.y + p.h > 100 + BOUNDS_EPSILON
  );
}

// --- Touch-target warning (plan section 3.7) ---

/** The reference glass every warning is estimated against.
 *
 *  1280x800 is the design reference. The density is the part that was wrong:
 *  100 px/inch describes a 15-inch panel at that resolution, and almost nobody
 *  deploys one. The mainstream wall panel at 1280x800 is 10.1 inches, which is
 *  ~149 px/inch -- so the old figure reported a control as half again more
 *  finger than it actually gets, and told an author 11.2mm where the panel
 *  gives 7.5mm. Measured against real panel diagonals 2026-08-04.
 */
export const TOUCH_REFERENCE = { width: 1280, height: 800, pxPerInch: 149 };

/** The comfortable finger minimum, and the actual rule.
 *
 *  It is in millimetres because that is the real question -- whether a thumb
 *  can hit the thing is physical, not pixel. The 44px runtime clamp this
 *  replaced was never that rule: 44 CSS px works out between 6.0mm and 8.5mm
 *  across every panel in the deployment range (800x480 at 5in through
 *  1920x1080 at 15.6in), so it sat under the comfortable minimum on all of
 *  them and only looked like a guarantee. Deleting the clamp lost nothing.
 *  Stating the rule in millimetres is what makes the advice true.
 */
export const TOUCH_MIN_MM = 9;

/** The millimetre rule in reference pixels, derived so the two cannot drift. */
export const TOUCH_MIN_PX = (TOUCH_MIN_MM / 25.4) * TOUCH_REFERENCE.pxPerInch;

export interface TouchWarning {
  /** Which axis is too small, for the message. */
  axis: "width" | "height" | "both";
  /** Estimated size at the reference, in px. */
  widthPx: number;
  heightPx: number;
  /** The same estimate in millimetres of finger. */
  widthMm: number;
  heightMm: number;
}

/**
 * Flag a control that would be awkward to touch, with the physical size shown
 * so the author can judge it. A warning, never a clamp: an author who wants a
 * 20px status dot is allowed to have one.
 */
export function touchTargetWarning(
  p: Placement,
  parentPx?: { width: number; height: number },
): TouchWarning | null {
  const ref = parentPx ?? { width: TOUCH_REFERENCE.width, height: TOUCH_REFERENCE.height };
  const widthPx = (p.w / 100) * ref.width;
  const heightPx = (p.h / 100) * ref.height;
  const narrow = widthPx < TOUCH_MIN_PX;
  const short = heightPx < TOUCH_MIN_PX;
  if (!narrow && !short) return null;
  const toMm = (px: number) => (px / TOUCH_REFERENCE.pxPerInch) * 25.4;
  return {
    axis: narrow && short ? "both" : narrow ? "width" : "height",
    widthPx: Math.round(widthPx),
    heightPx: Math.round(heightPx),
    widthMm: Math.round(toMm(widthPx) * 10) / 10,
    heightMm: Math.round(toMm(heightPx) * 10) / 10,
  };
}

/**
 * The pixel box an element's percentages are measured against, at the
 * reference size -- the page, or the container it sits in, walked up through
 * however many containers that takes.
 *
 * Anything expressed as a real-world ratio needs this. An aspect lock is a
 * PIXEL ratio (that is what CSS aspect-ratio means), so seeding one from an
 * element's current shape has to go through the box it actually sits in: a
 * child that is 50% wide and 50% tall inside a wide container is a wide
 * element, not a square one.
 */
export function referenceParentBox(
  page: UIPage,
  elementId: string,
  layoutId?: string | null,
): { width: number; height: number } {
  const placements = resolvePlacements(page, layoutId);
  const byId = new Map(page.elements.map((e) => [e.id, e]));
  const chain: string[] = [];
  let cursor = byId.get(elementId)?.parent ?? null;
  const seen = new Set<string>([elementId]);
  while (cursor && byId.has(cursor) && !seen.has(cursor)) {
    seen.add(cursor);
    chain.unshift(cursor);
    cursor = byId.get(cursor)?.parent ?? null;
  }
  let width = TOUCH_REFERENCE.width;
  let height = TOUCH_REFERENCE.height;
  for (const id of chain) {
    const box = placements[id];
    if (!box) continue;
    width = (box.w / 100) * width;
    height = (box.h / 100) * height;
  }
  return { width, height };
}

/** Whether a finger has to hit this element type at all. */
export function isTouchable(type: string): boolean {
  return TOUCHABLE_TYPES.has(type);
}

/** Element types a finger actually has to hit. A label being small is fine.
 *
 *  `fader` and `slider` are here because dragging is touch -- the same thumb
 *  grabs the handle. Neither can actually reach this check without already
 *  having failed its contents floor (a fader that holds its handle and scale is
 *  72x100, a slider 68x81, both past the 53px finger minimum), so they are here
 *  for consistency: the rule is "a control you touch has a physical minimum",
 *  and leaving the draggable ones out made it read like a rule about buttons.
 *  Mirrored in server/ui/page_review.py, with a test asserting the two match. */
const TOUCHABLE_TYPES = new Set([
  "button", "page_nav", "camera_preset", "select", "text_input", "keypad", "list",
  "fader", "slider",
]);

// --- The page review: what a page will actually draw wrong ---
//
// The mirror of server/ui/page_review.py, which runs at the AI write door. The
// two ask the same questions of the same measured numbers and answer in the
// same words, down to the byte -- tests/test_ui_review_parity.py pushes a
// project through both and fails on any difference. That matters because a
// human starves a status light by dragging exactly as easily as the AI does by
// sizing, and until now only one of them was ever told.
//
// Every message carries the failure twice: in reference pixels, and in the
// percentage of the element's REAL container to write instead. The second
// number is the one that was missing. An author edits a percentage of a parent,
// so "needs 29px" leaves them holding the same arithmetic that produced the
// problem -- 3% of 1280 is 38px, of which 20 is a dot that does not shrink.

/** One thing that will not draw the way it was written. */
export interface ReviewFinding {
  elementId: string;
  /** Groups findings for a caller that wants to count or filter them. */
  kind:
    | "too_small_for_contents"
    | "small_touch_target"
    | "outside_its_container"
    | "overlap"
    | "no_placement"
    | "binding_not_rendered"
    | "unknown_element_type"
    | "style_too_large"
    | "too_small_to_draw";
  /** The whole finding in one self-contained sentence. */
  message: string;
  /** What makes a finding the same finding across two arrangements -- an
   *  element can collide with three neighbours, and all three are worth saying
   *  once each rather than once per layout that inherits the collision. */
  key: string;
}

/** The theme slice a control minimum can move with (only a slider's thumb). */
export type ElementDefaults = Record<string, unknown> | null | undefined;

/** A part of a control that keeps its size when the box shrinks. */
interface ResolvedInternal {
  part: string;
  widthPx: number | null;
  heightPx: number | null;
}

export interface ControlMinimumBox {
  widthPx: number;
  heightPx: number;
  internals: ResolvedInternal[];
}

/**
 * Python's `format(value, '.Nf')`, which rounds ties to EVEN.
 *
 * `toFixed` rounds ties away from zero, so the two disagree on any box that
 * lands exactly on a half pixel -- reachable, because a height is a four-decimal
 * percentage of 800 and 0.0625% is exactly 0.5px. One digit of difference in one
 * message is a parity failure, and chasing it later from a red test would cost
 * far more than writing it correctly once.
 */
function fixed(value: number, digits: number): string {
  const negative = value < 0 || Object.is(value, -0);
  const scale = 10 ** digits;
  const scaled = Math.abs(value) * scale;
  const floor = Math.floor(scaled);
  const remainder = scaled - floor;
  const rounded =
    remainder > 0.5 ? floor + 1
    : remainder < 0.5 ? floor
    : floor % 2 === 0 ? floor : floor + 1;
  const body = (rounded / scale).toFixed(digits);
  return negative ? `-${body}` : body;
}

/** A percentage the way it would be written back into a placement. */
function pct(value: number): string {
  return fixed(value, 2).replace(/0+$/, "").replace(/\.$/, "") || "0";
}

function toMm(px: number): number {
  return (px / TOUCH_REFERENCE.pxPerInch) * 25.4;
}

/** An authored bound, without a trailing .0 on whole numbers.
 *
 *  The mirror of Python's `_num`, which has to strip one because a JSON `24`
 *  arrives there as a float. Numbers here are already doubles that print whole. */
function num(value: number): string {
  return String(value);
}

/** A table lookup keyed by an element TYPE, which is authored text.
 *
 *  A plain object inherits `constructor`, `toString` and friends, so a bare
 *  `TABLE[el.type]` hands back a function for a type named after one of them
 *  and the next line throws. Python's `.get` has no such hole; this closes it. */
function own<T>(table: Record<string, T>, key: string): T | undefined {
  return Object.prototype.hasOwnProperty.call(table, key) ? table[key] : undefined;
}

/** Whether this element draws text beside its control.
 *
 *  Only `label` does. The single type with a caption bonus is `status_led`, and
 *  panel.js builds its `.led-label` under `if (element.label)` and from nothing
 *  else -- the same thing HONORED_SHOW_SLOTS records (a status LED renders
 *  `show.look`, not `show.value`).
 *
 *  This used to count a bound `show.value` too. It is not rendered: the review
 *  would widen the floor by 9px to hold text that never draws, while separately
 *  warning that the same binding is inert. Mirrors `_has_caption` in
 *  server/ui/control_minimums.py. */
function hasCaption(el: UIElement): boolean {
  return !!(el.label ?? "").trim();
}

/** Resolve an authored internal: element wins, then theme, then the default. */
function scaledInternalPx(
  scale: ControlScalingInternal,
  el: UIElement,
  theme: ElementDefaults,
): number {
  let value = (el as unknown as Record<string, unknown>)[scale.property];
  if ((value === undefined || value === null) && scale.fromTheme && theme) {
    value = theme[scale.property];
  }
  return value === undefined || value === null
    ? scale.defaultPx
    : Number(value) * REM_BASE_PX;
}

/**
 * The smallest box this element can be drawn in, or null when unbounded.
 *
 * Null means the type has no fixed internals at all -- a button, a label, an
 * image. Those are limited by their text, which is unbounded and theme
 * dependent, and is therefore not a minimum box.
 */
export function controlMinimumBox(
  el: UIElement,
  theme?: ElementDefaults,
): ControlMinimumBox | null {
  const rule = own(CONTROL_MINIMUMS, el.type);
  if (!rule) return null;
  let widthPx = rule.baseWidthPx;
  let heightPx = rule.baseHeightPx;
  const internals: ResolvedInternal[] = rule.internals.map((i) => ({
    part: i.part,
    widthPx: i.widthPx,
    heightPx: i.heightPx,
  }));
  if (rule.scalesWith) {
    const size = scaledInternalPx(rule.scalesWith, el, theme);
    widthPx += rule.scalesWith.widthCoefficient * size;
    heightPx += rule.scalesWith.heightCoefficient * size;
    internals.push({
      part: rule.scalesWith.part,
      widthPx: rule.scalesWith.widthCoefficient ? size : null,
      heightPx: rule.scalesWith.heightCoefficient ? size : null,
    });
  }
  if (rule.captionWidthBonusPx && hasCaption(el)) widthPx += rule.captionWidthBonusPx;
  return { widthPx, heightPx, internals };
}

/**
 * The same floor as a percentage of the box the element actually sits in.
 *
 * This is the form an authoring surface needs: geometry is percentages, so a
 * minimum in pixels only becomes actionable once it is divided by the parent.
 */
export function controlMinimumPercent(
  el: UIElement,
  parentPx: { width: number; height: number },
  theme?: ElementDefaults,
): { w: number; h: number } | null {
  const box = controlMinimumBox(el, theme);
  if (!box) return null;
  return {
    w: (box.widthPx / parentPx.width) * 100,
    h: (box.heightPx / parentPx.height) * 100,
  };
}

function blame(internals: ResolvedInternal[], axis: "width" | "height"): string {
  const parts = internals
    .map((i) => ({ part: i.part, size: axis === "width" ? i.widthPx : i.heightPx }))
    .filter((i) => !!i.size)
    .map((i) => `${i.part} is ${fixed(i.size as number, 0)}px`);
  return parts.length ? ` (${parts.join(", ")})` : "";
}

/** Why this box is too small, per axis, with the numbers in it. Empty when it fits. */
function starvedReasons(
  box: ControlMinimumBox,
  widthPx: number,
  heightPx: number,
): string[] {
  const reasons: string[] = [];
  if (widthPx + 0.5 < box.widthPx) {
    reasons.push(
      `${fixed(widthPx, 0)}px wide, needs ${fixed(box.widthPx, 0)}px` +
        blame(box.internals, "width"),
    );
  }
  if (heightPx + 0.5 < box.heightPx) {
    reasons.push(
      `${fixed(heightPx, 0)}px tall, needs ${fixed(box.heightPx, 0)}px` +
        blame(box.internals, "height"),
    );
  }
  return reasons;
}

/** One box in an element's ancestry, as a remedy would have to name it. */
export interface Container {
  /** How it reads mid-sentence: `'grp_strip'`, or `the page` for the root. */
  label: string;
  widthPx: number;
  heightPx: number;
}

/** The root every chain ends at. It is the one box no remedy can grow. */
const PAGE_CONTAINER: Container = {
  label: "the page",
  widthPx: TOUCH_REFERENCE.width,
  heightPx: TOUCH_REFERENCE.height,
};

/** A starved axis whose floor is larger than the container itself. */
interface BlockedAxis {
  axis: "width" | "height";
  wants: number;
  has: number;
}

/**
 * The starved axes with no percentage to write.
 *
 * 100% of the parent is already under the floor, so every remedy phrased against
 * the parent comes out above 100 -- unreachable, and read as nonsense by anyone
 * who tries it. The container is the fault.
 */
function blockedAxes(
  box: ControlMinimumBox,
  boxPx: { width: number; height: number },
  parentPx: { width: number; height: number },
): BlockedAxis[] {
  const blocked: BlockedAxis[] = [];
  if (boxPx.width + 0.5 < box.widthPx && box.widthPx > parentPx.width + 0.5) {
    blocked.push({ axis: "width", wants: box.widthPx, has: boxPx.width });
  }
  if (boxPx.height + 0.5 < box.heightPx && box.heightPx > parentPx.height + 0.5) {
    blocked.push({ axis: "height", wants: box.heightPx, has: boxPx.height });
  }
  return blocked;
}

function grown(container: Container, axis: "width" | "height"): number {
  return axis === "width" ? container.widthPx : container.heightPx;
}

/**
 * The nearest ancestor that can be grown until every starved axis fits.
 *
 * Percentages cascade, so growing one box scales everything beneath it: a fader
 * at 90% of a 51px strip reaches 72px the moment the strip reaches 80px, and no
 * other edit is needed. That is what makes a single number actionable here where
 * `w at least 140.62%` was not.
 *
 * Walks outward because a container is only growable up to its own parent --
 * when the immediate one is already pinned, the fix is one level further out,
 * and it still lands on the element underneath.
 */
export function containerRemedy(
  ancestors: Container[],
  blocked: BlockedAxis[],
): { holder: Container; parent: Container; needs: Record<string, number> } | null {
  // A zero box has no share to scale; that is its own finding.
  if (blocked.some((b) => b.has <= 0)) return null;
  const chain = [...ancestors, PAGE_CONTAINER];
  for (let index = 0; index < chain.length - 1; index++) {
    const holder = chain[index];
    const parent = chain[index + 1];
    const needs: Record<string, number> = {};
    let fits = true;
    for (const { axis, wants, has } of blocked) {
      const size = (grown(holder, axis) * wants) / has;
      if (size > grown(parent, axis) + 0.5) {
        fits = false;
        break;
      }
      needs[axis] = size;
    }
    if (fits) return { holder, parent, needs };
  }
  return null;
}

/** The remedy for axes no percentage of the parent can reach. */
function containerClause(ancestors: Container[], blocked: BlockedAxis[]): string {
  const axes = blocked.map((b) => b.axis);
  const remedy = containerRemedy(ancestors, blocked);
  if (!remedy) {
    // Nothing in the ancestry has room to grow, so the page itself is the
    // binding constraint. Said as the ceiling rather than as a percentage,
    // because there is no percentage that helps.
    return blocked
      .map(({ axis, wants, has }) => {
        const outer = ancestors.length
          ? grown(ancestors[ancestors.length - 1], axis)
          : has;
        const page = grown(PAGE_CONTAINER, axis);
        const ceiling = outer > 0 ? (has * page) / outer : page;
        const word = axis === "width" ? "widest" : "tallest";
        return (
          ` No placement fits: the ${word} this can be drawn on a ` +
          `${fixed(page, 0)}px page is ${fixed(ceiling, 0)}px, and it needs ${fixed(wants, 0)}px.`
        );
      })
      .join("");
  }

  const { holder, parent, needs } = remedy;
  let size: string;
  let word: string;
  if (axes.length === 2) {
    size = `${fixed(holder.widthPx, 0)}x${fixed(holder.heightPx, 0)}px`;
    word = "size";
  } else if (axes[0] === "width") {
    size = `${fixed(holder.widthPx, 0)}px wide`;
    word = "width";
  } else {
    size = `${fixed(holder.heightPx, 0)}px tall`;
    word = "height";
  }
  const gives = axes
    .map(
      (axis) =>
        `${axis === "width" ? "w" : "h"} at least ` +
        `${pct((needs[axis] / grown(parent, axis)) * 100)}%`,
    )
    .join(" and ");
  return (
    ` ${holder.label} is ${size}, too small to hold it at any ${word}: ` +
    `give ${holder.label} ${gives} of ${parent.label}.`
  );
}

/**
 * A control smaller than the parts inside it that do not shrink.
 *
 * The dominant defect class, and the one nothing could see before the minimums
 * were measured: the box is a percentage and can be any size, while the dot, the
 * handle, the thumb and the key grid are pixels and are not negotiable. Below
 * the floor the control still draws -- it just draws with its contents cut off,
 * which reads as a styling bug rather than a sizing one.
 *
 * The remedy is a percentage of the element's own parent, EXCEPT when the parent
 * is itself too small to hold the floor. Then no percentage exists and
 * `ancestors` is what turns the finding back into something actionable, by
 * naming the box that has to grow instead.
 */
export function starvationFinding(
  el: UIElement,
  boxPx: { width: number; height: number },
  parentPx: { width: number; height: number },
  parentName: string,
  theme?: ElementDefaults,
  ancestors: Container[] = [],
): ReviewFinding | null {
  const box = controlMinimumBox(el, theme);
  if (!box) return null;
  const reasons = starvedReasons(box, boxPx.width, boxPx.height);
  if (!reasons.length) return null;

  const blocked = blockedAxes(box, boxPx, parentPx);
  const stuck = new Set(blocked.map((b) => b.axis));
  const need = controlMinimumPercent(el, parentPx, theme);
  const fixes: string[] = [];
  if (need) {
    if (boxPx.width + 0.5 < box.widthPx && !stuck.has("width")) {
      fixes.push(`w at least ${pct(need.w)}%`);
    }
    if (boxPx.height + 0.5 < box.heightPx && !stuck.has("height")) {
      fixes.push(`h at least ${pct(need.h)}%`);
    }
  }
  let fix = fixes.length ? ` Give it ${fixes.join(" and ")} of ${parentName}.` : "";
  if (blocked.length) fix += containerClause(ancestors, blocked);
  return {
    elementId: el.id,
    kind: "too_small_for_contents",
    message:
      `${el.id} (${el.type}) is ${fixed(boxPx.width, 0)}x${fixed(boxPx.height, 0)}px at the ` +
      `${TOUCH_REFERENCE.width}x${TOUCH_REFERENCE.height} reference, too small for what it ` +
      `draws: ${reasons.join("; ")}.${fix}`,
    key: `too_small_for_contents|${el.id}`,
  };
}

/** Below this on either axis an element is not small, it is absent.
 *
 *  Deliberately NOT a per-type floor. Two thirds of the element types publish no
 *  floor at all, because what limits them is their content -- a caption, an
 *  image, whatever a plugin draws -- which is unbounded and theme dependent, and
 *  inventing a curve for that would reject layouts that render correctly. That
 *  reasoning stands. It just does not cover 6x4px, which is not a judgement
 *  call.
 *
 *  10 sits under every measured floor (the lowest is a level meter at 13px
 *  wide), so this can never contradict one. Mirrored in
 *  server/ui/page_review.py, with a test asserting both the match and that
 *  relationship. */
export const MINIMUM_VISIBLE_PX = 10.0;

/**
 * A box too small to draw anything, on a type with no floor to breach.
 *
 * The gap this closes: a gauge and a clock at 0.5% x 0.5% of the page came back
 * completely clean. They have no fixed internals, so the starvation check has
 * nothing to measure them against, and 6x4px sailed through a review whose whole
 * purpose is catching controls too small to work.
 *
 * Runs only where `controlMinimumBox` has no opinion, so it never speaks over a
 * measured floor and never becomes one.
 */
export function degenerateFinding(
  el: UIElement,
  boxPx: { width: number; height: number },
  parentPx: { width: number; height: number },
  parentName: string,
  theme?: ElementDefaults,
): ReviewFinding | null {
  if (controlMinimumBox(el, theme)) return null;
  if (boxPx.width >= MINIMUM_VISIBLE_PX && boxPx.height >= MINIMUM_VISIBLE_PX) return null;

  const fixes: string[] = [];
  if (boxPx.width < MINIMUM_VISIBLE_PX && parentPx.width > 0) {
    fixes.push(`w at least ${pct((MINIMUM_VISIBLE_PX / parentPx.width) * 100)}%`);
  }
  if (boxPx.height < MINIMUM_VISIBLE_PX && parentPx.height > 0) {
    fixes.push(`h at least ${pct((MINIMUM_VISIBLE_PX / parentPx.height) * 100)}%`);
  }
  const fix = fixes.length ? ` Give it ${fixes.join(" and ")} of ${parentName}.` : "";
  return {
    elementId: el.id,
    kind: "too_small_to_draw",
    message:
      `${el.id} (${el.type}) is ${fixed(boxPx.width, 0)}x${fixed(boxPx.height, 0)}px at the ` +
      `${TOUCH_REFERENCE.width}x${TOUCH_REFERENCE.height} reference, which is not small, it is ` +
      `invisible. ${capitalized(article(el.type))} ${el.type} has no fixed floor -- what ` +
      `limits it is its content -- so ` +
      `nothing else here measures it.${fix}`,
    key: `too_small_to_draw|${el.id}`,
  };
}

/**
 * A control a finger will struggle to hit. Physical, not pixel.
 *
 * A separate question from starvation with a separate answer: a select holds
 * everything it draws at 44px tall and is still under the comfortable touch
 * minimum, which is exactly what shipped.
 */
export function touchFinding(
  el: UIElement,
  boxPx: { width: number; height: number },
): ReviewFinding | null {
  if (!TOUCHABLE_TYPES.has(el.type)) return null;
  // A box with no size is degenerate, not uncomfortable. Reporting "roughly
  // 43.6x0.0mm on a 10-inch panel -- under the 9mm comfortable touch minimum"
  // invites someone to make it a little bigger, when the thing is not on screen
  // at all. The degenerate check has the sentence for it, and one fix ends both.
  if (boxPx.width <= 0 || boxPx.height <= 0) return null;
  const narrow = boxPx.width < TOUCH_MIN_PX;
  const short = boxPx.height < TOUCH_MIN_PX;
  if (!narrow && !short) return null;
  const axis = narrow && short ? "width and height" : narrow ? "width" : "height";
  return {
    elementId: el.id,
    kind: "small_touch_target",
    message:
      `${el.id} (${el.type}) is about ${fixed(boxPx.width, 0)}x${fixed(boxPx.height, 0)}px, roughly ` +
      `${fixed(toMm(boxPx.width), 1)}x${fixed(toMm(boxPx.height), 1)}mm on a 10-inch panel -- under the ` +
      `${TOUCH_MIN_MM}mm comfortable touch minimum on ${axis} (${fixed(TOUCH_MIN_PX, 0)}px).`,
    key: `small_touch_target|${el.id}`,
  };
}

/** `a` or `an`, so a type name reads as a sentence rather than a token. */
function article(word: string): string {
  const first = word.slice(0, 1).toLowerCase();
  return first && "aeiou".includes(first) ? "an" : "a";
}

/** Python's `str.capitalize` on a word this short: first letter up. */
function capitalized(word: string): string {
  return word.slice(0, 1).toUpperCase() + word.slice(1);
}

/** A list read out loud: one, one and another, or one, another and a third. */
function joined(parts: string[]): string {
  if (parts.length < 3) return parts.join(" and ");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** The first of these style fields that is set, as `[name, value]`.
 *
 *  Ordered the way the panel resolves them: a specific axis wins over the
 *  shorthand, so the message names the field the author would actually edit. */
function styleMeasure(
  style: Record<string, unknown>,
  ...names: string[]
): [string, number] | null {
  for (const name of names) {
    const value = style[name];
    if (typeof value === "number" && value > 0) return [name, value];
  }
  return null;
}

/**
 * A `style` measurement that renders bigger than the element it is on.
 *
 * `style` measurements are **rem** -- px / 14, since project format 0.8.0 -- and
 * the number an author reaches for is the pixel one. `font_size: 24` is 24 rem,
 * which is 336px of text, and on a 32px label it is a third of a metre of type
 * overflowing a box the height of a line. Nothing caught it: the write lands,
 * the panel draws it, and it is only wrong to look at.
 *
 * Only measurements that cannot fit are reported, which is what makes this a
 * fact rather than a style opinion: 24 rem is perfectly reasonable on a box tall
 * enough to hold it, and this says nothing at all about that one.
 *
 * `border_radius` and `margin` are deliberately absent. CSS clamps a radius to
 * half the box, so an oversized one draws a legal pill rather than a defect; and
 * a margin sits outside a box the layout has already positioned by percentage,
 * so it has no size of its own to exceed.
 */
export function styleFinding(
  el: UIElement,
  boxPx: { width: number; height: number },
): ReviewFinding | null {
  const style = el.style as Record<string, unknown> | undefined;
  if (!style || typeof style !== "object") return null;
  const { width, height } = boxPx;
  const reasons: string[] = [];
  // Keyed by field, because `padding` is one number governing both axes: when it
  // breaks the box on each, it still gets one ceiling, the tighter one.
  const limits = new Map<string, number>();
  const note = (reason: string, field: string, limitPx: number) => {
    reasons.push(reason);
    const room = limitPx / REM_BASE_PX;
    limits.set(field, Math.min(limits.get(field) ?? room, room));
  };

  const font = styleMeasure(style, "font_size");
  if (font && height > 0 && font[1] * REM_BASE_PX > height + 0.5) {
    note(
      `font_size ${num(font[1])} draws ${fixed(font[1] * REM_BASE_PX, 0)}px of text in a box ` +
        `${fixed(height, 0)}px tall`,
      font[0], height,
    );
  }

  const down = styleMeasure(style, "padding_vertical", "padding");
  if (down && height > 0 && 2 * down[1] * REM_BASE_PX >= height) {
    note(
      `${down[0]} ${num(down[1])} leaves ${fixed(down[1] * REM_BASE_PX, 0)}px above and below ` +
        `in a box ${fixed(height, 0)}px tall`,
      down[0], height / 2,
    );
  }

  const across = styleMeasure(style, "padding_horizontal", "padding");
  if (across && width > 0 && 2 * across[1] * REM_BASE_PX >= width) {
    note(
      `${across[0]} ${num(across[1])} leaves ${fixed(across[1] * REM_BASE_PX, 0)}px each side ` +
        `in a box ${fixed(width, 0)}px wide`,
      across[0], width / 2,
    );
  }

  const edge = styleMeasure(style, "border_width");
  const smallest = Math.min(width, height);
  if (edge && smallest > 0 && 2 * edge[1] * REM_BASE_PX >= smallest) {
    note(
      `border_width ${num(edge[1])} draws a ${fixed(edge[1] * REM_BASE_PX, 0)}px border on a ` +
        `box ${fixed(width, 0)}x${fixed(height, 0)}px`,
      edge[0], smallest / 2,
    );
  }

  const gap = styleMeasure(style, "letter_spacing");
  if (gap && width > 0 && gap[1] * REM_BASE_PX > width) {
    note(
      `letter_spacing ${num(gap[1])} puts ${fixed(gap[1] * REM_BASE_PX, 0)}px between letters ` +
        `in a box ${fixed(width, 0)}px wide`,
      gap[0], width,
    );
  }

  if (!reasons.length) return null;
  const fixes = joined(
    [...limits].map(([field, room]) => `${field} at most ${pct(room)}`),
  );
  return {
    elementId: el.id,
    kind: "style_too_large",
    message:
      `${el.id} (${el.type}) is ${fixed(width, 0)}x${fixed(height, 0)}px and its style asks for ` +
      `more room than that: ${reasons.join("; ")}. style measurements are rem, not pixels -- ` +
      `px / ${num(REM_BASE_PX)} -- so write ${fixes}.`,
    key: `style_too_large|${el.id}`,
  };
}

/**
 * A box that runs off the edge of whatever holds it.
 *
 * Containers do not clip -- the panel sets `overflow: visible` on any element
 * with children -- so this draws rather than disappearing. It lands on top of
 * whatever sits beside the container, which is worse than being cut off,
 * because it looks intentional.
 */
export function overhangFinding(
  el: UIElement,
  p: Placement,
  parentName: string,
  parentPx: { width: number; height: number },
): ReviewFinding | null {
  const sides: string[] = [];
  if (p.x < -BOUNDS_EPSILON) {
    sides.push(`${fixed((-p.x / 100) * parentPx.width, 0)}px past the left (x ${pct(p.x)}%)`);
  }
  if (p.y < -BOUNDS_EPSILON) {
    sides.push(`${fixed((-p.y / 100) * parentPx.height, 0)}px past the top (y ${pct(p.y)}%)`);
  }
  if (p.x + p.w > 100 + BOUNDS_EPSILON) {
    sides.push(
      `${fixed(((p.x + p.w - 100) / 100) * parentPx.width, 0)}px past the right ` +
        `(x ${pct(p.x)}% + w ${pct(p.w)}% = ${pct(p.x + p.w)}%)`,
    );
  }
  if (p.y + p.h > 100 + BOUNDS_EPSILON) {
    sides.push(
      `${fixed(((p.y + p.h - 100) / 100) * parentPx.height, 0)}px past the bottom ` +
        `(y ${pct(p.y)}% + h ${pct(p.h)}% = ${pct(p.y + p.h)}%)`,
    );
  }
  if (!sides.length) return null;
  return {
    elementId: el.id,
    kind: "outside_its_container",
    message: `${el.id} extends beyond ${parentName}: ${sides.join(", and ")}.`,
    key: `outside_its_container|${el.id}`,
  };
}

/** An overlap smaller than this on either axis is a rounding artefact. */
const OVERLAP_MIN_PX = 1.0;
/** ...and it has to be a visible share of the smaller box, so two controls that
 *  graze a corner do not read like one sitting on top of another. */
const OVERLAP_MIN_SHARE = 1.0;
/** How many colliding neighbours a collapsed finding names before it counts the
 *  rest. All of them are still counted -- the sentence says "and N more". */
const OVERLAP_NAMED = 3;

/** How much two boxes share: pixels on each axis, and share of the smaller.
 *
 *  Null when they do not really collide -- no intersection, an intersection
 *  under a pixel on either axis (percentages are stored to four decimals, so
 *  that is rounding), or too small a share of the smaller box to read as one
 *  control sitting on another rather than two grazing a corner.
 *
 *  Checked between siblings only, by the caller. Boxes under different
 *  containers can legitimately overlap (a group laid over another is a design),
 *  and a container always contains its own children. */
export function overlapExtent(
  aBox: Placement,
  bBox: Placement,
): { oxPx: number; oyPx: number; share: number } | null {
  const ox = Math.min(aBox.x + aBox.w, bBox.x + bBox.w) - Math.max(aBox.x, bBox.x);
  const oy = Math.min(aBox.y + aBox.h, bBox.y + bBox.h) - Math.max(aBox.y, bBox.y);
  if (ox <= 0 || oy <= 0) return null;
  const oxPx = (ox / 100) * TOUCH_REFERENCE.width;
  const oyPx = (oy / 100) * TOUCH_REFERENCE.height;
  if (oxPx < OVERLAP_MIN_PX || oyPx < OVERLAP_MIN_PX) return null;
  const smaller = Math.min(aBox.w * aBox.h, bBox.w * bBox.h);
  const share = smaller ? (100 * (ox * oy)) / smaller : 100;
  if (share < OVERLAP_MIN_SHARE) return null;
  return { oxPx, oyPx, share };
}

/** One colliding pair, before the pairs are attributed to elements. */
interface OverlapPair {
  aId: string;
  bId: string;
  oxPx: number;
  oyPx: number;
  share: number;
}

/**
 * One finding per element rather than one per colliding pair.
 *
 * A single oversized box collides with everything beneath it, and reporting each
 * collision on its own line produced 23 warnings out of 56 on one page -- enough
 * to push the sizing failure that caused all of them out of reading range. The
 * reader then deletes the offender just to see the next round, which is what
 * actually happened.
 *
 * So the pairs are attributed to whichever element is in most of them and that
 * element answers for the lot in one sentence. Greedy and re-counted each round,
 * so the worst offender is named first and nothing is reported twice.
 *
 * A lone collision keeps the pairwise sentence and all of its arithmetic. Two
 * boxes on top of each other is the common case and is worth stating in full;
 * the collapse is for the case where stating it in full is the problem.
 */
export function overlapFindings(
  pairs: OverlapPair[],
  types: Map<string, string>,
  parentName: string,
): ReviewFinding[] {
  const findings: ReviewFinding[] = [];
  let remaining = pairs;
  while (remaining.length) {
    const counts = new Map<string, number>();
    for (const { aId, bId } of remaining) {
      counts.set(aId, (counts.get(aId) ?? 0) + 1);
      counts.set(bId, (counts.get(bId) ?? 0) + 1);
    }
    let owner: string | null = null;
    let best = 0;
    for (const [id, count] of counts) {
      if (owner === null || count > best || (count === best && id < owner)) {
        owner = id;
        best = count;
      }
    }
    if (owner === null) break; // unreachable: `remaining` is not empty
    const mine = remaining.filter((p) => p.aId === owner || p.bId === owner);
    remaining = remaining.filter((p) => p.aId !== owner && p.bId !== owner);
    const partners = mine
      .map((p) => ({
        id: p.aId === owner ? p.bId : p.aId,
        oxPx: p.oxPx,
        oyPx: p.oyPx,
        share: p.share,
      }))
      .sort((x, y) => (x.id < y.id ? -1 : x.id > y.id ? 1 : 0));
    findings.push(overlapMessage(owner, partners, types, parentName));
  }
  return findings;
}

function overlapMessage(
  owner: string,
  partners: { id: string; oxPx: number; oyPx: number; share: number }[],
  types: Map<string, string>,
  parentName: string,
): ReviewFinding {
  const ownerType = types.get(owner) ?? "?";
  const key = `overlap|${owner}|${partners.map((p) => p.id).join("|")}`;
  if (partners.length === 1) {
    const { id, oxPx, oyPx, share } = partners[0];
    return {
      elementId: owner,
      kind: "overlap",
      message:
        `${owner} (${ownerType}) and ${id} (${types.get(id) ?? "?"}) overlap by ` +
        `${fixed(oxPx, 0)}x${fixed(oyPx, 0)}px (${fixed(share, 0)}% of the smaller one) ` +
        `inside ${parentName}.`,
      key,
    };
  }
  const named = partners
    .slice(0, OVERLAP_NAMED)
    .map((p) => `${p.id} by ${fixed(p.oxPx, 0)}x${fixed(p.oyPx, 0)}px`)
    .join(", ");
  const rest = partners.length - OVERLAP_NAMED;
  const tail = rest > 0 ? `, and ${rest} more.` : ".";
  return {
    elementId: owner,
    kind: "overlap",
    message:
      `${owner} (${ownerType}) overlaps ${partners.length} elements inside ` +
      `${parentName}: ${named}${tail}`,
    key,
  };
}

// --- Deliberately stacked elements ---
//
// Two boxes in the same place are usually a mistake and sometimes the entire
// design: a tab strip is N panels at identical coordinates, each gated by a
// `visible_when` on the same key. Warning about those fires on every page that
// uses the pattern, and a checker that cries wolf on correct work teaches people
// to stop reading it -- which costs more than the collisions it does catch.
//
// So an overlap is suppressed only when the two conditions PROVABLY cannot both
// hold. Everything this cannot prove still warns: a missed collision between two
// conditionally-shown elements costs one warning, and a false one costs the
// credibility of every warning printed beside it.

const EQ_OPS = new Set(["eq", "equals", "=="]);
const NE_OPS = new Set(["ne", "not_equals", "!="]);
/** Range operators, mapped to whether the bound they set is inclusive. */
const LOWER_OPS: Record<string, boolean> = { gt: false, ">": false, gte: true, ">=": true };
const UPPER_OPS: Record<string, boolean> = { lt: false, "<": false, lte: true, "<=": true };

/** One condition, read off a `visible_when`. */
type Leaf = { key: string; op: string; value: unknown };

/**
 * Two authored literals, compared the way both surfaces can agree on.
 *
 * Not `==`: Python says `1 == True` and JavaScript says `"1" == 1`, and they
 * disagree about which. Same type family and same value, or different.
 */
function sameValue(left: unknown, right: unknown): boolean {
  if (typeof left === "boolean" || typeof right === "boolean") {
    return typeof left === "boolean" && typeof right === "boolean" && left === right;
  }
  if (typeof left === "number" && typeof right === "number") return left === right;
  if (typeof left === "string" && typeof right === "string") return left === right;
  return left === null && right === null;
}

/** A condition value as a number, or null when it is not one.
 *
 *  A numeric string is deliberately not one: the panel compares it with
 *  JavaScript's coercing `>`, which has no Python equivalent worth mirroring,
 *  and undecidable here only costs a warning nobody needed suppressed. */
function conditionNumber(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

/** Whether a key pinned to `value` satisfies `op target`.
 *
 *  True for anything undecidable, so an unreadable pair yields no proof of
 *  exclusivity rather than a false one. */
function conditionHolds(value: unknown, op: string, target: unknown): boolean {
  if (EQ_OPS.has(op)) return sameValue(value, target);
  if (NE_OPS.has(op)) return !sameValue(value, target);
  if (op === "truthy" || op === "falsy") {
    const scalar =
      value === null ||
      typeof value === "string" ||
      typeof value === "number" ||
      typeof value === "boolean";
    if (!scalar) return true;
    return op === "truthy" ? !!value : !value;
  }
  if (op in LOWER_OPS || op in UPPER_OPS) {
    const left = conditionNumber(value);
    const right = conditionNumber(target);
    if (left === null || right === null) return true;
    if (op in LOWER_OPS) return LOWER_OPS[op] ? left >= right : left > right;
    return UPPER_OPS[op] ? left <= right : left < right;
  }
  return true;
}

/** Whether condition `a` rules out `b`, read in one direction only. */
function rulesOut(a: Leaf, b: Leaf): boolean {
  if (EQ_OPS.has(a.op)) {
    // `key == a.value` pins the key to one value, so the other condition is
    // either satisfied by that value or contradicts it outright.
    return !conditionHolds(a.value, b.op, b.value);
  }
  if (a.op === "truthy" && b.op === "falsy") return true;
  if (a.op in LOWER_OPS && b.op in UPPER_OPS) {
    const low = conditionNumber(a.value);
    const high = conditionNumber(b.value);
    if (low === null || high === null) return false;
    const closed = LOWER_OPS[a.op] && UPPER_OPS[b.op];
    return high < low || (high === low && !closed);
  }
  return false;
}

/** One condition as a leaf, or null if it is unreadable. */
function conditionLeaf(condition: unknown): Leaf | null {
  if (!condition || typeof condition !== "object" || Array.isArray(condition)) return null;
  const raw = condition as Record<string, unknown>;
  if (typeof raw.key !== "string" || !raw.key) return null;
  return {
    key: raw.key,
    op: String(raw.operator || "eq").toLowerCase(),
    value: raw.value ?? null,
  };
}

/**
 * A `visible_when` as branches of ANDed leaves: visible if any branch is.
 *
 * A bare condition and an `all:` block are one branch; an `any:` block is one
 * branch per condition. Null means there is nothing to reason about, which is
 * not the same as an empty list -- an empty list of branches is never
 * satisfiable, and would suppress every overlap on the page.
 *
 * An unreadable leaf inside `all:` is dropped, which only widens the set: if the
 * wider condition is still exclusive, so is the real one. Inside `any:` the same
 * drop would NARROW it, which could prove an exclusivity that is not there, so
 * the whole block goes undecidable instead.
 */
function visibleWhenBranches(el: UIElement): Leaf[][] | null {
  const show = ((el.bindings ?? {}) as Record<string, unknown>).show as
    | Record<string, unknown>
    | undefined;
  const when = show?.visible_when;
  if (!when || typeof when !== "object" || Array.isArray(when)) return null;
  const block = when as Record<string, unknown>;

  if (Array.isArray(block.any)) {
    const leaves = block.any.map(conditionLeaf);
    if (!leaves.length || leaves.some((leaf) => leaf === null)) return null;
    return leaves.map((leaf) => [leaf as Leaf]);
  }
  if (Array.isArray(block.all)) {
    const kept = block.all.map(conditionLeaf).filter((leaf): leaf is Leaf => !!leaf);
    return kept.length ? [kept] : null;
  }
  const leaf = conditionLeaf(block);
  return leaf ? [[leaf]] : null;
}

/**
 * Whether two elements can never be on screen at the same time.
 *
 * Proof, not inference: every way `a` can be visible has to contradict every way
 * `b` can be. Two conditions contradict when they name the same key and no
 * single value satisfies both -- different `eq` values, an `eq` against its own
 * `ne`, `truthy` against `falsy`, or two bounds that leave no room between them.
 */
export function mutuallyExclusive(a: UIElement, b: UIElement): boolean {
  const aBranches = visibleWhenBranches(a);
  const bBranches = visibleWhenBranches(b);
  if (!aBranches?.length || !bBranches?.length) return false;
  return aBranches.every((aLeaves) =>
    bBranches.every((bLeaves) =>
      aLeaves.some((x) =>
        bLeaves.some((y) => x.key === y.key && (rulesOut(x, y) || rulesOut(y, x))),
      ),
    ),
  );
}

/**
 * An element the primary arrangement never positions.
 *
 * The renderer's fallback for a missing placement is {0, 0, 100, 100} -- it
 * fills its parent and covers everything already there. Nothing in the file says
 * so, which makes this the one geometry defect with no geometry to look at.
 */
export function noBoxFinding(el: UIElement, parentName: string): ReviewFinding {
  return {
    elementId: el.id,
    kind: "no_placement",
    message:
      `${el.id} (${el.type}) has no placement, so it fills ${parentName} edge to edge ` +
      `and covers whatever is already there. Give it {x, y, w, h}.`,
    key: `no_placement|${el.id}`,
  };
}

/** Every type the panel has a renderer for.
 *
 *  Read off the generated binding-reach table rather than listed again: a test
 *  re-derives that table's keys from `renderElement`'s dispatch in panel.js, so
 *  this IS the renderer's own set. */
const RENDERED_TYPES = new Set(Object.keys(HONORED_SHOW_SLOTS));

/**
 * A type the panel has no renderer for, which draws nothing at all.
 *
 * `UIElement.type` is a free-form string and every layer downstream treats it as
 * one: the loader accepts anything, this review used to return early on a type it
 * did not know, and `renderElement`'s switch falls through to a `console.warn`
 * and returns null. So the element has an id, a placement and bindings, the write
 * comes back created, and it is simply absent from the screen.
 *
 * The message lists the whole set on purpose -- it is the only place an author
 * who guessed is told what the alternatives are.
 */
export function elementTypeFinding(el: UIElement): ReviewFinding | null {
  if (RENDERED_TYPES.has(el.type)) return null;
  return {
    elementId: el.id,
    kind: "unknown_element_type",
    message:
      `${el.id} has type '${el.type}', which the panel has no renderer for, so it draws ` +
      `nothing at all. The types are: ${[...RENDERED_TYPES].sort().join(", ")}.`,
    key: `unknown_element_type|${el.id}`,
  };
}

/**
 * Bindings this element type's renderer never reads.
 *
 * Nothing rejects these and nothing logs them at runtime: the element draws, the
 * state key resolves, and the binding simply has no code path behind it for this
 * type. From the outside it is indistinguishable from a value that never
 * changes -- and the Builder does not even offer an editor for the slot, so a
 * binding written by anything else is invisible until it is badged.
 */
export function bindingFindings(el: UIElement): ReviewFinding[] {
  const honored = own(HONORED_SHOW_SLOTS, el.type);
  if (!honored) return []; // a type this module has never heard of; say nothing
  const show = ((el.bindings ?? {}) as Record<string, unknown>).show as
    | Record<string, unknown>
    | undefined;
  if (!show || typeof show !== "object") return [];

  const findings: ReviewFinding[] = [];
  const reads =
    REVIEWED_SHOW_SLOTS.filter((slot) => honored.includes(slot))
      .map((slot) => `show.${slot}`)
      .join(", ") || "nothing from show but show.visible_when";

  for (const slot of REVIEWED_SHOW_SLOTS) {
    if (!show[slot] || honored.includes(slot)) continue;
    const extra =
      slot === "look" && honored.includes("value")
        ? " Put whatever depended on it into show.value (a condition with " +
          "text_true / text_false), or use a button."
        : "";
    findings.push({
      elementId: el.id,
      kind: "binding_not_rendered",
      message:
        `${el.id} (${el.type}) declares show.${slot}, which a ${el.type} does not render ` +
        `-- it reads ${reads}. That binding has no effect.${extra}`,
      key: `binding_not_rendered|${el.id}|${slot}`,
    });
  }

  // A look binding can be honored for its colour while its state labels are
  // not: a status LED tints its dot, a select styles its options, and neither
  // has anywhere to put text.
  const look = show.look as Record<string, unknown> | undefined;
  const states = look && typeof look === "object" ? look.states : undefined;
  if (
    honored.includes("look") &&
    !STATE_LABEL_TYPES.includes(el.type) &&
    states && typeof states === "object" && !Array.isArray(states)
  ) {
    const labelled = Object.entries(states as Record<string, unknown>)
      .filter(([, spec]) =>
        spec && typeof spec === "object" && !Array.isArray(spec) &&
        (spec as Record<string, unknown>).label !== undefined &&
        (spec as Record<string, unknown>).label !== null)
      .map(([name]) => name)
      .sort();
    if (labelled.length) {
      findings.push({
        elementId: el.id,
        kind: "binding_not_rendered",
        message:
          `${el.id} (${el.type}): show.look.states sets a label for ` +
          `${labelled.join(", ")}, but a ${el.type} takes only colour from show.look, ` +
          `so that text never appears. Use the element's own label, or a label ` +
          `element bound to the same key.`,
        key: `binding_not_rendered|${el.id}|look.states.label`,
      });
    }
  }
  return findings;
}

/** The page-space box an element's percentages are measured against, in px. */
function parentBoxPx(
  parentId: string | null,
  absolute: Record<string, Placement>,
): { width: number; height: number } {
  const box = (parentId ? own(absolute, parentId) : undefined) ?? PAGE_BOX;
  return {
    width: (box.w / 100) * TOUCH_REFERENCE.width,
    height: (box.h / 100) * TOUCH_REFERENCE.height,
  };
}

function withWhere(finding: ReviewFinding, where: string): ReviewFinding {
  if (!where) return finding;
  return {
    ...finding,
    message: finding.message.replace(/\.+$/, "") + `${where}.`,
  };
}

export interface ReviewOptions {
  /** The project's own `theme_overrides.element_defaults.slider`, which is the
   *  only theme value any control minimum moves with. */
  theme?: ElementDefaults;
  /** Only these elements answer. Everything, when omitted. */
  touched?: Set<string>;
  /** Review only this arrangement, for a surface that is showing one. Every
   *  arrangement, when omitted -- which is what the AI door does, because a
   *  portrait variant can starve a control the primary leaves fine. */
  layoutId?: string | null;
}

/**
 * Everything a page will draw wrong, across every arrangement.
 *
 * Ordered and de-duplicated exactly the way the Python side is: bindings are a
 * property of the element and answered once; geometry is answered per
 * arrangement with the primary first, so its phrasing wins and a variant only
 * speaks up about something the primary got right.
 */
export function reviewPage(page: UIPage, options: ReviewOptions = {}): ReviewFinding[] {
  const { theme, touched, layoutId } = options;
  const inScope = (id: string) => (touched ? touched.has(id) : true);
  const findings: ReviewFinding[] = [];

  for (const el of page.elements) {
    if (!inScope(el.id)) continue;
    const typeFinding = elementTypeFinding(el);
    if (typeFinding) findings.push(typeFinding);
    findings.push(...bindingFindings(el));
  }

  const layouts = page.layouts ?? [];
  if (!layouts.length) return findings;

  const primaryId = primaryLayout(page)?.id;
  // One arrangement when the caller is looking at one (an unknown id resolves
  // to the primary, the way every other read of a layout id does).
  const chosen = layoutId === undefined ? layouts : [layoutById(page, layoutId) ?? layouts[0]];
  // Primary first: it is the arrangement every variant inherits from, so its
  // phrasing ("...") beats the variant's ("... in the portrait arrangement").
  const ordered = [...chosen].sort(
    (a, b) => Number(a.id !== primaryId) - Number(b.id !== primaryId),
  );
  const seen = new Set<string>();
  for (const layout of ordered) {
    const where = layout.id === primaryId ? "" : ` in the '${layout.id}' arrangement`;
    for (const finding of layoutFindings(page, layout.id, {
      theme,
      inScope,
      isPrimary: layout.id === primaryId,
    })) {
      if (seen.has(finding.key)) continue;
      seen.add(finding.key);
      findings.push(withWhere(finding, where));
    }
  }
  return findings;
}

/** Every geometry finding for one arrangement. */
function layoutFindings(
  page: UIPage,
  layoutId: string,
  ctx: {
    theme?: ElementDefaults;
    inScope: (id: string) => boolean;
    isPrimary: boolean;
  },
): ReviewFinding[] {
  const absolute = absolutePlacements(page, layoutId);
  const ownBoxes = resolvePlacements(page, layoutId);
  const hidden = resolveHidden(page, layoutId);
  const byId = new Map(page.elements.map((e) => [e.id, e]));
  const findings: ReviewFinding[] = [];

  const parentOf = (id: string): string | null => {
    const named = byId.get(id)?.parent;
    return named && byId.has(named) && named !== id ? named : null;
  };
  const parentName = (id: string): string => {
    const parent = parentOf(id);
    return parent ? `its container '${parent}'` : "the page";
  };
  /** Every container above this element, innermost first.
   *
   *  Only reached when a floor is larger than the immediate parent, and only as
   *  far as the boxes actually resolve -- a container the arrangement never
   *  positions ends the chain rather than guessing a size for it. */
  const ancestorsOf = (id: string): Container[] => {
    const chain: Container[] = [];
    const seen = new Set<string>([id]);
    let cursor = parentOf(id);
    while (cursor && !seen.has(cursor)) {
      seen.add(cursor);
      const box = own(absolute, cursor);
      if (!box) break;
      chain.push({
        label: `'${cursor}'`,
        widthPx: (box.w / 100) * TOUCH_REFERENCE.width,
        heightPx: (box.h / 100) * TOUCH_REFERENCE.height,
      });
      cursor = parentOf(cursor);
    }
    return chain;
  };

  for (const el of page.elements) {
    if (!ctx.inScope(el.id) || hidden.has(el.id)) continue;
    const box = own(absolute, el.id);
    if (!box) {
      // A variant legitimately carries no delta for an element -- it inherits
      // one. Only the primary having no box means it has none at all.
      if (ctx.isPrimary) findings.push(noBoxFinding(el, parentName(el.id)));
      continue;
    }
    const parentPx = parentBoxPx(parentOf(el.id), absolute);
    const boxPx = {
      width: (box.w / 100) * TOUCH_REFERENCE.width,
      height: (box.h / 100) * TOUCH_REFERENCE.height,
    };
    const candidates = [
      starvationFinding(el, boxPx, parentPx, parentName(el.id), ctx.theme, ancestorsOf(el.id)),
      degenerateFinding(el, boxPx, parentPx, parentName(el.id), ctx.theme),
      touchFinding(el, boxPx),
      styleFinding(el, boxPx),
    ];
    const stored = own(ownBoxes, el.id);
    if (stored) {
      candidates.push(overhangFinding(el, stored, parentName(el.id), parentPx));
    }
    for (const finding of candidates) if (finding) findings.push(finding);
  }

  // Siblings, in the space they share.
  //
  // An element that also hangs out of its container is NOT excused here, which
  // was tried and was wrong: two boxes can overlap in the middle of a container
  // while one of them separately runs off the edge, and fixing the overflow does
  // not touch the collision. Narrow a box from 80% to 70% and it sits inside its
  // parent while still lying on the neighbour it started at. Reporting both is
  // right; the volume that suppression was aimed at is what overlapFindings
  // collapses.
  const types = new Map(page.elements.map((e) => [e.id, e.type]));
  const byParent = new Map<string | null, string[]>();
  for (const el of page.elements) {
    if (hidden.has(el.id) || !own(absolute, el.id)) continue;
    const parent = parentOf(el.id);
    const kids = byParent.get(parent);
    if (kids) kids.push(el.id);
    else byParent.set(parent, [el.id]);
  }
  for (const [parent, kids] of byParent) {
    kids.sort();
    const pairs: OverlapPair[] = [];
    for (let i = 0; i < kids.length; i++) {
      for (let j = i + 1; j < kids.length; j++) {
        if (!ctx.inScope(kids[i]) && !ctx.inScope(kids[j])) continue;
        const a = byId.get(kids[i]) as UIElement;
        const b = byId.get(kids[j]) as UIElement;
        if (mutuallyExclusive(a, b)) continue;
        const extent = overlapExtent(
          own(absolute, kids[i]) as Placement,
          own(absolute, kids[j]) as Placement,
        );
        if (extent) pairs.push({ aId: kids[i], bId: kids[j], ...extent });
      }
    }
    findings.push(
      ...overlapFindings(pairs, types, parent ? `'${parent}'` : "the page"),
    );
  }
  return findings;
}

/**
 * The same checks a master element can be given.
 *
 * A master borrows no page's layout -- its box is a percentage of the viewport,
 * keyed by orientation -- so it has no siblings to collide with and no container
 * to hang out of. What is left is whether it holds its own contents, whether a
 * finger can hit it, and whether its bindings are read.
 */
export function reviewMasterElement(
  master: MasterElement,
  theme?: ElementDefaults,
): ReviewFinding[] {
  const typeFinding = elementTypeFinding(master);
  const findings = typeFinding
    ? [typeFinding, ...bindingFindings(master)]
    : bindingFindings(master);
  const placements = master.placements;
  if (!placements || typeof placements !== "object") return findings;
  const seen = new Set<string>();
  for (const [orientation, box] of Object.entries(placements)) {
    const boxPx = {
      width: ((box?.w ?? 100) / 100) * TOUCH_REFERENCE.width,
      height: ((box?.h ?? 100) / 100) * TOUCH_REFERENCE.height,
    };
    if (!Number.isFinite(boxPx.width) || !Number.isFinite(boxPx.height)) continue;
    const candidates = [
      starvationFinding(
        master, boxPx,
        { width: TOUCH_REFERENCE.width, height: TOUCH_REFERENCE.height },
        "the page", theme,
      ),
      degenerateFinding(
        master, boxPx,
        { width: TOUCH_REFERENCE.width, height: TOUCH_REFERENCE.height },
        "the page", theme,
      ),
      touchFinding(master, boxPx),
      styleFinding(master, boxPx),
    ];
    for (const finding of candidates) {
      if (!finding || seen.has(finding.key)) continue;
      seen.add(finding.key);
      findings.push(withWhere(finding, ` in the ${orientation} arrangement`));
    }
  }
  return findings;
}

/**
 * The review folded into one tooltip per element, for the canvas badges.
 *
 * Everything an element is guilty of, not just the first thing -- a control can
 * be starved AND overlapping AND out of its container, and picking one to show
 * would hide the other two behind a fix for the one.
 */
export function reviewWarningsByElement(
  page: UIPage,
  layoutId: string | null = null,
  theme?: ElementDefaults,
): Map<string, string> {
  const byElement = new Map<string, string[]>();
  for (const finding of reviewPage(page, { theme, layoutId })) {
    const lines = byElement.get(finding.elementId);
    if (lines) lines.push(finding.message);
    else byElement.set(finding.elementId, [finding.message]);
  }
  return new Map([...byElement].map(([id, lines]) => [id, lines.join("\n\n")]));
}

/**
 * The theme values a control minimum can move with, out of a project.
 *
 * Only a slider's thumb moves, and only through `element_defaults`. A custom
 * theme FILE that changed it is not seen here, but every built-in theme ships
 * the 44px default, so the project override is the case that can differ.
 */
export function sliderThemeDefaults(project: ProjectConfig | null | undefined): ElementDefaults {
  const overrides = (project?.ui?.settings?.theme_overrides ?? {}) as Record<string, unknown>;
  const defaults = (overrides.element_defaults ?? {}) as Record<string, unknown>;
  const slider = defaults.slider;
  return slider && typeof slider === "object" && !Array.isArray(slider)
    ? (slider as Record<string, unknown>)
    : null;
}

export interface ValidationIssue {
  severity: "error" | "warning";
  message: string;
  location: string;
  pageId?: string;
  elementId?: string;
}

export function validateProject(project: ProjectConfig): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  const pageIds = new Set(project.ui.pages.map((p) => p.id));
  const deviceIds = new Set(project.devices.map((d) => d.id));
  const macroIds = new Set(project.macros.map((m) => m.id));
  const checkElement = (el: UIElement, pageId: string, pageName: string) => {
    const loc = `${pageName} > ${el.id}`;
    const bindings = (el.bindings || {}) as Record<string, unknown>;
    const show = (bindings.show || {}) as Record<string, unknown>;
    const doMap = (bindings.do || {}) as Record<string, unknown>;

    // page_nav target
    if (el.type === "page_nav" && el.target_page && !pageIds.has(el.target_page)) {
      issues.push({ severity: "error", message: `Target page "${el.target_page}" does not exist`, location: loc, pageId, elementId: el.id });
    }

    // One action checker for every interaction. Recurses into value_map
    // per-option actions the same way the engine executes them.
    const checkAction = (b: Record<string, unknown>, slotLoc: string) => {
      if (b.action === "ui.navigate" && b.page && !pageIds.has(b.page as string)) {
        issues.push({ severity: "error", message: `Navigate to deleted page "${b.page}"`, location: slotLoc, pageId, elementId: el.id });
      }
      if (b.action === "device.command" && b.device && !deviceIds.has(b.device as string)) {
        issues.push({ severity: "error", message: `Device "${b.device}" not found`, location: slotLoc, pageId, elementId: el.id });
      }
      if (b.action === "macro" && b.macro && !macroIds.has(b.macro as string)) {
        issues.push({ severity: "error", message: `Macro "${b.macro}" not found`, location: slotLoc, pageId, elementId: el.id });
      }
      if (b.action === "value_map" && b.map && typeof b.map === "object" && !Array.isArray(b.map)) {
        for (const [optValue, mapped] of Object.entries(b.map as Record<string, unknown>)) {
          if (mapped && typeof mapped === "object" && !Array.isArray(mapped)) {
            checkAction(mapped as Record<string, unknown>, `${slotLoc} > "${optValue}"`);
          }
        }
      }
    };

    // DOES — each interaction holds an array of actions (legacy single objects
    // are normalized by slotActions). Check every action in each interaction.
    for (const slot of ACTION_SLOTS) {
      for (const b of slotActions(doMap, slot)) {
        checkAction(b, `${loc} > ${slot}`);
      }
    }

    // SHOWS → value / look device references
    const valueBinding = show.value as Record<string, unknown> | undefined;
    const lookBinding = show.look as Record<string, unknown> | undefined;
    for (const [b, label] of [[valueBinding, "value"], [lookBinding, "appearance"]] as const) {
      const key = b?.key as string | undefined;
      if (key?.startsWith("device.")) {
        const deviceId = key.split(".")[1];
        if (!deviceIds.has(deviceId)) {
          issues.push({ severity: "warning", message: `State key references unknown device "${deviceId}"`, location: `${loc} > ${label}`, pageId, elementId: el.id });
        }
      }
    }

    // A control whose Value reads a device key but never sends a command can't
    // actually drive that device — the drag/selection updates only the local
    // mirror, overwritten on the next poll. Two-way to a device must go through
    // a command (do.<interaction> device.command with $value), never a state
    // write. Display-only controls reading device state are fine.
    const cap = BINDING_CAPABILITIES[el.type];
    const valueKey = valueBinding?.key as string | undefined;
    if (cap?.value?.link && valueKey?.startsWith("device.")) {
      const hasCommand = (cap.does ?? []).some((d) =>
        actionsCommandDevice(slotActions(doMap, d.interaction)),
      );
      if (!hasCommand) {
        issues.push({ severity: "warning", message: `This control shows a device value but has no command to change it — add a command so touching it reaches the device`, location: `${loc} > value`, pageId, elementId: el.id });
      }
    }

    // SHOWS → visible_when conditions
    const vw = show.visible_when as Record<string, unknown> | undefined;
    if (vw) {
      const conditions = (vw.all || vw.any || [vw]) as Array<{ key?: string }>;
      for (const c of conditions) {
        if (c.key?.startsWith("device.")) {
          const deviceId = c.key.split(".")[1];
          if (!deviceIds.has(deviceId)) {
            issues.push({ severity: "warning", message: `Visibility condition references unknown device "${deviceId}"`, location: `${loc} > visible_when`, pageId, elementId: el.id });
          }
        }
      }
    }

    // Unbound interactive elements — an interactive control with no DOES action
    // and no two-way value does nothing when touched.
    if (["button", "slider", "fader", "select", "text_input", "keypad", "matrix", "list"].includes(el.type)) {
      const hasDoAction = ACTION_SLOTS.some((slot) => slotActions(doMap, slot).length > 0);
      const hasTwoWayValue = !!valueBinding?.write_back;
      if (!hasDoAction && !hasTwoWayValue) {
        issues.push({ severity: "warning", message: `Interactive element has no action`, location: loc, pageId, elementId: el.id });
      }
    }
  };

  // Check page elements. Geometry and binding reach come back from the shared
  // review -- the same code, the same numbers and the same sentences the AI
  // write path answers with, so the two surfaces cannot reach different
  // verdicts about the same file. Geometry is per-arrangement: a portrait
  // variant can push a control off-screen or starve it while the primary is
  // fine, so every layout is checked, and an issue is reported once per element
  // naming the arrangement only when it isn't the primary's fault.
  const theme = sliderThemeDefaults(project);
  for (const page of project.ui.pages) {
    const byId = new Map(page.elements.map((e) => [e.id, e]));
    const layoutIds = new Set((page.layouts ?? []).map((l) => l.id));

    // The arrangements themselves: a dangling inherits silently collapses the
    // variant (everything without its own box falls to default placements),
    // and orphaned ids are the residue a bad rename or hand edit leaves.
    for (const layout of page.layouts ?? []) {
      if (layout.inherits && !layoutIds.has(layout.inherits)) {
        issues.push({ severity: "error", message: `Arrangement inherits from "${layout.inherits}", which does not exist \u2014 controls without their own position here fall back to default boxes`, location: `${page.name} > ${layout.id}`, pageId: page.id });
      }
      for (const elId of Object.keys(layout.placements ?? {})) {
        if (!byId.has(elId)) {
          issues.push({ severity: "warning", message: `Arrangement "${layout.id}" positions "${elId}", which is not an element on this page`, location: `${page.name} > ${layout.id}`, pageId: page.id });
        }
      }
      for (const elId of layout.hidden ?? []) {
        if (!byId.has(elId)) {
          issues.push({ severity: "warning", message: `Arrangement "${layout.id}" hides "${elId}", which is not an element on this page`, location: `${page.name} > ${layout.id}`, pageId: page.id });
        }
      }
    }

    for (const el of page.elements) {
      checkElement(el, page.id, page.name);
      // A parent that doesn't exist leaves the child unrendered \u2014 the runtime
      // draws children into their container, and there is no container.
      if (el.parent && !byId.has(el.parent)) {
        issues.push({ severity: "error", message: `Container "${el.parent}" does not exist`, location: `${page.name} > ${el.id}`, pageId: page.id, elementId: el.id });
      }
    }

    for (const finding of reviewPage(page, { theme })) {
      issues.push({
        severity: "warning",
        message: finding.message,
        location: `${page.name} > ${finding.elementId}`,
        pageId: page.id,
        elementId: finding.elementId,
      });
    }
  }

  // Check master elements
  for (const mel of project.ui.master_elements || []) {
    checkElement(mel, "", "Master Elements");
    for (const finding of reviewMasterElement(mel, theme)) {
      issues.push({
        severity: "warning",
        message: finding.message,
        location: `Master Elements > ${finding.elementId}`,
        elementId: finding.elementId,
      });
    }
    if (Array.isArray(mel.pages)) {
      for (const pid of mel.pages) {
        if (!pageIds.has(pid)) {
          issues.push({ severity: "error", message: `References deleted page "${pid}"`, location: `Master Elements > ${mel.id}` });
        }
      }
    }
  }

  // Check macro steps
  for (const macro of project.macros) {
    const checkStep = (step: MacroStep, prefix: string) => {
      if (step.action === "device.command" && step.device && !deviceIds.has(step.device)) {
        issues.push({ severity: "error", message: `Device "${step.device}" not found`, location: `${prefix} > ${step.description || step.action}` });
      }
      if (step.action === "macro" && step.macro && !macroIds.has(step.macro)) {
        issues.push({ severity: "error", message: `Macro "${step.macro}" not found`, location: `${prefix} > ${step.description || "call macro"}` });
      }
      step.then_steps?.forEach((s) => checkStep(s, prefix));
      step.else_steps?.forEach((s) => checkStep(s, prefix));
    };
    for (const step of macro.steps) {
      checkStep(step, `Macro "${macro.name}"`);
    }
  }

  // Check idle_page
  const idlePage = project.ui.settings.idle_page;
  if (idlePage && !pageIds.has(idlePage)) {
    issues.push({ severity: "error", message: `Idle page "${idlePage}" does not exist`, location: "Settings > Idle Page" });
  }

  return issues;
}
