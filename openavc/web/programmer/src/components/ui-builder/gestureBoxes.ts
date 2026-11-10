/**
 * The box each authoring gesture on the canvas produces, as numbers: where the
 * pointer is as a percentage of the box it fell in; the two pulls a moving
 * edge feels (a line within 1.5%, else the snap increment) and the guides drawn
 * for them; a box moved or resized by the pointer's travel (Shift's axis lock
 * and ratio, an aspect lock, the 1% floor); a palette drop centred under the
 * pointer; and where a control lands when nobody pointed, the first free snap
 * cell or else a cascade from the page centre.
 *
 * Pure: boxes in, boxes out, in the percentages of whatever box the gesture is
 * measured against (a control's container, the page, a master's screen). The
 * canvas (Canvas.tsx) owns the pointer, the keys and the store; the view's
 * palette drop, click-to-add and a paste ask the rest. They read it through
 * uiBuilderHelpers.ts, which re-exports the names they use.
 */
import type { Placement, SnapConfig, UIElement } from "../../api/types";
import { roundPlacement } from "./pageGeometry";

// --- A pointer, and the snap increment ---

/**
 * Map a pointer coordinate to a percentage of the box it fell in.
 *
 * The page carries no padding any more (the grid's gutter died with the grid),
 * so this is the whole rect edge to edge -- the pointer lands exactly where the
 * pointer is.
 */
export function pointerToPercent(
  pointerPx: number,
  rectStart: number,
  rectLength: number,
): number {
  if (!(rectLength > 0)) return 0;
  return ((pointerPx - rectStart) / rectLength) * 100;
}

/** Round a value onto the nearest multiple of an increment. */
function snapToIncrement(value: number, increment: number): number {
  if (!(increment > 0)) return value;
  return Math.round(value / increment) * increment;
}

// --- Magnetic snapping ---

/**
 * How close (in percent) an edge has to be before element-edge magnetism grabs
 * it. Roughly a fifth of a default snap cell, so the grid still wins in open
 * space and edges win when you are genuinely near one.
 */
const MAGNET_TOLERANCE = 1.5;

/** What a gesture can be pulled toward, and what it takes to suspend it. */
export interface SnapContext {
  /** The page's snap increment. `enabled: false` leaves only edge magnetism. */
  snap: SnapConfig;
  /** Alt/Option held: suspend snapping entirely for this gesture, grid AND
   *  element-edge magnetism, so the control lands on the exact pointer. */
  bypass?: boolean;
  /** Sibling boxes to be magnetic against (same parent, moving ones excluded). */
  others?: Placement[];
}

/** A snapped gesture: the adjusted box, plus the guides worth drawing. */
export interface SnapOutcome {
  placement: Placement;
  /** Percent offsets where a vertical guide line should be drawn. */
  guidesX: number[];
  /** Percent offsets where a horizontal guide line should be drawn. */
  guidesY: number[];
}

/** Every line a moving edge can be attracted to on one axis: the page edges
 *  and centre, plus every sibling's edges and centre. Deliberately NOT the
 *  thirds — on an otherwise empty page they read as snapping to nothing. */
function magnetTargets(others: Placement[], axis: "x" | "y"): number[] {
  const targets = [0, 50, 100];
  for (const o of others) {
    const start = axis === "x" ? o.x : o.y;
    const size = axis === "x" ? o.w : o.h;
    targets.push(start, start + size / 2, start + size);
  }
  return targets;
}

/** How far one axis's moving edges travel, and the lines they land on. */
interface Pull {
  delta: number;
  guides: number[];
}

/**
 * Pull a set of moving edges onto the nearest line within MAGNET_TOLERANCE of
 * any of them, because being flush with a neighbour or the page is what the
 * author meant; the first of two equally near wins. Every line an edge ends up
 * on gets a guide, once, so a flush left AND a flush centre both draw. Null
 * when no line is in reach.
 */
function magnetPull(edges: number[], targets: number[]): Pull | null {
  let best: number | null = null;
  for (const edge of edges) {
    for (const target of targets) {
      const delta = target - edge;
      if (Math.abs(delta) > MAGNET_TOLERANCE) continue;
      if (best === null || Math.abs(delta) < Math.abs(best)) best = delta;
    }
  }
  if (best === null) return null;
  const delta = best;
  const moved = edges.map((e) => e + delta);
  const guides = targets.filter((t) => moved.some((m) => Math.abs(m - t) < 1e-6));
  return { delta, guides: [...new Set(guides)] };
}

