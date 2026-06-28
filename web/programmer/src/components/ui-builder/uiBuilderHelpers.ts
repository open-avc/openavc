import type { UIPage, UIElement, GridArea, MasterElement, PageGroup, MacroConfig, MacroStep, VariableConfig, ScriptConfig, ProjectConfig } from "../../api/types";
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
  { type: "group", label: "Group", category: "display", description: "Visual container to group related elements together" },
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

export function createDefaultElement(
  type: string,
  col: number,
  row: number,
  existingIds: Set<string>,
  panelElements: PluginExtension[] = [],
): UIElement {
  const id = generateId(type, existingIds);
  const base: UIElement = {
    id,
    type,
    grid_area: { col, row, col_span: 2, row_span: 1 },
    style: {},
    bindings: {},
  };

  switch (type) {
    case "button":
      return {
        ...base,
        label: "Button",
        grid_area: { col, row, col_span: 3, row_span: 2 },
        style: {},
      };
    case "label":
      return {
        ...base,
        text: "Label",
        grid_area: { col, row, col_span: 3, row_span: 1 },
        style: {},
      };
    case "status_led":
      return {
        ...base,
        label: "Status",
        grid_area: { col, row, col_span: 2, row_span: 1 },
      };
    case "slider":
      return {
        ...base,
        label: "Slider",
        min: 0,
        max: 100,
        step: 1,
        output_min: 0,
        output_max: 1,
        scale_to_full: true,
        grid_area: { col, row, col_span: 4, row_span: 1 },
      };
    case "page_nav":
      return {
        ...base,
        label: "Next Page",
        target_page: "",
        grid_area: { col, row, col_span: 2, row_span: 1 },
        style: {},
      };
    case "select":
      return {
        ...base,
        label: "Select",
        options: [
          { label: "Option 1", value: "option_1" },
          { label: "Option 2", value: "option_2" },
        ],
        grid_area: { col, row, col_span: 3, row_span: 1 },
      };
    case "text_input":
      return {
        ...base,
        label: "Input",
        placeholder: "Type here...",
        grid_area: { col, row, col_span: 3, row_span: 1 },
      };
    case "image":
      return {
        ...base,
        label: "",
        grid_area: { col, row, col_span: 3, row_span: 3 },
      };
    case "spacer":
      return {
        ...base,
        grid_area: { col, row, col_span: 1, row_span: 1 },
      };
    case "camera_preset":
      return {
        ...base,
        label: "Preset",
        preset_number: 1,
        grid_area: { col, row, col_span: 2, row_span: 2 },
        style: {},
      };
    case "gauge":
      return {
        ...base,
        label: "Gauge",
        min: 0,
        max: 100,
        unit: "%",
        arc_angle: 240,
        grid_area: { col, row, col_span: 3, row_span: 3 },
        style: { gauge_width: 8, show_value: true, show_ticks: true, tick_count: 5 },
      };
    case "level_meter":
      return {
        ...base,
        label: "Level",
        min: -60,
        max: 0,
        orientation: "vertical",
        grid_area: { col, row, col_span: 1, row_span: 4 },
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
        output_min: 0,
        output_max: 1,
        scale_to_full: true,
        orientation: "vertical",
        grid_area: { col, row, col_span: 2, row_span: 5 },
        style: { show_value: true, show_scale: true },
      };
    case "group":
      return {
        ...base,
        label: "Group",
        label_position: "top-left",
        grid_area: { col, row, col_span: 6, row_span: 4 },
        style: {},
      };
    case "clock":
      return {
        ...base,
        clock_mode: "time",
        grid_area: { col, row, col_span: 3, row_span: 1 },
        style: {},
      };
    case "keypad":
      return {
        ...base,
        label: "Keypad",
        digits: 4,
        auto_send: false,
        keypad_style: "numeric",
        show_display: true,
        grid_area: { col, row, col_span: 3, row_span: 5 },
        style: {},
      };
    case "list":
      return {
        ...base,
        label: "Sources",
        list_style: "selectable",
        item_height: 44,
        items: [
          { label: "Item 1", value: "1" },
          { label: "Item 2", value: "2" },
          { label: "Item 3", value: "3" },
        ],
        grid_area: { col, row, col_span: 3, row_span: 4 },
        style: {},
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
        grid_area: { col, row, col_span: 6, row_span: 5 },
        style: { cell_size: 44 },
      };
    default:
      // Plugin element: type is "plugin:<plugin_id>:<plugin_type>"
      if (type.startsWith("plugin:")) {
        const parts = type.split(":");
        const pluginId = parts[1];
        const pluginType = parts.slice(2).join(":");
        const ext = panelElements.find(
          (e) => e.plugin_id === pluginId && e.type === pluginType,
        );
        const colSpan = ext?.default_size?.col_span ?? 4;
        const rowSpan = ext?.default_size?.row_span ?? 3;
        return {
          ...base,
          type: "plugin",
          label: pluginType,
          plugin_type: pluginType,
          plugin_id: pluginId,
          plugin_config: {},
          grid_area: { col, row, col_span: colSpan, row_span: rowSpan },
        };
      }
      return { ...base, label: type };
  }
}

