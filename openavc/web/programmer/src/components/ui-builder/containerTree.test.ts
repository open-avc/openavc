import { describe, it, expect } from "vitest";
import {
  absolutePlacements,
  alignElements,
  commitGesturePlacements,
  descendantIds,
  dropTargetFor,
  reparentElement,
  topmostSelection,
} from "./uiBuilderHelpers";
import { outlineRows } from "./outlineRows";
import { referenceParentBox } from "./touchTarget";
import type { Layout, Placement, UIElement, UIPage } from "../../api/types";

// The container tree: which element a box is measured against, the chain of
// containers above it, the Outline's rows, where a dragged control lands, and
// the reference-pixel box the Layout panel measures a control in. Every answer
// below is what the Builder gives today, the hand-edited shapes included (a
// parent that names nothing, the element itself, or a cycle).

const el = (id: string, type = "button", parent: string | null = null): UIElement =>
  ({ id, type, bindings: {}, parent }) as unknown as UIElement;

const box = (x: number, y: number, w: number, h: number): Placement => ({ x, y, w, h });

function primary(placements: Record<string, Placement>, extra: Partial<Layout> = {}): Layout {
  return { id: "landscape", orientation: "landscape", primary: true, placements, hidden: [], ...extra };
}

function portrait(placements: Record<string, Placement>): Layout {
  return { id: "portrait", orientation: "portrait", primary: false, inherits: "landscape", placements, hidden: [] };
}

function page(elements: UIElement[], layouts: Layout[], id = "pg"): UIPage {
  return { id, name: "Page", elements, layouts } as UIPage;
}

describe("referenceParentBox: the reference pixels a control's percentages are of", () => {
  it("is the whole reference screen for a control at page level", () => {
    const p = page([el("a")], [primary({ a: box(0, 0, 10, 10) })]);
    expect(referenceParentBox(p, "a")).toEqual({ width: 1280, height: 800 });
  });

  it("is its container's share of the screen, each axis on its own", () => {
    const p = page([el("g", "group"), el("a", "button", "g")], [primary({ g: box(10, 10, 50, 25), a: box(0, 0, 10, 10) })]);
    expect(referenceParentBox(p, "a")).toEqual({ width: 640, height: 200 });
  });

  it("multiplies through every container above it", () => {
    const p = page(
      [el("outer", "group"), el("inner", "group", "outer"), el("a", "button", "inner")],
      [primary({ outer: box(0, 0, 50, 50), inner: box(0, 0, 50, 25), a: box(0, 0, 1, 1) })],
    );
    expect(referenceParentBox(p, "a")).toEqual({ width: 320, height: 100 });
  });

  it("passes over a container that has no box in this arrangement", () => {
    const p = page(
      [el("outer", "group"), el("inner", "group", "outer"), el("a", "button", "inner")],
      [primary({ inner: box(0, 0, 50, 50), a: box(0, 0, 1, 1) })],
    );
    expect(referenceParentBox(p, "a")).toEqual({ width: 640, height: 400 });
  });

  it("multiplies by a container with no width rather than falling back", () => {
    const p = page([el("g", "group"), el("a", "button", "g")], [primary({ g: box(0, 0, 0, 50), a: box(0, 0, 1, 1) })]);
    expect(referenceParentBox(p, "a")).toEqual({ width: 0, height: 400 });
  });

  it("is the portrait screen, through the variant's own boxes, when the portrait arrangement is asked for", () => {
    const p = page(
      [el("g", "group"), el("a", "button", "g")],
      [primary({ g: box(0, 0, 50, 50), a: box(0, 0, 1, 1) }), portrait({ g: box(0, 0, 25, 50) })],
    );
    expect(referenceParentBox(p, "a", "portrait")).toEqual({ width: 200, height: 640 });
    expect(referenceParentBox(p, "a")).toEqual({ width: 640, height: 400 });
  });

  it("reads an unknown arrangement as the primary", () => {
    const p = page([el("a")], [primary({ a: box(0, 0, 1, 1) }), portrait({})]);
    expect(referenceParentBox(p, "a", "nope")).toEqual({ width: 1280, height: 800 });
  });

  it.each([
    ["names itself", el("a", "button", "a")],
    ["names nothing on the page", el("a", "button", "ghost")],
  ])("is the whole screen for a control whose parent %s", (_name, element) => {
    const p = page([element], [primary({ a: box(0, 0, 10, 10) })]);
    expect(referenceParentBox(p, "a")).toEqual({ width: 1280, height: 800 });
  });

  it("is the whole screen for an id the page does not hold", () => {
    const p = page([el("a")], [primary({ a: box(0, 0, 10, 10) })]);
    expect(referenceParentBox(p, "missing")).toEqual({ width: 1280, height: 800 });
  });

  it("follows a parent cycle until it comes back to the control, whichever control is asked about", () => {
    const p = page(
      [el("a", "group", "b"), el("b", "group", "a")],
      [primary({ a: box(10, 10, 50, 50), b: box(20, 20, 25, 25) })],
    );
    // The page-space read breaks this cycle at `a` (so `b` is page-level), but
    // the pixel walk measures `b` against `a`'s stored box.
    expect(referenceParentBox(p, "a")).toEqual({ width: 320, height: 200 });
    expect(referenceParentBox(p, "b")).toEqual({ width: 640, height: 400 });
  });
});

