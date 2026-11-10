import { describe, it, expect } from "vitest";

// The page tools that act on a selection, aimed at a page that is not there:
// each hands back what it was given, and every page in it, untouched.

import {
  alignElements,
  distributeElements,
  duplicateElementInPage,
  duplicateElementsInPage,
  matchSizeElements,
  pasteIntoPage,
} from "./uiBuilderHelpers";
import type { UIElement, UIPage } from "../../api/types";

function onePage(): UIPage {
  return {
    id: "pg",
    name: "Pg",
    elements: [{ id: "a", type: "button" }, { id: "b", type: "button" }, { id: "c", type: "button" }] as UIElement[],
    layouts: [{
      id: "land",
      orientation: "landscape",
      primary: true,
      placements: { a: { x: 0, y: 0, w: 10, h: 10 }, b: { x: 20, y: 5, w: 10, h: 10 }, c: { x: 60, y: 9, w: 10, h: 10 } },
      hidden: [],
    }],
  } as unknown as UIPage;
}

const clipboard = { elements: [{ id: "a", type: "button" }] as UIElement[], placements: { a: { x: 0, y: 0, w: 10, h: 10 } } };

describe("a page that is not there", () => {
  it.each([
    ["duplicate one", (pages: UIPage[]) => duplicateElementInPage(pages, { pageId: "nope", layoutId: "land" }, "a", ["m"])],
    ["duplicate several", (pages: UIPage[]) => duplicateElementsInPage(pages, { pageId: "nope", elementIds: ["a", "b"], layoutId: "land" }, ["m"])],
    ["paste", (pages: UIPage[]) => pasteIntoPage(pages, { pageId: "nope", layoutId: "land" }, clipboard, ["m"])],
  ])("%s hands back the same pages", (_name, run) => {
    const pages = [onePage()];
    expect(run(pages)).toBe(pages);
  });

  it.each([
    ["align", (pages: UIPage[]) => alignElements(pages, { pageId: "nope", elementIds: ["a", "b"], layoutId: "land" }, "align-left")],
    ["distribute", (pages: UIPage[]) => distributeElements(pages, { pageId: "nope", elementIds: ["a", "b", "c"], layoutId: "land" }, "horizontal")],
    ["match size", (pages: UIPage[]) => matchSizeElements(pages, { pageId: "nope", elementIds: ["a", "b"], layoutId: "land" }, "match-both")],
  ])("%s hands back every page as it was", (_name, run) => {
    const pages = [onePage()];
    const out = run(pages);
    expect(out).toHaveLength(1);
    expect(out[0]).toBe(pages[0]);
  });
});

describe("a page that is there, with a control the selection names twice or not at all", () => {
  it("duplicate several copies a repeated id each time it is named and skips a missing one", () => {
    const out = duplicateElementsInPage([onePage()], { pageId: "pg", elementIds: ["a", "a", "ghost"], layoutId: null }, []);
    expect(out[0].elements.map((e) => e.id)).toEqual(["a", "b", "c", "button_1", "button_2"]);
  });

  it("duplicate one of a control that is not on the page hands back the same pages", () => {
    const pages = [onePage()];
    expect(duplicateElementInPage(pages, { pageId: "pg", layoutId: null }, "ghost", [])).toBe(pages);
  });
});
