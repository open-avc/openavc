/**
 * The page review: what a page will actually draw wrong.
 *
 * The mirror of openavc/ui/page_review.py, which runs at the AI write door. The
 * two ask the same questions of the same measured numbers and answer in the
 * same words, down to the byte -- tests/test_ui_review_parity.py pushes a
 * project through both and fails on any difference. That matters because a
 * human starves a status light by dragging exactly as easily as the AI does by
 * sizing, and until then only one of them was ever told.
 *
 * Every message carries the failure twice: in reference pixels, and in the
 * percentage of the element's REAL container to write instead. The second
 * number is the one that was missing. An author edits a percentage of a parent,
 * so "needs 29px" leaves them holding the same arithmetic that produced the
 * problem -- 3% of 1280 is 38px, of which 20 is a dot that does not shrink.
 *
 * This module walks a page, each of its arrangements and a master element
 * through the checks, the twin of "One element, wherever it is drawn" and "The
 * whole page" in page_review.py; the checks are the review*.ts modules beside
 * it, one per section of that file, and what they say is reviewFinding.ts.
 */

import type {
  Layout,
  MasterElement,
  Placement,
  ProjectConfig,
  UIElement,
  UIPage,
} from "../../api/types";
import { isPlainObject, own } from "./authoredValues";
import type { ElementDefaults } from "./controlMinimums";
import {
  PAGE_BOX,
  absolutePlacements,
  containerOf,
  containersAbove,
  layoutById,
  primaryLayout,
  resolveHidden,
  resolvePlacements,
} from "./pageGeometry";
import { doActionFindings } from "./reviewActions";
import { bindingFindings, customPageFindings, elementTypeFinding } from "./reviewBindings";
import type { Container, ReviewFinding, Seat } from "./reviewFinding";
import {
  boxFindings,
  overhangFinding,
  overlapExtent,
  overlapFindings,
  type OverlapPair,
} from "./reviewGeometry";
import { buriedMasterFindings, noBoxFinding, type DrawnPage } from "./reviewMasters";
import { matrixFindings } from "./reviewMatrix";
import { contentFindings, keypadFindings, propertyFindings } from "./reviewProperties";
import { mutuallyExclusive } from "./reviewStacking";
import { referenceBox } from "./touchTarget";

/** The page-space box an element's percentages are measured against, in px. */
function parentBoxPx(
  parentId: string | null,
  absolute: Record<string, Placement>,
  reference: { width: number; height: number },
): { width: number; height: number } {
  const box = (parentId ? own(absolute, parentId) : undefined) ?? PAGE_BOX;
  return {
    width: (box.w / 100) * reference.width,
    height: (box.h / 100) * reference.height,
  };
}

function withWhere(finding: ReviewFinding, where: string): ReviewFinding {
  if (!where) return finding;
  return {
    ...finding,
    message: finding.message.replace(/\.+$/, "") + `${where}.`,
  };
}

export interface ReviewOptions {
  /** The project's own `theme_overrides.element_defaults.slider`, which is the
   *  only theme value any control minimum moves with. */
  theme?: ElementDefaults;
  /** Only these elements answer. Everything, when omitted. */
  touched?: Set<string>;
  /** Review only this arrangement, for a surface that is showing one. Every
   *  arrangement, when omitted -- which is what the AI door does, because a
   *  portrait variant can starve a control the primary leaves fine. */
  layoutId?: string | null;
  /** The project's master elements, which the page does not carry and cannot
   *  reach: they live on `ui.master_elements` and draw underneath every page
   *  they are listed on. Passing them is what lets a control be told it is
   *  sitting on the nav bar. Omitted, that one check simply does not run. */
  masters?: MasterElement[];
}

/**
 * The checks an element gets on its own, before any box is measured, in the
 * order they report. A page and a master element both run this list, so a new
 * check is one entry here.
 */
const ELEMENT_CHECKS: readonly ((el: UIElement) => ReviewFinding[])[] = [
  bindingFindings,
  doActionFindings,
  propertyFindings,
  contentFindings,
  keypadFindings,
  matrixFindings,
];

/** Its type first (an unknown one draws nothing), then each of ELEMENT_CHECKS. */
function elementFindings(el: UIElement): ReviewFinding[] {
  const typeFinding = elementTypeFinding(el);
  return [...(typeFinding ? [typeFinding] : []), ...ELEMENT_CHECKS.flatMap((check) => check(el))];
}