describe("an element's container: a parent that names another element on the page", () => {
  it("measures a control whose parent names nothing on the page against the page, even where a layout keeps a box under that name", () => {
    const p = page([el("c", "button", "ghost")], [primary({ ghost: box(50, 50, 50, 50), c: box(10, 10, 10, 10) })]);
    const abs = absolutePlacements(p);
    expect(abs).toEqual({ c: box(10, 10, 10, 10) });
  });
});

describe("two controls sharing an id, written back", () => {
  it("measures the write-back against the first one's container, where the read took the last one's", () => {
    const p = page(
      [el("g", "group"), el("a", "button", "g"), el("a")],
      [primary({ g: box(0, 0, 50, 50), a: box(10, 10, 10, 10) })],
    );
    const [out] = alignElements([p], { pageId: "pg", elementIds: ["a"], layoutId: null }, "align-left");
    expect(out.layouts[0].placements.a).toEqual(box(20, 20, 20, 20));
  });
});

describe("a parent cycle, read and written back", () => {
  it("aligns a container in a cycle against a box the read never measured it against", () => {
    const p = page(
      [el("a", "group", "b"), el("b", "group", "a")],
      [primary({ a: box(10, 10, 50, 50), b: box(20, 20, 50, 50) })],
    );
    const [out] = alignElements([p], { pageId: "pg", elementIds: ["b"], layoutId: null }, "align-left");
    expect(out.layouts[0].placements.b).toEqual(box(-100, -20, 200, 200));
    expect(out.layouts[0].placements.a).toEqual(box(10, 10, 50, 50));
  });
});

describe("descendantIds: everything hanging off an element", () => {
  it("collects children and grandchildren, never the element's siblings", () => {
    const p = page(
      [el("g", "group"), el("h", "group", "g"), el("a", "button", "h"), el("b", "button", "g"), el("s")],
      [primary({})],
    );
    expect([...descendantIds(p, "g")]).toEqual(["h", "a", "b"]);
    expect([...descendantIds(p, "h")]).toEqual(["a"]);
    expect([...descendantIds(p, "s")]).toEqual([]);
  });

  it("does not count an element that names itself as its own child", () => {
    const p = page([el("a", "group", "a")], [primary({})]);
    expect([...descendantIds(p, "a")]).toEqual([]);
  });

  it("includes the element itself when it sits in a cycle under its own child", () => {
    const p = page([el("a", "group", "b"), el("b", "group", "a")], [primary({})]);
    expect([...descendantIds(p, "a")]).toEqual(["b", "a"]);
  });

  it("walks every child of a container whose first child repeats an id already collected", () => {
    const p = page(
      [el("r", "group"), el("y", "button", "r"), el("x", "group", "r"), el("y", "button", "x"), el("z", "button", "x")],
      [primary({})],
    );
    expect([...descendantIds(p, "r")]).toEqual(["y", "x", "z"]);
  });

  it("finds the children of an id the page does not hold", () => {
    const p = page([el("a", "button", "ghost"), el("b", "button", "ghost")], [primary({})]);
    expect([...descendantIds(p, "ghost")]).toEqual(["a", "b"]);
  });
});

