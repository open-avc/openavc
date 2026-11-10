/**
 * The page review's geometry findings: what a box will draw wrong, in pixels.
 *
 * A control drawn short of its floor and the container that starves it, one
 * too small to draw at all, one too small for a finger, a style measurement
 * bigger than its box, a box hanging out of its container, and siblings drawn
 * over each other. Each says the size the box is, the size it needs, and the
 * percentage of its real container to write instead.
 *
 * Mirrors "Geometry findings" in openavc/ui/page_review.py, sentence for
 * sentence: tests/test_ui_review_parity.py and
 * tests/fixtures/page_review_check_cases.json hold the two together.
 */

import type { Placement, UIElement } from "../../api/types";
import { PIXEL_SLACK, REM_BASE_PX } from "../../api/uiMinimums.gen";
import {
  controlMinimumBox,
  controlMinimumPercent,
  shortAxes,
  starvedReasons,
  type ControlMinimumBox,
  type ElementDefaults,
  type ShortAxis,
} from "./controlMinimums";
import { BOUNDS_EPSILON } from "./pageGeometry";
import {
  article,
  capitalized,
  joined,
  pageContainer,
  type Container,
  type ReviewFinding,
  type Seat,
} from "./reviewFinding";
import { fixed, num, pct } from "./sentenceNumbers";
import { TOUCHABLE_TYPES, TOUCH_MIN_MM, TOUCH_MIN_PX, referenceBox, toMm } from "./touchTarget";

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
export interface OverlapPair {
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
