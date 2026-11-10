/**
 * Where a control new from the palette lands and what it may be called: one
 * rule for the live preview of a drag, the drop, and click-to-add.
 *
 * A drop lands where the pointer is, worked out by `landingAt`, which the
 * preview asks on every pointer move and the drop asks again only when the
 * preview never saw the pointer: so what the preview shows is where the
 * control goes. Snapping and click-to-add both measure against the page's
 * top-level controls in the arrangement being authored (`topLevelBoxes`); a
 * control inside a container stores its box in the container's terms, so it
 * is left out.
 */
import type { MasterElement, Placement, UIPage } from "../../api/types";
import {
  getPlacement,
  pageSnap,
  paletteDragPlacement,
  pointerToPercent,
  resolveDropParent,
} from "../../components/ui-builder/uiBuilderHelpers";

export interface Point {
  x: number;
  y: number;
}

export interface Size {
  w: number;
  h: number;
}

/** The page's top-level boxes in the arrangement being authored. */
export function topLevelBoxes(page: UIPage, layoutId: string | null): Placement[] {
  return page.elements
    .filter((el) => !el.parent)
    .map((el) => getPlacement(page, el.id, layoutId));
}

/** Every id a new control may not take: page controls and masters share the
 *  ui.<id> namespace, on every page. */
export function takenIds(pages: UIPage[], masters: MasterElement[]): Set<string> {
  return new Set([
    ...pages.flatMap((p) => p.elements.map((e) => e.id)),
    ...masters.map((m) => m.id),
  ]);
}

/** The canvas a drop measures against: its rect is the scaled one, so zoom is
 *  already in it. */
export function canvasRect(): DOMRect | undefined {
  return document.querySelector("[data-canvas-grid]")?.getBoundingClientRect();
}

export function pointerInside(rect: DOMRect, p: Point): boolean {
  return p.x >= rect.left && p.x <= rect.right && p.y >= rect.top && p.y <= rect.bottom;
}

/** Where a control of `size` lands with the pointer at `pointer`: centred
 *  under it, snapped the way a drag snaps, Alt (`bypass`) setting snap aside. */
export function landingAt(
  pointer: Point,
  rect: DOMRect,
  size: Size,
  place: { page: UIPage; layoutId: string | null; bypass: boolean },
) {
  const centre = {
    x: pointerToPercent(pointer.x, rect.left, rect.width),
    y: pointerToPercent(pointer.y, rect.top, rect.height),
  };
  return paletteDragPlacement(centre, size, {
    snap: pageSnap(place.page),
    bypass: place.bypass,
    others: topLevelBoxes(place.page, place.layoutId),
  });
}

/** What the canvas draws while the pointer is over it: the landing box, its
 *  guides, and the container that would adopt it, measured against the
 *  arrangement on screen. */
export function previewAt(
  pointer: Point,
  rect: DOMRect,
  size: Size,
  place: { page: UIPage; layoutId: string | null; bypass: boolean; label: string },
) {
  const outcome = landingAt(pointer, rect, size, place);
  const { parentId } = resolveDropParent(place.page, outcome.placement, place.layoutId);
  return {
    placement: outcome.placement,
    guidesX: outcome.guidesX,
    guidesY: outcome.guidesY,
    adoptInto: parentId,
    label: place.label,
  };
}