describe("topmostSelection: a selected container already carries what it holds", () => {
  it("drops a selected control whose container, at any depth, is selected too", () => {
    const p = page(
      [el("g", "group"), el("h", "group", "g"), el("a", "button", "h"), el("b")],
      [primary({})],
    );
    expect(topmostSelection(p, ["a", "b", "g"])).toEqual(["b", "g"]);
    expect(topmostSelection(p, ["a", "h"])).toEqual(["h"]);
  });

  it("drops nothing for a selected id the page does not hold, even one a control names as its parent", () => {
    const p = page([el("c", "button", "ghost"), el("d")], [primary({})]);
    expect(topmostSelection(p, ["c", "ghost", "d"])).toEqual(["c", "ghost", "d"]);
  });

  it("keeps both controls of a selected cycle, and a control whose parent names nothing", () => {
    const p = page(
      [el("a", "group", "b"), el("b", "group", "a"), el("c", "button", "ghost")],
      [primary({})],
    );
    expect(topmostSelection(p, ["a", "c"])).toEqual(["a", "c"]);
    // Each one's walk reaches the other before it comes back to itself.
    expect(topmostSelection(p, ["a", "b"])).toEqual([]);
  });
});

describe("outlineRows: the Outline's tree", () => {
  const rowIds = (rows: { id: string; depth: number }[]) => rows.map((r) => `${r.id}@${r.depth}`);

  it("lists a flat page in page order, each row's neighbours the rows beside it", () => {
    const rows = outlineRows([el("a"), el("b"), el("c")]);
    expect(rows).toEqual([
      { id: "a", depth: 0, hasChildren: false, collapsed: false, prevSiblingId: undefined, nextSiblingId: "b" },
      { id: "b", depth: 0, hasChildren: false, collapsed: false, prevSiblingId: "a", nextSiblingId: "c" },
      { id: "c", depth: 0, hasChildren: false, collapsed: false, prevSiblingId: "b", nextSiblingId: undefined },
    ]);
  });

  it("nests children under their container, their neighbours their own siblings", () => {
    const rows = outlineRows([el("g", "group"), el("a", "button", "g"), el("b"), el("c", "button", "g")]);
    expect(rowIds(rows)).toEqual(["g@0", "a@1", "c@1", "b@0"]);
    expect(rows[0]).toMatchObject({ hasChildren: true, prevSiblingId: undefined, nextSiblingId: "b" });
    expect(rows[1]).toMatchObject({ prevSiblingId: undefined, nextSiblingId: "c" });
    expect(rows[3]).toMatchObject({ prevSiblingId: "g", nextSiblingId: undefined });
  });

  it("folds a collapsed container with children, and never marks an empty one collapsed", () => {
    const rows = outlineRows([el("g", "group"), el("a", "button", "g"), el("e", "group")], { collapsed: ["g", "e"] });
    expect(rowIds(rows)).toEqual(["g@0", "e@0"]);
    expect(rows[0]).toMatchObject({ hasChildren: true, collapsed: true });
    expect(rows[1]).toMatchObject({ hasChildren: false, collapsed: false });
  });

  it("keeps each match and every container above it, ignoring collapse, and drops the rest", () => {
    const elements = [
      el("g", "group"), el("h", "group", "g"), el("a", "button", "h"), el("x", "button", "h"),
      el("y", "button", "g"), el("b"),
    ];
    const rows = outlineRows(elements, { collapsed: ["g", "h"], matchIds: new Set(["a"]) });
    expect(rowIds(rows)).toEqual(["g@0", "h@1", "a@2"]);
    expect(rows[0]).toMatchObject({ hasChildren: true, collapsed: false, nextSiblingId: undefined });
    expect(rows[2]).toMatchObject({ prevSiblingId: undefined, nextSiblingId: undefined });
  });

  it("gives a row the neighbours a search leaves, not the ones it hid", () => {
    const elements = [el("a"), el("x"), el("b"), el("g", "group"), el("c", "button", "g"), el("y", "button", "g"), el("d", "button", "g")];
    const rows = outlineRows(elements, { matchIds: new Set(["a", "b", "c", "d"]) });
    expect(rowIds(rows)).toEqual(["a@0", "b@0", "g@0", "c@1", "d@1"]);
    expect(rows[1]).toMatchObject({ prevSiblingId: "a", nextSiblingId: "g" });
    expect(rows[2]).toMatchObject({ prevSiblingId: "b", nextSiblingId: undefined });
    expect(rows[3]).toMatchObject({ prevSiblingId: undefined, nextSiblingId: "d" });
    expect(rows[4]).toMatchObject({ prevSiblingId: "c", nextSiblingId: undefined });
  });

  it("matches a container on its own name without showing its children", () => {
    const rows = outlineRows([el("g", "group"), el("a", "button", "g")], { matchIds: new Set(["g"]) });
    expect(rows).toEqual([
      { id: "g", depth: 0, hasChildren: false, collapsed: false, prevSiblingId: undefined, nextSiblingId: undefined },
    ]);
  });

  it("puts a control whose parent names nothing or itself at page level", () => {
    const rows = outlineRows([el("a", "button", "ghost"), el("b", "button", "b")]);
    expect(rowIds(rows)).toEqual(["a@0", "b@0"]);
    expect(rows[1]).toMatchObject({ hasChildren: false, prevSiblingId: "a" });
  });

  it("keeps a control whose parent names nothing in page order among the page's own", () => {
    const rows = outlineRows([el("x"), el("a", "button", "ghost"), el("y")]);
    expect(rowIds(rows)).toEqual(["x@0", "a@0", "y@0"]);
    expect(rows[1]).toMatchObject({ prevSiblingId: "x", nextSiblingId: "y" });
  });

  it("shows the controls of a cycle at page level after everything else, with no children", () => {
    const rows = outlineRows([el("p"), el("a", "group", "b"), el("b", "group", "a"), el("c", "button", "a")]);
    expect(rows).toEqual([
      { id: "p", depth: 0, hasChildren: false, collapsed: false, prevSiblingId: undefined, nextSiblingId: undefined },
      { id: "a", depth: 0, hasChildren: false, collapsed: false, prevSiblingId: undefined, nextSiblingId: "b" },
      { id: "b", depth: 0, hasChildren: false, collapsed: false, prevSiblingId: "a", nextSiblingId: "c" },
      { id: "c", depth: 0, hasChildren: false, collapsed: false, prevSiblingId: "b", nextSiblingId: undefined },
    ]);
  });

  it("lists only the matched controls of a cycle when searching", () => {
    const rows = outlineRows([el("a", "group", "b"), el("b", "group", "a"), el("c", "button", "a")], { matchIds: new Set(["c"]) });
    expect(rows).toEqual([
      { id: "c", depth: 0, hasChildren: false, collapsed: false, prevSiblingId: undefined, nextSiblingId: undefined },
    ]);
  });

  it("is empty for no elements, with or without a search", () => {
    expect(outlineRows([])).toEqual([]);
    expect(outlineRows([], { matchIds: new Set(["a"]) })).toEqual([]);
  });

  it("lists a repeated id once per element and its children once", () => {
    const rows = outlineRows([el("g", "group"), el("g", "group"), el("a", "button", "g")]);
    expect(rowIds(rows)).toEqual(["g@0", "a@1", "g@0", "a@1"]);
  });

  it("stops a search at a page-level control's own id met again below it", () => {
    const rows = outlineRows([el("g", "group"), el("a", "group", "g"), el("b", "button", "g"), el("g", "group", "a")], { matchIds: new Set(["b"]) });
    expect(rowIds(rows)).toEqual(["g@0", "b@1"]);
  });

  it("stops a search at a repeated id below its own namesake", () => {
    const rows = outlineRows([el("g", "group"), el("a", "group", "g"), el("g", "group", "a")], { matchIds: new Set(["a"]) });
    expect(rowIds(rows)).toEqual(["g@0", "a@1"]);
    expect(rows[1]).toMatchObject({ hasChildren: false });
  });
});

