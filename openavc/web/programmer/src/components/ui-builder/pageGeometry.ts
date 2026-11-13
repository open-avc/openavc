/**
 * Where a control is drawn: the boxes a page stores, and the same boxes in page
 * space.
 *
 * Each arrangement of a page (a layout) stores a box per control as
 * percentages of its container, to four decimal places, and a variant stores
 * only what moved, reading the rest through its inherits chain; a layout also
 * hides controls, and hides accumulate down the chain. A master element keeps
 * its own box per orientation. `absolutePlacements` reads every box in page
 * space and `withAbsolutePlacements` writes page-space boxes back, which is how
 * anything comparing two controls under different containers works; which
 * container a control is measured against is `containerOf`.
 *
 * The rule worth knowing before changing anything here: a stored box means
 * nothing without its container, so a write that changes a control's container
 * has to convert its box in the same edit (the container tree does that), and
 * the panel runtime reads all of this the same way (`selectLayout`,
 * `masterPlacement` and the grouping in `drawElementTree`, in the panel's
 * modules/layout.js).
 *
 * An edit aimed from the screen says which page and which arrangement as one
 * value, `OnScreen` (`PageSelection` adds the controls selected on it), which
 * the page model's tools and a move's commit take.
 *
 * A leaf: the container tree, the arranging tools and the page review import
 * it (the canvas, the toolbar and the properties panel read it through the
 * helpers file's re-exports), and it imports only the project's types and the
 * first-free naming rule a new layout's id takes.
 */
import type { Layout, MasterElement, Placement, SnapConfig, UIElement, UIPage } from "../../api/types";
import { firstFree } from "./newElement";

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
const SNAP_FALLBACK: SnapConfig = { enabled: true, x: 100 / 12, y: 100 / 8 };

/** Where a placement lands when the page has never heard of the element. */
export const DEFAULT_PLACEMENT: Placement = { x: 0, y: 0, w: 25, h: 12.5 };

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
 * The page an edit is aimed at, and the arrangement on screen: where a tool
 * measures the boxes it reads and stores the ones it moves.
 *
 * The arrangement is required, never left to a default: a caller that forgot it
 * would read and write the primary while the author looks at another (null is
 * the primary, said on purpose). The reads in this module take it as an
 * optional last argument instead, an absent one the primary (`layoutById`).
 */
export interface OnScreen {
  pageId: string;
  layoutId: string | null;
}

/** ... and the controls selected on it, in the order they were selected. */
export interface PageSelection extends OnScreen {
  elementIds: string[];
}

/**
 * The layouts that feed a chosen one, base first, so whatever the chosen layout
 * says for itself wins over what it inherits.
 *
 * The seen-set is a cycle guard -- a hand-edited project can point two layouts
 * at each other and the builder still has to draw something. Mirrors the panel
 * runtime's selectLayout, which walks the same chain for the same reason.
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

/** Percentages are floats; a hair over 100 is rounding, not a mistake.
 *
 *  Placements are stored to four decimal places, so this is exactly the
 *  rounding floor and nothing wider: the AI door's review forgives the same
 *  amount (`_BOUNDS_EPSILON` in openavc/ui/page_review.py), so the two surfaces
 *  agree about the same file. */
export const BOUNDS_EPSILON = 0.0001;

// --- Layout variants ---
//
// A page holds one set of controls and one or more arrangements of them. The
// primary is the arrangement everything falls back to; a variant says only what
// moved. Everything below keeps that promise: a variant is written to sparsely,
// read through its inherits chain, and removable without touching the primary.

export type Orientation = "landscape" | "portrait";

const ORIENTATIONS: Orientation[] = ["landscape", "portrait"];

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
  const [id] = firstFree(taken, (n) => (n === 1 ? orientation : `${orientation}_${n}`));
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
 * differently. Mirrors the panel runtime's masterPlacement, fallbacks included.
 */
export function masterPlacement(
  master: Pick<MasterElement, "placements"> | undefined,
  orientation: Orientation,
): Placement | null {
  const p = master?.placements ?? {};
  return p[orientation] ?? p.landscape ?? p.portrait ?? Object.values(p)[0] ?? null;
}

/**
 * THE write half of the pair above, for every door that moves a master: the
 * canvas gesture, the arrow keys and the Layout fields.
 *
 * The READ falls back across keys, so a portrait arrangement with no portrait
 * box of its own shows the landscape one. The WRITE never falls back: placing a
 * master while a portrait arrangement is being authored gives it a portrait box
 * instead of quietly rewriting the landscape one that every other page is
 * drawn from.
 */
export function withMasterPlacement(
  master: MasterElement,
  orientation: Orientation,
  placement: Placement,
): MasterElement {
  return {
    ...master,
    placements: { ...master.placements, [orientation]: roundPlacement(placement) },
  };
}

/**
 * The boxes a page control keeps when it becomes a master element, one per
 * orientation.
 *
 * A master's box is a percentage of the VIEWPORT, so it is valid on every page
 * whatever those pages are arranged like. The STORED placement is a percentage
 * of the element's parent -- for a container child that is the container, not
 * the page -- so each converts through the flattened page space, per
 * orientation: a portrait arrangement's position becomes the master's portrait
 * box instead of being discarded. An orientation takes the primary's box when
 * the primary places the control, else the first arrangement of it that does;
 * the primary's orientation always gets one, the default box when none of its
 * arrangements places the control.
 */
