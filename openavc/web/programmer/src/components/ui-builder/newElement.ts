/**
 * What a control is when it comes off the palette: its id, the box and shape
 * it is dropped at, and the fields it starts with.
 *
 * A new palette type's starting fields are one entry in `NEW_ELEMENT_FIELDS`,
 * beside its size in `DEFAULT_ELEMENT_SIZES` and, when it has one, its lock in
 * `DEFAULT_ASPECT_LOCKS`; its palette entry (`ELEMENT_TYPES`) is in
 * uiBuilderHelpers.ts and what it can bind (`BINDING_CAPABILITIES`) in
 * bindingCapabilities.ts. A plugin element
 * is spelled `plugin:<plugin_id>:<plugin_type>`, and `pluginElementType` is
 * the one reading of that spelling all three answers ask.
 *
 * The fields are written in the order a saved project keeps them;
 * `newElementShape.test.ts` holds that order.
 *
 * The first-free naming rule a control's id is made with (`firstFree`) is
 * also the one a page's id and name, a page copy's controls and a layout
 * variant's id are made with in uiBuilderHelpers.ts.
 */
import type { UIElement } from "../../api/types";
import type { PluginExtension } from "../../api/pluginClient";
import { own } from "./authoredValues";

/**
 * The first spelling nobody has taken, counting up from `from`, and the count
 * it took. A control's id is named this way (`generateId`), and so are a page's
 * id and name, a page copy's controls and a layout variant's id in
 * uiBuilderHelpers.ts; `spell` says what the nth try is called.
 */
export function firstFree(
  taken: ReadonlySet<string>,
  spell: (n: number) => string,
  from = 1,
): [string, number] {
  let n = from;
  while (taken.has(spell(n))) n++;
  return [spell(n), n];
}

/** The first `<type>_<n>` nothing holds yet, counting from 1. */
export function generateId(type: string, existingIds: Set<string>): string {
  return firstFree(existingIds, (n) => `${type}_${n}`)[0];
}

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
  // Sized for the tile wall a new matrix now is: four cards in 426x300 draw
  // about 207x144 each, where the old 640x500 gave four cards a third of a page
  // to hold two short lines. It still holds the seeded 4x4 in either of the
  // other two styles (a crosspoint 4x4 needs 275x266, a list 148x168).
  matrix: { w: 33.3333, h: 37.5 },
  custom: { w: 33.3333, h: 37.5 },
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

interface PluginElementType {
  pluginId: string;
  pluginType: string;
}

/** A plugin element's two names, read off `plugin:<plugin_id>:<plugin_type>`
 *  (the type may itself hold a colon); null for any other type. */
function pluginElementType(type: string): PluginElementType | null {
  if (!type.startsWith("plugin:")) return null;
  const parts = type.split(":");
  return { pluginId: parts[1], pluginType: parts.slice(2).join(":") };
}

/** The default box for an element type, honouring a plugin's own declaration. */
export function defaultElementSize(
  type: string,
  panelElements: PluginExtension[] = [],
): { w: number; h: number } {
  const plugin = pluginElementType(type);
  if (!plugin) return own(DEFAULT_ELEMENT_SIZES, type) ?? FALLBACK_ELEMENT_SIZE;
  const declared = panelElements.find(
    (e) => e.plugin_id === plugin.pluginId && e.type === plugin.pluginType,
  )?.default_size;
  return {
    w: declared?.w ?? FALLBACK_ELEMENT_SIZE.w,
    h: declared?.h ?? FALLBACK_ELEMENT_SIZE.h,
  };
}

/** The aspect lock a new element of this type is born with, or null. */
export function defaultAspectLock(type: string): number | null {
  const plugin = pluginElementType(type);
  if (plugin) return VIDEO_PLUGIN_TYPES.has(plugin.pluginType) ? VIDEO_ASPECT_LOCK : null;
  return own(DEFAULT_ASPECT_LOCKS, type) ?? null;
}

/**
 * What each palette type starts with, after the fields every element has. Each
 * is a function so that no two controls share an object. A type with no entry
 * here is labelled with its own name.
 */
