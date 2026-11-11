/**
 * What is inside what on a page, and moving a control between containers.
 *
 * A control's container is its parent (`containerOf`, in pageGeometry.ts).
 * This answers the tree's questions: everything under a control, whether a
 * move is allowed (only into a container, never into itself or its own
 * contents), which containers a control could move into, what a drag or a
 * palette drop lands in, and the move itself, which converts the control's box
 * in every arrangement so it stays where it was on screen.
 *
 * The rule worth knowing before changing anything here: a change of container
 * and a change of box are one edit. 20% of a container is not 20% of the page,
 * so a write that sets `parent` alone moves the control; `reparentElement` and
 * `reparentPlacements` do both halves.
 *
 * The Outline lists this tree through `outlineRows` (outlineRows.ts), and
 * the canvas, the Outline, the properties panel and the view call these
 * through the helpers file's re-exports.
 */
import type { Layout, Placement, UIElement, UIPage } from "../../api/types";
import {
  BOUNDS_EPSILON,
  PAGE_BOX,
  absolutePlacements,
  containerBox,
  containersAbove,
  parentSpace,
  primaryLayout,
  roundPlacement,
  withPlacements,
  type OnScreen,
} from "./pageGeometry";

/** Everything hanging off an element, however deep. */
export function descendantIds(page: UIPage, elementId: string): Set<string> {
  const byParent = namedChildren(page.elements);
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
 * The ids of the controls that name each parent, in page order. Every name a
 * control gives counts, one the page does not hold included; naming itself
 * does not.
 */
function namedChildren(elements: UIElement[]): Map<string, string[]> {
  const byParent = new Map<string, string[]>();
  for (const el of elements) {
    const parent = el.parent;
    if (!parent || parent === el.id) continue;
    const list = byParent.get(parent) ?? [];
    list.push(el.id);
    byParent.set(parent, list);
  }
  return byParent;
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
export function reparentPlacements(
  source: UIPage,
  target: UIPage,
  ids: string[],
  newParentId: string | null,
): UIPage {
  const primaryId = primaryLayout(source)?.id;
  let next = target;
  for (const layout of source.layouts ?? []) {
    const absolute = absolutePlacements(source, layout.id);
    const base = newParentId ? containerBox(newParentId, absolute) : { ...PAGE_BOX };
    const rewritten = base ? againstContainer(absolute, rewrittenIn(layout, ids, primaryId), base) : {};
    if (Object.keys(rewritten).length) next = withPlacements(next, rewritten, layout.id);
  }
  return next;
}

/**
 * Which of these controls an arrangement stores a new box for: every one in the
 * primary, and in a variant only those it already holds a box of its own for.
 * Inventing entries in a variant would pin boxes that were happily inheriting.
 */
function rewrittenIn(layout: Layout, ids: string[], primaryId: string | undefined): string[] {
  if (layout.id === primaryId) return ids;
  const own = layout.placements ?? {};
  return ids.filter((id) => id in own);
}

/** These controls' page-space boxes as percentages of `container`; a control with no box is left out. */
function againstContainer(
  absolute: Record<string, Placement>,
  ids: string[],
  container: Placement,
): Record<string, Placement> {
  const out: Record<string, Placement> = {};
  for (const id of ids) {
    const box = absolute[id];
    if (box) out[id] = parentSpace(box, container);
  }
  return out;
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
  return innermostHolding(page, pageBox, absolute, banned) ?? stillTouching(element, pageBox, absolute, banned);
}

/** The smallest container that holds the box whole, the first of two the same size. */
function innermostHolding(
  page: UIPage,
  pageBox: Placement,
  absolute: Record<string, Placement>,
  banned: Set<string>,
): string | null {
  let best: { id: string; area: number } | null = null;
  for (const [id, c] of containersHolding(page, pageBox, absolute, banned)) {
    const area = c.w * c.h;
    if (!best || area < best.area) best = { id, area };
  }
  return best ? best.id : null;
}

/** Every container the box could join, in page order, with its page-space box. */
function containersHolding(
  page: UIPage,
  pageBox: Placement,
  absolute: Record<string, Placement>,
  banned: Set<string>,
): [string, Placement][] {
  const holding: [string, Placement][] = [];
  for (const el of page.elements) {
    if (el.type !== "group" || banned.has(el.id)) continue;
    const c = absolute[el.id];
    if (c && holdsWhole(c, pageBox)) holding.push([el.id, c]);
  }
  return holding;
}

/** Whether `outer` holds `inner` whole, forgiving the rounding floor at each edge. */
function holdsWhole(outer: Placement, inner: Placement): boolean {
  return (
    inner.x >= outer.x - BOUNDS_EPSILON &&
    inner.y >= outer.y - BOUNDS_EPSILON &&
    inner.x + inner.w <= outer.x + outer.w + BOUNDS_EPSILON &&
    inner.y + inner.h <= outer.y + outer.h + BOUNDS_EPSILON
  );
}

/** The container the control already lives in, while the box still touches it. */
function stillTouching(
  element: UIElement | undefined,
  pageBox: Placement,
  absolute: Record<string, Placement>,
  banned: Set<string>,
): string | null {
  const current = element?.parent ?? null;
  if (!current || banned.has(current)) return null;
  const box = absolute[current];
  return box && boxesOverlap(pageBox, box) ? current : null;
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
  { pageId, layoutId }: OnScreen,
  placements: Record<string, Placement>,
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

/**
 * Drop any element whose container is also selected.
 *
 * A container's children are percentages OF it, so moving or resizing the
 * container already carries them. Acting on both writes the same travel twice
 * and the child ends up somewhere nobody asked for. Easy to hit now that a
 * marquee can sweep a container and its contents in one gesture.
 */
export function topmostSelection(page: UIPage, elementIds: string[]): string[] {
  const selected = new Set(elementIds);
  const byId = new Map(page.elements.map((e) => [e.id, e]));
  return elementIds.filter((id) => !containersAbove(id, byId).some((c) => selected.has(c)));
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
  const base = parentId ? containerBox(parentId, absolutePlacements(page, layoutId)) : null;
  if (!base) {
    return { parentId: null, relative: roundPlacement(box) };
  }
  return { parentId, relative: roundPlacement(parentSpace(box, base)) };
}