describe("dropTargetFor: which container a dragged control belongs to", () => {
  const nested = () =>
    page(
      [el("outer", "group"), el("inner", "group", "outer"), el("a"), el("k", "button", "outer")],
      [primary({ outer: box(0, 0, 60, 60), inner: box(50, 50, 50, 50), a: box(80, 80, 5, 5), k: box(0, 0, 10, 10) })],
    );

  it("joins the innermost container that holds it whole", () => {
    expect(dropTargetFor(nested(), "a", box(35, 35, 5, 5))).toBe("inner");
    expect(dropTargetFor(nested(), "a", box(5, 5, 5, 5))).toBe("outer");
  });

  it("forgives a box that reaches the container's edge by the rounding floor, and no more", () => {
    expect(dropTargetFor(nested(), "a", box(-0.0001, 0, 10, 10))).toBe("outer");
    expect(dropTargetFor(nested(), "a", box(-0.0002, 0, 10, 10))).toBeNull();
  });

  it("keeps the first of two containers with the same area", () => {
    const p = page(
      [el("g1", "group"), el("g2", "group"), el("a")],
      [primary({ g1: box(0, 0, 50, 50), g2: box(0, 0, 50, 50), a: box(90, 90, 5, 5) })],
    );
    expect(dropTargetFor(p, "a", box(10, 10, 5, 5))).toBe("g1");
  });

  it("never puts a control inside something that is not a container, however tightly it holds the box", () => {
    const p = page(
      [el("g", "group"), el("card", "image"), el("a")],
      [primary({ g: box(0, 0, 50, 50), card: box(5, 5, 10, 10), a: box(90, 90, 5, 5) })],
    );
    expect(dropTargetFor(p, "a", box(6, 6, 2, 2))).toBe("g");
  });

  it("passes over a container with no box in this arrangement, and anything that is not a container", () => {
    const p = page(
      [el("nobox", "group"), el("btn"), el("g", "group"), el("a")],
      [primary({ btn: box(0, 0, 100, 100), g: box(0, 0, 50, 50), a: box(90, 90, 5, 5) })],
    );
    expect(dropTargetFor(p, "a", box(10, 10, 5, 5))).toBe("g");
  });

  it("keeps a control in the container it is in while it still touches it, and lets it out once it does not", () => {
    expect(dropTargetFor(nested(), "k", box(55, 55, 10, 10))).toBe("outer");
    expect(dropTargetFor(nested(), "k", box(70, 0, 10, 10))).toBeNull();
  });

  it("moves a control on into a smaller container inside the one it lives in", () => {
    expect(dropTargetFor(nested(), "k", box(35, 35, 5, 5))).toBe("inner");
  });

  it("does not count a box that hangs out of a container's bottom edge as held", () => {
    expect(dropTargetFor(nested(), "a", box(35, 35, 5, 40))).toBeNull();
  });

  it("never keeps a control that names itself as its own container", () => {
    const p = page([el("s", "button", "s")], [primary({ s: box(10, 10, 10, 10) })]);
    expect(dropTargetFor(p, "s", box(12, 12, 10, 10))).toBeNull();
  });

  it("never puts a container inside itself or inside its own contents", () => {
    expect(dropTargetFor(nested(), "outer", box(55, 55, 5, 5))).toBeNull();
    expect(dropTargetFor(nested(), "inner", box(10, 10, 5, 5))).toBe("outer");
  });

  it("reads a brand-new id as a palette drop: nothing inside it, nowhere it lives yet", () => {
    expect(dropTargetFor(nested(), "", box(35, 35, 5, 5))).toBe("inner");
    expect(dropTargetFor(nested(), "fresh", box(90, 90, 5, 5))).toBeNull();
  });

  it("answers with a container whose id is empty, which the project file allows", () => {
    const p = page([el("", "group"), el("a")], [primary({ "": box(0, 0, 50, 50), a: box(90, 90, 5, 5) })]);
    expect(dropTargetFor(p, "a", box(10, 10, 5, 5))).toBe("");
  });

  it("measures against the arrangement it is asked about", () => {
    const p = page(
      [el("g", "group"), el("a")],
      [primary({ g: box(0, 0, 20, 20), a: box(90, 90, 5, 5) }), portrait({ g: box(50, 50, 50, 50) })],
    );
    expect(dropTargetFor(p, "a", box(60, 60, 5, 5))).toBeNull();
    expect(dropTargetFor(p, "a", box(60, 60, 5, 5), "portrait")).toBe("g");
  });
});

