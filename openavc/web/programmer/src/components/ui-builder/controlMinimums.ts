/**
 * The smallest box a control can be drawn in: the TypeScript twin of
 * openavc/ui/control_minimums.py.
 *
 * The floors themselves are measured, not chosen: each type's rule (its base
 * size, the parts that keep their size when the box shrinks, the parts drawn
 * only when asked for, the one that grows with an authored size) was
 * binary-searched against a real browser and arrives generated, as
 * `CONTROL_MINIMUMS` in api/uiMinimums.gen.ts. What is written twice is the
 * arithmetic over a rule: how many of a repeated part an element draws, which
 * conditional parts are present, what the scaling part resolves to, and which
 * axes a box is short on. A change to that arithmetic (another way to count a
 * part, a part that turns with the screen) is made here and in
 * control_minimums.py together; tests/test_ui_review_parity.py compares the
 * sentences the two produce.
 *
 * Read by the page review's too-small finding and the Layout panel's minimum
 * size.
 */
import type { UIElement } from "../../api/types";
import {
  CONTROL_MINIMUMS,
  PIXEL_SLACK,
  REM_BASE_PX,
  type ControlMinimumRule,
  type ControlRepeatedInternal,
  type ControlScalingInternal,
} from "../../api/uiMinimums.gen";
import { own } from "./authoredValues";
import { matrixAxisCount } from "./matrixModel";
import { fixed } from "./sentenceNumbers";

/** The theme slice a control minimum can move with (only a slider's thumb). */
export type ElementDefaults = Record<string, unknown> | null | undefined;

/** A part of a control that keeps its size when the box shrinks. */
interface ResolvedInternal {
  part: string;
  widthPx: number | null;
  heightPx: number | null;
}

export interface ControlMinimumBox {
  widthPx: number;
  heightPx: number;
  internals: ResolvedInternal[];
}

/** Whether this element draws text beside its control.
 *
 *  Only `label` does. The single type with a caption bonus is `status_led`, and
 *  panel.js builds its `.led-label` under `if (element.label)` and from nothing
 *  else -- the same thing HONORED_SHOW_SLOTS records (a status LED renders
 *  `show.look`, not `show.value`).
 *
 *  This used to count a bound `show.value` too. It is not rendered: the review
 *  would widen the floor by 9px to hold text that never draws, while separately
 *  warning that the same binding is inert. Mirrors `_has_caption` in
 *  openavc/ui/control_minimums.py. */
function hasCaption(el: UIElement): boolean {
  return !!(el.label ?? "").trim();
}

/** Resolve an authored internal: element wins, then theme, then the default. */
function scaledInternalPx(
  scale: ControlScalingInternal,
  el: UIElement,
  theme: ElementDefaults,
): number {
  const value =
    (el as unknown as Record<string, unknown>)[scale.property] ?? themeValue(scale, theme);
  return value === undefined || value === null
    ? scale.defaultPx
    : Number(value) * REM_BASE_PX;
}

/** What the theme says for a scaling part, when the part reads the theme at all. */
function themeValue(scale: ControlScalingInternal, theme: ElementDefaults): unknown {
  return scale.fromTheme && theme ? theme[scale.property] : undefined;
}

