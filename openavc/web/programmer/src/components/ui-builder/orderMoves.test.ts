import { describe, it, expect } from "vitest";
import { reorderPage, swapElementsInOrder } from "./uiBuilderHelpers";
import type { UIElement, UIPage } from "../../api/types";

// The two moves that trade places in a list: a control with the neighbour the
// Outline names (the element list is the z-order), and a page with the one
// beside it in the tab strip. What each hands back when there is nothing to
// trade is pinned by reference: the same list, or the same page.

const page = (id: string, elementIds: string[] = []): UIPage =>
  ({ id, name: id, elements: elementIds.map((e) => ({ id: e }) as UIElement), layouts: [] }) as unknown as UIPage;

const ids = (p: UIPage) => p.elements.map((e) => e.id);

describe("swapElementsInOrder: a control trades places with a neighbour", () => {
  it("trades the two, wherever they are in the list", () => {
    const out = swapElementsInOrder([page("pg", ["a", "b", "c", "d"])], "pg", "d", "a");
    expect(ids(out[0])).toEqual(["d", "b", "c", "a"]);
  });

  it("leaves the stored page as it was", () => {
    const pages = [page("pg", ["a", "b"])];
    swapElementsInOrder(pages, "pg", "a", "b");
    expect(ids(pages[0])).toEqual(["a", "b"]);
  });

  it.each([
    ["the control is not on the page", "nope", "a"],
    ["the neighbour is not on the page", "a", "nope"],
    ["the neighbour is the control itself", "a", "a"],
  ])("hands back the same page when %s", (_name, elementId, neighbourId) => {
    const pages = [page("pg", ["a", "b"]), page("other", ["a", "b"])];
    const out = swapElementsInOrder(pages, "pg", elementId, neighbourId);
    expect(out).not.toBe(pages);
    expect(out[0]).toBe(pages[0]);
    expect(out[1]).toBe(pages[1]);
  });

  it("trades on the first of two controls that share an id", () => {
    const out = swapElementsInOrder([page("pg", ["a", "b", "a"])], "pg", "b", "a");
    expect(ids(out[0])).toEqual(["b", "a", "a"]);
  });
});

describe("reorderPage: a page trades places with the one beside it", () => {
  const pages = () => [page("one"), page("two"), page("three")];

  it.each([
    ["left", "two", ["two", "one", "three"]],
    ["right", "two", ["one", "three", "two"]],
    ["left", "three", ["one", "three", "two"]],
    ["right", "one", ["two", "one", "three"]],
  ] as const)("moves %s from %s", (direction, pageId, expected) => {
    const before = pages();
    const out = reorderPage(before, pageId, direction);
    expect(out.map((p) => p.id)).toEqual(expected);
    expect(before.map((p) => p.id)).toEqual(["one", "two", "three"]);
  });

  it.each([
    ["the first page moved left", "one", "left"],
    ["the last page moved right", "three", "right"],
    ["no such page, moved right", "nope", "right"],
    ["no such page, moved left", "nope", "left"],
  ] as const)("hands back the same list for %s", (_name, pageId, direction) => {
    const before = pages();
    expect(reorderPage(before, pageId, direction)).toBe(before);
  });

  it("does nothing to a lone page", () => {
    const before = [page("one")];
    expect(reorderPage(before, "one", "left")).toBe(before);
    expect(reorderPage(before, "one", "right")).toBe(before);
  });
});
