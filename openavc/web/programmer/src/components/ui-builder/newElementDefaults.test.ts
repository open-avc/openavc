import { describe, it, expect } from "vitest";
import { ELEMENT_TYPES, createDefaultElement, reviewPage } from "./uiBuilderHelpers";
import type { UIPage } from "../../api/types";

// A control fresh from the palette has had no authoring decision made yet, so
// nothing the Builder wrote into it may be something the page review then
// complains about. An integrator who sees the warning badge on every new
// control learns to ignore it.

function reviewedAlone(type: string) {
  const element = createDefaultElement(type, new Set());
  const page: UIPage = { id: "main", name: "Main", elements: [element] } as UIPage;
  return { element, findings: reviewPage(page) };
}

const PALETTE = [
  ...ELEMENT_TYPES.map((t) => t.type),
  "plugin:acme_tiles:status_tile",
];

describe("a new element from the palette", () => {
  it.each(PALETTE)("%s sets no property it does not draw", (type) => {
    const { findings } = reviewedAlone(type);
    expect(findings.filter((f) => f.kind === "property_not_rendered")).toEqual([]);
  });

  it("a custom control and a plugin element carry no label", () => {
    expect(reviewedAlone("custom").element.label).toBeUndefined();
    expect(reviewedAlone("plugin:acme_tiles:status_tile").element.label).toBeUndefined();
  });

  it("a type that draws its label still gets one", () => {
    expect(reviewedAlone("button").element.label).toBe("Button");
    expect(reviewedAlone("slider").element.label).toBe("Slider");
  });
});
