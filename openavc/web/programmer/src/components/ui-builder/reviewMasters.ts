/**
 * A page's controls against the master elements drawn underneath them, and a
 * control with no box at all.
 *
 * Mirrors "A page's controls against the masters underneath them" in
 * openavc/ui/page_review.py.
 */

import type { MasterElement, Placement, UIElement } from "../../api/types";
import type { ReviewFinding } from "./reviewFinding";
import { overlapExtent } from "./reviewGeometry";
import { mutuallyExclusive } from "./reviewStacking";
import { fixed } from "./sentenceNumbers";
import { referenceBox } from "./touchTarget";

//
// Masters are appended to the page surface BEFORE the page's own elements
// (panel.js `renderCurrentPage`), and every child of `.panel-page` shares one z-index,
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
 * The panel's `masterPlacement`: this orientation's own box, then landscape,
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
 *  defaults `placeElement` applies to a missing coordinate. */
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
