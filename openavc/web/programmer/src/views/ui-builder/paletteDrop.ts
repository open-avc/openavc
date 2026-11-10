/**
 * Where a control new from the palette lands and what it may be called.
 *
 * A drop lands where the pointer is, worked out by `landingAt`, which the
 * preview asks on every pointer move and the drop asks again only when the
 * preview never saw the pointer: so what the preview shows is where the
 * control goes. A drop inside a container makes the control its child
 * (`dropLanding`). Click-to-add has no pointer and takes the first free spot
 * instead (`clickLanding`); it and the drop's snapping both measure against
 * the page's top-level controls in the arrangement being authored
 * (`topLevelBoxes`): a control inside a container stores its box in the
 * container's terms, so it is left out. Either way the page makes the control
 * from the `Landing` alone, named past `takenIds`.
 */
import type { MasterElement, Placement, UIPage } from "../../api/types";
import {
  autoPlace,
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

/** Where a new control goes: the container that adopts it, if one does, and
 *  its box in that container's terms (the page's when none does). */
export interface Landing {
  parentId: string | null;
  placement: Placement;
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

/** A drop at `placement`, in page terms. Inside a container the control
 *  becomes its child, re-expressed relative to it, which is what dropping it
 *  there shows was meant. Measured against the arrangement on screen: a
 *  container can sit somewhere else entirely in another layout, and the
 *  pointer was over THIS one. */
export function dropLanding(page: UIPage, placement: Placement, layoutId: string | null): Landing {
  const { parentId, relative } = resolveDropParent(page, placement, layoutId);
  return { parentId, placement: relative };
}

/** Click-to-add: the first free snap cell, scanning row-major, or the page
 *  centre plus a cascade of one step per control on the page when snap is
 *  off. */
export function clickLanding(page: UIPage, layoutId: string | null, size: Size): Landing {
  const placement = autoPlace(topLevelBoxes(page, layoutId), size, pageSnap(page), page.elements.length);
  return { parentId: null, placement };
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
  return {
    placement: outcome.placement,
    guidesX: outcome.guidesX,
    guidesY: outcome.guidesY,
    adoptInto: dropLanding(place.page, outcome.placement, place.layoutId).parentId,
    label: place.label,
  };
}