describe("commitGesturePlacements: where everything landed, and what it landed in", () => {
  it("writes the boxes, then moves each control into the container it now sits inside, without moving it", () => {
    const p = page(
      [el("g", "group"), el("a"), el("b")],
      [primary({ g: box(20, 20, 50, 50), a: box(0, 0, 5, 5), b: box(90, 90, 5, 5) })],
    );
    const [out] = commitGesturePlacements([p], "pg", { a: box(30, 30, 10, 10) });
    expect(out.elements.map((e) => [e.id, e.parent ?? null])).toEqual([["g", null], ["a", "g"], ["b", null]]);
    expect(out.layouts[0].placements.a).toEqual(box(20, 20, 20, 20));
    expect(out.layouts[0].placements.b).toEqual(box(90, 90, 5, 5));
  });

  it("writes a box for an id the page does not hold without adopting anything, and leaves other pages alone", () => {
    const p = page([el("g", "group")], [primary({ g: box(0, 0, 50, 50) })]);
    const other = page([el("z")], [primary({ z: box(0, 0, 5, 5) })], "other");
    const out = commitGesturePlacements([p, other], "pg", { ghost: box(10, 10, 5, 5) });
    expect(out[0].layouts[0].placements.ghost).toEqual(box(10, 10, 5, 5));
    expect(out[0].elements).toEqual(p.elements);
    expect(out[1]).toBe(other);
  });

  it("takes a control out to the page once it no longer touches its container", () => {
    const p = page([el("g", "group"), el("a", "button", "g")], [primary({ g: box(0, 0, 50, 50), a: box(0, 0, 10, 10) })]);
    const [out] = commitGesturePlacements([p], "pg", { a: box(300, 0, 10, 10) });
    expect(out.elements.find((e) => e.id === "a")?.parent).toBeNull();
    expect(out.layouts[0].placements.a).toEqual(box(150, 0, 5, 5));
  });
});