/**
 * Everything a page will draw wrong, across every arrangement.
 *
 * Ordered and de-duplicated exactly the way the Python side is: bindings are a
 * property of the element and answered once; geometry is answered per
 * arrangement with the primary first, so its phrasing wins and a variant only
 * speaks up about something the primary got right.
 */
export function reviewPage(page: UIPage, options: ReviewOptions = {}): ReviewFinding[] {
  const { theme, touched, layoutId, masters } = options;
  const inScope = (id: string) => (touched ? touched.has(id) : true);
  const findings: ReviewFinding[] = [];

  // A page drawing its own markup draws none of its controls, so everything
  // below is about pixels nobody will see. Said once, and nothing else. Not
  // scoped by `touched`: dropping a control onto such a page is exactly when
  // somebody needs to hear it.
  const custom = customPageFindings(page);
  if (custom.length && custom[0].kind === "custom_page_elements_not_drawn") return custom;
  findings.push(...custom);

  for (const el of page.elements) {
    if (inScope(el.id)) findings.push(...elementFindings(el));
  }

  const layouts = page.layouts ?? [];
  if (!layouts.length) return findings;
  // One arrangement when the caller is looking at one (an unknown id resolves
  // to the primary, the way every other read of a layout id does).
  const chosen = layoutId === undefined ? layouts : [layoutById(page, layoutId) ?? layouts[0]];
  findings.push(...arrangementFindings(page, chosen, { theme, inScope, masters: masters ?? [] }));
  return findings;
}

/** What one review was asked about: which elements, under which theme, over which masters. */
interface ReviewScope {
  theme?: ElementDefaults;
  inScope: (id: string) => boolean;
  masters: MasterElement[];
}

/**
 * One arrangement of a page as the panel resolves it.
 *
 * `absolute` is where each element is drawn, in page percentages with container
 * nesting flattened; `ownBoxes` is the box each one stores, a percentage of its
 * container. Every percentage resolves against `reference`, which is the whole
 * of what portrait changes: the same screen, turned.
 *
 * The twin of `_Arrangement` in page_review.py, whose methods are the free
 * functions here: `resolveArrangement` is `_Arrangement.of`, and `parentIn`,
 * `parentNameIn`, `ancestorsIn`, `seatIn` and `isDrawn` are `parent_of`,
 * `parent_name`, `ancestors_of`, `seat` and `is_drawn`.
 */
interface Arrangement {
  page: UIPage;
  byId: Map<string, UIElement>;
  absolute: Record<string, Placement>;
  ownBoxes: Record<string, Placement>;
  hidden: Set<string>;
  reference: { width: number; height: number };
  orientation: string;
  isPrimary: boolean;
}

function resolveArrangement(page: UIPage, layout: Layout, primaryId: string | undefined): Arrangement {
  const orientation = layout.orientation ?? "landscape";
  return {
    page,
    byId: new Map(page.elements.map((e) => [e.id, e])),
    absolute: absolutePlacements(page, layout.id),
    ownBoxes: resolvePlacements(page, layout.id),
    hidden: resolveHidden(page, layout.id),
    reference: referenceBox(orientation),
    orientation,
    isPrimary: layout.id === primaryId,
  };
}

/** Shown in this arrangement and given a box in it. */
function isDrawn(arr: Arrangement, id: string): boolean {
  return !arr.hidden.has(id) && !!own(arr.absolute, id);
}

function parentIn(arr: Arrangement, id: string): string | null {
  return containerOf(arr.byId.get(id), arr.byId);
}

function parentNameIn(arr: Arrangement, id: string): string {
  const parent = parentIn(arr, id);
  return parent ? `its container '${parent}'` : "the page";
}

/** Every container above this element, innermost first.
 *
 *  Only as far as the boxes actually resolve -- a container the arrangement
 *  never positions ends the chain rather than guessing a size for it. */
function ancestorsIn(arr: Arrangement, id: string): Container[] {
  const chain: Container[] = [];
  for (const container of containersAbove(id, arr.byId)) {
    const box = own(arr.absolute, container);
    if (!box) break;
    chain.push({
      label: `'${container}'`,
      widthPx: (box.w / 100) * arr.reference.width,
      heightPx: (box.h / 100) * arr.reference.height,
    });
  }
  return chain;
}

/**
 * Every geometry finding across these arrangements, each said once.
 *
 * Primary first: it is the arrangement every variant inherits from, so its
 * phrasing ("...") beats the variant's ("... in the portrait arrangement").
 */
