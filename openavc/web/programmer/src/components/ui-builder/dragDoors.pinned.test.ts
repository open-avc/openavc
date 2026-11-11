import { describe, it, expect } from "vitest";

// The doors a drag, a resize, a nudge, a copy and a marquee go through, each
// asked about an arrangement other than the primary: which boxes it reads,
// which it writes, and what it hands back when the page is not there.

import {
  clipboardForSelection,
  commitGesturePlacements,
  elementsIntersectingRect,
  moveElementsInPage,
} from "./uiBuilderHelpers";
import { nudgedPages } from "../../views/ui-builder/elementEdits";
import type { UIElement, UIPage } from "../../api/types";

const LAND = {
  a: { x: 0, y: 0, w: 10, h: 10 },
  g: { x: 60, y: 60, w: 40, h: 40 },
  k: { x: 10, y: 10, w: 50, h: 50 },
};
const PORT = {
  a: { x: 0, y: 80, w: 10, h: 10 },
  g: { x: 0, y: 0, w: 40, h: 40 },
};

function onePage(): UIPage {
  return {
    id: "pg",
    name: "Pg",
    elements: [
      { id: "a", type: "button" },
      { id: "g", type: "group" },
      { id: "k", type: "button", parent: "g" },
    ] as UIElement[],
    layouts: [
      { id: "land", orientation: "landscape", primary: true, placements: LAND, hidden: [] },
      { id: "port", orientation: "portrait", primary: false, inherits: "land", placements: PORT, hidden: [] },
    ],
  } as unknown as UIPage;
}

function otherPage(): UIPage {
  return {
    id: "other",
    name: "Other",
    elements: [{ id: "z", type: "button" }] as UIElement[],
    layouts: [{ id: "o_land", orientation: "landscape", primary: true, placements: { z: { x: 1, y: 1, w: 1, h: 1 } }, hidden: [] }],
  } as unknown as UIPage;
}

const stored = (p: UIPage, layout: string) => p.layouts!.find((l) => l.id === layout)!.placements;

describe("moving boxes, as a resize and a multi-select drag write them", () => {
  it("writes the arrangement named, rounded, and leaves the primary and the other pages", () => {
    const pages = [onePage(), otherPage()];
    const out = moveElementsInPage(pages, "pg", { a: { x: 5.123456, y: 70, w: 10, h: 10 } }, "port");
    expect(stored(out[0], "port")).toEqual({ ...PORT, a: { x: 5.1235, y: 70, w: 10, h: 10 } });
    expect(stored(out[0], "land")).toBe(LAND);
    expect(out[1]).toBe(pages[1]);
  });

  it("writes the primary for null, and for an arrangement the page does not have", () => {
    for (const layoutId of [null, "gone"]) {
      const out = moveElementsInPage([onePage()], "pg", { a: { x: 5, y: 5, w: 10, h: 10 } }, layoutId);
      expect(stored(out[0], "land").a).toEqual({ x: 5, y: 5, w: 10, h: 10 });
      expect(stored(out[0], "port")).toBe(PORT);
    }
  });

  it("writes a box for an id the page does not hold", () => {
    const out = moveElementsInPage([onePage()], "pg", { ghost: { x: 1, y: 2, w: 3, h: 4 } }, "port");
    expect(stored(out[0], "port").ghost).toEqual({ x: 1, y: 2, w: 3, h: 4 });
    expect(out[0].elements.map((e) => e.id)).toEqual(["a", "g", "k"]);
  });

  it("aimed at a page that is not there, hands back a new list of the same pages", () => {
    const pages = [onePage(), otherPage()];
    const out = moveElementsInPage(pages, "nope", { a: { x: 5, y: 5, w: 10, h: 10 } }, "port");
    expect(out).not.toBe(pages);
    expect(out).toHaveLength(2);
    expect(out[0]).toBe(pages[0]);
    expect(out[1]).toBe(pages[1]);
  });
});

