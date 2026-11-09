import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { resolve } from "path";
import {
  buriedMasterFindings,
  masterBox,
  matrixFindings,
  propertyFindings,
  styleFinding,
} from "./uiBuilderHelpers";
import type { MasterElement, Placement, UIElement } from "../../api/types";

// The Builder's single-element review checks, held sentence for sentence to the
// AI side's: a matrix's lists, a style measurement against its box, a property
// its type's renderer never reads, the box a master draws in and the controls
// drawn over it. tests/fixtures/page_review_check_cases.json holds each case
// and what the review says about it; tests/test_ui_page_review_checks_pinned.py
// holds openavc/ui/page_review.py to the same file.

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
