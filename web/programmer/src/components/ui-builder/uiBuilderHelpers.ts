import type { UIPage, UIElement, GridArea, MasterElement, PageGroup, MacroConfig, MacroStep, VariableConfig, ScriptConfig, ProjectConfig } from "../../api/types";

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

// --- Binding slots per element type ---

export const BINDING_SLOTS: Record<string, string[]> = {
  button: ["press", "release", "hold", "feedback"],
  label: ["text"],
  slider: ["variable", "change", "value"],
  fader: ["value", "change"],
  status_led: ["color"],
  page_nav: [],
  select: ["variable", "change", "value"],
  text_input: ["variable", "change", "value"],
  camera_preset: ["press", "feedback"],
  image: [],
  spacer: [],
  gauge: ["value"],
  level_meter: ["value"],
  group: [],
  clock: [],
  keypad: ["submit"],
  matrix: ["route"],
  list: ["items", "selected", "select"],
  plugin: [],
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
        return {
          ...base,
          type: "plugin",
          label: pluginType,
          plugin_type: pluginType,
          plugin_id: pluginId,
          plugin_config: {},
          grid_area: { col, row, col_span: 4, row_span: 3 },
        };
      }
      return { ...base, label: type };
  }
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
        // Clear press bindings with navigate action pointing to deleted page
        if (el.bindings?.press && (el.bindings.press as any).action === "navigate" && (el.bindings.press as any).page === pageId) {
          updated = { ...updated, bindings: { ...updated.bindings, press: {} } };
        }
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
): UIPage[] {
  const page = pages.find((p) => p.id === pageId);
  if (!page) return pages;
  const element = page.elements.find((e) => e.id === elementId);
  if (!element) return pages;

  // Collect IDs from ALL pages to avoid cross-page collisions
  const existingIds = new Set(pages.flatMap((p) => p.elements.map((e) => e.id)));
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

  const newElement: UIElement = {
    ...JSON.parse(JSON.stringify(element)),
    id: newId,
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

  // Deep clone elements with new IDs
  const existingElementIds = new Set(
    pages.flatMap((p) => p.elements.map((e) => e.id)),
  );
  const newElements = page.elements.map((el) => {
    let elId = `${el.type}_${newId}_1`;
    let c = 1;
    while (existingElementIds.has(elId)) {
      c++;
      elId = `${el.type}_${newId}_${c}`;
    }
    existingElementIds.add(elId);
    return { ...JSON.parse(JSON.stringify(el)), id: elId };
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

  // Add to master elements with pages: "*"
  const masterEl: MasterElement = { ...element, pages: "*" };
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

  // Add to target page (strip the pages field)
  const { pages: _pagesField, ...elementFields } = masterEl;
  const newPages = pages.map(p =>
    p.id === targetPageId
      ? { ...p, elements: [...p.elements, elementFields as UIElement] }
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
  const newPages = pages.map((p) => ({
    ...p,
    elements: p.elements.map((el) => rewriteElement(el, oldId, newId)),
  }));
  const newMasters = masterElements.map((m) => rewriteElement(m as unknown as UIElement, oldId, newId) as MasterElement);
  const newMacros = macros.map((m) => rewriteMacro(m, oldId, newId));
  const newVars = variables.map((v) => rewriteVariable(v, oldId, newId));
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
    const bindings = el.bindings || {};

    // page_nav target
    if (el.type === "page_nav" && el.target_page && !pageIds.has(el.target_page)) {
      issues.push({ severity: "error", message: `Target page "${el.target_page}" does not exist`, location: loc, pageId, elementId: el.id });
    }

    // press/release/hold bindings
    for (const slot of ["press", "release", "hold"]) {
      const b = bindings[slot] as Record<string, unknown> | undefined;
      if (!b) continue;
      if (b.action === "navigate" && b.page && !pageIds.has(b.page as string)) {
        issues.push({ severity: "error", message: `Navigate to deleted page "${b.page}"`, location: `${loc} > ${slot}`, pageId, elementId: el.id });
      }
      if (b.action === "device.command" && b.device && !deviceIds.has(b.device as string)) {
        issues.push({ severity: "error", message: `Device "${b.device}" not found`, location: `${loc} > ${slot}`, pageId, elementId: el.id });
      }
      if (b.action === "macro" && b.macro && !macroIds.has(b.macro as string)) {
        issues.push({ severity: "error", message: `Macro "${b.macro}" not found`, location: `${loc} > ${slot}`, pageId, elementId: el.id });
      }
    }

    // change/submit bindings
    for (const slot of ["change", "submit"]) {
      const b = bindings[slot] as Record<string, unknown> | undefined;
      if (!b) continue;
      if (b.action === "device.command" && b.device && !deviceIds.has(b.device as string)) {
        issues.push({ severity: "error", message: `Device "${b.device}" not found`, location: `${loc} > ${slot}`, pageId, elementId: el.id });
      }
      if (b.action === "macro" && b.macro && !macroIds.has(b.macro as string)) {
        issues.push({ severity: "error", message: `Macro "${b.macro}" not found`, location: `${loc} > ${slot}`, pageId, elementId: el.id });
      }
    }

    // variable/value bindings
    for (const slot of ["variable", "value", "text", "color", "items", "selected"]) {
      const b = bindings[slot] as Record<string, unknown> | undefined;
      if (!b || !b.key) continue;
      const key = b.key as string;
      if (key.startsWith("device.")) {
        const deviceId = key.split(".")[1];
        if (!deviceIds.has(deviceId)) {
          issues.push({ severity: "warning", message: `State key references unknown device "${deviceId}"`, location: `${loc} > ${slot}`, pageId, elementId: el.id });
        }
      }
    }

    // visible_when conditions
    const vw = bindings.visible_when as Record<string, unknown> | undefined;
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

    // Unbound interactive elements
    if (["button", "slider", "fader", "select", "text_input", "keypad"].includes(el.type)) {
      const hasBinding = Object.keys(bindings).some((k) => {
        const v = bindings[k];
        return v && typeof v === "object" && Object.keys(v).length > 0;
      });
      if (!hasBinding) {
        issues.push({ severity: "warning", message: `Interactive element has no bindings`, location: loc, pageId, elementId: el.id });
      }
    }
  };

  // Check page elements
  for (const page of project.ui.pages) {
    for (const el of page.elements) {
      checkElement(el, page.id, page.name);
      const a = el.grid_area;
      if (a.col + a.col_span - 1 > page.grid.columns || a.row + a.row_span - 1 > page.grid.rows) {
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
