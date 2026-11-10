import type {
  UIPage,
  UIElement,
  Placement,
  Layout,
  MasterElement,
  PageGroup,
  MacroConfig,
  MacroStep,
  ProjectConfig,
} from "../../api/types";
import {
  ACTION_SLOTS,
  REFERENCE_NOUNS,
  brokenReference,
  pageExists,
  slotActions,
  actionsCommandDevice,
  stepAndBranches,
  type BindingRefIds,
  type BrokenReference,
} from "./bindingActions";
import { firstFree, generateId } from "./newElement";
import {
  BOUNDS_EPSILON,
  DEFAULT_PLACEMENT,
  GEOMETRY_PRECISION,
  PAGE_BOX,
  absolutePlacements,
  containerOf,
  containersAbove,
  getPlacement,
  layoutById,
  pageSnap,
  primaryLayout,
  resolveHidden,
  resolvePlacements,
  roundPlacement,
  withAbsolutePlacements,
  withPlacement,
  withPlacements,
  withoutPlacement,
} from "./pageGeometry";
import { rewriteElement } from "./projectRewrites";
import { descendantIds, reparentPlacements, topmostSelection } from "./containerTree";
import { CASCADE_STEP, autoPlace } from "./gestureBoxes";
import { isPlainObject, own } from "./authoredValues";
import { fixed, num, pct } from "./sentenceNumbers";
import {
  controlMinimumBox,
  controlMinimumPercent,
  shortAxes,
  starvedReasons,
  type ControlMinimumBox,
  type ElementDefaults,
  type ShortAxis,
} from "./controlMinimums";
import {
  matrixRouteMatches,
  matrixSourceReports,
  resolveMatrixAxis,
  type MatrixAxisName,
  type MatrixEntry,
} from "./matrixModel";
import { TOUCHABLE_TYPES, TOUCH_MIN_MM, TOUCH_MIN_PX, referenceBox, toMm } from "./touchTarget";
// Both generated, both from Python, because both are measured facts rather than
// design decisions: the floors' slack and the panel's rem size come with the
// per-control floors, binary-searched against a real browser, and the
// binding-reach table is read off the panel renderer itself. See
// openavc/ui/minimums_gen.py and openavc/ui/review_gen.py.
import { PIXEL_SLACK, REM_BASE_PX } from "../../api/uiMinimums.gen";
import {
  DISPATCHED_ACTIONS,
  HONORED_PROPERTIES,
  HONORED_SHOW_SLOTS,
  MACRO_ONLY_ACTIONS,
  MATRIX_CONFIG_KEYS,
  MATRIX_PANEL_WRITABLE_PREFIXES,
  REVIEWED_SHOW_SLOTS,
  STATE_ICON_TYPES,
  STATE_IMAGE_TYPES,
  STATE_LABEL_TYPES,
  STRUCTURAL_PROPERTIES,
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
  { type: "custom", label: "Custom Control", category: "display", description: "Your own HTML/CSS/JS, running in its own box on the page" },
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
  /**
   * The element draws no words, so its Appearance card offers a background
   * colour and no text colour, and a new state starts empty rather than with a
   * colour seeded behind what the element shows.
   */
  lookWithoutText?: boolean;
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
  // A label takes the same state-driven appearance a button does: the words and
  // the colour both track the state, which is how you draw ONLINE in green and
  // OFFLINE in red. Which per-state fields the card offers (words, icon,
  // picture) comes from the panel's own tables, in BindingProperties.
  label: { value: { editor: "text", label: "Text" }, look: "feedback" },
  // An image changes its picture per state: a script sets a variable to
  // `christmas` and the background becomes the holiday one.
  image: { look: "feedback", lookWithoutText: true },
  // A Page Nav takes a button's Appearance: colours, words and an icon per
  // state. Its lit tab is a property of its own (highlight_current), not a
  // binding: it follows the page on screen, not state.
  page_nav: { look: "feedback" },
  // A Container's frame takes a colour or a background picture per state.
  group: { look: "feedback", lookWithoutText: true },
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
  // clock / plugin / custom: "Visible when…" only.
  // A custom control talks to the room through its own message bridge, not
  // through bindings, so there is nothing here to give it.
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

// --- A new control: its id, the box it is dropped at, its starting fields ---

export {
  DEFAULT_ELEMENT_SIZES,
  FALLBACK_ELEMENT_SIZE,
  DEFAULT_ASPECT_LOCKS,
  VIDEO_ASPECT_LOCK,
  defaultElementSize,
  defaultAspectLock,
  createDefaultElement,
} from "./newElement";

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

// --- Where a control is drawn: the boxes a page stores, and page space ---

export {
  roundPct,
  roundPlacement,
  pageSnap,
  layoutById,
  layoutChain,
  resolvePlacements,
  getPlacement,
  withPlacement,
  withPlacements,
  withoutPlacement,
  type Orientation,
  layoutOrientation,
  missingOrientations,
  addLayout,
  removeLayout,
  resolveHidden,
  isHiddenInLayout,
  withHidden,
  masterPlacement,
  withMasterPlacement,
  presetForOrientation,
  absolutePlacements,
  toPageBox,
  containerOf,
  containersAbove,
} from "./pageGeometry";

// --- Where a box goes under the pointer, and with none ---

export {
  pointerToPercent,
  snapMove,
  snapResize,
  paletteDragPlacement,
  movedBox,
  resizedBox,
  sameBox,
  aspectLockOf,
  autoPlace,
} from "./gestureBoxes";

/**
 * Every id a new control on any page may not take: every page's controls, and
 * the reserved ones (master elements share the ui.<id> namespace, so a copy, or
 * a control promoted to or demoted from a master, cannot be named onto one).
 */
export function takenElementIds(pages: UIPage[], reservedIds: string[]): Set<string> {
  const taken = new Set(pages.flatMap((p) => p.elements.map((e) => e.id)));
  for (const id of reservedIds) taken.add(id);
  return taken;
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
  const [id, counter] = firstFree(ids, (n) => `${prefix}_${n}`);
  const [name] = firstFree(names, (n) => `${label} ${n}`, counter);

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

// --- What a binding action names and needs ---

export {
  slotActions,
  actionsCommandDevice,
  type BindingRefIds,
  actionDanglingRef,
  actionIncompleteCheck,
  elementCommandActions,
  projectCommandActions,
} from "./bindingActions";

export { removePage, removePageAndScrubRefs } from "./projectRewrites";

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

/**
 * An element and everything living inside it, in page order.
 *
 * Page order is z-order, so carrying it across is what makes a copy stack the
 * way the original does instead of arriving reshuffled. The element itself
 * leads: a container draws behind its own contents.
 */
function elementSubtree(page: UIPage, elementId: string): UIElement[] {
  const root = page.elements.find((e) => e.id === elementId);
  if (!root) return [];
  const inside = descendantIds(page, elementId);
  return [root, ...page.elements.filter((e) => e.id !== elementId && inside.has(e.id))];
}

/**
 * Assign every element of a subtree a fresh id.
 *
 * The ids are drawn from outside `taken`, which holds every id already spoken
 * for -- so a generated id can never be one of the originals, and the rewrite
 * pass below can walk the whole map without a later pair catching an element an
 * earlier pair already renamed.
 */
function freshIds(elements: UIElement[], taken: Set<string>): Map<string, string> {
  const idMap = new Map<string, string>();
  for (const el of elements) {
    const id = generateId(el.type, taken);
    taken.add(id);
    idMap.set(el.id, id);
  }
  return idMap;
}

/**
 * One element of a copied subtree, wired to the copy rather than the original.
 *
 * Every id in the subtree is rewritten, not just this element's own: a control
 * bound to `ui.<sibling>.value` has to follow the copy, or eight duplicated
 * cards all read the first card's state. `rewriteElement` carries the `parent`
 * link across too, which is what puts each child inside the copied container.
 */
function cloneWithIds(el: UIElement, idMap: Map<string, string>): UIElement {
  let clone = JSON.parse(JSON.stringify(el)) as UIElement;
  for (const [oldId, newId] of idMap) clone = rewriteElement(clone, oldId, newId);
  return clone;
}

/**
 * Duplicate an element, and everything inside it.
 *
 * A container is its contents -- an author who copies a card wants the card,
 * not an empty frame the same size. Only the top copy is nudged: a child's
 * percentages are of its container, the container has already moved, and
 * nudging them again would slide the contents around inside the copy.
 */
export function duplicateElementInPage(
  pages: UIPage[],
  pageId: string,
  elementId: string,
  reservedIds: string[] = [],
  layoutId?: string | null,
): UIPage[] {
  const page = pages.find((p) => p.id === pageId);
  if (!page) return pages;
  const subtree = elementSubtree(page, elementId);
  if (!subtree.length) return pages;

  const idMap = freshIds(subtree, takenElementIds(pages, reservedIds));

  // Place the copy down-right of the original, the nudge every design tool
  // uses, and pull it back inside the parent box if that would push it off.
  // Measured against the arrangement on screen, so the copy lands beside the
  // control the author is actually looking at; stored in the primary by
  // addElementToPage, because a copy is a new control and exists everywhere.
  const src = getPlacement(page, elementId, layoutId);
  const nudge = { x: src.x + CASCADE_STEP, y: src.y + CASCADE_STEP, w: src.w, h: src.h };
  if (nudge.x + nudge.w > 100) nudge.x = Math.max(0, 100 - nudge.w);
  if (nudge.y + nudge.h > 100) nudge.y = Math.max(0, 100 - nudge.h);

  let result = pages;
  for (const el of subtree) {
    const placement = el.id === elementId ? nudge : getPlacement(page, el.id, layoutId);
    result = addElementToPage(result, pageId, cloneWithIds(el, idMap), placement);
  }
  return result;
}

/**
 * Duplicate a whole selection in one go, without copying anything twice.
 *
 * A duplicate already carries everything inside the element, so a selection
 * holding both a container and something in it would otherwise produce that
 * child twice -- once in the copied container, once loose beside it. The
 * ancestor's copy is the one that covers it.
 */
export function duplicateElementsInPage(
  pages: UIPage[],
  pageId: string,
  elementIds: string[],
  reservedIds: string[] = [],
  layoutId?: string | null,
): UIPage[] {
  const page = pages.find((p) => p.id === pageId);
  if (!page) return pages;
  let result = pages;
  for (const id of topmostSelection(page, elementIds)) {
    result = duplicateElementInPage(result, pageId, id, reservedIds, layoutId);
  }
  return result;
}

/** What a copy carries: the elements, and the box each one is drawn at. */
export interface ElementClipboard {
  elements: UIElement[];
  placements: Record<string, Placement>;
}

/**
 * Fill the clipboard from a selection.
 *
 * The selection is widened to everything inside it, for the same reason a
 * duplicate is: copying a container means copying what is in it.
 *
 * The boxes come back in two different spaces on purpose. An element whose
 * container travelled with it keeps its container-relative percentages,
 * because the paste will put it back inside that container. One whose
 * container stayed behind comes back as the box the eye saw, in page space,
 * because the paste ejects it to page level -- and the stored number would
 * otherwise reinterpret half-a-container as half-a-page.
 */
export function clipboardForSelection(
  page: UIPage,
  elementIds: string[],
  layoutId?: string | null,
): ElementClipboard {
  const wanted = new Set<string>();
  for (const id of elementIds) {
    if (!page.elements.some((e) => e.id === id)) continue;
    wanted.add(id);
    for (const child of descendantIds(page, id)) wanted.add(child);
  }
  const elements = page.elements.filter((e) => wanted.has(e.id));
  const absolute = absolutePlacements(page, layoutId);
  const placements: Record<string, Placement> = {};
  for (const el of elements) {
    placements[el.id] = keepsItsParent(el, wanted)
      ? getPlacement(page, el.id, layoutId)
      : absolute[el.id] ?? getPlacement(page, el.id, layoutId);
  }
  return { elements: JSON.parse(JSON.stringify(elements)) as UIElement[], placements };
}

/** Whether an element's container came along with it, and so survives the trip. */
function keepsItsParent(el: UIElement, copied: Set<string>): boolean {
  return !!el.parent && el.parent !== el.id && copied.has(el.parent);
}

/**
 * Paste the clipboard onto a page.
 *
 * Whatever was copied out of a container goes back into one: an element whose
 * own container travelled with it keeps that container, remapped to the
 * container's copy, and keeps the container-relative box it was stored with.
 * Everything else is a top of the copied structure and lands at page level,
 * auto-placed, because a paste has no pointer to land under.
 */
export function pasteIntoPage(
  pages: UIPage[],
  pageId: string,
  clipboard: ElementClipboard,
  reservedIds: string[] = [],
  layoutId?: string | null,
): UIPage[] {
  const page = pages.find((p) => p.id === pageId);
  if (!page || clipboard.elements.length === 0) return pages;

  const taken = takenElementIds(pages, reservedIds);
  // A clipboard id that is still free is kept, so a paste into a fresh page
  // reads the way it was authored. Generated ids steer clear of every id on
  // the clipboard as well, so one element can never be handed the id another
  // is about to keep.
  const copied = new Set(clipboard.elements.map((e) => e.id));
  const avoid = new Set([...taken, ...copied]);
  const idMap = new Map<string, string>();
  for (const el of clipboard.elements) {
    const id = taken.has(el.id) ? generateId(el.type, avoid) : el.id;
    taken.add(id);
    avoid.add(id);
    idMap.set(el.id, id);
  }

  const snap = pageSnap(page);
  const occupied = page.elements.map((el) => getPlacement(page, el.id, layoutId));
  let ejected = 0;
  let result = pages;
  for (const src of clipboard.elements) {
    const clone = cloneWithIds(src, idMap);
    if (keepsItsParent(src, copied)) {
      result = addElementToPage(
        result,
        pageId,
        clone,
        clipboard.placements[src.id] ?? { ...DEFAULT_PLACEMENT },
      );
      continue;
    }
    // Paste has no pointer, so it takes the auto-placement rule: the first
    // free snap cell, or the page centre plus a cascade with snap off.
    const size = clipboard.placements[src.id] ?? DEFAULT_PLACEMENT;
    const placement = autoPlace(occupied, size, snap, ejected);
    occupied.push(placement);
    ejected++;
    result = addElementToPage(result, pageId, { ...clone, parent: null }, placement);
  }
  return result;
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

/**
 * Copy a page and everything on it, straight after the original.
 *
 * Every control gets a fresh id named from its type and the copy's id, and
 * every reference between the page's controls follows the copy
 * (`cloneWithIds`), the container hierarchy included. Geometry does not live
 * on the elements: every arrangement keys its placements and hidden list by
 * element id, so the renames reach them too (`layoutRenamed`).
 */
export function duplicatePage(
  pages: UIPage[],
  pageId: string,
  reservedIds: string[] = [],
): UIPage[] {
  const idx = pages.findIndex((p) => p.id === pageId);
  if (idx === -1) return pages;
  const page = pages[idx];

  const [newId] = firstFree(new Set(pages.map((p) => p.id)), (n) =>
    n === 1 ? `${page.id}_copy` : `${page.id}_copy_${n}`,
  );
  const [newName] = firstFree(new Set(pages.map((p) => p.name)), (n) =>
    n === 1 ? `${page.name} (Copy)` : `${page.name} (Copy ${n})`,
  );
  const taken = takenElementIds(pages, reservedIds);
  const idMap = new Map<string, string>();
  for (const el of page.elements) {
    const [elId] = firstFree(taken, (n) => `${el.type}_${newId}_${n}`);
    taken.add(elId);
    idMap.set(el.id, elId);
  }

  const clonedPage = JSON.parse(JSON.stringify(page)) as UIPage;
  const newPage: UIPage = {
    ...clonedPage,
    id: newId,
    name: newName,
    elements: page.elements.map((el) => cloneWithIds(el, idMap)),
    layouts: (clonedPage.layouts ?? []).map((l) => layoutRenamed(l, idMap)),
  };

  const result = [...pages];
  result.splice(idx + 1, 0, newPage);
  return result;
}

/** A layout with every id in `idMap` renamed, its boxes kept in their order. */
function layoutRenamed(layout: Layout, idMap: Map<string, string>): Layout {
  const placements: Record<string, Placement> = {};
  for (const [elId, p] of Object.entries(layout.placements ?? {})) {
    placements[idMap.get(elId) ?? elId] = p;
  }
  return {
    ...layout,
    placements,
    hidden: (layout.hidden ?? []).map((id) => idMap.get(id) ?? id),
  };
}

// --- Containers: what is inside what, and moving things between them ---

export {
  descendantIds,
  canReparent,
  reparentElement,
  containerChoices,
  dropTargetFor,
  resolveDropParent,
  commitGesturePlacements,
  outlineDropParent,
  topmostSelection,
} from "./containerTree";

// --- The Outline's rows ---

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
    const key = containerOf(el, ids);
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

// --- Alignment helpers ---

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

/**
 * What an arranging tool works on: one page's selection, read in page space.
 *
 * The alignment tools share everything but their arithmetic: a selected
 * container already carries its children (`topmostSelection`), the boxes are
 * compared where the eye sees them (`absolutePlacements`), a locked control
 * counts but never moves (`movableIds`), and what moved is written back against
 * each control's own container (`withAbsolutePlacements`). `arrangeSelection`
 * is that walk; each tool is the function it hands the walk.
 */
interface Arranging {
  /** The selection less anything a selected container carries, in the order selected. */
  ids: string[];
  /** The same controls in page order, each one on the page. */
  targets: UIElement[];
  /** Every box on the page, in page space; read once, after a tool's own guard. */
  pageBoxes: () => Record<string, Placement>;
  /** The selected controls a tool may move. */
  movable: Set<string>;
}

/** The page-space boxes an arranging tool moved; empty when it moved none. */
type Arranged = Record<string, Placement>;

function arrangeSelection(
  pages: UIPage[],
  pageId: string,
  selection: { elementIds: string[]; layoutId?: string | null },
  arrange: (sel: Arranging) => Arranged,
): UIPage[] {
  return pages.map((p) => {
    if (p.id !== pageId) return p;
    const ids = topmostSelection(p, selection.elementIds);
    const kept = new Set(ids);
    let absolute: Record<string, Placement> | undefined;
    const pageBoxes = () => (absolute ??= absolutePlacements(p, selection.layoutId));
    const moved = arrange({
      ids,
      targets: p.elements.filter((el) => kept.has(el.id)),
      pageBoxes,
      movable: movableIds(p, ids),
    });
    if (Object.keys(moved).length === 0) return p;
    return withAbsolutePlacements(p, moved, pageBoxes(), selection.layoutId);
  });
}

export type AlignAction =
  | "align-left" | "align-center" | "align-right"
  | "align-top" | "align-middle" | "align-bottom";

/** The edges a selection is lined up on. */
interface Span {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

/** What each alignment does to one box, given the edges it lines up on. */
const ALIGNED = new Map<AlignAction, (box: Placement, span: Span) => Partial<Placement>>([
  ["align-left", (_box, span) => ({ x: span.left })],
  ["align-center", (box, span) => ({ x: span.left + (span.right - span.left - box.w) / 2 })],
  ["align-right", (box, span) => ({ x: span.right - box.w })],
  ["align-top", (_box, span) => ({ y: span.top })],
  ["align-middle", (box, span) => ({ y: span.top + (span.bottom - span.top - box.h) / 2 })],
  ["align-bottom", (box, span) => ({ y: span.bottom - box.h })],
]);

/**
 * Several selected: the selection's own bounding box, which is what "line
 * these up with each other" means. One selected: the page, which in page space
 * is the whole 0..100 box -- so a lone element centres on the page rather than
 * sitting still.
 */
function alignSpan(count: number, boxes: Placement[]): Span {
  if (count <= 1) return { left: 0, right: 100, top: 0, bottom: 100 };
  return {
    left: Math.min(...boxes.map((b) => b.x)),
    right: Math.max(...boxes.map((b) => b.x + b.w)),
    top: Math.min(...boxes.map((b) => b.y)),
    bottom: Math.max(...boxes.map((b) => b.y + b.h)),
  };
}

export function alignElements(
  pages: UIPage[],
  pageId: string,
  elementIds: string[],
  action: AlignAction,
  layoutId?: string | null,
): UIPage[] {
  return arrangeSelection(pages, pageId, { elementIds, layoutId }, ({ targets, pageBoxes, movable }) => {
    if (targets.length === 0) return {};
    const absolute = pageBoxes();
    const boxes = new Map(
      targets.map((el) => [el.id, absolute[el.id] ?? { ...DEFAULT_PLACEMENT }]),
    );
    const span = alignSpan(targets.length, [...boxes.values()]);
    const place = ALIGNED.get(action);
    const moved: Arranged = {};
    for (const el of targets) {
      if (!movable.has(el.id)) continue;
      const box = { ...boxes.get(el.id)! };
      moved[el.id] = { ...box, ...place?.(box, span) };
    }
    return moved;
  });
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
  return arrangeSelection(pages, pageId, { elementIds, layoutId }, ({ targets, pageBoxes, movable }) => {
    if (targets.length < 3) return {};
    const absolute = pageBoxes();
    const pos = axis === "horizontal" ? "x" : "y";
    const size = axis === "horizontal" ? "w" : "h";
    const boxes = new Map(
      targets.map((el) => [el.id, { ...(absolute[el.id] ?? DEFAULT_PLACEMENT) }]),
    );

    const sorted = alongAxis(targets, boxes, pos, size);
    const first = boxes.get(sorted[0].id)!;
    const last = boxes.get(sorted[sorted.length - 1].id)!;
    const span = last[pos] + last[size] - first[pos];
    const occupied = sorted.reduce((sum, el) => sum + boxes.get(el.id)![size], 0);
    const gap = (span - occupied) / (sorted.length - 1);

    // A locked element in the middle is not written. The run carries on from
    // the place it was given, not from where it sits, so the gaps either side
    // of it do not even out.
    const moved: Arranged = {};
    let cursor = first[pos] + first[size];
    for (let i = 1; i < sorted.length - 1; i++) {
      const box = { ...boxes.get(sorted[i].id)! };
      box[pos] = cursor + gap;
      cursor = box[pos] + box[size];
      if (movable.has(sorted[i].id)) moved[sorted[i].id] = box;
    }
    return moved;
  });
}

/**
 * The order a distribute walks: the leading edge, with the far edge and then
 * the id as tie-breaks, so two elements starting at the same coordinate still
 * sort the same way every time it runs.
 */
function alongAxis(
  targets: UIElement[],
  boxes: Map<string, Placement>,
  pos: "x" | "y",
  size: "w" | "h",
): UIElement[] {
  const lead = (el: UIElement) => boxes.get(el.id)![pos];
  const far = (el: UIElement) => lead(el) + boxes.get(el.id)![size];
  return [...targets].sort(
    (a, b) => lead(a) - lead(b) || far(a) - far(b) || (a.id < b.id ? -1 : 1),
  );
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
  return arrangeSelection(pages, pageId, { elementIds, layoutId }, ({ ids, pageBoxes, movable }) => {
    if (ids.length < 2) return {};
    const absolute = pageBoxes();
    const anchor = absolute[ids[0]];
    if (!anchor) return {};
    const moved: Arranged = {};
    for (const id of ids.slice(1)) {
      const box = absolute[id];
      if (!movable.has(id) || !box) continue;
      const next = { ...box };
      if (action !== "match-height") next.w = anchor.w;
      if (action !== "match-width") next.h = anchor.h;
      // An aspect-locked element is centred inside its box by the renderer, so
      // resizing the box is still the right write -- the lock reshapes what is
      // drawn inside it, exactly as it does during a handle drag.
      moved[id] = next;
    }
    return moved;
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
  const taken = takenElementIds(newPages, masterElements.map((m) => m.id));
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
  const taken = takenElementIds(pages, newMasters.map((m) => m.id));
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

// --- Renaming a control ---

export { validateElementId, renameElement, type RenameResult } from "./projectRewrites";

// --- The page review: what a page will actually draw wrong ---
//
// The mirror of openavc/ui/page_review.py, which runs at the AI write door. The
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
    | "binding_without_key"
    | "binding_not_dispatched"
    | "property_not_rendered"
    | "nothing_to_draw"
    | "keypad_never_sends"
    | "keypad_sends_early"
    | "unknown_element_type"
    | "style_too_large"
    | "too_small_to_draw"
    | "matrix_not_configured"
    | "matrix_config_unread"
    | "matrix_no_route_feedback"
    | "matrix_default_size"
    | "matrix_duplicate_values"
    | "matrix_lock_unbacked"
    | "custom_page_elements_not_drawn"
    | "custom_page_without_a_file"
    | "covers_master";
  /** The whole finding in one self-contained sentence. */
  message: string;
  /** What makes a finding the same finding across two arrangements -- an
   *  element can collide with three neighbours, and all three are worth saying
   *  once each rather than once per layout that inherits the collision. */
  key: string;
}

// The floor each control is measured against is controlMinimums.ts, the twin
// of openavc/ui/control_minimums.py.
export type { ElementDefaults } from "./controlMinimums";

/** An element's `show` bindings, when it carries an object of them.
 *
 *  Every review check that reads a slot (`value`, `look`, `items`,
 *  `visible_when`) starts here. Mirrors `_show` in page_review.py, which
 *  also turns away a list; no check here reads a slot a list has. */
function showOf(el: UIElement): Record<string, unknown> | null {
  const show = ((el.bindings ?? {}) as Record<string, unknown>).show;
  return show && typeof show === "object" ? (show as Record<string, unknown>) : null;
}

/** One box in an element's ancestry, as a remedy would have to name it. */
export interface Container {
  /** How it reads mid-sentence: `'grp_strip'`, or `the page` for the root. */
  label: string;
  widthPx: number;
  heightPx: number;
}

/**
 * Where one box is drawn, in reference pixels: what every per-box check measures.
 *
 * Its own size, the container it has to fit (and how a sentence names that
 * container), the containers above that one, innermost first, and which way
 * round the screen is.
 */
export interface Seat {
  boxPx: { width: number; height: number };
  parentPx: { width: number; height: number };
  parentName: string;
  ancestors: Container[];
  orientation: string;
}

/** The root every chain ends at. It is the one box no remedy can grow.
 *
 *  A function rather than a constant because a portrait arrangement is drawn on
 *  the same screen turned. Mirrors `page_container` in page_review.py. */
function pageContainer(orientation: string = "landscape"): Container {
  const ref = referenceBox(orientation);
  return { label: "the page", widthPx: ref.width, heightPx: ref.height };
}

/** A starved axis whose floor is larger than the container itself. */
type BlockedAxis = ShortAxis;

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
  return shortAxes(box, boxPx).filter(({ axis, wants }) => wants > parentPx[axis] + PIXEL_SLACK);
}

function grown(container: Container, axis: "width" | "height"): number {
  return axis === "width" ? container.widthPx : container.heightPx;
}

/** One axis of a remedy: `w at least 7.25%`. */
function atLeast(axis: "width" | "height", percent: number): string {
  return `${axis === "width" ? "w" : "h"} at least ${pct(percent)}%`;
}

/** The remedy as shares of the element's own parent, width first; empty with none. */
function giveIt(shares: Partial<Record<"width" | "height", number>>, parentName: string): string {
  const parts = (["width", "height"] as const)
    .filter((axis) => shares[axis] !== undefined)
    .map((axis) => atLeast(axis, shares[axis] as number));
  return parts.length ? ` Give it ${parts.join(" and ")} of ${parentName}.` : "";
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
  page: Container = pageContainer(),
): { holder: Container; parent: Container; needs: Record<string, number> } | null {
  // A zero box has no share to scale; that is its own finding.
  if (blocked.some((b) => b.has <= 0)) return null;
  const chain = [...ancestors, page];
  for (let index = 0; index < chain.length - 1; index++) {
    const holder = chain[index];
    const parent = chain[index + 1];
    const needs: Record<string, number> = {};
    let fits = true;
    for (const { axis, wants, has } of blocked) {
      const size = (grown(holder, axis) * wants) / has;
      if (size > grown(parent, axis) + PIXEL_SLACK) {
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
function containerClause(
  ancestors: Container[],
  blocked: BlockedAxis[],
  pageRoot: Container = pageContainer(),
): string {
  const axes = blocked.map((b) => b.axis);
  const remedy = containerRemedy(ancestors, blocked, pageRoot);
  if (!remedy) {
    // Nothing in the ancestry has room to grow, so the page itself is the
    // binding constraint. Said as the ceiling rather than as a percentage,
    // because there is no percentage that helps.
    return blocked
      .map(({ axis, wants, has }) => {
        const outer = ancestors.length
          ? grown(ancestors[ancestors.length - 1], axis)
          : has;
        const page = grown(pageRoot, axis);
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
    .map((axis) => atLeast(axis, (needs[axis] / grown(parent, axis)) * 100))
    .join(" and ");
  return (
    ` ${holder.label} is ${size}, too small to hold it at any ${word}: ` +
    `give ${holder.label} ${gives} of ${parent.label}.`
  );
}

/**
 * The share of its parent each short axis needs, for the axes a share can still
 * reach. `need` is the floor as shares of the parent. A blocked axis is left
 * out: its share would be over 100%, and the container clause names the box that
 * has to grow instead.
 */
function sharesWithinReach(
  short: ShortAxis[],
  blocked: BlockedAxis[],
  need: { w: number; h: number } | null,
): Partial<Record<"width" | "height", number>> {
  if (!need) return {};
  const stuck = new Set(blocked.map((b) => b.axis));
  const shares: Partial<Record<"width" | "height", number>> = {};
  for (const { axis } of short) {
    if (!stuck.has(axis)) shares[axis] = axis === "width" ? need.w : need.h;
  }
  return shares;
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
  seat: Seat,
  theme?: ElementDefaults,
): ReviewFinding | null {
  const { boxPx, parentPx, parentName, ancestors, orientation } = seat;
  const box = controlMinimumBox(el, theme, orientation);
  if (!box) return null;
  const reasons = starvedReasons(box, boxPx.width, boxPx.height);
  if (!reasons.length) return null;

  const blocked = blockedAxes(box, boxPx, parentPx);
  const need = controlMinimumPercent(el, parentPx, theme, orientation);
  const pageRoot = pageContainer(orientation);
  let fix = giveIt(sharesWithinReach(shortAxes(box, boxPx), blocked, need), parentName);
  if (blocked.length) fix += containerClause(ancestors, blocked, pageRoot);
  return {
    elementId: el.id,
    kind: "too_small_for_contents",
    message:
      `${el.id} (${el.type}) is ${fixed(boxPx.width, 0)}x${fixed(boxPx.height, 0)}px at the ` +
      `${fixed(pageRoot.widthPx, 0)}x${fixed(pageRoot.heightPx, 0)} reference, too small for what it ` +
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
 *  openavc/ui/page_review.py, with a test asserting both the match and that
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
  seat: Seat,
  theme?: ElementDefaults,
): ReviewFinding | null {
  const { boxPx, parentPx, parentName, orientation } = seat;
  if (controlMinimumBox(el, theme, orientation)) return null;
  if (boxPx.width >= MINIMUM_VISIBLE_PX && boxPx.height >= MINIMUM_VISIBLE_PX) return null;

  const shares: Partial<Record<"width" | "height", number>> = {};
  for (const axis of ["width", "height"] as const) {
    if (boxPx[axis] < MINIMUM_VISIBLE_PX && parentPx[axis] > 0) {
      shares[axis] = (MINIMUM_VISIBLE_PX / parentPx[axis]) * 100;
    }
  }
  const fix = giveIt(shares, parentName);
  const pageRoot = pageContainer(orientation);
  return {
    elementId: el.id,
    kind: "too_small_to_draw",
    message:
      `${el.id} (${el.type}) is ${fixed(boxPx.width, 0)}x${fixed(boxPx.height, 0)}px at the ` +
      `${fixed(pageRoot.widthPx, 0)}x${fixed(pageRoot.heightPx, 0)} reference, which is not small, it is ` +
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
  const short = underTouch(boxPx);
  if (!short.length) return null;
  return {
    elementId: el.id,
    kind: "small_touch_target",
    message:
      `${el.id} (${el.type}) is about ${fixed(boxPx.width, 0)}x${fixed(boxPx.height, 0)}px, roughly ` +
      `${fixed(toMm(boxPx.width), 1)}x${fixed(toMm(boxPx.height), 1)}mm on a 10-inch panel -- under the ` +
      `${TOUCH_MIN_MM}mm comfortable touch minimum on ${joined(short)} (${fixed(TOUCH_MIN_PX, 0)}px).`,
    key: `small_touch_target|${el.id}`,
  };
}

/** The axes a finger has too little of, width first: each needs `TOUCH_MIN_PX`.
 *  Mirrors `_under_touch` in page_review.py. */
function underTouch(boxPx: { width: number; height: number }): string[] {
  return (["width", "height"] as const).filter((axis) => boxPx[axis] < TOUCH_MIN_PX);
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

/** One style field the panel draws: its name, its value in rem, and that value
 *  in pixels. */
interface StyleMeasure {
  field: string;
  rem: number;
  px: number;
}

/** The first of these style fields that is set.
 *
 *  Ordered the way the panel resolves them: a specific axis wins over the
 *  shorthand, so the message names the field the author would actually edit. */
function styleMeasure(style: Record<string, unknown>, ...names: string[]): StyleMeasure | null {
  for (const name of names) {
    const value = style[name];
    if (typeof value === "number" && value > 0) return { field: name, rem: value, px: value * REM_BASE_PX };
  }
  return null;
}

/** The room a side gives a measurement drawn once across it, when it is longer.
 *
 *  Text against the box's height, the gap between two letters against its width:
 *  the room is the side itself, and `slack` is how far past it still draws alike.
 *  null when it fits. A side with no size is not measured -- nothing is drawn
 *  there to outgrow. Its twin in page_review.py is `_past_one_side`. */
function pastOneSide(measure: StyleMeasure, side: number, slack = 0): number | null {
  return side > 0 && measure.px > side + slack ? side : null;
}

/** The room each edge has for a measurement drawn on both edges of a side, when
 *  the two meet.
 *
 *  Padding on two opposite sides, a border across the box's shorter side: each
 *  edge has half the side. null when they leave a gap between them. A side with
 *  no size is not measured. Its twin in page_review.py is `_meets_in_middle`. */
function meetsInMiddle(measure: StyleMeasure, side: number): number | null {
  return side > 0 && 2 * measure.px >= side ? side / 2 : null;
}

/** One style measurement the box cannot hold: the words for it, the field to
 *  change, and the most room the box gives that field, in pixels. */
type StyleBreach = [reason: string, field: string, limitPx: number];

type StyleCheck = (style: Record<string, unknown>, width: number, height: number) => StyleBreach | null;

/** Text taller than the box, past half a pixel of rounding. */
function fontBreach(style: Record<string, unknown>, _width: number, height: number): StyleBreach | null {
  const font = styleMeasure(style, "font_size");
  if (!font) return null;
  const room = pastOneSide(font, height, PIXEL_SLACK);
  if (room === null) return null;
  return [
    `font_size ${num(font.rem)} draws ${fixed(font.px, 0)}px of text in a box ` +
      `${fixed(height, 0)}px tall`,
    font.field, room,
  ];
}

/** Padding on one axis: the fields that set it, and how a sentence names it.
 *  `names` is in the panel's order, the axis's own field before the shorthand,
 *  so a finding names the field the author would actually edit. */
interface PaddingAxis {
  names: [string, string];
  where: string;
  extent: string;
}

const PADDING_DOWN: PaddingAxis = {
  names: ["padding_vertical", "padding"], where: "above and below", extent: "tall",
};
const PADDING_ACROSS: PaddingAxis = {
  names: ["padding_horizontal", "padding"], where: "each side", extent: "wide",
};

/** Padding on two opposite sides that meets in the middle of the box. */
function paddingBreach(
  style: Record<string, unknown>,
  axis: PaddingAxis,
  size: number,
): StyleBreach | null {
  const pad = styleMeasure(style, ...axis.names);
  if (!pad) return null;
  const room = meetsInMiddle(pad, size);
  if (room === null) return null;
  return [
    `${pad.field} ${num(pad.rem)} leaves ${fixed(pad.px, 0)}px ${axis.where} ` +
      `in a box ${fixed(size, 0)}px ${axis.extent}`,
    pad.field, room,
  ];
}

const paddingDown: StyleCheck = (style, _width, height) => paddingBreach(style, PADDING_DOWN, height);

const paddingAcross: StyleCheck = (style, width) => paddingBreach(style, PADDING_ACROSS, width);

/** A border that meets in the middle of the box's shorter side. */
function borderBreach(style: Record<string, unknown>, width: number, height: number): StyleBreach | null {
  const edge = styleMeasure(style, "border_width");
  const smallest = Math.min(width, height);
  if (!edge) return null;
  const room = meetsInMiddle(edge, smallest);
  if (room === null) return null;
  return [
    `border_width ${num(edge.rem)} draws a ${fixed(edge.px, 0)}px border on a ` +
      `box ${fixed(width, 0)}x${fixed(height, 0)}px`,
    edge.field, room,
  ];
}

/** A gap between two letters wider than the box. */
function letterBreach(style: Record<string, unknown>, width: number): StyleBreach | null {
  const gap = styleMeasure(style, "letter_spacing");
  if (!gap) return null;
  const room = pastOneSide(gap, width);
  if (room === null) return null;
  return [
    `letter_spacing ${num(gap.rem)} puts ${fixed(gap.px, 0)}px between letters ` +
      `in a box ${fixed(width, 0)}px wide`,
    gap.field, room,
  ];
}

/** Every style measurement that can outgrow its box, in the order a finding reads
 *  them out. A new one is a function asking `pastOneSide` or `meetsInMiddle` for
 *  its room and an entry here, and the same in `_STYLE_BREACHES`, its twin in
 *  page_review.py. */
const STYLE_BREACHES: readonly StyleCheck[] = [
  fontBreach,
  paddingDown,
  paddingAcross,
  borderBreach,
  letterBreach,
];

/** The most each field may be, in rem. Keyed by field, because `padding` is one
 *  number governing both axes: when it breaks the box on each, it still gets one
 *  ceiling, the tighter one. */
function styleCeilings(breaches: StyleBreach[]): Map<string, number> {
  const limits = new Map<string, number>();
  for (const [, field, limitPx] of breaches) {
    const room = limitPx / REM_BASE_PX;
    limits.set(field, Math.min(limits.get(field) ?? room, room));
  }
  return limits;
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
  const breaches = STYLE_BREACHES
    .map((check) => check(style, width, height))
    .filter((breach): breach is StyleBreach => breach !== null);
  if (!breaches.length) return null;
  const fixes = joined(
    [...styleCeilings(breaches)].map(([field, room]) => `${field} at most ${pct(room)}`),
  );
  return {
    elementId: el.id,
    kind: "style_too_large",
    message:
      `${el.id} (${el.type}) is ${fixed(width, 0)}x${fixed(height, 0)}px and its style asks for ` +
      `more room than that: ${breaches.map(([reason]) => reason).join("; ")}. style measurements ` +
      `are rem, not pixels -- px / ${num(REM_BASE_PX)} -- so write ${fixes}.`,
    key: `style_too_large|${el.id}`,
  };
}

/**
 * Every check one box gets, wherever it is drawn, in the order they report.
 *
 * A page's arrangement and a master's own orientation-keyed boxes both run
 * exactly these; only an element on a page also has a container to hang out of.
 */
export function boxFindings(el: UIElement, seat: Seat, theme?: ElementDefaults): ReviewFinding[] {
  return [
    starvationFinding(el, seat, theme),
    degenerateFinding(el, seat, theme),
    touchFinding(el, seat.boxPx),
    styleFinding(el, seat.boxPx),
  ].filter((f): f is ReviewFinding => !!f);
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
  reference: { width: number; height: number } = referenceBox("landscape"),
): { oxPx: number; oyPx: number; share: number } | null {
  const ox = Math.min(aBox.x + aBox.w, bBox.x + bBox.w) - Math.max(aBox.x, bBox.x);
  const oy = Math.min(aBox.y + aBox.h, bBox.y + bBox.h) - Math.max(aBox.y, bBox.y);
  if (ox <= 0 || oy <= 0) return null;
  const oxPx = (ox / 100) * reference.width;
  const oyPx = (oy / 100) * reference.height;
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
    const owner = overlapOwner(remaining);
    const involved = (p: OverlapPair) => p.aId === owner || p.bId === owner;
    const partners = remaining
      .filter(involved)
      .map((p) => ({
        id: p.aId === owner ? p.bId : p.aId,
        oxPx: p.oxPx,
        oyPx: p.oyPx,
        share: p.share,
      }))
      .sort((x, y) => textOrder(x.id, y.id));
    remaining = remaining.filter((p) => !involved(p));
    findings.push(overlapMessage(owner, partners, types, parentName));
  }
  return findings;
}

/** Two ids in the AI side's order for text: by character, never by locale. */
function textOrder(a: string, b: string): number {
  if (a === b) return 0;
  return a < b ? -1 : 1;
}

/**
 * Who answers for a set of colliding pairs: whichever element is in most of
 * them, the earlier id on a tie. Called again on what is left after each
 * answer, so the worst offender is named first. Mirrors `_overlap_owner` in
 * page_review.py. `pairs` is never empty.
 */
function overlapOwner(pairs: OverlapPair[]): string {
  const counts = new Map<string, number>();
  for (const { aId, bId } of pairs) {
    counts.set(aId, (counts.get(aId) ?? 0) + 1);
    counts.set(bId, (counts.get(bId) ?? 0) + 1);
  }
  const ranked = [...counts].sort(([aId, a], [bId, b]) => b - a || textOrder(aId, bId));
  return ranked[0][0];
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

/** The operator spellings the panel's `_evalConditionOp` understands, by what
 *  they mean; held to the AI side's by tests/test_ui_page_review_mirrors.py. */
const EQ_OPS = new Set(["eq", "equals", "=="]);
const NE_OPS = new Set(["ne", "not_equals", "!="]);
/** Range operators, mapped to whether the bound they set is inclusive. */
const LOWER_OPS: Record<string, boolean> = { gt: false, ">": false, gte: true, ">=": true };
const UPPER_OPS: Record<string, boolean> = { lt: false, "<": false, lte: true, "<=": true };

/** One condition, read off a `visible_when`. */
type Leaf = { key: string; op: string; value: unknown };

/** The kinds of authored literal both surfaces read alike. */
type LiteralKind = "null" | "bool" | "number" | "text";

/**
 * Which kind of authored literal this is, where both surfaces agree; null where
 * they do not.
 *
 * A list or an object as a condition value is not something either surface can
 * reason about, and Python and JavaScript do not even agree on whether an empty
 * one is truthy. Equality, truthiness and the bounds below all ask this first.
 * Mirrors `_literal_kind` in page_review.py.
 */
function literalKind(value: unknown): LiteralKind | null {
  if (value === null) return "null";
  switch (typeof value) {
    case "boolean":
      return "bool";
    case "number":
      return "number";
    case "string":
      return "text";
    default:
      return null;
  }
}

/**
 * Two authored literals, compared the way both surfaces can agree on.
 *
 * Not `==`: Python says `1 == True` and JavaScript says `"1" == 1`, and they
 * disagree about which. Same kind and same value, or different.
 */
function sameValue(left: unknown, right: unknown): boolean {
  const kind = literalKind(left);
  return kind !== null && kind === literalKind(right) && left === right;
}

/** A condition value as a number, or null when it is not one.
 *
 *  A numeric string is deliberately not one: the panel compares it with
 *  JavaScript's coercing `>`, which has no Python equivalent worth mirroring,
 *  and undecidable here only costs a warning nobody needed suppressed. */
function conditionNumber(value: unknown): number | null {
  return literalKind(value) === "number" ? (value as number) : null;
}

/** Whether a key pinned to `value` satisfies `op target`.
 *
 *  True for anything undecidable, so an unreadable pair yields no proof of
 *  exclusivity rather than a false one. */
function conditionHolds(value: unknown, op: string, target: unknown): boolean {
  if (EQ_OPS.has(op)) return sameValue(value, target);
  if (NE_OPS.has(op)) return !sameValue(value, target);
  if (op === "truthy" || op === "falsy") return truthHolds(value, op);
  if (op in LOWER_OPS || op in UPPER_OPS) return boundHolds(value, op, target);
  return true;
}

/** Whether a key pinned to `value` is `truthy` or `falsy`, as `op` asks.
 *
 *  True (undecidable) for a value the two surfaces would not read alike. */
function truthHolds(value: unknown, op: string): boolean {
  if (literalKind(value) === null) return true;
  return op === "truthy" ? !!value : !value;
}

/** Whether a key pinned to `value` is on the near side of the bound `op target`.
 *
 *  True (undecidable) unless both are numbers. */
function boundHolds(value: unknown, op: string, target: unknown): boolean {
  const left = conditionNumber(value);
  const right = conditionNumber(target);
  if (left === null || right === null) return true;
  if (op in LOWER_OPS) return LOWER_OPS[op] ? left >= right : left > right;
  return UPPER_OPS[op] ? left <= right : left < right;
}

/** Whether a lower bound and an upper bound leave no number that meets both.
 *
 *  False (no proof) unless both bounds are numbers. */
function noRoomBetween(lower: Leaf, upper: Leaf): boolean {
  const low = conditionNumber(lower.value);
  const high = conditionNumber(upper.value);
  if (low === null || high === null) return false;
  const closed = LOWER_OPS[lower.op] && UPPER_OPS[upper.op];
  return high < low || (high === low && !closed);
}

/** Whether condition `a` rules out `b`, read in one direction only. */
function rulesOut(a: Leaf, b: Leaf): boolean {
  if (EQ_OPS.has(a.op)) {
    // `key == a.value` pins the key to one value, so the other condition is
    // either satisfied by that value or contradicts it outright.
    return !conditionHolds(a.value, b.op, b.value);
  }
  if (a.op === "truthy") return b.op === "falsy";
  if (a.op in LOWER_OPS && b.op in UPPER_OPS) return noRoomBetween(a, b);
  return false;
}

/** One condition as a leaf, or null if it is unreadable. */
function conditionLeaf(condition: unknown): Leaf | null {
  if (!isPlainObject(condition)) return null;
  const { key, operator, value } = condition;
  if (typeof key !== "string" || !key) return null;
  return { key, op: String(operator || "eq").toLowerCase(), value: value ?? null };
}

/**
 * A `visible_when` as branches of ANDed leaves: visible if any branch is.
 *
 * A bare condition and an `all:` block are one branch; an `any:` block is one
 * branch per condition. Null means there is nothing to reason about, which is
 * not the same as an empty list -- an empty list of branches is never
 * satisfiable, and would suppress every overlap on the page. An unreadable
 * condition is dropped from `all:` and makes `any:` undecidable; each block's
 * reader says why.
 */
function visibleWhenBranches(el: UIElement): Leaf[][] | null {
  const block = showOf(el)?.visible_when;
  if (!isPlainObject(block)) return null;

  if (Array.isArray(block.any)) return anyBranches(block.any);
  if (Array.isArray(block.all)) return allBranch(block.all);
  const leaf = conditionLeaf(block);
  return leaf ? [[leaf]] : null;
}

/** An `any:` block, one branch per condition; null if any one is unreadable.
 *  Dropping an unreadable condition here would NARROW the set, which could
 *  prove an exclusivity that is not there. */
function anyBranches(conditions: unknown[]): Leaf[][] | null {
  const leaves = conditions.map(conditionLeaf);
  if (!leaves.length || leaves.some((leaf) => leaf === null)) return null;
  return leaves.map((leaf) => [leaf as Leaf]);
}

/** An `all:` block as one branch of its readable conditions; null when none is.
 *  Dropping an unreadable condition only widens the set: if the wider condition
 *  is still exclusive, so is the real one. */
function allBranch(conditions: unknown[]): Leaf[][] | null {
  const kept = conditions.map(conditionLeaf).filter((leaf): leaf is Leaf => !!leaf);
  return kept.length ? [kept] : null;
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

// --- A page's controls against the masters underneath them -----------------
//
// Masters are appended to the page surface BEFORE the page's own elements
// (panel.js `renderPage`), and every child of `.panel-page` shares one z-index,
// so the later sibling wins: a control laid over a master hides it and takes the
// touch. That is the whole failure. A master nav bar is how somebody gets off a
// page -- decision 15 of the custom-UI plan makes it the ONLY way off a page
// that draws its own markup -- so burying one is not a cosmetic collision.
//
// It was invisible on both surfaces: the overlap check collides a page's
// elements with each other, and masters are not among them.
//
// A custom page is the exception and needs no test here. There the frame is
// appended FIRST and the masters paint over it, and `reviewPage` has already
// returned by the time this runs (its controls are not drawn at all, which is
// the one thing it says about such a page).

/**
 * Which of a master's stored boxes is drawn on a page arranged this way.
 *
 * The panel's `_masterPlacement`: this orientation's own box, then landscape,
 * then portrait, then whatever is there. A master has to be valid on every page
 * it appears on, so it carries its own orientation-keyed boxes rather than
 * borrowing a layout's -- and the panel falls back rather than drawing nothing,
 * so a master with only a landscape box still appears on a portrait screen.
 *
 * The canvas reads the same chain through `masterPlacement` (pageGeometry.ts), as
 * the panel does. This one, like `_stored_master_box` on the AI side, differs
 * from both past the named orientations: they take the first stored value and
 * this the first that is set, so a box stored as null draws nothing there and
 * is skipped here. The project model refuses a null box.
 */
function storedMasterBox(placements: Record<string, Placement>, orientation: string): Placement | null {
  return (
    placements[orientation] ||
    placements.landscape ||
    placements.portrait ||
    Object.values(placements).find((box) => box) ||
    null
  );
}

/** A stored box as the panel draws it, or null when it is not numbers: the same
 *  defaults `_placeElement` applies to a missing coordinate. */
function drawnBox(raw: Placement): Placement | null {
  const box = {
    x: Number(raw.x || 0),
    y: Number(raw.y || 0),
    w: Number(raw.w || 100),
    h: Number(raw.h || 100),
  };
  return Object.values(box).every((n) => Number.isFinite(n)) ? box : null;
}

/** The box a master draws in on a page arranged this way. */
export function masterBox(
  master: MasterElement,
  orientation: string,
): Placement | null {
  const placements = master.placements;
  if (!placements || typeof placements !== "object") return null;
  const raw = storedMasterBox(placements, orientation);
  return raw ? drawnBox(raw) : null;
}

/** Whether a master appears on this page. The panel's own test, exactly. */
export function masterDrawsOn(pages: string | string[], pageId: string): boolean {
  return pages === "*" || (Array.isArray(pages) && pages.includes(pageId));
}

/** How widely a master draws, said the way it matters to the reader.
 *
 *  The point of the clause is that a master is shared, so moving it is not a
 *  local fix -- whatever it is doing here, it is doing somewhere else too. */
function masterScope(pages: string | string[]): string {
  if (Array.isArray(pages)) return `${pages.length} page${pages.length === 1 ? "" : "s"}`;
  return "every page";
}

/**
 * A page's controls as one arrangement draws them, which is what a master
 * underneath them is checked against.
 *
 * `boxes` are absolute -- page percentages with container nesting already
 * flattened -- because a master's box is a percentage of the viewport and a
 * nested control's is a percentage of its container, so the two are not
 * comparable until one of them has been folded down. They hold only the
 * controls drawn in this arrangement; `elements` and `parents` hold every
 * element on the page, by id. Its twin in page_review.py is `DrawnPage`.
 */
export interface DrawnPage {
  pageId: string;
  orientation: string;
  boxes: Map<string, Placement>;
  elements: Map<string, UIElement>;
  parents: Map<string, string | null>;
}

/** One master as it draws on one page: its box, its size, the screen it is on. */
interface MasterHere {
  master: MasterElement;
  pageId: string;
  box: Placement;
  widthPx: number;
  heightPx: number;
  reference: { width: number; height: number };
}

/** A master as it draws on this page, or null when it draws nothing here:
 *  hidden, listed for other pages only, with no box, or with no size. */
function masterHere(master: MasterElement, drawn: DrawnPage): MasterHere | null {
  const { pageId, orientation } = drawn;
  if (master.hidden || !masterDrawsOn(master.pages ?? "*", pageId)) return null;
  const box = masterBox(master, orientation);
  if (!box) return null;
  const reference = referenceBox(orientation);
  const widthPx = (box.w / 100) * reference.width;
  const heightPx = (box.h / 100) * reference.height;
  if (widthPx <= 0 || heightPx <= 0) return null;
  return { master, pageId, box, widthPx, heightPx, reference };
}

type Covered = Map<string, { oxPx: number; oyPx: number }>;

/** Each drawn control lying on this master, with the pixels it covers per axis.
 *  A control that can never be on screen with the master is not lying on it. */
function coveredBy(here: MasterHere, drawn: DrawnPage): Covered {
  const covered: Covered = new Map();
  for (const [elId, box] of drawn.boxes) {
    if (mutuallyExclusive(drawn.elements.get(elId) as UIElement, here.master)) continue;
    const extent = overlapExtent(box, here.box, here.reference);
    if (extent) covered.set(elId, { oxPx: extent.oxPx, oyPx: extent.oyPx });
  }
  return covered;
}

function coversMasterFinding(
  here: MasterHere,
  elId: string,
  el: UIElement | undefined,
  overlap: { oxPx: number; oyPx: number },
): ReviewFinding {
  const { master, pageId } = here;
  const { oxPx, oyPx } = overlap;
  const share = (100 * (oxPx * oyPx)) / (here.widthPx * here.heightPx);
  return {
    elementId: elId,
    kind: "covers_master",
    // The coverage goes LAST because a variant arrangement appends
    // " in the 'portrait' arrangement" to whatever the sentence ends with,
    // and geometry is the half that is per-arrangement -- a master's page
    // list is not.
    message:
      `${elId} (${el?.type ?? "?"}) is drawn over the master element ` +
      `${master.id} (${master.type}), which draws on ` +
      `${masterScope(master.pages ?? "*")} and sits behind a page's own controls. ` +
      `Move ${elId} off it, or stop ${master.id} drawing on ${pageId}. ` +
      `${elId} covers ${fixed(oxPx, 0)}x${fixed(oyPx, 0)}px of ${master.id}, ` +
      `${fixed(share, 0)}% of it.`,
    key: `covers_master|${elId}|${master.id}`,
  };
}

/**
 * Every control on this page that is drawn over a master element.
 *
 * Reported on the CONTROL rather than on the master: the control is what moved,
 * it is the thing being edited, and the master has no page to be warned about it
 * on. A control inside a container that is itself burying the master says the
 * same thing twice, so only the container answers -- moving it is the fix, and
 * the child has done nothing wrong.
 */
export function buriedMasterFindings(
  drawn: DrawnPage,
  masters: MasterElement[],
  inScope: (id: string) => boolean,
): ReviewFinding[] {
  const findings: ReviewFinding[] = [];
  for (const master of masters) {
    const here = masterHere(master, drawn);
    if (!here) continue;
    const covered = coveredBy(here, drawn);
    findings.push(
      ...[...covered.keys()].sort()
        .filter((elId) => inScope(elId) && !coveredAncestor(elId, covered, drawn.parents))
        .map((elId) => coversMasterFinding(
          here, elId, drawn.elements.get(elId), covered.get(elId) as { oxPx: number; oyPx: number },
        )),
    );
  }
  return findings;
}

/** Whether a container above this element is burying the same master. */
function coveredAncestor(
  elId: string,
  covered: Map<string, unknown>,
  parents: Map<string, string | null>,
): boolean {
  const seen = new Set<string>([elId]);
  let cursor = parents.get(elId) ?? null;
  while (cursor && !seen.has(cursor)) {
    if (covered.has(cursor)) return true;
    seen.add(cursor);
    cursor = parents.get(cursor) ?? null;
  }
  return false;
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
/**
 * What a page that draws its own markup is doing with the controls on it.
 *
 * Two ways this goes wrong, and neither has any geometry to look at.
 *
 * A page set to draw its own markup keeps every control that was ever placed on
 * it -- deliberately, so switching back restores the page rather than asking
 * somebody to rebuild it. But the panel draws none of them, so every other
 * finding about them would be advice about pixels nobody will see, and a clean
 * review would read as "those controls are fine".
 *
 * And a page set to draw its own markup that names no file draws its controls
 * after all, which looks from the file like the switch did nothing. That is the
 * same shape as an image with no `src`.
 */
export function customPageFindings(page: UIPage): ReviewFinding[] {
  if (page.render_mode !== "custom") return [];
  const count = page.elements?.length ?? 0;
  if (!page.custom_file) {
    return [{
      elementId: page.id,
      kind: "custom_page_without_a_file",
      message:
        `${page.id} is set to show a page you wrote but names no file, so it still ` +
        `draws its controls. Choose a file in the project's ui/ folder, or set the ` +
        `page back to controls.`,
      key: `custom_page_without_a_file|${page.id}`,
    }];
  }
  if (!count) return [];
  const controls = count === 1 ? "control" : "controls";
  const them = count === 1 ? "it" : "them";
  return [{
    elementId: page.id,
    kind: "custom_page_elements_not_drawn",
    message:
      `${page.id} shows ${page.custom_file}, so the ${count} ${controls} on it ` +
      `${count === 1 ? "is" : "are"} not drawn. Move ${them} to another page, or set ` +
      `the page back to controls to show ${them} again.`,
    key: `custom_page_elements_not_drawn|${page.id}`,
  }];
}

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

/** One element's `show` bindings, beside what its type's renderer reads. */
interface Shown {
  el: UIElement;
  honored: readonly string[];
  show: Record<string, unknown>;
}

function shownBindings(el: UIElement): Shown | null {
  const honored = own(HONORED_SHOW_SLOTS, el.type);
  if (!honored) return null; // a type this module has never heard of; say nothing
  const show = showOf(el);
  if (!show) return null;
  return { el, honored, show };
}

/** `show.look` when this type reads one at all, else null. */
function shownLook(shown: Shown): Record<string, unknown> | null {
  const look = shown.show.look;
  return shown.honored.includes("look") && isPlainObject(look) ? look : null;
}

/** The sentence for a per-state setting this type takes nothing of from show.look.
 *
 *  `setting` continues `show.look`: ` sets an icon for on, off`. */
function lookDropped(shown: Shown, setting: string, noun: string, remedy: string): string {
  const { el } = shown;
  return (
    `${el.id} (${el.type}): show.look${setting}, but ${withArticle(el.type)} takes only ` +
    `${lookTakes(el.type)} from show.look, so that ${noun} never appears. ${remedy}`
  );
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
  const shown = shownBindings(el);
  if (!shown) return [];
  return [
    ...unreadSlotFindings(shown),
    ...lookLabelFindings(shown),
    ...lookFieldFindings(shown),
    ...keylessSlotFindings(shown),
  ];
}

/** A whole show slot this type has no code path for. */
function unreadSlotFindings({ el, honored, show }: Shown): ReviewFinding[] {
  const reads =
    REVIEWED_SHOW_SLOTS.filter((slot) => honored.includes(slot))
      .map((slot) => `show.${slot}`)
      .join(", ") || "nothing from show but show.visible_when";
  const findings: ReviewFinding[] = [];
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
        `${el.id} (${el.type}) declares show.${slot}, which ${withArticle(el.type)} does not render ` +
        `-- it reads ${reads}. That binding has no effect.${extra}`,
      key: `binding_not_rendered|${el.id}|${slot}`,
    });
  }
  return findings;
}

/**
 * Per-state words on a type that takes only colour from a look.
 *
 * A look binding can be honored for its colour while its state labels are not:
 * a status LED tints its dot, a select styles its options, an image swaps its
 * picture, and none of them has anywhere to put text. A label that is present
 * counts even when it is empty: the renderer acts on the field.
 */
function lookLabelFindings(shown: Shown): ReviewFinding[] {
  const look = shownLook(shown);
  const { el } = shown;
  if (!look || STATE_LABEL_TYPES.includes(el.type)) return [];
  const states = look.states;
  if (!isPlainObject(states)) return [];
  const labelled = Object.entries(states)
    .filter(([, spec]) => isPlainObject(spec) && (spec.label ?? null) !== null)
    .map(([name]) => name)
    .sort();
  if (!labelled.length) return [];
  const remedy =
    own(STATE_TEXT_REMEDY, el.type) ??
    "Use the element's own label, or a label element bound to the same key.";
  return [{
    elementId: el.id,
    kind: "binding_not_rendered",
    message: lookDropped(shown, `.states sets a label for ${labelled.join(", ")}`, "text", remedy),
    key: `binding_not_rendered|${el.id}|look.states.label`,
  }];
}

/** A per-state look field only some types draw, and what to say when one is dropped. */
interface LookField {
  field: string;
  drawnBy: readonly string[];
  /** How the sentence introduces one: `an icon`. */
  named: string;
  /** How it refers back to it: `icon`. */
  noun: string;
  /** What to do instead, from the element's type. */
  remedy: (type: string) => string;
}

function iconRemedy(type: string): string {
  return (own(HONORED_PROPERTIES, type) ?? []).includes("icon")
    ? "Set the element's own icon -- it draws that one, it just cannot change it per state."
    : "Use a button, which does draw one.";
}

/**
 * Content a look can set per state, and the only types that draw it. A look can
 * be honored for its colour AND its text while a per-state icon is still
 * dropped, because an icon is content rather than style and only the button's
 * evaluator rebuilds it; a per-state picture is drawn only by the types in
 * STATE_IMAGE_TYPES, and a label, a status LED or a select stores one and never
 * shows it. A new per-state field the panel draws for some types is a row here,
 * beside its own STATE_* type set (generated from page_review.py) and its word
 * in lookTakes.
 */
const LOOK_FIELDS: readonly LookField[] = [
  { field: "icon", drawnBy: STATE_ICON_TYPES, named: "an icon", noun: "icon", remedy: iconRemedy },
  {
    field: "button_image",
    drawnBy: STATE_IMAGE_TYPES,
    named: "a picture (button_image)",
    noun: "picture",
    remedy: () => "Use an image element bound to the same key.",
  },
];

/** Each per-state field in LOOK_FIELDS set on a type that does not draw it. */
function lookFieldFindings(shown: Shown): ReviewFinding[] {
  const look = shownLook(shown);
  if (!look) return [];
  const { el } = shown;
  const findings: ReviewFinding[] = [];
  for (const spec of LOOK_FIELDS) {
    if (spec.drawnBy.includes(el.type)) continue;
    const names = lookStatesNaming(look, spec.field);
    if (!names.length) continue;
    findings.push({
      elementId: el.id,
      kind: "binding_not_rendered",
      message: lookDropped(
        shown, ` sets ${spec.named} for ${names.join(", ")}`, spec.noun, spec.remedy(el.type),
      ),
      key: `binding_not_rendered|${el.id}|look.${spec.field}`,
    });
  }
  return findings;
}

/** Whether a show binding names what it reads: its state key, or its key pattern
 *  for `items`. A value shown from a macro's progress reads neither. */
function namesItsSource(slot: string, binding: Record<string, unknown>): boolean {
  if (slot === "value" && binding.source === "macro_progress") return true;
  const field = slot === "items" ? "key_pattern" : "key";
  return Boolean(String(binding[field] ?? "").trim());
}

/**
 * A binding the renderer DOES read, that names nothing to read.
 *
 * It is the half-finished state every one of these passes through -- the editor
 * writes the binding when the source is chosen and the key a moment later -- and
 * left that way it is worse than no binding at all, because every other check
 * here treats a present binding as a supplied one and goes quiet.
 */
function keylessSlotFindings({ el, honored, show }: Shown): ReviewFinding[] {
  const findings: ReviewFinding[] = [];
  for (const slot of REVIEWED_SHOW_SLOTS) {
    const binding = show[slot];
    if (!honored.includes(slot) || !isPlainObject(binding)) continue;
    if (namesItsSource(slot, binding)) continue;
    const named = slot === "items" ? "key pattern" : "state key";
    findings.push({
      elementId: el.id,
      kind: "binding_without_key",
      message:
        `${el.id} (${el.type}): show.${slot} has no ${named}, so it reads nothing and ` +
        `never updates. ${UNBOUND_CONSEQUENCE[slot]} Pick the ${named} it should ` +
        `read, or remove the binding.`,
      key: `binding_without_key|${el.id}|${slot}`,
    });
  }
  return findings;
}

/**
 * Which states of a `show.look` name `field` (an icon, a picture), in both
 * spellings.
 *
 * The multi-state form carries one appearance per state; the binary form
 * carries two, `style_active` and `style_inactive`. Both are offered by the
 * same editor, so both reach the file. Mirrors _look_states_naming in
 * page_review.py.
 */
function lookStatesNaming(look: Record<string, unknown>, field: string): string[] {
  const names = (spec: unknown) => isPlainObject(spec) && !!spec[field];
  const states = look.states;
  if (isPlainObject(states)) {
    return Object.entries(states)
      .filter(([, spec]) => names(spec))
      .map(([name]) => name)
      .sort();
  }
  return ([["active", "style_active"], ["inactive", "style_inactive"]] as const)
    .filter(([, side]) => names(look[side]))
    .map(([half]) => half);
}

/** What a type draws from `show.look`, in words. Mirrors _look_takes in page_review.py. */
function lookTakes(type: string): string {
  const parts = ["color"];
  if (STATE_LABEL_TYPES.includes(type)) parts.push("text");
  if (STATE_ICON_TYPES.includes(type)) parts.push("an icon");
  if (STATE_IMAGE_TYPES.includes(type)) parts.push("a picture");
  return parts.length === 1
    ? parts[0]
    : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** A type name with its article: "a button", "an image". Mirrors _a in page_review.py. */
function withArticle(type: string): string {
  return ["a", "e", "i", "o", "u"].includes(type.slice(0, 1)) ? `an ${type}` : `a ${type}`;
}

/**
 * Where to put per-state words instead, for a type with no words of its own to
 * change. Mirrors _STATE_TEXT_REMEDY in page_review.py.
 */
const STATE_TEXT_REMEDY: Record<string, string> = {
  image: "Place a label element bound to the same key over it.",
};

/**
 * What an author sees when a slot is bound to nothing, per slot. Mirrors
 * _UNBOUND_CONSEQUENCE in page_review.py.
 */
const UNBOUND_CONSEQUENCE: Record<string, string> = {
  value: "The control shows what an unset value looks like: blank text, or the low end of a range.",
  look: "Its appearance stays on whatever an unmatched state gives it.",
  items: "The list comes up empty.",
};
// --- What a `do` action can be called --------------------------------------
//
// Mirrors do_action_findings in openavc/ui/page_review.py. The other half of the
// same silence: `bindingFindings` above answers whether this type's renderer
// READS a show slot, this answers whether the runtime DISPATCHES a do action.
// Both closed sets are generated from the modules that own them, so a new
// action name or a new macro step arrives here without anyone typing it.

const NESTED_ACTION_FIELDS = ["off_action", "hold_action"] as const;

type ActionEntry = [string, Record<string, unknown>];

/** One action and everything nested inside it, each with its own path. */
function walkAction(path: string, action: unknown): ActionEntry[] {
  if (!isPlainObject(action)) return [];
  const found: ActionEntry[] = [[path, action]];
  for (const field of NESTED_ACTION_FIELDS) {
    found.push(...walkAction(`${path}.${field}`, action[field]));
  }
  const mapped = action.map;
  if (action.action === "value_map" && isPlainObject(mapped)) {
    // Sorted, because this side hoists integer-like keys to the front of an
    // object whatever the order they were written in, and the Python side
    // walks them as authored. See the comment on the Python twin.
    for (const value of Object.keys(mapped).sort()) {
      found.push(...walkAction(`${path}.map.${value}`, mapped[value]));
    }
  }
  return found;
}

/** Every action dict under `do`, with the path a reader can go and edit. */
function actionEntries(do_: Record<string, unknown>): ActionEntry[] {
  const found: ActionEntry[] = [];
  for (const [interaction, binding] of Object.entries(do_)) {
    const entries = Array.isArray(binding) ? binding : [binding];
    for (const entry of entries) {
      found.push(...walkAction(`do.${interaction}`, entry));
    }
  }
  return found;
}

/** The same walk over a matrix row that overrides the element's do.route. */
function destinationRouteEntries(el: UIElement): ActionEntry[] {
  if (el.type !== "matrix") return [];
  const config = (el as unknown as Record<string, unknown>).matrix_config;
  if (!isPlainObject(config)) return [];
  const found: ActionEntry[] = [];
  for (const entry of resolveMatrixAxis(config, "destinations")) {
    if (!Array.isArray(entry.route)) continue;
    const named = entry.label !== undefined ? entry.label : entry.value;
    for (const action of entry.route) {
      found.push(...walkAction(`do.route for '${named}'`, action));
    }
  }
  return found;
}

/**
 * Every action this element can run, each with the path a reader can go and
 * edit: its own `do` first, then each matrix row's override of `do.route`. A new
 * place an action lives is added here. Mirrors `_element_actions` in
 * page_review.py.
 */
function elementActions(el: UIElement): ActionEntry[] {
  const do_ = ((el.bindings ?? {}) as Record<string, unknown>).do;
  const entries = isPlainObject(do_) ? actionEntries(do_) : [];
  return [...entries, ...destinationRouteEntries(el)];
}

/** The names a binding action can have, as a sentence lists them. */
const BINDING_ACTIONS_SAID = [...DISPATCHED_ACTIONS].sort().join(", ");

/**
 * What the action at `path` is, said after "has", when no branch of the
 * runtime takes it. The middle case is why the sentence is worth reading: a
 * macro step is the natural wrong guess, and how to reach one is the fix.
 * Mirrors `_why_nothing_runs` in page_review.py.
 */
function whyNothingRuns(path: string, action: string): string {
  if (!action) return `a ${path} entry with no action, so nothing runs when it fires.`;
  if (MACRO_ONLY_ACTIONS.includes(action)) {
    return (
      `${path} action '${action}', which is a macro step and not a binding action, ` +
      `so nothing runs when it fires. Put it in a macro and call that one with ` +
      `action 'macro'.`
    );
  }
  return `${path} action '${action}', which nothing dispatches, so nothing runs when it fires.`;
}

/**
 * Actions the runtime has no branch for, so the interaction does nothing.
 *
 * The chain in `ui_events.execute_action` ends with the names it knows
 * (`DISPATCHED_ACTIONS`); anything else reaches no branch. Nothing rejected it on the way in and --
 * until this shipped alongside the runtime's own warning -- nothing logged it
 * either, so a control authored with a retired spelling drew perfectly, took
 * the press, and did nothing at all.
 */
export function doActionFindings(el: UIElement): ReviewFinding[] {
  const findings: ReviewFinding[] = [];
  for (const [path, def] of elementActions(el)) {
    const action = typeof def.action === "string" ? def.action : "";
    if (DISPATCHED_ACTIONS.includes(action)) continue;
    findings.push({
      elementId: el.id,
      kind: "binding_not_dispatched",
      message:
        `${el.id} (${el.type}) has ${whyNothingRuns(path, action)} A binding action is ` +
        `one of: ${BINDING_ACTIONS_SAID}.`,
      key: `binding_not_dispatched|${el.id}|${path}|${action}`,
    });
  }
  return findings;
}

/**
 * Controls that are inert without one particular thing, and what to set.
 *
 * Not "required" -- almost nothing is, and a half-built page mid-drag should
 * not be scolded. These are the four where the element draws an empty box and
 * there is no reading under which that was the intent. The second entry is the
 * binding slot that can supply it instead, so a label bound to `show.value`
 * needs no `text`.
 *
 * Hand-mirrored from INERT_WITHOUT in page_review.py; the parity suite compares
 * the resulting sentences byte for byte, which is what keeps the two together.
 */
const INERT_WITHOUT: Record<string, [string, string | null, string]> = {
  image: ["src", null, "an asset ref ('assets://logo.png') or a URL"],
  page_nav: ["target_page", null, "the page id it should open, or '$back'"],
  label: ["text", "value", "the string to draw, or a show.value binding"],
  select: ["options", "items", "[{label, value}, ...] for the list of choices"],
  custom: ["custom_file", null, "a page in the project's ui/ folder, e.g. 'room_map/index.html'"],
};

/**
 * Whether a `show.look` puts TEXT on screen, rather than only colour.
 *
 * Two shapes do: a `states` map with a `label` on any entry, and the binary
 * form's `label_active` / `label_inactive`. A look carrying only colours
 * supplies none. Mirrors look_supplies_text in page_review.py.
 */
export function lookSuppliesText(look: unknown): boolean {
  if (!isPlainObject(look)) return false;
  return present(look.label_active) || present(look.label_inactive) || statesHaveWords(look.states);
}

/** Whether any state in a look's `states` carries words of its own. A list is
 *  read as states here, where the AI side reads none from it. */
function statesHaveWords(states: unknown): boolean {
  if (!states || typeof states !== "object") return false;
  return Object.values(states as Record<string, unknown>).some(
    (spec) => isPlainObject(spec) && present(spec.label),
  );
}

/** Neither missing nor null: Python's `is not None`, for a field read off JSON. */
function present(value: unknown): boolean {
  return value !== undefined && value !== null;
}

/**
 * A control with nothing to draw, which draws an empty box and says nothing.
 *
 * Named by a logo placeholder created as a master image and never given a src,
 * because no tool could set one. It stored, it took a placement, and it
 * rendered as nothing -- while the request that asked for it was reported done.
 */
export function contentFindings(el: UIElement): ReviewFinding[] {
  const rule = own(INERT_WITHOUT, el.type);
  if (!rule) return [];
  const [prop, slot, remedy] = rule;
  if (contentSupplied(el, prop, slot)) return [];
  const instead = slot ? `, and no show.${slot} to supply one` : "";
  return [{
    elementId: el.id,
    kind: "nothing_to_draw",
    message:
      `${el.id} (${el.type}) has no ${prop}${instead}, so it draws an empty box. ` +
      `Set ${prop} to ${remedy}.`,
    key: `nothing_to_draw|${el.id}`,
  }];
}

/**
 * Whether anything gives this element what it draws: the property itself, the
 * `show` slot that stands in for it (a label bound to `show.value` needs no
 * `text`), or, for a type that draws a look's words (STATE_LABEL_TYPES), a look
 * that has some. Warning about a state chip whose captions live in its look
 * taught authors to add a dead `text` to silence the check. Another source the
 * panel learns to draw from is one more line here, and in `_content_supplied`
 * in page_review.py.
 */
function contentSupplied(el: UIElement, prop: string, slot: string | null): boolean {
  if (isWritten((el as unknown as Record<string, unknown>)[prop])) return true;
  const show = showOf(el);
  if (!show) return false;
  if (slot && show[slot]) return true;
  return STATE_LABEL_TYPES.includes(el.type) && lookSuppliesText(show.look);
}

/** A property value an author wrote: not missing, not empty text, not an empty
 *  list. The AI side asks Python's truthiness instead, which also turns away 0,
 *  false and {}: values the project model refuses for every property in
 *  INERT_WITHOUT. */
function isWritten(value: unknown): boolean {
  if (!present(value) || value === "") return false;
  return !Array.isArray(value) || value.length > 0;
}

/** A value a full model dump carries for a field nobody set: not an authoring
 *  decision, so never worth a finding. `0` and `false` are decisions. */
function isUnset(value: unknown): boolean {
  return (
    value === null || value === undefined || value === "" ||
    (Array.isArray(value) && value.length === 0) ||
    (isPlainObject(value) && Object.keys(value).length === 0)
  );
}

/** Why a property this type does not read is there, said as the repair: the
 *  right idea one level too high on a matrix, the right idea on the wrong type,
 *  or an invented field. */
function unreadReason(type: string, name: string, honored: readonly string[]): string {
  if (type === "matrix" && MATRIX_CONFIG_KEYS.includes(name)) {
    return `'${name}' belongs inside matrix_config, not on the element.`;
  }
  const elsewhere = Object.entries(HONORED_PROPERTIES)
    .filter(([, props]) => props.includes(name))
    .map(([other]) => other)
    .sort();
  if (!elsewhere.length) return `No element type reads '${name}'.`;
  const why = `'${name}' is read by ${joined(elsewhere.map((t) => `\`${t}\``))}, not by a ${type}.`;
  return name === "label" && honored.includes("text") ? `${why} A label element draws \`text\`.` : why;
}

/**
 * Properties this element type's renderer never reads.
 *
 * The same silence as an unread binding: the field is declared on UIElement for
 * some other type (or none), the loader stores it, and no renderer has a line
 * that looks at it. `unreadReason` says which of those it is, because the repair
 * is different.
 */
export function propertyFindings(el: UIElement): ReviewFinding[] {
  const honored = own(HONORED_PROPERTIES, el.type);
  if (!honored) return []; // a type this module has never heard of; say nothing
  const reads = [...honored].sort().join(", ");
  return Object.entries(el as unknown as Record<string, unknown>)
    .filter(([name, value]) =>
      !honored.includes(name) && !STRUCTURAL_PROPERTIES.includes(name) && !isUnset(value))
    .map(([name]) => ({
      elementId: el.id,
      kind: "property_not_rendered",
      message:
        `${el.id} (${el.type}) sets '${name}', which a ${el.type} does not render. ` +
        `${unreadReason(el.type, name, honored)} A ${el.type} reads: ${reads}.`,
      key: `property_not_rendered|${el.id}|${name}`,
    }));
}

/**
 * The keypad's defaults, as panel.js renderKeypad reads them. The delay is also
 * the review's threshold: nobody has measured how long a person takes between
 * two keys on a panel, so the one number with a claim to it is the default.
 * Hand-mirrored from page_review.py; the keypad's own properties show the same
 * two numbers as its defaults.
 */
export const KEYPAD_DIGITS_DEFAULT = 4;
export const KEYPAD_SEND_DELAY_DEFAULT_MS = 1500;

/** How a keypad sends, with the panel's defaults filled in the way renderKeypad fills them. */
interface Keypad {
  id: string;
  /** The Phone style, which has no Enter key. */
  phone: boolean;
  /** Send without Enter. */
  autoSend: boolean;
  digits: unknown;
  /** Send Delay, in ms. */
  delay: unknown;
}

function keypadOf(el: UIElement): Keypad {
  return {
    id: el.id,
    phone: el.keypad_style === "phone",
    autoSend: Boolean(el.auto_send),
    digits: el.digits ?? KEYPAD_DIGITS_DEFAULT,
    delay: el.auto_send_delay_ms ?? KEYPAD_SEND_DELAY_DEFAULT_MS,
  };
}

/** Whether a pause after a key press sends what was typed: only with Send
 *  without Enter on, and only with more than one digit to wait for. */
function pauseSends(pad: Keypad): boolean {
  if (!pad.autoSend || !Number.isInteger(pad.digits)) return false;
  return (pad.digits as number) >= 2;
}

/** The Phone style has no Enter key, so with Send without Enter off nothing typed is sent. */
function neverSends(pad: Keypad): ReviewFinding | null {
  if (!pad.phone || pad.autoSend) return null;
  return {
    elementId: pad.id,
    kind: "keypad_never_sends",
    message:
      `${pad.id} (keypad) uses the Phone style, which has no Enter key, and ` +
      `Send without Enter is off, so nothing typed on it is ever sent. Turn on ` +
      `Send without Enter, or choose the Numeric style.`,
    key: `keypad_never_sends|${pad.id}`,
  };
}

/** A Send Delay under the default can send a code before the last digit is typed. */
function sendsEarly(pad: Keypad): ReviewFinding | null {
  if (!pauseSends(pad) || typeof pad.delay !== "number") return null;
  if (pad.delay >= KEYPAD_SEND_DELAY_DEFAULT_MS) return null;
  return {
    elementId: pad.id,
    kind: "keypad_sends_early",
    message:
      `${pad.id} (keypad) sends what has been typed ${pad.delay} ms after the last ` +
      `key press, shorter than the ${KEYPAD_SEND_DELAY_DEFAULT_MS} ms default, so a ` +
      `code can be sent before it is finished. Set Send Delay to ` +
      `${KEYPAD_SEND_DELAY_DEFAULT_MS} ms or more; an entry of ${pad.digits} digits is sent ` +
      `as soon as the last one is pressed.`,
    key: `keypad_sends_early|${pad.id}`,
  };
}

/** A keypad's warnings, in the order they are said. The two cannot both fire:
 *  one needs Send without Enter off and the other needs it on. */
const KEYPAD_CHECKS: ((pad: Keypad) => ReviewFinding | null)[] = [neverSends, sendsEarly];

/**
 * A keypad that never sends, or sends a code before it is finished.
 *
 * With `auto_send` the panel sends at `digits` keys AND after
 * `auto_send_delay_ms` with no key press. The phone layout has no Enter key, so
 * with `auto_send` off it cannot send at all; a delay under the default can
 * send after a PIN's first digit. With one digit the delay never runs. Mirrors
 * keypad_findings in page_review.py, pinned sentence for sentence.
 */
export function keypadFindings(el: UIElement): ReviewFinding[] {
  if (el.type !== "keypad") return [];
  const pad = keypadOf(el);
  return KEYPAD_CHECKS.map((check) => check(pad)).filter((f): f is ReviewFinding => f !== null);
}

/** Which direction each axis routes, for a sentence that has to say which. */
const MATRIX_AXIS_DIRECTION: Record<MatrixAxisName, string> = {
  sources: "route from",
  destinations: "route to",
};

/** How many entries a matrix finding names before it counts the rest. */
const MATRIX_NAMED = 4;

function matrixNamed(labels: string[]): string {
  const shown = labels.slice(0, MATRIX_NAMED).join(", ");
  const rest = labels.length - MATRIX_NAMED;
  return rest > 0 ? `${shown} and ${rest} more` : shown;
}

/**
 * What a review sentence calls one matrix row: the name it was given, else its
 * value. Not the panel's caption (`_entryLabel`), which can read a live name off
 * the device: a review has no state to read. The AI side's twin is
 * `_entry_name` in page_review.py.
 */
function matrixEntryName(entry: MatrixEntry): string {
  return String(entry.label ?? entry.value);
}

/** `1 destination` or `3 destinations`. */
function destinationsCounted(count: number): string {
  return `${count} destination${count === 1 ? "" : "s"}`;
}

type MatrixCheck = (elId: string, config: Record<string, unknown>) => ReviewFinding[];

/** Keys in `matrix_config` the matrix renderer never reads. */
function unreadConfigFindings(elId: string, config: Record<string, unknown>): ReviewFinding[] {
  const unread = Object.keys(config).filter((k) => !MATRIX_CONFIG_KEYS.includes(k)).sort();
  if (!unread.length) return [];
  return [{
    elementId: elId,
    kind: "matrix_config_unread",
    message:
      `${elId} (matrix) sets matrix_config ${joined(unread.map((k) => `'${k}'`))}, ` +
      `which the matrix renderer does not read. The keys it reads are: ` +
      `${[...MATRIX_CONFIG_KEYS].sort().join(", ")}.`,
    key: `matrix_config_unread|${elId}|${unread.join("|")}`,
  }];
}

/** Each axis that resolves to nothing, or has two rows claiming one value. */
function axisFindings(elId: string, config: Record<string, unknown>): ReviewFinding[] {
  const findings: ReviewFinding[] = [];
  for (const axis of ["sources", "destinations"] as const) {
    const entries = resolveMatrixAxis(config, axis);
    if (!entries.length) {
      findings.push({
        elementId: elId,
        kind: "matrix_default_size",
        message:
          `${elId} (matrix) resolves to no ${axis}, so it draws nothing to ` +
          `${MATRIX_AXIS_DIRECTION[axis]}. List them under ` +
          `matrix_config.${axis}, or generate them with ${axis}.from.count.`,
        key: `matrix_default_size|${elId}|${axis}`,
      });
      continue;
    }
    const collided = collidingEntries(entries, axis);
    if (collided.length) {
      findings.push({
        elementId: elId,
        kind: "matrix_duplicate_values",
        message:
          `${elId} (matrix) has ${collided.length} of its ${axis} claiming a value ` +
          `another one already has (${matrixNamed(collided)}), and a routed ` +
          `source is matched by value -- so they cannot be told apart. Give ` +
          `each one the value its device actually reports.`,
        key: `matrix_duplicate_values|${elId}|${axis}|${collided.join("|")}`,
      });
    }
  }
  return findings;
}

/**
 * The rows claiming a value an earlier row already has, by name.
 *
 * Two entries claiming one value is new in 0.10.0 and is only possible because a
 * value is now opaque and authored. The crosspoint match lights every entry the
 * device's report names, and a list dropdown can only hold one option per value,
 * so the second is either a duplicate light or a row that cannot be selected.
 */
function collidingEntries(entries: MatrixEntry[], axis: MatrixAxisName): string[] {
  const seen: unknown[] = [];
  const collided: string[] = [];
  for (const entry of entries) {
    // By what the DEVICE calls it, which is what a report is matched against --
    // the same thing as the value on all but a couple of drivers, and the only
    // thing that can collide on those.
    const value = axis === "sources" ? matrixSourceReports(entry) : entry.value;
    if (seen.some((other) => matrixRouteMatches(value, other))) {
      collided.push(matrixEntryName(entry));
    } else {
      seen.push(value);
    }
  }
  return collided;
}

/**
 * Lock buttons drawn for destinations no variable backs.
 *
 * A lock only the panel that pressed it remembers is not a lock: it is gone the
 * next time the page draws, the panel by the other door never hears about it,
 * and the rack does not know either. Backing it with a variable is what makes it
 * one, and `var.`/`plugin.` are the only prefixes a panel is allowed to write, so
 * the check is the prefix rather than merely presence.
 */
function unbackedLockFindings(elId: string, config: Record<string, unknown>): ReviewFinding[] {
  if (config.show_lock !== true) return [];
  const unbacked = resolveMatrixAxis(config, "destinations")
    .filter((entry) => !MATRIX_PANEL_WRITABLE_PREFIXES.some(
      (prefix) => String(entry.lock_key ?? "").startsWith(prefix)))
    .map(matrixEntryName);
  if (!unbacked.length) return [];
  const one = unbacked.length === 1;
  return [{
    elementId: elId,
    kind: "matrix_lock_unbacked",
    message:
      `${elId} (matrix) draws lock buttons, but ${destinationsCounted(unbacked.length)} ` +
      `(${matrixNamed(unbacked)}) ` +
      `${one ? "has" : "have"} no lock_key under ` +
      `${MATRIX_PANEL_WRITABLE_PREFIXES.map((p) => `'${p}'`).join(" or ")}, so locking ` +
      `${one ? "it" : "them"} is this panel's own memory ` +
      `-- forgotten when the page redraws, and invisible to every other ` +
      `panel in the space. Give each one a variable, e.g. ` +
      `'var.${elId}_lock_1'.`,
    key: `matrix_lock_unbacked|${elId}`,
  }];
}

/** Destinations with no `route_key`, which never show what is routed to them. */
function blindDestinationFindings(elId: string, config: Record<string, unknown>): ReviewFinding[] {
  const blind = resolveMatrixAxis(config, "destinations")
    .filter((entry) => !entry.route_key)
    .map(matrixEntryName);
  if (!blind.length) return [];
  return [{
    elementId: elId,
    kind: "matrix_no_route_feedback",
    message:
      `${elId} (matrix) has ${destinationsCounted(blind.length)} with no route_key ` +
      `(${matrixNamed(blind)}), so those never show what is routed -- the ` +
      `matrix routes, and the panel cannot say which source is selected. Give ` +
      `each one the state key holding its routed source, e.g. ` +
      `'device.<id>.output.1.input'.`,
    key: `matrix_no_route_feedback|${elId}`,
  }];
}

/** The checks a configured matrix gets, in the order they report. A new one is
 *  an entry here and one in `_MATRIX_CHECKS`, its twin in page_review.py. */
const MATRIX_CHECKS: readonly MatrixCheck[] = [
  unreadConfigFindings,
  axisFindings,
  unbackedLockFindings,
  blindDestinationFindings,
];

/**
 * A matrix that will draw, and route, and never show what is routed.
 *
 * `route_key` is what makes a destination visible rather than merely operable:
 * without one the panel registers no state binding for that row, so it keeps its
 * inactive colour for the life of the panel. Clicking still routes, so the
 * control is half-alive and a bench test that only asks "does it switch" passes.
 *
 * It is per destination now rather than per element, which is the whole shape of
 * project format 0.10.0: each destination owns its key, so half a matrix can be
 * blind while the other half reports. The rest of MATRIX_CHECKS are what make
 * that hard to notice.
 */
export function matrixFindings(el: UIElement): ReviewFinding[] {
  if (el.type !== "matrix") return [];
  const config = el.matrix_config as Record<string, unknown> | undefined;
  if (!hasMatrixConfig(config)) {
    return [{
      elementId: el.id,
      kind: "matrix_not_configured",
      message:
        `${el.id} (matrix) has no matrix_config, so it draws an empty box. Set ` +
        `sources and destinations -- each is a list of entries, or a generator ` +
        `standing for one, e.g. destinations: ` +
        `{'from': {'count': 8, 'route_key': 'device.<id>.output.*.input'}}.`,
      key: `matrix_not_configured|${el.id}`,
    }];
  }
  return MATRIX_CHECKS.flatMap((check) => check(el.id, config));
}

/**
 * Whether a matrix carries a config to check: any object with a key of its own.
 *
 * A list with entries counts, its positions read as keys, where `matrix_findings`
 * in page_review.py takes only a mapping; that is left as it is, since narrowing it
 * here would change what such a matrix is told.
 */
function hasMatrixConfig(config: unknown): config is Record<string, unknown> {
  return !!config && typeof config === "object" && Object.keys(config).length > 0;
}

/** The page-space box an element's percentages are measured against, in px. */
function parentBoxPx(
  parentId: string | null,
  absolute: Record<string, Placement>,
  reference: { width: number; height: number },
): { width: number; height: number } {
  const box = (parentId ? own(absolute, parentId) : undefined) ?? PAGE_BOX;
  return {
    width: (box.w / 100) * reference.width,
    height: (box.h / 100) * reference.height,
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
  /** The project's master elements, which the page does not carry and cannot
   *  reach: they live on `ui.master_elements` and draw underneath every page
   *  they are listed on. Passing them is what lets a control be told it is
   *  sitting on the nav bar. Omitted, that one check simply does not run. */
  masters?: MasterElement[];
}

/**
 * The checks an element gets on its own, before any box is measured, in the
 * order they report. A page and a master element both run this list, so a new
 * check is one entry here.
 */
const ELEMENT_CHECKS: readonly ((el: UIElement) => ReviewFinding[])[] = [
  bindingFindings,
  doActionFindings,
  propertyFindings,
  contentFindings,
  keypadFindings,
  matrixFindings,
];

/** Its type first (an unknown one draws nothing), then each of ELEMENT_CHECKS. */
function elementFindings(el: UIElement): ReviewFinding[] {
  const typeFinding = elementTypeFinding(el);
  return [...(typeFinding ? [typeFinding] : []), ...ELEMENT_CHECKS.flatMap((check) => check(el))];
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
  const { theme, touched, layoutId, masters } = options;
  const inScope = (id: string) => (touched ? touched.has(id) : true);
  const findings: ReviewFinding[] = [];

  // A page drawing its own markup draws none of its controls, so everything
  // below is about pixels nobody will see. Said once, and nothing else. Not
  // scoped by `touched`: dropping a control onto such a page is exactly when
  // somebody needs to hear it.
  const custom = customPageFindings(page);
  if (custom.length && custom[0].kind === "custom_page_elements_not_drawn") return custom;
  findings.push(...custom);

  for (const el of page.elements) {
    if (inScope(el.id)) findings.push(...elementFindings(el));
  }

  const layouts = page.layouts ?? [];
  if (!layouts.length) return findings;
  // One arrangement when the caller is looking at one (an unknown id resolves
  // to the primary, the way every other read of a layout id does).
  const chosen = layoutId === undefined ? layouts : [layoutById(page, layoutId) ?? layouts[0]];
  findings.push(...arrangementFindings(page, chosen, { theme, inScope, masters: masters ?? [] }));
  return findings;
}

/** What one review was asked about: which elements, under which theme, over which masters. */
interface ReviewScope {
  theme?: ElementDefaults;
  inScope: (id: string) => boolean;
  masters: MasterElement[];
}

/**
 * One arrangement of a page as the panel resolves it.
 *
 * `absolute` is where each element is drawn, in page percentages with container
 * nesting flattened; `ownBoxes` is the box each one stores, a percentage of its
 * container. Every percentage resolves against `reference`, which is the whole
 * of what portrait changes: the same screen, turned.
 *
 * The twin of `_Arrangement` in page_review.py, whose methods are the free
 * functions here: `resolveArrangement` is `_Arrangement.of`, and `parentIn`,
 * `parentNameIn`, `ancestorsIn`, `seatIn` and `isDrawn` are `parent_of`,
 * `parent_name`, `ancestors_of`, `seat` and `is_drawn`.
 */
interface Arrangement {
  page: UIPage;
  byId: Map<string, UIElement>;
  absolute: Record<string, Placement>;
  ownBoxes: Record<string, Placement>;
  hidden: Set<string>;
  reference: { width: number; height: number };
  orientation: string;
  isPrimary: boolean;
}

function resolveArrangement(page: UIPage, layout: Layout, primaryId: string | undefined): Arrangement {
  const orientation = layout.orientation ?? "landscape";
  return {
    page,
    byId: new Map(page.elements.map((e) => [e.id, e])),
    absolute: absolutePlacements(page, layout.id),
    ownBoxes: resolvePlacements(page, layout.id),
    hidden: resolveHidden(page, layout.id),
    reference: referenceBox(orientation),
    orientation,
    isPrimary: layout.id === primaryId,
  };
}

/** Shown in this arrangement and given a box in it. */
function isDrawn(arr: Arrangement, id: string): boolean {
  return !arr.hidden.has(id) && !!own(arr.absolute, id);
}

function parentIn(arr: Arrangement, id: string): string | null {
  return containerOf(arr.byId.get(id), arr.byId);
}

function parentNameIn(arr: Arrangement, id: string): string {
  const parent = parentIn(arr, id);
  return parent ? `its container '${parent}'` : "the page";
}

/** Every container above this element, innermost first.
 *
 *  Only as far as the boxes actually resolve -- a container the arrangement
 *  never positions ends the chain rather than guessing a size for it. */
function ancestorsIn(arr: Arrangement, id: string): Container[] {
  const chain: Container[] = [];
  for (const container of containersAbove(id, arr.byId)) {
    const box = own(arr.absolute, container);
    if (!box) break;
    chain.push({
      label: `'${container}'`,
      widthPx: (box.w / 100) * arr.reference.width,
      heightPx: (box.h / 100) * arr.reference.height,
    });
  }
  return chain;
}

/**
 * Every geometry finding across these arrangements, each said once.
 *
 * Primary first: it is the arrangement every variant inherits from, so its
 * phrasing ("...") beats the variant's ("... in the portrait arrangement").
 */
function arrangementFindings(page: UIPage, layouts: Layout[], scope: ReviewScope): ReviewFinding[] {
  const primaryId = primaryLayout(page)?.id;
  const ordered = [...layouts].sort(
    (a, b) => Number(a.id !== primaryId) - Number(b.id !== primaryId),
  );
  const findings: ReviewFinding[] = [];
  const said = new Set<string>();
  for (const layout of ordered) {
    const arr = resolveArrangement(page, layout, primaryId);
    const where = arr.isPrimary ? "" : ` in the '${layout.id}' arrangement`;
    findings.push(...unsaid(layoutFindings(arr, scope), said).map((f) => withWhere(f, where)));
  }
  return findings;
}

/** The findings whose `key` has not been said yet, each marked as said. */
function unsaid(findings: ReviewFinding[], said: Set<string>): ReviewFinding[] {
  const fresh: ReviewFinding[] = [];
  for (const finding of findings) {
    if (said.has(finding.key)) continue;
    said.add(finding.key);
    fresh.push(finding);
  }
  return fresh;
}

/** Every geometry finding for one arrangement: each box, then siblings, then masters. */
function layoutFindings(arr: Arrangement, scope: ReviewScope): ReviewFinding[] {
  return [
    ...boxFindingsIn(arr, scope),
    ...siblingOverlaps(arr, scope),
    ...buriedMastersIn(arr, scope),
  ];
}

function boxFindingsIn(arr: Arrangement, scope: ReviewScope): ReviewFinding[] {
  const findings: ReviewFinding[] = [];
  for (const el of arr.page.elements) {
    if (!scope.inScope(el.id) || arr.hidden.has(el.id)) continue;
    if (own(arr.absolute, el.id)) {
      findings.push(...placedBoxFindings(arr, el, scope.theme));
    } else if (arr.isPrimary) {
      // A variant legitimately carries no delta for an element -- it inherits
      // one. Only the primary having no box means it has none at all.
      findings.push(noBoxFinding(el, parentNameIn(arr, el.id)));
    }
  }
  return findings;
}

/** Where this element is drawn in this arrangement. It must have a box here. */
function seatIn(arr: Arrangement, el: UIElement): Seat {
  const box = own(arr.absolute, el.id) as Placement;
  return {
    boxPx: {
      width: (box.w / 100) * arr.reference.width,
      height: (box.h / 100) * arr.reference.height,
    },
    parentPx: parentBoxPx(parentIn(arr, el.id), arr.absolute, arr.reference),
    parentName: parentNameIn(arr, el.id),
    ancestors: ancestorsIn(arr, el.id),
    orientation: arr.orientation,
  };
}

function placedBoxFindings(
  arr: Arrangement,
  el: UIElement,
  theme: ElementDefaults | undefined,
): ReviewFinding[] {
  const seat = seatIn(arr, el);
  const findings = boxFindings(el, seat, theme);
  const stored = own(arr.ownBoxes, el.id);
  const overhang = stored ? overhangFinding(el, stored, seat.parentName, seat.parentPx) : null;
  return overhang ? [...findings, overhang] : findings;
}

/**
 * Siblings, in the space they share.
 *
 * An element that also hangs out of its container is NOT excused here, which
 * was tried and was wrong: two boxes can overlap in the middle of a container
 * while one of them separately runs off the edge, and fixing the overflow does
 * not touch the collision. Narrow a box from 80% to 70% and it sits inside its
 * parent while still lying on the neighbour it started at. Reporting both is
 * right; the volume that suppression was aimed at is what overlapFindings
 * collapses.
 */
function siblingOverlaps(arr: Arrangement, scope: ReviewScope): ReviewFinding[] {
  const types = new Map(arr.page.elements.map((e) => [e.id, e.type]));
  const byParent = new Map<string | null, string[]>();
  for (const el of arr.page.elements) {
    if (!isDrawn(arr, el.id)) continue;
    const parent = parentIn(arr, el.id);
    const kids = byParent.get(parent);
    if (kids) kids.push(el.id);
    else byParent.set(parent, [el.id]);
  }
  const findings: ReviewFinding[] = [];
  for (const [parent, kids] of byParent) {
    kids.sort();
    findings.push(
      ...overlapFindings(collidingPairs(arr, kids, scope), types, parent ? `'${parent}'` : "the page"),
    );
  }
  return findings;
}

/** Each pair of siblings whose boxes overlap, at least one of them in scope. */
function collidingPairs(arr: Arrangement, kids: string[], scope: ReviewScope): OverlapPair[] {
  const pairs: OverlapPair[] = [];
  kids.forEach((aId, i) => {
    for (const bId of kids.slice(i + 1)) {
      if (!scope.inScope(aId) && !scope.inScope(bId)) continue;
      if (mutuallyExclusive(arr.byId.get(aId) as UIElement, arr.byId.get(bId) as UIElement)) continue;
      const extent = overlapExtent(
        own(arr.absolute, aId) as Placement,
        own(arr.absolute, bId) as Placement,
        arr.reference,
      );
      if (extent) pairs.push({ aId, bId, ...extent });
    }
  });
  return pairs;
}

/** ...and the masters under all of them, which are nobody's sibling. */
function buriedMastersIn(arr: Arrangement, scope: ReviewScope): ReviewFinding[] {
  const boxes = new Map<string, Placement>();
  for (const el of arr.page.elements) {
    if (isDrawn(arr, el.id)) boxes.set(el.id, own(arr.absolute, el.id) as Placement);
  }
  const drawn: DrawnPage = {
    pageId: arr.page.id,
    orientation: arr.orientation,
    boxes,
    elements: arr.byId,
    parents: new Map(arr.page.elements.map((el) => [el.id, parentIn(arr, el.id)])),
  };
  return buriedMasterFindings(drawn, scope.masters, scope.inScope);
}

/**
 * The same checks a master element can be given.
 *
 * A master borrows no page's layout -- its box is a percentage of the viewport,
 * keyed by orientation -- so it has no container to hang out of and no sibling in
 * any layout to collide with. What is left is whether it holds its own contents,
 * whether a finger can hit it, and whether its bindings are read.
 *
 * It CAN be collided with, by the controls on the pages it draws under, and that
 * is answered on the page rather than here: the finding belongs on the control
 * that moved (`buriedMasterFindings`), and a master reviewed on its own has no
 * page in hand to look at.
 */
export function reviewMasterElement(
  master: MasterElement,
  theme?: ElementDefaults,
): ReviewFinding[] {
  const findings = elementFindings(master);
  const placements = master.placements;
  if (!placements || typeof placements !== "object") return findings;
  const said = new Set<string>();
  for (const [orientation, box] of Object.entries(placements)) {
    const seat = masterSeat(orientation, box);
    if (!seat) continue;
    findings.push(
      ...unsaid(boxFindings(master, seat, theme), said).map((f) =>
        withWhere(f, ` in the ${orientation} arrangement`),
      ),
    );
  }
  return findings;
}

/**
 * A master's box for one orientation, or null when it is not a finite size.
 *
 * A master carries its own orientation-keyed boxes, so the key IS the way round
 * this one is drawn -- the portrait box is measured against a portrait screen
 * even on a project whose pages are all landscape. Its only container is the
 * screen; a missing `w` or `h` is the whole of it.
 */
function masterSeat(orientation: string, box: { w?: number; h?: number } | undefined): Seat | null {
  const reference = referenceBox(orientation);
  const boxPx = {
    width: ((box?.w ?? 100) / 100) * reference.width,
    height: ((box?.h ?? 100) / 100) * reference.height,
  };
  if (!Number.isFinite(boxPx.width) || !Number.isFinite(boxPx.height)) return null;
  return { boxPx, parentPx: reference, parentName: "the page", ancestors: [], orientation };
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
  masters: MasterElement[] = [],
): Map<string, string> {
  const byElement = new Map<string, string[]>();
  for (const finding of reviewPage(page, { theme, layoutId, masters })) {
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
  return isPlainObject(slider) ? slider : null;
}

export interface ValidationIssue {
  severity: "error" | "warning";
  message: string;
  location: string;
  pageId?: string;
  elementId?: string;
}

/** Validate's words for a reference that is not there. Where the badge says
 *  `Page "x" not found`, this says the navigate goes to a deleted page. */
function validateSays({ kind, id }: BrokenReference): string {
  return kind === "page" ? `Navigate to deleted page "${id}"` : `${REFERENCE_NOUNS[kind]} "${id}" not found`;
}

/** The device a `device.<id>.<property>` state key reads, or null for a key
 *  that is not a device's. Validate and the binding card's badge both ask it
 *  before saying a key reads a device that is not in the project. */
export function stateKeyDevice(key: string | undefined): string | null {
  return key?.startsWith("device.") ? key.split(".")[1] : null;
}

/** What a project's references resolve against, and what the platform said
 *  about the commands it was asked about (`validateProject`'s second argument). */
interface ProjectRefs extends BindingRefIds {
  commandIssues?: Map<Record<string, unknown>, string[]>;
}

/** One thing Validate says about a control. `where` follows the control in the
 *  location it is reported at (` > press`, ` > change > "hdmi"`). */
interface ControlIssue {
  severity: ValidationIssue["severity"];
  message: string;
  where?: string;
}

type ControlCheck = (el: UIElement, refs: ProjectRefs) => ControlIssue[];

function doMapOf(el: UIElement): Record<string, unknown> {
  return (((el.bindings || {}) as Record<string, unknown>).do || {}) as Record<string, unknown>;
}

/** The binding a show slot holds, when it holds one. */
function shownBinding(el: UIElement, slot: string): Record<string, unknown> | undefined {
  return showOf(el)?.[slot] as Record<string, unknown> | undefined;
}

/** A page nav aimed at a page that is not there. */
function targetPageIssues(el: UIElement, refs: ProjectRefs): ControlIssue[] {
  const target = el.type === "page_nav" ? el.target_page : undefined;
  if (!target || pageExists(target, refs.pageIds)) return [];
  return [{ severity: "error", message: `Target page "${target}" does not exist` }];
}

/** A grant naming a device that is gone matches nothing at runtime, and a
 *  grant that matches nothing looks exactly like a grant nobody set: the
 *  control just sits there seeing no state. */
function grantIssues(el: UIElement, refs: ProjectRefs): ControlIssue[] {
  const issues: ControlIssue[] = [];
  for (const granted of el.grant?.devices ?? []) {
    if (!refs.deviceIds.has(granted)) {
      issues.push({ severity: "error", message: `Granted device "${granted}" not found, so this control cannot read or control it` });
    }
  }
  return issues;
}

/**
 * One action and the value_map options under it: what it names that is not
 * there, then what the platform said about it. Reads one action per option, as
 * the runtime runs it; the binding card's checks read a list option as well
 * (`valueMapActions` in bindingActions.ts).
 */
function actionIssuesAt(a: Record<string, unknown>, where: string, refs: ProjectRefs): ControlIssue[] {
  const broken = brokenReference(a, refs);
  const issues: ControlIssue[] = broken ? [{ severity: "error", message: validateSays(broken), where }] : [];
  // The device is here and the command is one it has, and the driver will
  // still refuse it: a parameter it declares required was never filled in.
  // The sentence is the platform's, so this warning and the refusal on the
  // wall are the same words.
  for (const message of refs.commandIssues?.get(a) ?? []) issues.push({ severity: "error", message, where });
  if (a.action === "value_map" && isPlainObject(a.map)) {
    for (const [option, mapped] of Object.entries(a.map)) {
      if (isPlainObject(mapped)) issues.push(...actionIssuesAt(mapped, `${where} > "${option}"`, refs));
    }
  }
  return issues;
}

/** Every action in every interaction, the slots in their own order (an
 *  interaction written as one object rather than a list is read the same). */
function actionIssues(el: UIElement, refs: ProjectRefs): ControlIssue[] {
  const doMap = doMapOf(el);
  return ACTION_SLOTS.flatMap((slot) =>
    slotActions(doMap, slot).flatMap((a) => actionIssuesAt(a, ` > ${slot}`, refs)),
  );
}

/** The value and the appearance read a device that is not in the project. */
function shownDeviceIssues(el: UIElement, refs: ProjectRefs): ControlIssue[] {
  const issues: ControlIssue[] = [];
  for (const [slot, label] of [["value", "value"], ["look", "appearance"]] as const) {
    const deviceId = stateKeyDevice(shownBinding(el, slot)?.key as string | undefined);
    if (deviceId !== null && !refs.deviceIds.has(deviceId)) {
      issues.push({ severity: "warning", message: `State key references unknown device "${deviceId}"`, where: ` > ${label}` });
    }
  }
  return issues;
}

/**
 * A control whose Value reads a device key but never sends a command can't
 * actually drive that device — the drag/selection updates only the local
 * mirror, overwritten on the next poll. Two-way to a device must go through
 * a command (do.<interaction> device.command with $value), never a state
 * write. Display-only controls reading device state are fine.
 */
function deviceValueIssues(el: UIElement): ControlIssue[] {
  const cap = BINDING_CAPABILITIES[el.type];
  const valueKey = shownBinding(el, "value")?.key as string | undefined;
  if (!cap?.value?.link || !valueKey?.startsWith("device.")) return [];
  const doMap = doMapOf(el);
  if ((cap.does ?? []).some((d) => actionsCommandDevice(slotActions(doMap, d.interaction)))) return [];
  return [{
    severity: "warning",
    message: "This control shows a device value but has no command to change it. Add a command so touching it reaches the device",
    where: " > value",
  }];
}

/** A visibility condition reads a device that is not in the project. `all`
 *  is read before `any`, and a single condition stands on its own. */
function visibilityIssues(el: UIElement, refs: ProjectRefs): ControlIssue[] {
  const vw = shownBinding(el, "visible_when");
  if (!vw) return [];
  const issues: ControlIssue[] = [];
  const conditions = (vw.all || vw.any || [vw]) as Array<{ key?: string }>;
  for (const c of conditions) {
    const deviceId = stateKeyDevice(c.key);
    if (deviceId !== null && !refs.deviceIds.has(deviceId)) {
      issues.push({ severity: "warning", message: `Visibility condition references unknown device "${deviceId}"`, where: " > visible_when" });
    }
  }
  return issues;
}

/** The types a touch is meant to do something on. */
const INTERACTIVE_TYPES = ["button", "slider", "fader", "select", "text_input", "keypad", "matrix", "list"];

/** An interactive control with no action and no two-way value does nothing
 *  when touched. */
function noActionIssues(el: UIElement): ControlIssue[] {
  if (!INTERACTIVE_TYPES.includes(el.type)) return [];
  const doMap = doMapOf(el);
  const acts = ACTION_SLOTS.some((slot) => slotActions(doMap, slot).length > 0);
  const writesBack = !!shownBinding(el, "value")?.write_back;
  return acts || writesBack ? [] : [{ severity: "warning", message: "Interactive element has no action" }];
}

/** Validate's checks of one control, reported in this order. A new check is one
 *  function and one entry here; pages and master elements both run the list. */
const CONTROL_CHECKS: readonly ControlCheck[] = [
  targetPageIssues,
  grantIssues,
  actionIssues,
  shownDeviceIssues,
  deviceValueIssues,
  visibilityIssues,
  noActionIssues,
];

/** What Validate says about one control, placed under its page (a master's
 *  page is the empty string). */
function controlIssues(el: UIElement, pageId: string, pageName: string, refs: ProjectRefs): ValidationIssue[] {
  const location = `${pageName} > ${el.id}`;
  return CONTROL_CHECKS.flatMap((check) => check(el, refs)).map((issue) => ({
    severity: issue.severity,
    message: issue.message,
    location: location + (issue.where ?? ""),
    pageId,
    elementId: el.id,
  }));
}

/** A custom page carries the same grant an iframe element does, and can name a
 *  device that has since left the project in exactly the same invisible way: it
 *  matches nothing, which looks like a grant nobody set. */
function pageGrantIssues(page: UIPage, refs: ProjectRefs): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  for (const granted of page.grant?.devices ?? []) {
    if (!refs.deviceIds.has(granted)) {
      issues.push({ severity: "error", message: `Granted device "${granted}" not found, so the page shown here cannot read or control it`, location: `${page.name} > page`, pageId: page.id });
    }
  }
  return issues;
}

/** The arrangements themselves: a dangling inherits silently collapses the
 *  variant (everything without its own box falls to default placements), and
 *  orphaned ids are the residue a bad rename or hand edit leaves. */
function arrangementIssues(page: UIPage, onPage: ReadonlySet<string>): ValidationIssue[] {
  const layoutIds = new Set((page.layouts ?? []).map((l) => l.id));
  const issues: ValidationIssue[] = [];
  for (const layout of page.layouts ?? []) issues.push(...layoutIssues(layout, page, layoutIds, onPage));
  return issues;
}

function layoutIssues(
  layout: Layout,
  page: UIPage,
  layoutIds: ReadonlySet<string>,
  onPage: ReadonlySet<string>,
): ValidationIssue[] {
  const at = { location: `${page.name} > ${layout.id}`, pageId: page.id };
  const strays = (ids: Iterable<string>, says: (elId: string) => string): ValidationIssue[] =>
    [...ids].filter((elId) => !onPage.has(elId)).map((elId) => ({ severity: "warning", message: says(elId), ...at }));
  const dangling: ValidationIssue[] = layout.inherits && !layoutIds.has(layout.inherits)
    ? [{ severity: "error", message: `Arrangement inherits from "${layout.inherits}", which does not exist \u2014 controls without their own position here fall back to default boxes`, ...at }]
    : [];
  return [
    ...dangling,
    ...strays(Object.keys(layout.placements ?? {}), (elId) => `Arrangement "${layout.id}" positions "${elId}", which is not an element on this page`),
    ...strays(layout.hidden ?? [], (elId) => `Arrangement "${layout.id}" hides "${elId}", which is not an element on this page`),
  ];
}

/** A parent that doesn't exist leaves the child unrendered \u2014 the runtime
 *  draws children into their container, and there is no container. */
function containerIssues(el: UIElement, page: UIPage, onPage: ReadonlySet<string>): ValidationIssue[] {
  if (!el.parent || onPage.has(el.parent)) return [];
  return [{ severity: "error", message: `Container "${el.parent}" does not exist`, location: `${page.name} > ${el.id}`, pageId: page.id, elementId: el.id }];
}

/**
 * Everything Validate says about one page: its grant, its arrangements, each
 * control in turn, then what the page review says.
 *
 * Geometry and binding reach come back from the shared review -- the same code,
 * the same numbers and the same sentences the AI write path answers with, so
 * the two surfaces cannot reach different verdicts about the same file.
 * Geometry is per-arrangement: a portrait variant can push a control off-screen
 * or starve it while the primary is fine, so every layout is checked, and an
 * issue is reported once per element naming the arrangement only when it isn't
 * the primary's fault.
 */
function pageIssues(
  page: UIPage,
  refs: ProjectRefs,
  review: Pick<ReviewOptions, "theme" | "masters">,
): ValidationIssue[] {
  const onPage = new Set(page.elements.map((e) => e.id));
  return [
    ...pageGrantIssues(page, refs),
    ...arrangementIssues(page, onPage),
    ...page.elements.flatMap((el) => [
      ...controlIssues(el, page.id, page.name, refs),
      ...containerIssues(el, page, onPage),
    ]),
    ...reviewPage(page, review).map((finding): ValidationIssue => ({
      severity: "warning",
      message: finding.message,
      location: `${page.name} > ${finding.elementId}`,
      pageId: page.id,
      elementId: finding.elementId,
    })),
  ];
}

/** A master element: the control checks with no page, its own review, then
 *  each page it names that is not in the project. */
function masterIssues(mel: MasterElement, refs: ProjectRefs, theme: ElementDefaults): ValidationIssue[] {
  const issues: ValidationIssue[] = [
    ...controlIssues(mel, "", "Master Elements", refs),
    ...reviewMasterElement(mel, theme).map((finding): ValidationIssue => ({
      severity: "warning",
      message: finding.message,
      location: `Master Elements > ${finding.elementId}`,
      elementId: finding.elementId,
    })),
  ];
  if (Array.isArray(mel.pages)) {
    for (const pid of mel.pages) {
      if (!refs.pageIds.has(pid)) {
        issues.push({ severity: "error", message: `References deleted page "${pid}"`, location: `Master Elements > ${mel.id}` });
      }
    }
  }
  return issues;
}

/** The device or macro a step names that is not in the project. A step's page
 *  is not checked. `at` places a finding, given what to call a step with no
 *  description. */
function stepReferenceIssues(step: MacroStep, at: (unnamed: string) => string, refs: ProjectRefs): ValidationIssue[] {
  if (step.action === "device.command" && step.device && !refs.deviceIds.has(step.device)) {
    return [{ severity: "error", message: `Device "${step.device}" not found`, location: at(step.action) }];
  }
  if (step.action === "macro" && step.macro && !refs.macroIds.has(step.macro)) {
    return [{ severity: "error", message: `Macro "${step.macro}" not found`, location: at("call macro") }];
  }
  return [];
}

/** One macro step: what it names that is not there, then what the platform
 *  said about it. */
function stepIssues(step: MacroStep, prefix: string, refs: ProjectRefs): ValidationIssue[] {
  const at = (unnamed: string) => `${prefix} > ${step.description || unnamed}`;
  const said = refs.commandIssues?.get(step as unknown as Record<string, unknown>) ?? [];
  return [
    ...stepReferenceIssues(step, at, refs),
    ...said.map((message): ValidationIssue => ({ severity: "error", message, location: at(step.action) })),
  ];
}

/** Every step of every macro, nested branches included, in the order the
 *  macro holds them. */
function macroIssues(macros: MacroConfig[], refs: ProjectRefs): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  for (const macro of macros) {
    for (const step of macro.steps) {
      for (const each of stepAndBranches(step)) issues.push(...stepIssues(each, `Macro "${macro.name}"`, refs));
    }
  }
  return issues;
}

function idlePageIssues(project: ProjectConfig, pageIds: ReadonlySet<string>): ValidationIssue[] {
  const idlePage = project.ui.settings.idle_page;
  if (!idlePage || pageIds.has(idlePage)) return [];
  return [{ severity: "error", message: `Idle page "${idlePage}" does not exist`, location: "Settings > Idle Page" }];
}

/**
 * @param commandIssues What the platform says about each `device.command` in
 *   the project, keyed by the action object (see `api/commandIssues.ts`).
 *   Omitted means it was not asked or could not be reached, and nothing is
 *   claimed either way -- this file cannot see the drivers, and guessing is
 *   how Validate came to answer "No Issues" for a control the device refuses.
 */
export function validateProject(
  project: ProjectConfig,
  commandIssues?: Map<Record<string, unknown>, string[]>,
): ValidationIssue[] {
  const refs: ProjectRefs = {
    pageIds: new Set(project.ui.pages.map((p) => p.id)),
    deviceIds: new Set(project.devices.map((d) => d.id)),
    macroIds: new Set(project.macros.map((m) => m.id)),
    commandIssues,
  };
  const theme = sliderThemeDefaults(project);
  const review = { theme, masters: project.ui.master_elements ?? [] };
  return [
    ...project.ui.pages.flatMap((page) => pageIssues(page, refs, review)),
    ...(project.ui.master_elements || []).flatMap((mel) => masterIssues(mel, refs, theme)),
    ...macroIssues(project.macros, refs),
    ...idlePageIssues(project, refs.pageIds),
  ];
}

// --- In-place text editing ---
//
// The panel records which authored field produced the text on screen and hands
// that path back when an edit commits (panel.js `_recordTextSource`). These two
// turn such a path into a patch the element write door already takes.

/**
 * Slots the renderer reads by TRUTHINESS rather than by `!== undefined`, so an
 * empty string there does not blank the text -- it falls through to the next
 * thing in the chain.
 *
 * `evaluateFeedback`'s binary branch tests `isActive && binding.label_active`
 * and `evaluateToggleLook` tests `word !== ''`, both falling back to the
 * element's own `label`. Its multi-state branch, by contrast, tests
 * `appearance.label !== undefined`, so an empty string there really does blank
 * it. Clearing one of these four is therefore the author asking for the
 * fallback, and the honest write is to REMOVE the key rather than store a ""
 * that reads as a different word on screen.
 */
const TRUTHINESS_READ_SLOTS = new Set([
  "label_active",
  "label_inactive",
  "on_label",
  "off_label",
]);

/**
 * Whether committing an empty string at this path should delete the key
 * instead of storing "".
 */
export function emptyCommitDeletes(path: (string | number)[]): boolean {
  const last = path[path.length - 1];
  return typeof last === "string" && TRUTHINESS_READ_SLOTS.has(last);
}

/**
 * Turn a recorded source path plus a new string into a patch for the element.
 *
 * Returns a patch containing only the top-level key the path reaches into, so
 * the existing `handlePropertyChange` write door -- and the undo burst it
 * already runs -- work unchanged. Structures along the way are cloned, never
 * mutated: the project object in the store is shared with the render.
 *
 * An empty value at a truthiness-read slot deletes that key (see
 * `TRUTHINESS_READ_SLOTS`). Everywhere else it stores the empty string, which
 * is what blanking those fields means.
 *
 * Array segments stay arrays -- `bindings.do.press` is a list whose first
 * action carries the toggle words.
 */
export function patchForTextPath(
  element: UIElement,
  path: (string | number)[],
  value: string,
): Partial<UIElement> | null {
  if (path.length === 0) return null;
  const drop = value === "" && emptyCommitDeletes(path);

  const write = (node: unknown, depth: number): unknown => {
    const seg = path[depth];
    const last = depth === path.length - 1;

    if (last) {
      if (typeof seg === "number") {
        const arr = Array.isArray(node) ? [...node] : [];
        arr[seg] = value;
        return arr;
      }
      const obj = { ...(node && typeof node === "object" ? node : {}) } as
        Record<string, unknown>;
      if (drop) delete obj[seg];
      else obj[seg] = value;
      return obj;
    }

    if (typeof seg === "number") {
      const arr = Array.isArray(node) ? [...node] : [];
      arr[seg] = write(arr[seg], depth + 1);
      return arr;
    }
    const obj = { ...(node && typeof node === "object" ? node : {}) } as
      Record<string, unknown>;
    obj[seg] = write(obj[seg], depth + 1);
    return obj;
  };

  const top = path[0];
  if (typeof top !== "string") return null;
  // The element itself is the outermost container, so depth 0 is `top`. One
  // walk covers a bare `label` and a five-deep look slot alike.
  const next = write(element, 0) as Record<string, unknown>;
  return { [top]: next[top] } as Partial<UIElement>;
}