describe("a move's commit, where it landed and what it landed in", () => {
  it("measures the container it lands in where the arrangement on screen draws it", () => {
    // In portrait the group is at the top left; in the primary it is bottom right.
    const out = commitGesturePlacements([onePage()], "pg", { a: { x: 10, y: 10, w: 10, h: 10 } }, "port");
    expect(out[0].elements.find((e) => e.id === "a")!.parent).toBe("g");
    expect(stored(out[0], "port").a).toEqual({ x: 25, y: 25, w: 25, h: 25 });
  });

  it("in the primary, the same box lands at page level and stays there", () => {
    const out = commitGesturePlacements([onePage()], "pg", { a: { x: 10, y: 10, w: 10, h: 10 } }, null);
    expect(out[0].elements.find((e) => e.id === "a")!.parent ?? null).toBe(null);
    expect(stored(out[0], "land").a).toEqual({ x: 10, y: 10, w: 10, h: 10 });
    expect(stored(out[0], "port")).toBe(PORT);
  });

  it("aimed at a page that is not there, hands back a new list of the same pages", () => {
    const pages = [onePage(), otherPage()];
    const out = commitGesturePlacements(pages, "nope", { a: { x: 10, y: 10, w: 10, h: 10 } }, "port");
    expect(out).not.toBe(pages);
    expect(out[0]).toBe(pages[0]);
    expect(out[1]).toBe(pages[1]);
  });
});

describe("a nudge's pages", () => {
  it("moves each box in the arrangement named, rounded, and leaves the primary", () => {
    const out = nudgedPages([onePage(), otherPage()], "pg", { ids: ["a", "k"], dx: 0.123456, dy: -1 }, "port");
    expect(stored(out[0], "port").a).toEqual({ x: 0.1235, y: 79, w: 10, h: 10 });
    // k has no portrait box of its own: the nudge starts from the one it inherits.
    expect(stored(out[0], "port").k).toEqual({ x: 10.1235, y: 9, w: 50, h: 50 });
    expect(stored(out[0], "land")).toBe(LAND);
  });

  it("gives an id the page does not hold the default box, moved", () => {
    const out = nudgedPages([onePage()], "pg", { ids: ["ghost"], dx: 1, dy: 1 }, null);
    expect(Object.keys(stored(out[0], "land"))).toEqual(["a", "g", "k", "ghost"]);
  });

  it("aimed at a page that is not there, hands back the same list", () => {
    const pages = [onePage(), otherPage()];
    expect(nudgedPages(pages, "nope", { ids: ["a"], dx: 1, dy: 1 }, "port")).toBe(pages);
  });
});

describe("the clipboard a copy fills", () => {
  it("in the arrangement on screen, a control whose container stays behind comes back in page space", () => {
    const clip = clipboardForSelection(onePage(), ["k"], "port");
    expect(clip.placements).toEqual({ k: { x: 4, y: 4, w: 20, h: 20 } });
  });

  it("a container and what is in it come back as the arrangement on screen stores them", () => {
    const clip = clipboardForSelection(onePage(), ["g"], "port");
    expect(clip.elements.map((e) => e.id)).toEqual(["g", "k"]);
    expect(clip.placements).toEqual({ g: { x: 0, y: 0, w: 40, h: 40 }, k: { x: 10, y: 10, w: 50, h: 50 } });
  });

  it("in the primary, the primary's boxes", () => {
    const clip = clipboardForSelection(onePage(), ["k", "a"], null);
    expect(clip.elements.map((e) => e.id)).toEqual(["a", "k"]);
    expect(clip.placements).toEqual({ a: { x: 0, y: 0, w: 10, h: 10 }, k: { x: 64, y: 64, w: 20, h: 20 } });
  });
});

describe("the controls a marquee touches", () => {
  it("are measured where the arrangement on screen draws them", () => {
    const page = onePage();
    // Where a sits in portrait, and where it sits in the primary.
    expect(elementsIntersectingRect(page, { x: 1, y: 81, w: 2, h: 2 }, "port")).toEqual(["a"]);
    expect(elementsIntersectingRect(page, { x: 1, y: 1, w: 2, h: 2 }, "port")).toEqual(["g"]);
    expect(elementsIntersectingRect(page, { x: 1, y: 1, w: 2, h: 2 }, null)).toEqual(["a"]);
    expect(elementsIntersectingRect(page, { x: 1, y: 81, w: 2, h: 2 }, null)).toEqual([]);
  });
});
