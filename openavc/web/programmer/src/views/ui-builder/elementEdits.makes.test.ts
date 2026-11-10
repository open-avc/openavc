import { describe, it, expect } from "vitest";

// A duplicate and a paste as the Builder's edits make them: on the page on
// screen, measured in the arrangement being authored, and clear of every
// master's id (the shared ui.<id> namespace).

import { duplicateControls, pasteControls, type EditPages } from "./elementEdits";
import { el, page } from "../../test/uiBuilderHarness";
import type { UIPage } from "../../api/types";

const LANDSCAPE = { btn_1: { x: 50, y: 50, w: 20, h: 10 } };
const PORTRAIT = { btn_1: { x: 0, y: 0, w: 20, h: 10 } };

function startingPages(): UIPage[] {
  const p = page("main", [el("btn_1", "button")], LANDSCAPE);
  return [
    { ...p, layouts: [p.layouts![0], { ...p.layouts![1], placements: PORTRAIT }] } as UIPage,
    page("other", [el("btn_9", "button")], { btn_9: { x: 0, y: 0, w: 10, h: 10 } }),
  ];
}

/** The edit surface: pages that each edit replaces, and the words it records. */
function surface(layoutId: string | null, masterIds: string[]) {
  const state = { pages: startingPages(), said: [] as string[] };
  const editPages: EditPages = (mutate, description) => {
    state.pages = mutate(state.pages);
    state.said.push(description);
  };
  return { state, on: { page: state.pages[0], editPages, layoutId, masterIds } };
}

const added = (pages: UIPage[]) => pages[0].elements.filter((e) => e.id !== "btn_1");
const primaryBox = (pages: UIPage[], id: string) => pages[0].layouts![0].placements[id];

describe("a duplicate", () => {
  it("nudges from the box in the arrangement on screen and steers clear of the masters' ids", () => {
    const { state, on } = surface("main_port", ["button_1"]);
    duplicateControls(on, ["btn_1"]);
    expect(added(state.pages).map((e) => e.id)).toEqual(["button_2"]);
    expect(primaryBox(state.pages, "button_2")).toEqual({ x: 2.5, y: 2.5, w: 20, h: 10 });
    expect(state.said).toEqual(["Duplicate element"]);
    expect(state.pages[1]).toEqual(startingPages()[1]);
  });

  it("in the primary arrangement with no masters, nudges the primary box and takes the first free id", () => {
    const { state, on } = surface(null, []);
    duplicateControls(on, ["btn_1"]);
    expect(added(state.pages).map((e) => e.id)).toEqual(["button_1"]);
    expect(primaryBox(state.pages, "button_1")).toEqual({ x: 52.5, y: 52.5, w: 20, h: 10 });
  });
});

describe("a paste", () => {
  const clipboard = { elements: [el("button_1", "button")], placements: { button_1: { x: 0, y: 0, w: 20, h: 10 } } };

  it("lands clear of the boxes in the arrangement on screen and of the masters' ids", () => {
    const { state, on } = surface("main_port", ["button_1"]);
    pasteControls(on, clipboard);
    expect(added(state.pages).map((e) => e.id)).toEqual(["button_2"]);
    expect(primaryBox(state.pages, "button_2")).toEqual({ x: 20, y: 0, w: 20, h: 10 });
    expect(state.said).toEqual(["Paste element"]);
    expect(state.pages[1]).toEqual(startingPages()[1]);
  });

  it("in the primary arrangement with no masters, keeps a free id and the first free cell", () => {
    const { state, on } = surface(null, []);
    pasteControls(on, clipboard);
    expect(added(state.pages).map((e) => e.id)).toEqual(["button_1"]);
    expect(primaryBox(state.pages, "button_1")).toEqual({ x: 0, y: 0, w: 20, h: 10 });
  });
});
