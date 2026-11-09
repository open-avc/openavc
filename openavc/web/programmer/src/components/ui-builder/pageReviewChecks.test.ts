import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { resolve } from "path";
import {
  buriedMasterFindings,
  contentFindings,
  degenerateFinding,
  keypadFindings,
  masterBox,
  matrixFindings,
  mutuallyExclusive,
  propertyFindings,
  starvationFinding,
  styleFinding,
} from "./uiBuilderHelpers";
import type { ElementDefaults, Seat } from "./uiBuilderHelpers";
import type { MasterElement, Placement, UIElement } from "../../api/types";

// The Builder's single-element review checks, held sentence for sentence to the
// AI side's: a matrix's lists, a style measurement against its box, a property
// its type's renderer never reads, the box a master draws in and the controls
// drawn over it, two visibility conditions that can never both hold, a control
// with nothing to draw, a keypad that never sends or sends early, and a box too
// small for what it draws or too small to draw at all.
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
    const findings = buriedMasterFindings(
      toMap(c.boxes as Record<string, Placement>),
      toMap(c.dumps as Record<string, UIElement>),
      toMap(c.parents as Record<string, string | null>),
      c.masters as MasterElement[],
      c.page_id as string,
      c.orientation as string,
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
