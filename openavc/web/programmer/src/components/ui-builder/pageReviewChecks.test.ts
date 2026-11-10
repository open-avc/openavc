import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { resolve } from "path";
import { doActionFindings } from "./reviewActions";
import { bindingFindings } from "./reviewBindings";
import {
  degenerateFinding,
  overlapExtent,
  overlapFindings,
  starvationFinding,
  styleFinding,
  touchFinding,
} from "./reviewGeometry";
import { buriedMasterFindings, masterBox } from "./reviewMasters";
import { matrixFindings } from "./reviewMatrix";
import { contentFindings, keypadFindings, propertyFindings } from "./reviewProperties";
import { mutuallyExclusive } from "./reviewStacking";
import type { ElementDefaults } from "./controlMinimums";
import type { Seat } from "./reviewFinding";
import type { DrawnPage } from "./reviewMasters";
import type { MasterElement, Placement, UIElement } from "../../api/types";

// The Builder's single-element review checks, held sentence for sentence to the
// AI side's: a matrix's lists, a style measurement against its box, a property
// its type's renderer never reads, the box a master draws in and the controls
// drawn over it, two visibility conditions that can never both hold, a control
// with nothing to draw, a keypad that never sends or sends early, a box too
// small for what it draws or too small to draw at all, a binding action nothing
// runs, siblings drawn over each other, a control too small for a finger, and a
// per-state icon or picture on a type that does not draw one.
// tests/fixtures/page_review_check_cases.json holds each case and what the
// review says about it; tests/test_ui_page_review_checks_pinned.py holds
// openavc/ui/page_review.py to the same file.

type Said = { element_id: string; kind: string; message: string; key: string };
type Case = { name: string; expected: Said[] } & Record<string, unknown>;

const corpus = JSON.parse(
  readFileSync(
    resolve(process.cwd(), "../../../tests/fixtures/page_review_check_cases.json"),
    "utf-8",
  ),
) as Record<string, Case[]>;

const said = (findings: { elementId: string; kind: string; message: string; key: string }[]) =>
  findings.map((f) => ({ element_id: f.elementId, kind: f.kind, message: f.message, key: f.key }));

const toMap = <T,>(record: Record<string, T>) => new Map(Object.entries(record));

describe("matrixFindings", () => {
  it.each(corpus.matrix_findings.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(said(matrixFindings(c.element as UIElement))).toEqual(c.expected);
  });
});

// The Builder's own reading of a matrix_config written as a list, which the AI
// side calls not configured whatever it holds: an empty list is not
// configured here too, and a list with entries is read as a config whose keys
// are its positions. Recorded as the code reads it today.
describe("matrixFindings: a matrix_config written as a list", () => {
  const matrix = (config: unknown) =>
    ({ id: "mx", type: "matrix", matrix_config: config }) as unknown as UIElement;

  it("an empty list is not configured", () => {
    expect(matrixFindings(matrix([])).map((f) => f.key)).toEqual(["matrix_not_configured|mx"]);
  });

  it("a list with entries is checked as a config keyed by position", () => {
    expect(matrixFindings(matrix([1])).map((f) => f.key)).toEqual([
      "matrix_config_unread|mx|0",
      "matrix_default_size|mx|sources",
      "matrix_default_size|mx|destinations",
    ]);
  });
});

describe("styleFinding", () => {
  it.each(corpus.style_finding.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const [width, height] = c.box_px as [number, number];
    const found = styleFinding(c.element as UIElement, { width, height });
    expect(said(found ? [found] : [])).toEqual(c.expected);
  });
});

describe("propertyFindings", () => {
  it.each(corpus.property_findings.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(said(propertyFindings(c.element as UIElement))).toEqual(c.expected);
  });
});

describe("masterBox", () => {
  it.each(corpus.master_box.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(masterBox(c.master as MasterElement, c.orientation as string)).toEqual(c.expected);
  });
});

describe("buriedMasterFindings", () => {
  it.each(corpus.buried_master_findings.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const scope = c.in_scope as string[] | null;
    const drawn: DrawnPage = {
      pageId: c.page_id as string,
      orientation: c.orientation as string,
      boxes: toMap(c.boxes as Record<string, Placement>),
      elements: toMap(c.dumps as Record<string, UIElement>),
      parents: toMap(c.parents as Record<string, string | null>),
    };
    const findings = buriedMasterFindings(
      drawn,
      c.masters as MasterElement[],
      scope === null ? () => true : (id) => scope.includes(id),
    );
    expect(said(findings)).toEqual(c.expected);
  });
});

