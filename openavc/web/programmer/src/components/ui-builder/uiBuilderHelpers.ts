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
  DEFAULT_PLACEMENT,
  GEOMETRY_PRECISION,
  absolutePlacements,
  containerOf,
  getPlacement,
  pageSnap,
  primaryLayout,
  roundPlacement,
  withAbsolutePlacements,
  withPlacement,
  withPlacements,
  withoutPlacement,
} from "./pageGeometry";
import { rewriteElement } from "./projectRewrites";
import { descendantIds, reparentPlacements, topmostSelection } from "./containerTree";
import { CASCADE_STEP, autoPlace } from "./gestureBoxes";
import { isPlainObject } from "./authoredValues";
import type { ElementDefaults } from "./controlMinimums";
import {
  reviewMasterElement,
  reviewPage,
  sliderThemeDefaults,
  type ReviewOptions,
} from "./pageReview";
import { showOf } from "./reviewFinding";

// The panel's rem size, generated from Python with the per-control floors
// (openavc/ui/minimums_gen.py), because it is a measured fact rather than a
// design decision.
import { REM_BASE_PX } from "../../api/uiMinimums.gen";

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
