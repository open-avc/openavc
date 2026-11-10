import type {
  UIPage,
  UIElement,
  Placement,
  Layout,
  MasterElement,
  PageGroup,
} from "../../api/types";
import { firstFree, generateId } from "./newElement";
import {
  DEFAULT_PLACEMENT,
  GEOMETRY_PRECISION,
  absolutePlacements,
  getPlacement,
  masterPlacement,
  masterPlacementsFromPage,
  pageSnap,
  withAbsolutePlacements,
  withPlacement,
  withPlacements,
  withoutPlacement,
} from "./pageGeometry";
import { rewriteElement } from "./projectRewrites";
import { descendantIds, reparentPlacements, topmostSelection } from "./containerTree";
import { CASCADE_STEP, autoPlace } from "./gestureBoxes";

// The panel's rem size, generated from Python with the per-control floors
// (openavc/ui/minimums_gen.py), because it is a measured fact rather than a
// design decision.
import { REM_BASE_PX } from "../../api/uiMinimums.gen";

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
    const els = swapped(p.elements, a, b);
    return els ? { ...p, elements: els } : p;
  });
}

/**
 * A copy of a list with the items at two positions traded, or null when there
 * is nothing to trade: either is not a position in the list (a -1 from a
 * lookup that found nothing, or one step past an end), or they are the same.
 */
function swapped<T>(list: T[], i: number, j: number): T[] | null {
  const inList = (n: number) => n >= 0 && n < list.length;
  const both = inList(i) && inList(j);
  if (!both || i === j) return null;
  const out = [...list];
  [out[i], out[j]] = [out[j], out[i]];
  return out;
}

// --- Page reordering ---

export function reorderPage(
  pages: UIPage[],
  pageId: string,
  direction: "left" | "right",
): UIPage[] {
  const idx = pages.findIndex((p) => p.id === pageId);
  const newIdx = direction === "left" ? idx - 1 : idx + 1;
  return swapped(pages, idx, newIdx) ?? pages;
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

/**
 * A control crossing between a page and the master elements, under an id the
 * shared ui.<id> namespace leaves free.
 *
 * Masters and page elements share ui.<id>, and two same-id elements would
 * break ui.<id> resolution at runtime. So when the id is already taken (an
 * imported or hand-edited project, or a page control created with this id
 * while it lived as a master), the control takes a fresh one and its
 * references to itself follow it.
 */
function underFreeId(element: UIElement, taken: Set<string>): UIElement {
  if (!taken.has(element.id)) return element;
  return rewriteElement(element, element.id, generateId(element.type, taken));
}

/**
 * Make a page control a master element, drawn on every page.
 *
 * It keeps the box it had on screen in each orientation (`masterPlacementsFromPage`),
 * and leaves its parent link behind: masters are placed against the viewport,
 * never inside a page's container. If a whole container is being promoted, its
 * children stay behind on the page, re-homed to the container's own parent
 * without moving -- a master cannot be anyone's parent, and losing six wired
 * controls to one promote is not a trade anybody wants. Same conversion a
 * container delete does.
 */
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

  const placements = masterPlacementsFromPage(page, elementId);
  const newPages = removeElementFromPage(pages, pageId, elementId);
  const promoted = underFreeId(element, takenElementIds(newPages, masterElements.map((m) => m.id)));
  const masterEl: MasterElement = {
    ...promoted,
    parent: null,
    pages: "*",
    placements,
    hidden: false,
  };
  return { pages: newPages, masterElements: [...masterElements, masterEl] };
}

/**
 * Put a master element back on a page as an ordinary control.
 *
 * The master-only fields go; the box it comes back with is the one a landscape
 * arrangement draws it at (`masterPlacement`, its fallbacks included, else the
 * default box), written into the page's primary arrangement.
 */
export function demoteFromMaster(
  pages: UIPage[],
  masterElements: MasterElement[],
  masterElementId: string,
  targetPageId: string,
): { pages: UIPage[]; masterElements: MasterElement[] } {
  const masterEl = masterElements.find(m => m.id === masterElementId);
  if (!masterEl) return { pages, masterElements };

  const newMasters = masterElements.filter(m => m.id !== masterElementId);
  const { pages: _pagesField, placements: _placements, hidden: _hidden, ...elementFields } = masterEl;
  const box = masterPlacement(masterEl, "landscape") ?? { ...DEFAULT_PLACEMENT };
  const demoted = underFreeId(elementFields as UIElement, takenElementIds(pages, newMasters.map((m) => m.id)));

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

// --- Validate ---
//
// Forwarded because the Builder view's Validate report and dialogs import it
// from here.

export { validateProject, type ValidationIssue } from "./validateProject";