/** A sub-object off the element, or an empty one. Mirrors `_config`. */
function subObject(el: UIElement, name: string): Record<string, unknown> {
  const value = (el as unknown as Record<string, unknown>)[name];
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

/** An authored measurement out of `element.style`, in px (style is rem).
 *
 *  Mirrors `_style_px` in openavc/ui/control_minimums.py. */
function stylePx(el: UIElement, key: string): number | null {
  if (!key) return null;
  const value = subObject(el, "style")[key];
  return typeof value === "number" ? value * REM_BASE_PX : null;
}

/** Whether a ControlConditionalPart is drawn. Mirrors `part_is_present`
 *  in openavc/ui/control_minimums.py -- each case is the panel matrix's own
 *  test (its modules/matrix.js). */
function partIsPresent(when: string, el: UIElement): boolean {
  const config = subObject(el, "matrix_config");
  switch (when) {
    case "label":
      return hasCaption(el);
    case "presets":
      return Array.isArray(config.presets) && config.presets.length > 0;
    case "lock_column":
      // Opt-in, where it used to be on unless turned off: it cost every matrix
      // ever authored a whole column for a button that sent nothing (F10).
      return config.show_lock === true;
    case "mute_column":
      return (
        config.show_mute !== false &&
        !!(subObject(el, "bindings").do as Record<string, unknown> | undefined)?.mute_route
      );
    default:
      return false;
  }
}

/** How many columns and rows a wall of `count` tiles is drawn in.
 *
 *  Mirrors `tile_grid_shape` in openavc/ui/control_minimums.py and
 *  `matrixTileGridShape` in the panel's modules/matrix.js. All three agree or
 *  the floor stated here is for a shape the panel does not draw. */
export function matrixTileGridShape(
  count: number,
  orientation: string = "landscape",
): [number, number] {
  if (count <= 0) return [0, 0];
  const root = Math.max(1, Math.floor(Math.sqrt(count)));
  return orientation === "portrait"
    ? [root, Math.ceil(count / root)]
    : [Math.ceil(count / root), root];
}

/** Whether a count is said by listing the things counted: a list of entries, or
 *  a generator object standing for one. A matrix says its sources and
 *  destinations that way -- the entries themselves, written out or generated. */
function saysCountByListing(raw: unknown): boolean {
  return !!raw && typeof raw === "object";
}

/** The count a config says, either way it says one: a number outright, or a list
 *  (or generator) as long as it resolves to. Anything else is the part's default. */
function itemCount(config: Record<string, unknown>, key: string, fallback: number): number {
  const raw = config[key];
  if (saysCountByListing(raw)) return matrixAxisCount(config, key);
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? Math.trunc(value) : fallback;
}

/** How many of this repeated part the element draws along its axis. Mirrors
 *  `_count` in openavc/ui/control_minimums.py.
 *
 *  The layout decides how the count lands on an axis: usually one part per
 *  item, but a tile wall's one list fills BOTH axes -- which is why the
 *  orientation reaches this far down. The same eight destinations are four
 *  columns of two on a landscape panel and two columns of four on a portrait
 *  one. */
function repeatedCount(
  r: ControlRepeatedInternal,
  el: UIElement,
  orientation: string = "landscape",
): number {
  const count = itemCount(subObject(el, r.countIn), r.countKey, r.defaultCount);
  if (r.layout === "linear") return count;
  const [columns, rows] = matrixTileGridShape(count, orientation);
  return r.layout === "grid_columns" ? columns : rows;
}

/** The rule this element is measured against, style variant resolved.
 *
 *  Mirrors `rule_for` in openavc/ui/control_minimums.py: the matrix's floor is
 *  a different function for `crosspoint`, `list` and `tiles`, and the rule in
 *  the table IS the default (crosspoint), exactly as the panel's createMatrix
 *  reads it. */
function ruleFor(el: UIElement): ControlMinimumRule | undefined {
  const rule = own(CONTROL_MINIMUMS, el.type);
  if (!rule || !rule.styleProperty) return rule;
  const style = String(
    (el as unknown as Record<string, unknown>)[rule.styleProperty] ?? "",
  );
  return own(rule.styles, style) ?? rule;
}

/** Add `px` to the floor along one axis. */
function grow(box: ControlMinimumBox, axis: "width" | "height", px: number): void {
  if (axis === "width") box.widthPx += px;
  else box.heightPx += px;
}

/** The parts a rule declares at a fixed size, less the ones it repeats: a
 *  repeated part's declared cell gives way to the cell it resolves to. */
function declaredInternals(rule: ControlMinimumRule): ResolvedInternal[] {
  const repeatedParts = new Set(rule.repeated.map((r) => r.part));
  return rule.internals
    .filter((i) => !repeatedParts.has(i.part))
    .map((i) => ({ part: i.part, widthPx: i.widthPx, heightPx: i.heightPx }));
}

/**
 * The repeated parts: each adds its count of cells along its axis, and the
 * cell it resolves to is added to the internals, so a warning blames the size
 * this matrix actually draws. A crosspoint declares the same part on both axes,
 * so the two are merged into one internal rather than reported twice.
 */
function addRepeatedParts(
  box: ControlMinimumBox,
  rule: ControlMinimumRule,
  el: UIElement,
  orientation: string,
): void {
  const resolved = new Map<string, ResolvedInternal>();
  for (const r of rule.repeated) {
    const size = stylePx(el, r.sizeProperty) ?? r.sizePx;
    grow(box, r.axis, repeatedCount(r, el, orientation) * (size + r.gapPx));
    const part = resolved.get(r.part) ?? { part: r.part, widthPx: null, heightPx: null };
    if (r.axis === "width") part.widthPx = size;
    else part.heightPx = size;
    resolved.set(r.part, part);
  }
  box.internals.push(...resolved.values());
}

/** The parts drawn only when the element asks for them (a caption, presets, a
 *  lock or a mute column). */
function addConditionalParts(box: ControlMinimumBox, rule: ControlMinimumRule, el: UIElement): void {
  for (const c of rule.conditionals) {
    if (partIsPresent(c.when, el)) grow(box, c.axis, c.sizePx);
  }
}

/** The one part that grows with an authored size (a slider's thumb, a list's item). */
function addScalingPart(
  box: ControlMinimumBox,
  scale: ControlScalingInternal,
  el: UIElement,
  theme: ElementDefaults,
): void {
  const size = scaledInternalPx(scale, el, theme);
  box.widthPx += scale.widthCoefficient * size;
  box.heightPx += scale.heightCoefficient * size;
  box.internals.push({
    part: scale.part,
    widthPx: scale.widthCoefficient ? size : null,
    heightPx: scale.heightCoefficient ? size : null,
  });
}

/**
 * The smallest box this element can be drawn in, or null when unbounded.
 *
 * Null means the type has no fixed internals at all -- a button, a label, an
 * image. Those are limited by their text, which is unbounded and theme
 * dependent, and is therefore not a minimum box.
 */
export function controlMinimumBox(
  el: UIElement,
  theme?: ElementDefaults,
  orientation: string = "landscape",
): ControlMinimumBox | null {
  const rule = ruleFor(el);
  if (!rule) return null;
  const box: ControlMinimumBox = {
    widthPx: rule.baseWidthPx,
    heightPx: rule.baseHeightPx,
    internals: declaredInternals(rule),
  };
  addRepeatedParts(box, rule, el, orientation);
  addConditionalParts(box, rule, el);
  if (rule.scalesWith) addScalingPart(box, rule.scalesWith, el, theme);
  if (rule.captionWidthBonusPx && hasCaption(el)) box.widthPx += rule.captionWidthBonusPx;
  return box;
}

/**
 * The same floor as a percentage of the box the element actually sits in.
 *
 * This is the form an authoring surface needs: geometry is percentages, so a
 * minimum in pixels only becomes actionable once it is divided by the parent.
 */
export function controlMinimumPercent(
  el: UIElement,
  parentPx: { width: number; height: number },
  theme?: ElementDefaults,
  orientation: string = "landscape",
): { w: number; h: number } | null {
  const box = controlMinimumBox(el, theme, orientation);
  if (!box) return null;
  return {
    w: (box.widthPx / parentPx.width) * 100,
    h: (box.heightPx / parentPx.height) * 100,
  };
}

function blame(internals: ResolvedInternal[], axis: "width" | "height"): string {
  const parts = internals
    .map((i) => ({ part: i.part, size: axis === "width" ? i.widthPx : i.heightPx }))
    .filter((i) => !!i.size)
    .map((i) => `${i.part} is ${fixed(i.size as number, 0)}px`);
  return parts.length ? ` (${parts.join(", ")})` : "";
}

/** One axis a box is drawn short of its floor on: what it needs, and what it has. */
export interface ShortAxis {
  axis: "width" | "height";
  wants: number;
  has: number;
}

/**
 * Each axis a box this size is drawn short of the floor on, width first.
 *
 * PIXEL_SLACK of slack, so a box that rounds to its floor is not short. The
 * reasons, the container blame and the remedy all read the shortfall from here.
 * Mirrors `MinimumBox.short_axes` in control_minimums.py.
 */
export function shortAxes(box: ControlMinimumBox, boxPx: { width: number; height: number }): ShortAxis[] {
  const drawn: ShortAxis[] = [
    { axis: "width", wants: box.widthPx, has: boxPx.width },
    { axis: "height", wants: box.heightPx, has: boxPx.height },
  ];
  return drawn.filter(({ wants, has }) => has + PIXEL_SLACK < wants);
}

/** Why this box is too small, per axis, with the numbers in it. Empty when it fits. */
export function starvedReasons(
  box: ControlMinimumBox,
  widthPx: number,
  heightPx: number,
): string[] {
  return shortAxes(box, { width: widthPx, height: heightPx }).map(
    ({ axis, wants, has }) =>
      `${fixed(has, 0)}px ${axis === "width" ? "wide" : "tall"}, needs ${fixed(wants, 0)}px` +
      blame(box.internals, axis),
  );
}