function arrangementFindings(page: UIPage, layouts: Layout[], scope: ReviewScope): ReviewFinding[] {
  const primaryId = primaryLayout(page)?.id;
  const ordered = [...layouts].sort(
    (a, b) => Number(a.id !== primaryId) - Number(b.id !== primaryId),
  );
  const findings: ReviewFinding[] = [];
  const said = new Set<string>();
  for (const layout of ordered) {
    const arr = resolveArrangement(page, layout, primaryId);
    const where = arr.isPrimary ? "" : ` in the '${layout.id}' arrangement`;
    findings.push(...unsaid(layoutFindings(arr, scope), said).map((f) => withWhere(f, where)));
  }
  return findings;
}

/** The findings whose `key` has not been said yet, each marked as said. */
function unsaid(findings: ReviewFinding[], said: Set<string>): ReviewFinding[] {
  const fresh: ReviewFinding[] = [];
  for (const finding of findings) {
    if (said.has(finding.key)) continue;
    said.add(finding.key);
    fresh.push(finding);
  }
  return fresh;
}

/** Every geometry finding for one arrangement: each box, then siblings, then masters. */
function layoutFindings(arr: Arrangement, scope: ReviewScope): ReviewFinding[] {
  return [
    ...boxFindingsIn(arr, scope),
    ...siblingOverlaps(arr, scope),
    ...buriedMastersIn(arr, scope),
  ];
}

function boxFindingsIn(arr: Arrangement, scope: ReviewScope): ReviewFinding[] {
  const findings: ReviewFinding[] = [];
  for (const el of arr.page.elements) {
    if (!scope.inScope(el.id) || arr.hidden.has(el.id)) continue;
    if (own(arr.absolute, el.id)) {
      findings.push(...placedBoxFindings(arr, el, scope.theme));
    } else if (arr.isPrimary) {
      // A variant legitimately carries no delta for an element -- it inherits
      // one. Only the primary having no box means it has none at all.
      findings.push(noBoxFinding(el, parentNameIn(arr, el.id)));
    }
  }
  return findings;
}

/** Where this element is drawn in this arrangement. It must have a box here. */
function seatIn(arr: Arrangement, el: UIElement): Seat {
  const box = own(arr.absolute, el.id) as Placement;
  return {
    boxPx: {
      width: (box.w / 100) * arr.reference.width,
      height: (box.h / 100) * arr.reference.height,
    },
    parentPx: parentBoxPx(parentIn(arr, el.id), arr.absolute, arr.reference),
    parentName: parentNameIn(arr, el.id),
    ancestors: ancestorsIn(arr, el.id),
    orientation: arr.orientation,
  };
}

function placedBoxFindings(
  arr: Arrangement,
  el: UIElement,
  theme: ElementDefaults | undefined,
): ReviewFinding[] {
  const seat = seatIn(arr, el);
  const findings = boxFindings(el, seat, theme);
  const stored = own(arr.ownBoxes, el.id);
  const overhang = stored ? overhangFinding(el, stored, seat.parentName, seat.parentPx) : null;
  return overhang ? [...findings, overhang] : findings;
}

/**
 * Siblings, in the space they share.
 *
 * An element that also hangs out of its container is NOT excused here, which
 * was tried and was wrong: two boxes can overlap in the middle of a container
 * while one of them separately runs off the edge, and fixing the overflow does
 * not touch the collision. Narrow a box from 80% to 70% and it sits inside its
 * parent while still lying on the neighbour it started at. Reporting both is
 * right; the volume that suppression was aimed at is what overlapFindings
 * collapses.
 */
function siblingOverlaps(arr: Arrangement, scope: ReviewScope): ReviewFinding[] {
  const types = new Map(arr.page.elements.map((e) => [e.id, e.type]));
  const byParent = new Map<string | null, string[]>();
  for (const el of arr.page.elements) {
    if (!isDrawn(arr, el.id)) continue;
    const parent = parentIn(arr, el.id);
    const kids = byParent.get(parent);
    if (kids) kids.push(el.id);
    else byParent.set(parent, [el.id]);
  }
  const findings: ReviewFinding[] = [];
  for (const [parent, kids] of byParent) {
    kids.sort();
    findings.push(
      ...overlapFindings(collidingPairs(arr, kids, scope), types, parent ? `'${parent}'` : "the page"),
    );
  }
  return findings;
}

/** Each pair of siblings whose boxes overlap, at least one of them in scope. */
function collidingPairs(arr: Arrangement, kids: string[], scope: ReviewScope): OverlapPair[] {
  const pairs: OverlapPair[] = [];
  kids.forEach((aId, i) => {
    for (const bId of kids.slice(i + 1)) {
      if (!scope.inScope(aId) && !scope.inScope(bId)) continue;
      if (mutuallyExclusive(arr.byId.get(aId) as UIElement, arr.byId.get(bId) as UIElement)) continue;
      const extent = overlapExtent(
        own(arr.absolute, aId) as Placement,
        own(arr.absolute, bId) as Placement,
        arr.reference,
      );
      if (extent) pairs.push({ aId, bId, ...extent });
    }
  });
  return pairs;
}

