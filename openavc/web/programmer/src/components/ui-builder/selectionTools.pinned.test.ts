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
    ["duplicate one", (pages: UIPage[]) => duplicateElementInPage(pages, "nope", "a", ["m"], "land")],
    ["duplicate several", (pages: UIPage[]) => duplicateElementsInPage(pages, "nope", ["a", "b"], ["m"], "land")],
    ["paste", (pages: UIPage[]) => pasteIntoPage(pages, "nope", clipboard, ["m"], "land")],
  ])("%s hands back the same pages", (_name, run) => {
    const pages = [onePage()];
    expect(run(pages)).toBe(pages);
  });

  it.each([
    ["align", (pages: UIPage[]) => alignElements(pages, "nope", ["a", "b"], "align-left", "land")],
    ["distribute", (pages: UIPage[]) => distributeElements(pages, "nope", ["a", "b", "c"], "horizontal", "land")],
    ["match size", (pages: UIPage[]) => matchSizeElements(pages, "nope", ["a", "b"], "match-both", "land")],
  ])("%s hands back every page as it was", (_name, run) => {
    const pages = [onePage()];
    const out = run(pages);
    expect(out).toHaveLength(1);
    expect(out[0]).toBe(pages[0]);
  });
});

describe("a page that is there, with a control the selection names twice or not at all", () => {
  it("duplicate several copies a repeated id each time it is named and skips a missing one", () => {
    const out = duplicateElementsInPage([onePage()], "pg", ["a", "a", "ghost"]);
    expect(out[0].elements.map((e) => e.id)).toEqual(["a", "b", "c", "button_1", "button_2"]);
  });

  it("duplicate one of a control that is not on the page hands back the same pages", () => {
    const pages = [onePage()];
    expect(duplicateElementInPage(pages, "pg", "ghost")).toBe(pages);
  });
});
