/**
 * The finger rule, and the screen it is measured on.
 *
 * Every size the Builder warns about is estimated on one reference panel
 * (`TOUCH_REFERENCE`), turned for a portrait arrangement (`referenceBox`), and a
 * control a finger has to hit needs `TOUCH_MIN_MM` of it. The page review's
 * touch check, the Layout properties' touch warning and the box an aspect lock
 * is measured in all read these, so the reference panel and the minimum are
 * changed here once.
 *
 * Mirrors the finger rule at the top of openavc/ui/page_review.py and
 * `reference_box` in openavc/ui/control_minimums.py;
 * tests/test_ui_page_review_mirrors.py reads this file to hold them together.
 */

import type { Placement, UIPage } from "../../api/types";
import { containersAbove, layoutOrientation, resolvePlacements } from "./pageGeometry";

/** The reference glass every warning is estimated against.
 *
 *  1280x800 is the design reference. The density is the part that was wrong:
 *  100 px/inch describes a 15-inch panel at that resolution, and almost nobody
 *  deploys one. The mainstream wall panel at 1280x800 is 10.1 inches, which is
 *  ~149 px/inch -- so the old figure reported a control as half again more
 *  finger than it actually gets, and told an author 11.2mm where the panel
 *  gives 7.5mm. Measured against real panel diagonals 2026-08-04.
 */
export const TOUCH_REFERENCE = { width: 1280, height: 800, pxPerInch: 149 };

/** The reference glass for an arrangement, the way round it is drawn.
 *
 *  A portrait arrangement is not a different panel, it is this one turned, so
 *  the pixels are the landscape pair swapped rather than a second measurement
 *  -- the same thing `presetForOrientation` does to the canvas. What does NOT
 *  swap is `pxPerInch`: rotating a tablet does not change its density, so the
 *  finger rule means the same thing either way round.
 *
 *  Mirrors `reference_box` in openavc/ui/control_minimums.py. It exists because
 *  a portrait page used to be measured against 1280x800 unconditionally, which
 *  was 60% too generous on width and wrong in the direction that ACCEPTS. */
export function referenceBox(orientation: string): { width: number; height: number } {
  return orientation === "portrait"
    ? { width: TOUCH_REFERENCE.height, height: TOUCH_REFERENCE.width }
    : { width: TOUCH_REFERENCE.width, height: TOUCH_REFERENCE.height };
}

/** The comfortable finger minimum, and the actual rule.
 *
 *  It is in millimetres because that is the real question -- whether a thumb
 *  can hit the thing is physical, not pixel. The 44px runtime clamp this
 *  replaced was never that rule: 44 CSS px works out between 6.0mm and 8.5mm
 *  across every panel in the deployment range (800x480 at 5in through
 *  1920x1080 at 15.6in), so it sat under the comfortable minimum on all of
 *  them and only looked like a guarantee. Deleting the clamp lost nothing.
 *  Stating the rule in millimetres is what makes the advice true.
 */
export const TOUCH_MIN_MM = 9;

/** The millimetre rule in reference pixels, derived so the two cannot drift. */
export const TOUCH_MIN_PX = (TOUCH_MIN_MM / 25.4) * TOUCH_REFERENCE.pxPerInch;

/** Reference pixels as millimetres of finger, at the reference density.
 *  Mirrors `_to_mm` in page_review.py. */
export function toMm(px: number): number {
  return (px / TOUCH_REFERENCE.pxPerInch) * 25.4;
}

export interface TouchWarning {
  /** Which axis is too small, for the message. */
  axis: "width" | "height" | "both";
  /** Estimated size at the reference, in px. */
  widthPx: number;
  heightPx: number;
  /** The same estimate in millimetres of finger. */
  widthMm: number;
  heightMm: number;
}

/**
 * Flag a control that would be awkward to touch, with the physical size shown
 * so the author can judge it. A warning, never a clamp: an author who wants a
 * 20px status dot is allowed to have one.
 */
export function touchTargetWarning(
  p: Placement,
  parentPx?: { width: number; height: number },
): TouchWarning | null {
  const ref = parentPx ?? { width: TOUCH_REFERENCE.width, height: TOUCH_REFERENCE.height };
  const widthPx = (p.w / 100) * ref.width;
  const heightPx = (p.h / 100) * ref.height;
  const narrow = widthPx < TOUCH_MIN_PX;
  const short = heightPx < TOUCH_MIN_PX;
  if (!narrow && !short) return null;
  return {
    axis: narrow && short ? "both" : narrow ? "width" : "height",
    widthPx: Math.round(widthPx),
    heightPx: Math.round(heightPx),
    widthMm: Math.round(toMm(widthPx) * 10) / 10,
    heightMm: Math.round(toMm(heightPx) * 10) / 10,
  };
}

/**
 * The pixel box an element's percentages are measured against, at the
 * reference size -- the page, or the container it sits in, walked up through
 * however many containers that takes.
 *
 * Anything expressed as a real-world ratio needs this. An aspect lock is a
 * PIXEL ratio (that is what CSS aspect-ratio means), so seeding one from an
 * element's current shape has to go through the box it actually sits in: a
 * child that is 50% wide and 50% tall inside a wide container is a wide
 * element, not a square one.
 */
export function referenceParentBox(
  page: UIPage,
  elementId: string,
  layoutId?: string | null,
): { width: number; height: number } {
  const placements = resolvePlacements(page, layoutId);
  const byId = new Map(page.elements.map((e) => [e.id, e]));
  const ref = referenceBox(layoutOrientation(page, layoutId));
  let width = ref.width;
  let height = ref.height;
  // Outermost first: the same products, in the same order, every time.
  for (const id of containersAbove(elementId, byId).reverse()) {
    const box = placements[id];
    if (!box) continue;
    width = (box.w / 100) * width;
    height = (box.h / 100) * height;
  }
  return { width, height };
}

/** Whether a finger has to hit this element type at all. */
export function isTouchable(type: string): boolean {
  return TOUCHABLE_TYPES.has(type);
}

/** Element types a finger actually has to hit. A label being small is fine.
 *
 *  `fader` and `slider` are here because dragging is touch -- the same thumb
 *  grabs the handle. Neither can actually reach this check without already
 *  having failed its contents floor (a fader that holds its handle and scale is
 *  72x100, a slider 68x81, both past the 53px finger minimum), so they are here
 *  for consistency: the rule is "a control you touch has a physical minimum",
 *  and leaving the draggable ones out made it read like a rule about buttons.
 *  Mirrored in openavc/ui/page_review.py, with a test asserting the two match. */
export const TOUCHABLE_TYPES = new Set([
  "button", "page_nav", "camera_preset", "select", "text_input", "keypad", "list",
  "fader", "slider",
]);