/** ...and the masters under all of them, which are nobody's sibling. */
function buriedMastersIn(arr: Arrangement, scope: ReviewScope): ReviewFinding[] {
  const boxes = new Map<string, Placement>();
  for (const el of arr.page.elements) {
    if (isDrawn(arr, el.id)) boxes.set(el.id, own(arr.absolute, el.id) as Placement);
  }
  const drawn: DrawnPage = {
    pageId: arr.page.id,
    orientation: arr.orientation,
    boxes,
    elements: arr.byId,
    parents: new Map(arr.page.elements.map((el) => [el.id, parentIn(arr, el.id)])),
  };
  return buriedMasterFindings(drawn, scope.masters, scope.inScope);
}

/**
 * The same checks a master element can be given.
 *
 * A master borrows no page's layout -- its box is a percentage of the viewport,
 * keyed by orientation -- so it has no container to hang out of and no sibling in
 * any layout to collide with. What is left is whether it holds its own contents,
 * whether a finger can hit it, and whether its bindings are read.
 *
 * It CAN be collided with, by the controls on the pages it draws under, and that
 * is answered on the page rather than here: the finding belongs on the control
 * that moved (`buriedMasterFindings`), and a master reviewed on its own has no
 * page in hand to look at.
 */
export function reviewMasterElement(
  master: MasterElement,
  theme?: ElementDefaults,
): ReviewFinding[] {
  const findings = elementFindings(master);
  const placements = master.placements;
  if (!placements || typeof placements !== "object") return findings;
  const said = new Set<string>();
  for (const [orientation, box] of Object.entries(placements)) {
    const seat = masterSeat(orientation, box);
    if (!seat) continue;
    findings.push(
      ...unsaid(boxFindings(master, seat, theme), said).map((f) =>
        withWhere(f, ` in the ${orientation} arrangement`),
      ),
    );
  }
  return findings;
}

/**
 * A master's box for one orientation, or null when it is not a finite size.
 *
 * A master carries its own orientation-keyed boxes, so the key IS the way round
 * this one is drawn -- the portrait box is measured against a portrait screen
 * even on a project whose pages are all landscape. Its only container is the
 * screen; a missing `w` or `h` is the whole of it.
 */
function masterSeat(orientation: string, box: { w?: number; h?: number } | undefined): Seat | null {
  const reference = referenceBox(orientation);
  const boxPx = {
    width: ((box?.w ?? 100) / 100) * reference.width,
    height: ((box?.h ?? 100) / 100) * reference.height,
  };
  if (!Number.isFinite(boxPx.width) || !Number.isFinite(boxPx.height)) return null;
  return { boxPx, parentPx: reference, parentName: "the page", ancestors: [], orientation };
}

/**
 * The review folded into one tooltip per element, for the canvas badges.
 *
 * Everything an element is guilty of, not just the first thing -- a control can
 * be starved AND overlapping AND out of its container, and picking one to show
 * would hide the other two behind a fix for the one.
 */
export function reviewWarningsByElement(
  page: UIPage,
  layoutId: string | null = null,
  theme?: ElementDefaults,
  masters: MasterElement[] = [],
): Map<string, string> {
  const byElement = new Map<string, string[]>();
  for (const finding of reviewPage(page, { theme, layoutId, masters })) {
    const lines = byElement.get(finding.elementId);
    if (lines) lines.push(finding.message);
    else byElement.set(finding.elementId, [finding.message]);
  }
  return new Map([...byElement].map(([id, lines]) => [id, lines.join("\n\n")]));
}

/**
 * The theme values a control minimum can move with, out of a project.
 *
 * Only a slider's thumb moves, and only through `element_defaults`. A custom
 * theme FILE that changed it is not seen here, but every built-in theme ships
 * the 44px default, so the project override is the case that can differ.
 */
export function sliderThemeDefaults(project: ProjectConfig | null | undefined): ElementDefaults {
  const overrides = (project?.ui?.settings?.theme_overrides ?? {}) as Record<string, unknown>;
  const defaults = (overrides.element_defaults ?? {}) as Record<string, unknown>;
  const slider = defaults.slider;
  return isPlainObject(slider) ? slider : null;
}