export function masterPlacementsFromPage(page: UIPage, elementId: string): Record<string, Placement> {
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
  return placements;
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

  // `seen` is the chain walked so far: a container already on it reads as no
  // container, which breaks a cycle at the element the walk started from.
  const resolve = (id: string, seen: Set<string>): Placement | null => {
    const memo = out[id];
    if (memo) return memo;
    const p = placements[id];
    if (!p) return null;
    const parentId = containerOf(byId.get(id), byId);
    const base = parentId && !seen.has(parentId) ? resolve(parentId, new Set(seen).add(parentId)) : null;
    out[id] = base ? pageSpace(p, base) : { ...p };
    return out[id];
  };

  for (const el of page.elements) resolve(el.id, new Set([el.id]));
  return out;
}

/** The ids an element's parent is looked up among: a page's, or a list's. */
type OnPage = { has(id: string): boolean };

/**
 * The container an element's box is measured against: its `parent`, when that
 * names another element on this page. A parent that names nothing here, or the
 * element itself, leaves it at page level, because a hand-edited project still
 * has to draw. A cycle is not this rule's concern: a walk up the chain stops
 * where it comes back round (`containersAbove`, and `absolutePlacements`' own).
 *
 * The panel groups a page the same way (`drawElementTree` in modules/layout.js),
 * except that it takes a self-parent at its word, so that element is drawn
 * nowhere. The page review asks this too, so a change here changes what the
 * review says and has to move `parent_of` in openavc/ui/page_review.py with it
 * (tests/test_ui_review_parity.py holds the two to the same words).
 */
export function containerOf(el: UIElement | undefined, onPage: OnPage): string | null {
  if (!el) return null;
  const named = el.parent;
  return named && named !== el.id && onPage.has(named) ? named : null;
}

/** Every container above an element, innermost first, stopping where a cycle comes back round. */
export function containersAbove(id: string, byId: ReadonlyMap<string, UIElement>): string[] {
  const chain: string[] = [];
  const seen = new Set<string>([id]);
  let cursor = containerOf(byId.get(id), byId);
  while (cursor && !seen.has(cursor)) {
    seen.add(cursor);
    chain.push(cursor);
    cursor = containerOf(byId.get(cursor), byId);
  }
  return chain;
}

/** A container's page-space box, when it has one with room to measure against. */
export function containerBox(id: string | null, absolute: Record<string, Placement>): Placement | null {
  const box = id ? absolute[id] : undefined;
  return hasArea(box) ? box : null;
}

/** The whole page, as a box. What a page-level element's percentages are of. */
export const PAGE_BOX: Placement = { x: 0, y: 0, w: 100, h: 100 };

/**
 * A box stored as percentages of `parent`, in the space `parent` is in.
 *
 * This and `parentSpace` below are the formula, each way, that the page-space
 * read (`absolutePlacements`, `toPageBox`) and every write of a page-space box
 * back against a container (the alignment tools, a reparent, a palette drop)
 * use. They take no view on a parent with no area; each caller decides what
 * that means for it (`hasArea`, `containerBox`). Which container a child is
 * measured against is `containerOf`; `referenceParentBox` walks the chain in
 * pixels with its own answer for a container with no box.
 */
function pageSpace(relative: Placement, parent: Placement): Placement {
  return {
    x: parent.x + (relative.x / 100) * parent.w,
    y: parent.y + (relative.y / 100) * parent.h,
    w: (relative.w / 100) * parent.w,
    h: (relative.h / 100) * parent.h,
  };
}

/** A box in the space `parent` is in, as percentages of `parent`. */
export function parentSpace(box: Placement, parent: Placement): Placement {
  return {
    x: ((box.x - parent.x) / parent.w) * 100,
    y: ((box.y - parent.y) / parent.h) * 100,
    w: (box.w / parent.w) * 100,
    h: (box.h / parent.h) * 100,
  };
}

/** Whether a box has room in it to measure another box against. */
function hasArea(box: Placement | null | undefined): box is Placement {
  return !!box && box.w > 0 && box.h > 0;
}

/** The page-space box an element's own percentages are measured against, its
 *  container read off the first control on the page with that id. A parent
 *  cycle is not broken here the way `absolutePlacements` breaks it, so for an
 *  element in one this can be a different box from the one it was read in. */
function parentAbsoluteBox(
  page: UIPage,
  elementId: string,
  absolute: Record<string, Placement>,
  onPage: OnPage,
): Placement {
  const parent = containerOf(page.elements.find((e) => e.id === elementId), onPage);
  return containerBox(parent, absolute) ?? { ...PAGE_BOX };
}

/** Write page-space boxes back as percentages of whatever each element's own
 *  parent is. The inverse of absolutePlacements, and the only way an alignment
 *  computed across containers lands where it was computed. */
export function withAbsolutePlacements(
  page: UIPage,
  absoluteBoxes: Record<string, Placement>,
  absolute: Record<string, Placement>,
  layoutId?: string | null,
): UIPage {
  const onPage = new Set(page.elements.map((e) => e.id));
  const relative: Record<string, Placement> = {};
  for (const [id, box] of Object.entries(absoluteBoxes)) {
    relative[id] = parentSpace(box, parentAbsoluteBox(page, id, absolute, onPage));
  }
  return withPlacements(page, relative, layoutId);
}

/** A parent-relative box, expressed in page space. */
export function toPageBox(relative: Placement, parentBox?: Placement | null): Placement {
  return hasArea(parentBox) ? pageSpace(relative, parentBox) : { ...relative };
}
