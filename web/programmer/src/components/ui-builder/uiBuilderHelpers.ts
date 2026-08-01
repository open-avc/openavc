import type { UIPage, UIElement, Placement, Layout, SnapConfig, MasterElement, PageGroup, MacroConfig, MacroStep, VariableConfig, ScriptConfig, ProjectConfig } from "../../api/types";
import type { PluginExtension } from "../../api/pluginClient";

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
  { type: "spacer", label: "Spacer", category: "display", description: "Empty space for layout alignment" },
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
  // page_nav / image / spacer / group / clock / plugin: "Visible when…" only.
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
  spacer: { w: 8.3333, h: 12.5 },
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
    case "spacer":
      return { ...base };
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

/** 1rem at the 1280x800 reference, where `1.75vmin` resolves to 14px. */
export const REM_BASE_PX = 14;

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
 * Fold an inherits chain down to one placement map, base first so the chosen
 * layout's own placements win. The seen-set is a cycle guard -- a hand-edited
 * project can point two layouts at each other and the builder still has to draw
 * something. Mirrors the panel runtime's _selectLayout.
 */
export function resolvePlacements(
  page: UIPage,
  layoutId?: string | null,
): Record<string, Placement> {
  const layouts = page.layouts ?? [];
  const chosen = layoutById(page, layoutId);
  if (!chosen) return {};
  const chain: Layout[] = [];
  const seen = new Set<string>();
  let cursor: Layout | undefined = chosen;
  while (cursor && !seen.has(cursor.id)) {
    seen.add(cursor.id);
    chain.unshift(cursor);
    cursor = cursor.inherits ? layouts.find((l) => l.id === cursor!.inherits) : undefined;
  }
  const placements: Record<string, Placement> = {};
  for (const layout of chain) Object.assign(placements, layout.placements ?? {});
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

/** Every line a moving edge can be attracted to on one axis. */
function magnetTargets(others: Placement[], axis: "x" | "y"): number[] {
  const targets = [0, 100 / 3, 50, (100 * 2) / 3, 100];
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
 * coordinates are once it is in there.
 *
 * Dropping a control inside a container is how you say it belongs to it, so a
 * drop that lands fully inside one adopts into it -- the same rule the 0.8.0
 * migration used on existing projects. Innermost (smallest) wins when
 * containers nest, and a partial overlap adopts nothing: it stays a page-level
 * peer rendered over the frame, which is conservative and never surprising.
 */
export function resolveDropParent(
  page: UIPage,
  box: Placement,
  layoutId?: string | null,
): { parentId: string | null; relative: Placement } {
  const placements = resolvePlacements(page, layoutId);
  let best: { id: string; area: number; box: Placement } | null = null;
  for (const el of page.elements) {
    if (el.type !== "group") continue;
    const c = placements[el.id];
    if (!c) continue;
    const contains =
      box.x >= c.x - BOUNDS_EPSILON &&
      box.y >= c.y - BOUNDS_EPSILON &&
      box.x + box.w <= c.x + c.w + BOUNDS_EPSILON &&
      box.y + box.h <= c.y + c.h + BOUNDS_EPSILON;
    if (!contains) continue;
    const area = c.w * c.h;
    if (!best || area < best.area) best = { id: el.id, area, box: c };
  }
  if (!best || best.box.w <= 0 || best.box.h <= 0) {
    return { parentId: null, relative: roundPlacement(box) };
  }
  return {
    parentId: best.id,
    relative: roundPlacement({
      x: ((box.x - best.box.x) / best.box.w) * 100,
      y: ((box.y - best.box.y) / best.box.h) * 100,
      w: (box.w / best.box.w) * 100,
      h: (box.h / best.box.h) * 100,
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
 *  longer exists. The runtime accepts "page" as an alias for "navigate"
 *  (engine.py executes both), and a value_map runs the per-option action
 *  branches, so both are checked here too — AI tools and imports emit them,
 *  and a Broken badge that misses them lets a dead reference look fine. */
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
  if ((act === "navigate" || act === "page") && a.page && !ids.pageIds.has(a.page as string)) {
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
  if (act === "navigate" || act === "page") return !a.page;
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
    (a as Record<string, unknown>).action === "navigate" &&
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
      const parentBox = getPlacement(p, elementId);
      const promoted: Record<string, Placement> = {};
      for (const child of children) {
        const rel = getPlacement(p, child.id);
        promoted[child.id] = {
          x: parentBox.x + (rel.x / 100) * parentBox.w,
          y: parentBox.y + (rel.y / 100) * parentBox.h,
          w: (rel.w / 100) * parentBox.w,
          h: (rel.h / 100) * parentBox.h,
        };
      }
      next = {
        ...next,
        elements: next.elements.map((e) =>
          e.parent === elementId ? { ...e, parent: target.parent ?? null } : e,
        ),
      };
      next = withPlacements(next, promoted);
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
  const src = getPlacement(page, elementId);
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
// trade places with — the OutlinePanel passes the *visible* neighbour from its
// (possibly search-filtered) list, so reordering swaps the element the user
// actually sees adjacent to it, not a hidden full-list neighbour. With no
// filter the visible neighbour is the full-list adjacent, so this reduces to a
// plain adjacent move.
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
    return cloned;
  });

  const newPage: UIPage = {
    ...JSON.parse(JSON.stringify(page)),
    id: newId,
    name: newName,
    elements: newElements,
  };

  // Insert after source page
  const idx = pages.findIndex((p) => p.id === pageId);
  const result = [...pages];
  result.splice(idx + 1, 0, newPage);
  return result;
}

// --- Alignment helpers ---

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
    const targets = p.elements.filter((el) => elementIds.includes(el.id));
    if (targets.length === 0) return p;

    const boxes = new Map(targets.map((el) => [el.id, getPlacement(p, el.id, layoutId)]));

    // Several selected: align to the selection's own bounding box, which is
    // what "line these up with each other" means. One selected: align to its
    // parent box, which is the whole page (0..100) or the container it sits in
    // -- and in percentages the container IS 0..100 of itself, so it is the
    // same arithmetic either way.
    let left = 0, right = 100, top = 0, bottom = 100;
    if (targets.length > 1) {
      const all = [...boxes.values()];
      left = Math.min(...all.map((b) => b.x));
      right = Math.max(...all.map((b) => b.x + b.w));
      top = Math.min(...all.map((b) => b.y));
      bottom = Math.max(...all.map((b) => b.y + b.h));
    }

    const moved: Record<string, Placement> = {};
    for (const el of targets) {
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
    return withPlacements(p, moved, layoutId);
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
  // page whatever those pages are arranged like. Take the box it had on the
  // page it is being promoted from as its landscape placement.
  const box = getPlacement(page, elementId);

  // Remove from page (and drop the box it no longer needs there)
  const newPages = pages.map(p =>
    p.id === pageId
      ? withoutPlacement(
          { ...p, elements: p.elements.filter(e => e.id !== elementId) },
          elementId,
        )
      : p
  );

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

  // Add to master elements with pages: "*"
  const masterEl: MasterElement = {
    ...promoted,
    pages: "*",
    placements: { landscape: roundPlacement(box) },
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

/** Percentages are floats; a hair over 100 is rounding, not a mistake. */
const BOUNDS_EPSILON = 0.01;

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

/** IDs of elements hanging outside their parent -- the page, or the container
 *  they belong to. A child's percentages are of its container, so the same
 *  0..100 test covers both cases; only the box it is measured against changes.
 *  Used by the canvas to badge live, not just when Validate is pressed. */
export function findOutOfBoundsIds(
  page: UIPage,
  layoutId?: string | null,
): Set<string> {
  const placements = resolvePlacements(page, layoutId);
  const ids = new Set<string>();
  for (const el of page.elements) {
    const p = placements[el.id];
    if (p && isOutOfBounds(p)) ids.add(el.id);
  }
  return ids;
}

/** IDs of elements that overlap a sibling under the same parent.
 *
 *  Containers are excluded: overlapping their children is the entire point of
 *  being a container. Siblings are only compared within one parent, because a
 *  child's percentages mean nothing next to a page-level element's. */
export function findOverlappingIds(
  page: UIPage,
  layoutId?: string | null,
): Set<string> {
  const placements = resolvePlacements(page, layoutId);
  const ids = new Set<string>();
  const byParent = new Map<string, UIElement[]>();
  for (const el of page.elements) {
    if (el.type === "group") continue;
    const key = el.parent ?? "";
    const list = byParent.get(key);
    if (list) list.push(el);
    else byParent.set(key, [el]);
  }
  for (const siblings of byParent.values()) {
    for (let i = 0; i < siblings.length; i++) {
      const a = placements[siblings[i].id];
      if (!a) continue;
      for (let j = i + 1; j < siblings.length; j++) {
        const b = placements[siblings[j].id];
        if (b && placementsOverlap(a, b)) {
          ids.add(siblings[i].id);
          ids.add(siblings[j].id);
        }
      }
    }
  }
  return ids;
}

// --- Touch-target warning (plan section 3.7) ---

/** The reference glass every warning is estimated against: 1280x800, and a
 *  15-inch panel at that resolution is ~100 px per inch. */
export const TOUCH_REFERENCE = { width: 1280, height: 800, pxPerInch: 100 };

/** Below this many reference pixels a control is uncomfortable to hit. This is
 *  the 44px minimum that used to be a runtime clamp -- as a clamp it overrode
 *  small percentage heights and shoved elements out of their boxes into overlap
 *  on every touch panel, so it lives here as advice instead. */
export const TOUCH_MIN_PX = 44;

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

/** Element types a finger actually has to hit. A label being small is fine. */
const TOUCHABLE_TYPES = new Set([
  "button", "page_nav", "camera_preset", "select", "text_input", "keypad", "list",
]);

/** IDs of interactive elements whose box is under the comfortable touch size. */
export function findSmallTouchTargetIds(
  page: UIPage,
  layoutId?: string | null,
): Set<string> {
  const placements = resolvePlacements(page, layoutId);
  const ids = new Set<string>();
  for (const el of page.elements) {
    if (!TOUCHABLE_TYPES.has(el.type)) continue;
    const p = placements[el.id];
    // A child is a percentage of its container, so the reference box is the
    // container's own pixels, not the page's -- half a page-width inside a
    // quarter-page container is an eighth of the panel.
    if (p && touchTargetWarning(p, referenceParentBox(page, el.id, layoutId))) {
      ids.add(el.id);
    }
  }
  return ids;
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
      if (b.action === "navigate" && b.page && !pageIds.has(b.page as string)) {
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

  // Check page elements
  for (const page of project.ui.pages) {
    const placements = resolvePlacements(page);
    const byId = new Map(page.elements.map((e) => [e.id, e]));
    for (const el of page.elements) {
      checkElement(el, page.id, page.name);
      const p = placements[el.id];
      if (p && isOutOfBounds(p)) {
        const where = el.parent ? `its container` : `the page`;
        issues.push({ severity: "warning", message: `Element extends beyond ${where}`, location: `${page.name} > ${el.id}`, pageId: page.id, elementId: el.id });
      }
      // A parent that doesn't exist leaves the child unrendered \u2014 the runtime
      // draws children into their container, and there is no container.
      if (el.parent && !byId.has(el.parent)) {
        issues.push({ severity: "error", message: `Container "${el.parent}" does not exist`, location: `${page.name} > ${el.id}`, pageId: page.id, elementId: el.id });
      }
      const touch = p ? touchTargetWarning(p) : null;
      if (touch && TOUCHABLE_TYPES.has(el.type)) {
        issues.push({ severity: "warning", message: `Small touch target (about ${touch.widthPx}\u00d7${touch.heightPx}px, ${touch.widthMm}\u00d7${touch.heightMm}mm on a 15-inch panel)`, location: `${page.name} > ${el.id}`, pageId: page.id, elementId: el.id });
      }
    }
  }

  // Check master elements
  for (const mel of project.ui.master_elements || []) {
    checkElement(mel, "", "Master Elements");
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