const NEW_ELEMENT_FIELDS = new Map<string, () => Partial<UIElement>>([
  ["button", () => ({ label: "Button" })],
  ["label", () => ({ text: "Label" })],
  ["status_led", () => ({ label: "Status" })],
  ["slider", () => ({
    label: "Slider",
    min: 0,
    max: 100,
    step: 1,
    scale_to_full: true,
    response: "linear",
  })],
  // Lit while its page is showing, on for every nav placed from now on.
  // Absent on navs placed before, so a panel already installed does not
  // change its look when it updates.
  ["page_nav", () => ({ label: "Next Page", target_page: "", highlight_current: true })],
  ["select", () => ({
    label: "Select",
    options: [
      { label: "Option 1", value: "option_1" },
      { label: "Option 2", value: "option_2" },
    ],
  })],
  ["text_input", () => ({ label: "Input", placeholder: "Type here..." })],
  ["image", () => ({ label: "" })],
  ["camera_preset", () => ({ label: "Preset", preset_number: 1 })],
  ["gauge", () => ({
    label: "Gauge",
    min: 0,
    max: 100,
    unit: "%",
    arc_angle: 240,
    style: { gauge_width: 8, show_value: true, show_ticks: true, tick_count: 5 },
  })],
  ["level_meter", () => ({
    label: "Level",
    min: -60,
    max: 0,
    orientation: "vertical",
    style: { meter_segments: 20, show_peak: true, peak_hold_ms: 1500 },
  })],
  ["fader", () => ({
    label: "Fader",
    min: 0,
    max: 100,
    step: 1,
    unit: "%",
    scale_to_full: true,
    response: "linear",
    orientation: "vertical",
    style: { show_value: true, show_scale: true },
  })],
  ["group", () => ({ label: "Container", label_position: "top-left" })],
  ["clock", () => ({ clock_mode: "time" })],
  ["keypad", () => ({
    label: "Keypad",
    digits: 4,
    auto_send: false,
    keypad_style: "numeric",
    show_display: true,
  })],
  ["list", () => ({
    label: "Sources",
    list_style: "selectable",
    item_height: 44 / 14,
    items: [
      { label: "Item 1", value: "1" },
      { label: "Item 2", value: "2" },
      { label: "Item 3", value: "3" },
    ],
  })],
  ["matrix", () => ({
    label: "Video Routing",
    // Two generators rather than two written-out lists: a new matrix has no
    // device behind it yet, and `count: 4` is the shorthand that says "four
    // of them, numbered from one" without inventing four entries somebody
    // then has to edit one at a time. Picking a device writes the entries
    // out; until then this is the terse, correct form.
    matrix_config: {
      sources: {
        from: { count: 4, labels: ["Input 1", "Input 2", "Input 3", "Input 4"] },
      },
      destinations: {
        from: { count: 4, labels: ["Output 1", "Output 2", "Output 3", "Output 4"] },
      },
    },
    // A new matrix is a tile wall: one card per destination naming what is
    // on it, in type you can read across a room, with the sources behind a
    // tap rather than spread across the control. That is what somebody
    // standing in a space actually wants to know -- what is on the main
    // display -- where a grid of dots is a transliteration of a 1990s front
    // panel. List and Crosspoint stay a click away in Style.
    //
    // Decided by looking at all three rendered side by side
    // (2026-08-13). Only NEW matrices: the panel still falls back to
    // crosspoint when an element says nothing, so no panel already built
    // changes shape.
    matrix_style: "tiles",
    // No cell_size: absent means the crosspoint cells fit themselves to the
    // box. The seed used to be 44, which is the floor, so every new matrix
    // was pinned at its smallest cell however much room it was given.
  })],
  // The file is chosen in the properties panel, from what is actually in the
  // project's ui/ folder — a new one starts empty and says so on the canvas
  // rather than pointing at a path nobody has written yet. No label: a custom
  // control never draws one, and the page review would warn about it the
  // moment the control was added.
  ["custom", () => ({ custom_file: "", custom_config: {} })],
]);

export function createDefaultElement(
  type: string,
  existingIds: Set<string>,
): UIElement {
  const base: UIElement = {
    id: generateId(type, existingIds),
    type,
    parent: null,
    aspect_lock: defaultAspectLock(type),
    style: {},
    bindings: {},
  };
  const plugin = pluginElementType(type);
  if (plugin) {
    // No label, for the same reason as a custom control: a plugin element
    // draws its own content and never a label.
    return {
      ...base,
      type: "plugin",
      plugin_type: plugin.pluginType,
      plugin_id: plugin.pluginId,
      plugin_config: {},
    };
  }
  const fields = NEW_ELEMENT_FIELDS.get(type);
  return { ...base, ...(fields ? fields() : { label: type }) };
}