/** Pull a set of moving edges onto the snap increment nearest any of them, the
 *  first of two equally near winning. A coarser pull with no tolerance: there
 *  is always a nearest increment. */
function gridPull(edges: number[], increment: number): Pull {
  let best: number | null = null;
  for (const edge of edges) {
    const delta = snapToIncrement(edge, increment) - edge;
    if (best === null || Math.abs(delta) < Math.abs(best)) best = delta;
  }
  return { delta: best ?? 0, guides: [] };
}

/** One axis of a gesture: a line within reach wins, else the snap increment
 *  when snap is on, else the edges stay where the pointer put them. */
function snapAxis(edges: number[], increment: number, gridOn: boolean, targets: number[]): Pull {
  return magnetPull(edges, targets) ?? (gridOn ? gridPull(edges, increment) : { delta: 0, guides: [] });
}

/**
 * Snap a box that is being MOVED: the size is fixed, so both edges and the
 * centre travel together and whichever of them is nearest something decides.
 */
export function snapMove(rect: Placement, ctx: SnapContext): SnapOutcome {
  if (ctx.bypass) return { placement: roundPlacement(rect), guidesX: [], guidesY: [] };
  const snap = ctx.snap;
  const others = ctx.others ?? [];
  const gridOn = snap.enabled !== false;

  const xr = snapAxis(
    [rect.x, rect.x + rect.w / 2, rect.x + rect.w],
    snap.x,
    gridOn,
    magnetTargets(others, "x"),
  );
  const yr = snapAxis(
    [rect.y, rect.y + rect.h / 2, rect.y + rect.h],
    snap.y,
    gridOn,
    magnetTargets(others, "y"),
  );

  return {
    placement: roundPlacement({ ...rect, x: rect.x + xr.delta, y: rect.y + yr.delta }),
    guidesX: xr.guides,
    guidesY: yr.guides,
  };
}

/**
 * Snap a box that is being RESIZED. Only the edges the handle actually drags
 * are attracted -- an east drag must not pull the west edge along with it.
 */
export function snapResize(
  rect: Placement,
  direction: string,
  ctx: SnapContext,
): SnapOutcome {
  if (ctx.bypass) return { placement: roundPlacement(rect), guidesX: [], guidesY: [] };
  const snap = ctx.snap;
  const others = ctx.others ?? [];
  const gridOn = snap.enabled !== false;
  const next = { ...rect };
  let guidesX: number[] = [];
  let guidesY: number[] = [];

  if (direction.includes("w")) {
    const r = snapAxis([next.x], snap.x, gridOn, magnetTargets(others, "x"));
    next.x += r.delta;
    next.w -= r.delta;
    guidesX = r.guides;
  } else if (direction.includes("e")) {
    const r = snapAxis([next.x + next.w], snap.x, gridOn, magnetTargets(others, "x"));
    next.w += r.delta;
    guidesX = r.guides;
  }
  if (direction.includes("n")) {
    const r = snapAxis([next.y], snap.y, gridOn, magnetTargets(others, "y"));
    next.y += r.delta;
    next.h -= r.delta;
    guidesY = r.guides;
  } else if (direction.includes("s")) {
    const r = snapAxis([next.y + next.h], snap.y, gridOn, magnetTargets(others, "y"));
    next.h += r.delta;
    guidesY = r.guides;
  }

  return { placement: roundPlacement(next), guidesX, guidesY };
}

/**
 * Where a palette drag sits right now, and lands when released: the new
 * element centred under the pointer, then snapped exactly like a move. The
 * live preview and the drop both call this, so what the guides show IS the
 * landing spot.
 */
export function paletteDragPlacement(
  centre: { x: number; y: number },
  size: { w: number; h: number },
  ctx: SnapContext,
): SnapOutcome {
  return snapMove(
    { x: centre.x - size.w / 2, y: centre.y - size.h / 2, w: size.w, h: size.h },
    ctx,
  );
}