// --- Grid geometry helpers ---

/**
 * Clamp an element origin so the element's full span stays inside the grid.
 * Both the palette-drop and click-to-add paths place an element of a known
 * span; neither may push col+col_span past `columns` (or row+row_span past
 * `rows`), which overflows the grid and trips validateProject's "extends
 * beyond the NxN grid" warning. Mirrors the canvas-move clamp.
 */
export function clampOriginToGrid(
  col: number,
  row: number,
  colSpan: number,
  rowSpan: number,
  columns: number,
  rows: number,
): { col: number; row: number } {
  return {
    col: Math.max(1, Math.min(columns - colSpan + 1, col)),
    row: Math.max(1, Math.min(rows - rowSpan + 1, row)),
  };
}

/**
 * Find the first grid position where an element of the given span fits without
 * overlapping any existing element. Scans row-major. Falls back to a clamped
 * (1,1) when nothing fits (element larger than the free area) so the result is
 * always on-grid. Used by click-to-add so a multi-cell element doesn't overflow
 * the grid or land on top of a neighbour.
 */
export function findFreeGridPosition(
  elements: { grid_area: GridArea }[],
  colSpan: number,
  rowSpan: number,
  columns: number,
  rows: number,
): { col: number; row: number } {
  const occupied = new Set<string>();
  for (const el of elements) {
    const { col, row, col_span, row_span } = el.grid_area;
    for (let r = row; r < row + row_span; r++)
      for (let c = col; c < col + col_span; c++) occupied.add(`${c},${r}`);
  }
  for (let r = 1; r + rowSpan - 1 <= rows; r++) {
    for (let c = 1; c + colSpan - 1 <= columns; c++) {
      let fits = true;
      for (let rr = r; rr < r + rowSpan && fits; rr++)
        for (let cc = c; cc < c + colSpan && fits; cc++)
          if (occupied.has(`${cc},${rr}`)) fits = false;
      if (fits) return { col: c, row: r };
    }
  }
  return clampOriginToGrid(1, 1, colSpan, rowSpan, columns, rows);
}

/**
 * Map a pointer coordinate to a 1-based grid cell index. The canvas grid is
 * drawn inside a container whose CSS padding (outerGap) renders at `pad` screen
 * pixels; that padding is NOT part of the cell area, so it must be removed from
 * both the origin and the length before dividing into cells. Without this the
 * mapping is biased toward the start by ~one pad-width near the far edge,
 * landing edge drops one cell off.
 */
export function pointerToCell(
  pointerPx: number,
  rectStart: number,
  rectLength: number,
  pad: number,
  count: number,
): number {
  const inner = Math.max(1, rectLength - 2 * pad);
  const frac = (pointerPx - (rectStart + pad)) / inner;
  return Math.floor(frac * count) + 1;
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
    grid: pageType === "page"
      ? { columns: 12, rows: 8 }
      : { columns: 4, rows: 4 },
    elements: [],
  };

  if (pageType === "overlay") {
    newPage.page_type = "overlay";
    newPage.overlay = {
      width: 400,
      height: 300,
      position: "center",
      backdrop: "dim",
      dismiss_on_backdrop: true,
      animation: "fade",
    };
  } else if (pageType === "sidebar") {
    newPage.page_type = "sidebar";
    newPage.overlay = {
      width: 320,
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

  // Scrub master_elements.pages arrays that reference this page
  const newMasters = masterElements.map((m) => {
    if (m.pages === "*" || !Array.isArray(m.pages)) return m;
    const filtered = (m.pages as string[]).filter((pid) => pid !== pageId);
    if (filtered.length === m.pages.length) return m;
    return { ...m, pages: filtered.length > 0 ? filtered : "*" };
  });

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

  return { pages: newPages, masterElements: newMasters, macros: newMacros };
}

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
): UIPage[] {
  return pages.map((p) =>
    p.id === pageId ? { ...p, elements: [...p.elements, element] } : p,
  );
}