describe("reparentElement on a primary that inherits another arrangement", () => {
  it("gives the primary a box of its own against the new container, and leaves an arrangement where the container has no box alone", () => {
    const p = page(
      [el("g", "group"), el("a")],
      [
        primary({ g: box(20, 20, 50, 50) }, { inherits: "base" }),
        { id: "base", orientation: "landscape", primary: false, placements: { a: box(30, 30, 10, 10) }, hidden: [] },
      ],
    );
    const [out] = reparentElement([p], "pg", "a", "g");
    expect(out.layouts[0].placements).toEqual({ g: box(20, 20, 50, 50), a: box(20, 20, 20, 20) });
    expect(out.layouts[1].placements).toEqual({ a: box(30, 30, 10, 10) });
  });
});

describe("reparentElement on a control named constructor", () => {
  // The page-space read looks a control up in a plain object, where
  // `constructor` is always there: the box it hands back is no numbers at all.
  it("writes a box of no numbers into every arrangement, a variant that held none of its own included", () => {
    const p = page(
      [el("g", "group"), el("constructor")],
      [primary({ g: box(20, 20, 50, 50), constructor: box(30, 30, 10, 10) }), portrait({ g: box(0, 0, 50, 50) })],
    );
    const [out] = reparentElement([p], "pg", "constructor", "g");
    expect(out.layouts[0].placements).toEqual({ g: box(20, 20, 50, 50), constructor: box(NaN, NaN, NaN, NaN) });
    expect(out.layouts[1].placements).toEqual({ g: box(0, 0, 50, 50), constructor: box(NaN, NaN, NaN, NaN) });
  });
});