// --- A box moved or resized by the pointer ---

/** How far the pointer has travelled since the press, and the box a gesture's
 *  percentages are of: a page control's container (or the page), a master
 *  element's screen. */
export interface Travel {
  /** Pointer travel in screen pixels. */
  dx: number;
  dy: number;
  /** That box's size on screen, in pixels. */
  frame: { width: number; height: number };
}

/** Two boxes with the same four numbers: a gesture that ends on its start
 *  box changed nothing. */
export function sameBox(a: Placement, b: Placement): boolean {
  return a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h;
}

/** A control's aspect lock (its width over its height, in pixels), when it
 *  has one that is a positive number. */
export function aspectLockOf(el: Pick<UIElement, "aspect_lock"> | undefined): number | null {
  const lock = el?.aspect_lock;
  return typeof lock === "number" && lock > 0 ? lock : null;
}

/** The keys held during a gesture: Alt (`bypass`) sets snapping aside, Shift
 *  (`constrain`) keeps a move to one axis and a resize to its starting ratio. */
export interface GestureContext extends SnapContext {
  constrain?: boolean;
}

/**
 * A box MOVED by the pointer: Shift keeps it to whichever axis has travelled
 * further (x on a tie), the standard "keep it in line" modifier, and then it
 * is snapped like any move.
 */
export function movedBox(start: Placement, travel: Travel, ctx: GestureContext): SnapOutcome {
  let { dx, dy } = travel;
  if (ctx.constrain) {
    if (Math.abs(dx) >= Math.abs(dy)) dy = 0;
    else dx = 0;
  }
  return snapMove(
    {
      ...start,
      x: start.x + (dx / travel.frame.width) * 100,
      y: start.y + (dy / travel.frame.height) * 100,
    },
    ctx,
  );
}

/**
 * A box RESIZED from one of its grips by the pointer.
 *
 * The edges the grip drags travel with it and the others stay put, neither
 * side goes under MIN_ELEMENT_SIZE, and the dragged edges are snapped. Then a
 * box with a ratio to keep keeps it: its aspect lock (`lock`), or on Shift the
 * ratio it started with. The ratio is in PIXELS, so it converts back to
 * percentages through the frame's own proportions.
 */
export function resizedBox(
  start: Placement,
  direction: string,
  travel: Travel,
  ctx: GestureContext & { lock: number | null },
): SnapOutcome {
  const snapped = snapResize(draggedEdges(start, direction, travel), direction, ctx);
  const ratio =
    ctx.lock ?? (ctx.constrain && start.h > 0 ? pixelRatio(start, travel.frame) : null);
  const box = ratio ? keptRatio(snapped.placement, direction, ratio, travel.frame) : snapped.placement;
  return { placement: roundPlacement(box), guidesX: snapped.guidesX, guidesY: snapped.guidesY };
}

/** The box with the edges a grip drags moved by the pointer's travel, each
 *  side held at MIN_ELEMENT_SIZE or more. */
function draggedEdges(start: Placement, direction: string, travel: Travel): Placement {
  const ddx = (travel.dx / travel.frame.width) * 100;
  const ddy = (travel.dy / travel.frame.height) * 100;
  const raw = { ...start };
  if (direction.includes("e")) raw.w = start.w + ddx;
  if (direction.includes("w")) {
    raw.x = start.x + ddx;
    raw.w = start.w - ddx;
  }
  if (direction.includes("s")) raw.h = start.h + ddy;
  if (direction.includes("n")) {
    raw.y = start.y + ddy;
    raw.h = start.h - ddy;
  }
  raw.w = Math.max(MIN_ELEMENT_SIZE, raw.w);
  raw.h = Math.max(MIN_ELEMENT_SIZE, raw.h);
  return raw;
}

/** A box's width over its height, in the frame's pixels. */
function pixelRatio(box: Placement, frame: Travel["frame"]): number {
  return ((box.w / 100) * frame.width) / ((box.h / 100) * frame.height);
}

/**
 * The box with the side the grip does not drive set from the side it does,
 * through `ratio`: a grip that moves the width (east or west) sets the
 * height, any other sets the width. When the grip also drags that side's
 * start edge (north for the height, west for the width), its far edge stays
 * put. The side set this way is held at MIN_ELEMENT_SIZE or more.
 */