export function removeElementFromPage(
  pages: UIPage[],
  pageId: string,
  elementId: string,
): UIPage[] {
  return pages.map((p) =>
    p.id === pageId
      ? { ...p, elements: p.elements.filter((e) => e.id !== elementId) }
      : p,
  );
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

export function moveElementInPage(
  pages: UIPage[],
  pageId: string,
  elementId: string,
  newGridArea: GridArea,
): UIPage[] {
  return updateElementInPage(pages, pageId, elementId, {
    grid_area: newGridArea,
  });
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
  // Place duplicate adjacent, clamped to grid bounds
  let newCol = element.grid_area.col + element.grid_area.col_span;
  let newRow = element.grid_area.row;
  // If it would overflow horizontally, try placing below instead
  if (newCol + element.grid_area.col_span - 1 > page.grid.columns) {
    newCol = element.grid_area.col;
    newRow = element.grid_area.row + element.grid_area.row_span;
  }
  // Clamp to grid
  newCol = Math.max(1, Math.min(page.grid.columns - element.grid_area.col_span + 1, newCol));
  newRow = Math.max(1, Math.min(page.grid.rows - element.grid_area.row_span + 1, newRow));

  // Rewrite self-referencing ui.<oldId>.* state keys (bindings, visibility)
  // to the duplicate's id — same machinery the rename path uses — so the
  // copy is wired to its own state, not the original's.
  const clone = JSON.parse(JSON.stringify(element)) as UIElement;
  const rewritten = rewriteElement(clone, element.id, newId);
  const newElement: UIElement = {
    ...rewritten,
    grid_area: {
      ...element.grid_area,
      col: newCol,
      row: newRow,
    },
  };
  return addElementToPage(pages, pageId, newElement);
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

export function moveElementInOrder(
  pages: UIPage[],
  pageId: string,
  elementId: string,
  direction: "up" | "down",
): UIPage[] {
  return pages.map((p) => {
    if (p.id !== pageId) return p;
    const idx = p.elements.findIndex((e) => e.id === elementId);
    if (idx === -1) return p;
    const newIdx = direction === "up" ? idx - 1 : idx + 1;
    if (newIdx < 0 || newIdx >= p.elements.length) return p;
    const els = [...p.elements];
    [els[idx], els[newIdx]] = [els[newIdx], els[idx]];
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
  gridConfig: { columns: number; rows: number },
): UIPage[] {
  return pages.map((p) => {
    if (p.id !== pageId) return p;
    const targets = p.elements.filter((el) => elementIds.includes(el.id));
    if (targets.length === 0) return p;

    // When multiple elements are selected, align relative to the selection
    // bounding box. Single element aligns to the page grid.
    const useSelectionBounds = targets.length > 1;
    let boundsLeft: number, boundsRight: number, boundsTop: number, boundsBottom: number;
    if (useSelectionBounds) {
      boundsLeft = Math.min(...targets.map((el) => el.grid_area.col));
      boundsRight = Math.max(...targets.map((el) => el.grid_area.col + el.grid_area.col_span - 1));
      boundsTop = Math.min(...targets.map((el) => el.grid_area.row));
      boundsBottom = Math.max(...targets.map((el) => el.grid_area.row + el.grid_area.row_span - 1));
    } else {
      boundsLeft = 1;
      boundsRight = gridConfig.columns;
      boundsTop = 1;
      boundsBottom = gridConfig.rows;
    }
    const boundsW = boundsRight - boundsLeft + 1;
    const boundsH = boundsBottom - boundsTop + 1;

    return {
      ...p,
      elements: p.elements.map((el) => {
        if (!elementIds.includes(el.id)) return el;
        const area = { ...el.grid_area };
        switch (action) {
          case "align-left":
            area.col = boundsLeft;
            break;
          case "align-center":
            area.col = Math.max(1, boundsLeft + Math.round((boundsW - area.col_span) / 2));
            break;
          case "align-right":
            area.col = boundsRight - area.col_span + 1;
            break;
          case "align-top":
            area.row = boundsTop;
            break;
          case "align-middle":
            area.row = Math.max(1, boundsTop + Math.round((boundsH - area.row_span) / 2));
            break;
          case "align-bottom":
            area.row = boundsBottom - area.row_span + 1;
            break;
        }
        return { ...el, grid_area: area };
      }),
    };
  });
}

export function alignElement(
  pages: UIPage[],
  pageId: string,
  elementId: string,
  action: AlignAction,
  gridConfig: { columns: number; rows: number },
): UIPage[] {
  return alignElements(pages, pageId, [elementId], action, gridConfig);
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

  // Remove from page
  const newPages = pages.map(p =>
    p.id === pageId
      ? { ...p, elements: p.elements.filter(e => e.id !== elementId) }
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
  const masterEl: MasterElement = { ...promoted, pages: "*" };
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

  // Strip the pages field
  const { pages: _pagesField, ...elementFields } = masterEl;
  let demoted = elementFields as UIElement;

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
      ? { ...p, elements: [...p.elements, demoted] }
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

/** True when an element's grid span extends beyond the page grid. */
export function isOutOfBounds(
  area: GridArea,
  grid: { columns: number; rows: number },
): boolean {
  return (
    area.col < 1 || area.row < 1 ||
    area.col + area.col_span - 1 > grid.columns ||
    area.row + area.row_span - 1 > grid.rows
  );
}

/** IDs of elements whose grid span extends beyond the page grid. Used by the
 *  canvas to badge orphaned elements live (e.g. right after a grid shrink),
 *  not just when Validate is pressed. */
export function findOutOfBoundsIds(
  elements: UIElement[],
  grid: { columns: number; rows: number },
): Set<string> {
  const ids = new Set<string>();
  for (const el of elements) {
    if (isOutOfBounds(el.grid_area, grid)) ids.add(el.id);
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
    for (const el of page.elements) {
      checkElement(el, page.id, page.name);
      if (isOutOfBounds(el.grid_area, page.grid)) {
        issues.push({ severity: "warning", message: `Element extends beyond the ${page.grid.columns}\u00d7${page.grid.rows} grid`, location: `${page.name} > ${el.id}`, pageId: page.id, elementId: el.id });
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
