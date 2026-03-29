import type { UIPage, UIElement, GridArea, MasterElement, PageGroup } from "../../api/types";

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
  { type: "slider", label: "Slider", category: "controls", description: "Horizontal control for numeric values (volume, brightness)" },
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
  label: ["text", "feedback"],
  slider: ["variable", "change", "value", "feedback"],
  fader: ["value", "change", "meter", "feedback"],
  status_led: ["color"],
  page_nav: [],
  select: ["variable", "change", "value", "feedback"],
  text_input: ["variable", "change", "value", "feedback"],
  camera_preset: ["press", "feedback"],
  image: [],
  spacer: [],
  gauge: ["value", "feedback"],
  level_meter: ["value", "feedback"],
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
        style: { font_size: 16 },
      };
    case "label":
      return {
        ...base,
        text: "Label",
        grid_area: { col, row, col_span: 3, row_span: 1 },
        style: { font_size: 14 },
      };
    case "status_led":
      return {
        ...base,
        grid_area: { col, row, col_span: 1, row_span: 1 },
      };
    case "slider":
      return {
        ...base,
        label: "Slider",
        min: 0,
        max: 100,
        step: 1,
        grid_area: { col, row, col_span: 4, row_span: 1 },
      };
    case "page_nav":
      return {
        ...base,
        label: "Next Page",
        target_page: "",
        grid_area: { col, row, col_span: 2, row_span: 1 },
        style: { font_size: 14 },
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
        min: -80,
        max: 10,
        step: 0.5,
        unit: "dB",
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
        format: "h:mm A",
        grid_area: { col, row, col_span: 3, row_span: 1 },
        style: { font_size: 24 },
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

// --- Element templates ---

export interface ElementTemplate {
  id: string;
  label: string;
  description: string;
  elements: Array<Omit<UIElement, "id">>;
}

export const ELEMENT_TEMPLATES: ElementTemplate[] = [
  {
    id: "volume_control",
    label: "Volume Control",
    description: "Fader + mute button + label",
    elements: [
      { type: "label", text: "Volume", grid_area: { col: 0, row: 0, col_span: 2, row_span: 1 }, style: { font_size: 12, text_align: "center" }, bindings: {} },
      { type: "fader", label: "", min: -80, max: 10, step: 0.5, unit: "dB", orientation: "vertical", grid_area: { col: 0, row: 1, col_span: 2, row_span: 4 }, style: { show_value: true, show_scale: true }, bindings: {} },
      { type: "button", label: "Mute", grid_area: { col: 0, row: 5, col_span: 2, row_span: 1 }, style: {}, bindings: {} },
    ],
  },
  {
    id: "source_selector",
    label: "Source Selector",
    description: "4-button source row with feedback",
    elements: [
      { type: "button", label: "HDMI 1", grid_area: { col: 0, row: 0, col_span: 2, row_span: 2 }, style: {}, bindings: {} },
      { type: "button", label: "HDMI 2", grid_area: { col: 2, row: 0, col_span: 2, row_span: 2 }, style: {}, bindings: {} },
      { type: "button", label: "USB-C", grid_area: { col: 4, row: 0, col_span: 2, row_span: 2 }, style: {}, bindings: {} },
      { type: "button", label: "Wireless", grid_area: { col: 6, row: 0, col_span: 2, row_span: 2 }, style: {}, bindings: {} },
    ],
  },
  {
    id: "power_toggle",
    label: "Power Toggle",
    description: "Toggle button + status LED",
    elements: [
      { type: "button", label: "Power", icon: "power", icon_position: "left", grid_area: { col: 0, row: 0, col_span: 3, row_span: 2 }, style: {}, bindings: {} },
      { type: "status_led", grid_area: { col: 3, row: 0, col_span: 1, row_span: 2 }, style: {}, bindings: {} },
    ],
  },
  {
    id: "room_header",
    label: "Room Header",
    description: "Room name + clock",
    elements: [
      { type: "label", text: "Conference Room", grid_area: { col: 0, row: 0, col_span: 4, row_span: 1 }, style: { font_size: 18, font_weight: "600", text_align: "left" }, bindings: {} },
      { type: "clock", clock_mode: "time", format: "h:mm A", grid_area: { col: 10, row: 0, col_span: 3, row_span: 1 }, style: { font_size: 18, text_align: "right" }, bindings: {} },
    ],
  },
  {
    id: "mixer_strip",
    label: "Mixer Strip",
    description: "Fader + meter + mute + label",
    elements: [
      { type: "label", text: "Ch 1", grid_area: { col: 0, row: 0, col_span: 2, row_span: 1 }, style: { font_size: 11, text_align: "center" }, bindings: {} },
      { type: "level_meter", label: "", min: -60, max: 0, orientation: "vertical", grid_area: { col: 0, row: 1, col_span: 1, row_span: 4 }, style: { meter_segments: 16, show_peak: true }, bindings: {} },
      { type: "fader", label: "", min: -80, max: 10, step: 0.5, unit: "dB", orientation: "vertical", grid_area: { col: 1, row: 1, col_span: 1, row_span: 4 }, style: { show_value: false, show_scale: false }, bindings: {} },
      { type: "button", label: "M", grid_area: { col: 0, row: 5, col_span: 2, row_span: 1 }, style: { font_size: 12 }, bindings: {} },
    ],
  },
];

// --- Alignment helpers ---

export type AlignAction =
  | "align-left" | "align-center" | "align-right"
  | "align-top" | "align-middle" | "align-bottom";

export function alignElement(
  pages: UIPage[],
  pageId: string,
  elementId: string,
  action: AlignAction,
  gridConfig: { columns: number; rows: number },
): UIPage[] {
  return pages.map((p) => {
    if (p.id !== pageId) return p;
    return {
      ...p,
      elements: p.elements.map((el) => {
        if (el.id !== elementId) return el;
        const area = { ...el.grid_area };
        switch (action) {
          case "align-left":
            area.col = 1;
            break;
          case "align-center":
            area.col = Math.max(1, Math.round((gridConfig.columns - area.col_span) / 2) + 1);
            break;
          case "align-right":
            area.col = gridConfig.columns - area.col_span + 1;
            break;
          case "align-top":
            area.row = 1;
            break;
          case "align-middle":
            area.row = Math.max(1, Math.round((gridConfig.rows - area.row_span) / 2) + 1);
            break;
          case "align-bottom":
            area.row = gridConfig.rows - area.row_span + 1;
            break;
        }
        return { ...el, grid_area: area };
      }),
    };
  });
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