function keptRatio(box: Placement, direction: string, ratio: number, frame: Travel["frame"]): Placement {
  if (direction.includes("e") || direction.includes("w")) {
    const hPx = ((box.w / 100) * frame.width) / ratio;
    const [y, h] = following(box.y, box.h, (hPx / frame.height) * 100, direction.includes("n"));
    return { ...box, y, h };
  }
  const wPx = ((box.h / 100) * frame.height) * ratio;
  const [x, w] = following(box.x, box.w, (wPx / frame.width) * 100, direction.includes("w"));
  return { ...box, x, w };
}

/** One side's start and size once it takes `size`: the start moves only when
 *  the grip holds that side's start edge, so the far edge stays where it was. */
function following(origin: number, current: number, size: number, fromStart: boolean): [number, number] {
  return [fromStart ? origin + (current - size) : origin, Math.max(MIN_ELEMENT_SIZE, size)];
}

// --- Auto-placement (the pointerless paths) ---

/** The smallest box a resize or an auto-place will produce, in percent. */
const MIN_ELEMENT_SIZE = 1;

/** The down-right nudge, in percent, a copy takes from where it would
 *  otherwise sit: each step of the cascade, and a duplicate from its original. */
export const CASCADE_STEP = 2.5;

/** True when two boxes share any area at all. */
function placementsOverlap(a: Placement, b: Placement): boolean {
  return (
    a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y
  );
}

/**
 * Where a control lands when nobody pointed at anything: click-to-add from the
 * palette, and paste.
 *
 * With snap on this is the first free snap cell scanning row-major, which is
 * exactly what the old grid did for free, so click-to-add is visually unchanged
 * by the whole move to percentages. With snap off "first free cell" is
 * meaningless, so it falls back to the page centre plus a cascade -- the
 * down-right nudge every design tool uses for a paste -- and so does a page
 * with no free cell left. A size under MIN_ELEMENT_SIZE grows to it first.
 */
export function autoPlace(
  occupied: Placement[],
  size: { w: number; h: number },
  snap: SnapConfig,
  cascadeIndex = 0,
): Placement {
  const w = Math.max(MIN_ELEMENT_SIZE, size.w);
  const h = Math.max(MIN_ELEMENT_SIZE, size.h);
  return firstFreeCell(occupied, w, h, snap) ?? cascaded(w, h, cascadeIndex);
}

/** The first snap cell, row by row, that a `w` x `h` box can take. Null with
 *  snap off, an increment that is not positive, or no cell free. */
function firstFreeCell(occupied: Placement[], w: number, h: number, snap: SnapConfig): Placement | null {
  if (!hasCells(snap)) return null;
  const cols = Math.max(1, Math.floor(100 / snap.x));
  const rows = Math.max(1, Math.floor(100 / snap.y));
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const cell = { x: c * snap.x, y: r * snap.y, w, h };
      if (cellIsFree(cell, occupied)) return roundPlacement(cell);
    }
  }
  return null;
}

/** Snap divides the page into cells only when it is on and both increments
 *  are positive. */
function hasCells(snap: SnapConfig): boolean {
  return snap.enabled !== false && snap.x > 0 && snap.y > 0;
}

/** A cell a box can take: within the page's far edges, give or take float
 *  noise, and sharing no area with anything already there. */
function cellIsFree(cell: Placement, occupied: Placement[]): boolean {
  const pastTheEdge = cell.x + cell.w > 100 + 1e-6 || cell.y + cell.h > 100 + 1e-6;
  return !pastTheEdge && !occupied.some((o) => placementsOverlap(cell, o));
}

/** The page centre, nudged CASCADE_STEP down-right per add and wrapped every
 *  eight so a long cascade cannot march a control off the page, then held on
 *  the page. */
function cascaded(w: number, h: number, cascadeIndex: number): Placement {
  const offset = (cascadeIndex * CASCADE_STEP) % 20;
  return roundPlacement({
    x: Math.max(0, Math.min(100 - w, 50 - w / 2 + offset)),
    y: Math.max(0, Math.min(100 - h, 50 - h / 2 + offset)),
    w,
    h,
  });
}