describe("mutuallyExclusive", () => {
  it.each(corpus.mutually_exclusive.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(mutuallyExclusive(c.a as UIElement, c.b as UIElement)).toBe(c.expected);
  });
});

describe("contentFindings", () => {
  it.each(corpus.content_findings.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(said(contentFindings(c.element as UIElement))).toEqual(c.expected);
  });
});

describe("keypadFindings", () => {
  it.each(corpus.keypad_findings.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(said(keypadFindings(c.element as UIElement))).toEqual(c.expected);
  });
});

type RawSeat = {
  box_px: [number, number];
  parent_px: [number, number];
  parent_name: string;
  ancestors: { label: string; width_px: number; height_px: number }[];
  orientation: string;
};

const toSeat = (raw: RawSeat): Seat => ({
  boxPx: { width: raw.box_px[0], height: raw.box_px[1] },
  parentPx: { width: raw.parent_px[0], height: raw.parent_px[1] },
  parentName: raw.parent_name,
  ancestors: raw.ancestors.map((a) => ({ label: a.label, widthPx: a.width_px, heightPx: a.height_px })),
  orientation: raw.orientation,
});

describe("starvationFinding", () => {
  it.each(corpus.starvation_finding.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const found = starvationFinding(
      c.element as UIElement, toSeat(c.seat as RawSeat), c.theme as ElementDefaults,
    );
    expect(said(found ? [found] : [])).toEqual(c.expected);
  });
});

describe("degenerateFinding", () => {
  it.each(corpus.degenerate_finding.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const found = degenerateFinding(c.element as UIElement, toSeat(c.seat as RawSeat));
    expect(said(found ? [found] : [])).toEqual(c.expected);
  });
});

describe("doActionFindings", () => {
  it.each(corpus.do_action_findings.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(said(doActionFindings(c.element as UIElement))).toEqual(c.expected);
  });
});

type RawPair = [string, string, number, number, number];

describe("overlapFindings", () => {
  it.each(corpus.overlap_findings.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const pairs = (c.pairs as RawPair[]).map(([aId, bId, oxPx, oyPx, share]) => ({
      aId, bId, oxPx, oyPx, share,
    }));
    const findings = overlapFindings(
      pairs, toMap(c.types as Record<string, string>), c.parent_name as string,
    );
    expect(said(findings)).toEqual(c.expected);
  });
});

describe("overlapExtent", () => {
  type Extent = { ox_px: number; oy_px: number; share: number } | null;
  it.each(corpus.overlap_extent.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const ref = c.reference as [number, number] | null;
    const found = ref
      ? overlapExtent(c.a as Placement, c.b as Placement, { width: ref[0], height: ref[1] })
      : overlapExtent(c.a as Placement, c.b as Placement);
    const said: Extent = found ? { ox_px: found.oxPx, oy_px: found.oyPx, share: found.share } : null;
    expect(said).toEqual(c.expected as unknown as Extent);
  });
});

describe("touchFinding", () => {
  it.each(corpus.touch_finding.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const [width, height] = c.box_px as [number, number];
    const found = touchFinding(c.element as UIElement, { width, height });
    expect(said(found ? [found] : [])).toEqual(c.expected);
  });
});

describe("bindingFindings: which states of a look name a field", () => {
  it.each(corpus.look_states_naming.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(said(bindingFindings(c.element as UIElement))).toEqual(c.expected);
  });
});

// The one shape the two sides answer differently, so it is pinned per side.
// `states` is a map of value to appearance. Written as a list, this side reads
// the list's entries as states and says nothing; the AI side reads no words
// from it and says the label is empty.
it("reads words from a look whose states are a list", () => {
  const chip = {
    id: "chip", type: "label",
    bindings: { show: { look: { states: [{ label: "On" }] } } },
  } as unknown as UIElement;
  expect(contentFindings(chip)).toEqual([]);
});

// Two more shapes the sides answer differently, pinned per side. A Send Delay
// written as true is no number here (the AI side counts it as one); an operator
// named after a property every object inherits is read as a bound here (the AI
// side, like the panel, does not know it).
it("reads a Send Delay written as true as no delay", () => {
  const pad = {
    id: "pin", type: "keypad", auto_send: true, auto_send_delay_ms: true,
  } as unknown as UIElement;
  expect(keypadFindings(pad)).toEqual([]);
});

it("reads an operator named constructor as a bound", () => {
  const shown = (when: unknown) =>
    ({ id: "x", type: "group", bindings: { show: { visible_when: when } } }) as unknown as UIElement;
  expect(mutuallyExclusive(
    shown({ key: "var.k", operator: "constructor", value: 5 }),
    shown({ key: "var.k", operator: "lt", value: 3 }),
  )).toBe(true);
});
