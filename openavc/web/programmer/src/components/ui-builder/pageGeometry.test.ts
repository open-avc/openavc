import { describe, it, expect } from "vitest";
import {
  absolutePlacements,
  alignElements,
  distributeElements,
  duplicatePage,
  matchSizeElements,
  reparentElement,
  resolveDropParent,
  toPageBox,
  type AlignAction,
} from "./uiBuilderHelpers";
import type { Layout, Placement, UIElement, UIPage } from "../../api/types";

// Where a control sits, read and written: a box stored as percentages of its
// container, the same box in page space, and the tools that work in page space
// and write each box back against its own container. Every number below is
// what the Builder answers today, the odd ones included.

const el = (id: string, type = "button", parent: string | null = null, extra: object = {}): UIElement =>
  ({ id, type, bindings: {}, parent, ...extra }) as unknown as UIElement;

const box = (x: number, y: number, w: number, h: number): Placement => ({ x, y, w, h });

function primary(placements: Record<string, Placement>, extra: Partial<Layout> = {}): Layout {
  return { id: "landscape", orientation: "landscape", primary: true, placements, hidden: [], ...extra };
}

function page(elements: UIElement[], layouts: Layout[], id = "pg"): UIPage {
  return { id, name: "Page", elements, layouts } as UIPage;
}

describe("toPageBox: a container's percentages in page space", () => {
  it("scales the box by the container and offsets it by the container's corner", () => {
    expect(toPageBox(box(10, 20, 50, 25), box(20, 40, 50, 40))).toEqual(box(25, 48, 25, 10));
  });

  it("does not round: the percentage is divided by 100 before it is scaled", () => {
    const out = toPageBox(box(7, 7, 7, 7), box(0, 0, 3, 3));
    expect(out.x).toBe(0.21000000000000002);
    expect(out.w).toBe(0.21000000000000002);
  });

  it.each([
    ["no container", null],
    ["an undefined container", undefined],
    ["a zero-width container", box(10, 10, 0, 50)],
    ["a zero-height container", box(10, 10, 50, 0)],
    ["a container with no number for a width", box(10, 10, NaN, 50)],
    ["a negative-width container", box(10, 10, -5, 50)],
  ])("hands back a copy of the box itself for %s", (_name, parent) => {
    const relative = box(10, 20, 30, 40);
    const out = toPageBox(relative, parent as Placement | null | undefined);
    expect(out).toEqual(relative);
    expect(out).not.toBe(relative);
  });
});

describe("absolutePlacements: every box in page space", () => {
  it("multiplies through a container with no width rather than falling back", () => {
    const p = page(
      [el("g", "group"), el("c", "button", "g")],
      [primary({ g: box(10, 10, 0, 50), c: box(50, 50, 50, 50) })],
    );
    expect(absolutePlacements(p).c).toEqual(box(10, 35, 0, 25));
  });

  it("flattens two levels of nesting", () => {
    const p = page(
      [el("outer", "group"), el("inner", "group", "outer"), el("c", "button", "inner")],
      [primary({ outer: box(20, 20, 50, 50), inner: box(50, 50, 50, 50), c: box(0, 0, 50, 50) })],
    );
    const abs = absolutePlacements(p);
    expect(abs.inner).toEqual(box(45, 45, 25, 25));
    expect(abs.c).toEqual(box(45, 45, 12.5, 12.5));
  });

  it("keeps a child's own box when its container has no box", () => {
    const p = page([el("g", "group"), el("c", "button", "g")], [primary({ c: box(5, 6, 7, 8) })]);
    const abs = absolutePlacements(p);
    expect(abs.c).toEqual(box(5, 6, 7, 8));
    expect("g" in abs).toBe(false);
  });

  it("reads a parent that is the element itself, or not on the page, as no parent", () => {
    const p = page(
      [el("self", "button", "self"), el("orphan", "button", "ghost")],
      [primary({ self: box(1, 2, 3, 4), orphan: box(5, 6, 7, 8) })],
    );
    const abs = absolutePlacements(p);
    expect(abs.self).toEqual(box(1, 2, 3, 4));
    expect(abs.orphan).toEqual(box(5, 6, 7, 8));
  });

  it("breaks a parent cycle at the element it started from", () => {
    const p = page(
      [el("a", "group", "b"), el("b", "group", "a")],
      [primary({ a: box(10, 10, 50, 50), b: box(20, 20, 50, 50) })],
    );
    const abs = absolutePlacements(p);
    expect(abs.a).toEqual(box(25, 25, 25, 25));
    expect(abs.b).toEqual(box(20, 20, 50, 50));
  });

  it("folds a variant's deltas over what it inherits", () => {
    const p = page(
      [el("g", "group"), el("c", "button", "g")],
      [
        primary({ g: box(0, 0, 50, 50), c: box(0, 0, 50, 50) }),
        { id: "portrait", orientation: "portrait", primary: false, inherits: "landscape", placements: { g: box(50, 50, 50, 50) }, hidden: [] },
      ],
    );
    expect(absolutePlacements(p, "portrait").c).toEqual(box(50, 50, 25, 25));
    expect(absolutePlacements(p).c).toEqual(box(0, 0, 25, 25));
  });
});

describe("reparentElement: the numbers change, the pixels do not", () => {
  const variant = (placements: Record<string, Placement>): Layout => ({
    id: "portrait", orientation: "portrait", primary: false, inherits: "landscape", placements, hidden: [],
  });

  it("gives a variant a box against the new container only where it had a box of its own", () => {
    const p = page(
      [el("g", "group"), el("a")],
      [primary({ g: box(20, 20, 50, 50), a: box(30, 30, 10, 10) }), variant({ g: box(0, 0, 50, 50) })],
    );
    const [out] = reparentElement([p], "pg", "a", "g");
    expect(out.elements.find((e) => e.id === "a")?.parent).toBe("g");
    expect(out.layouts[0].placements.a).toEqual(box(20, 20, 20, 20));
    expect("a" in out.layouts[1].placements).toBe(false);
  });

  it("rewrites a variant's own box against the container as that variant draws it", () => {
    const p = page(
      [el("g", "group"), el("a")],
      [primary({ g: box(20, 20, 50, 50), a: box(30, 30, 10, 10) }), variant({ g: box(0, 0, 50, 50), a: box(10, 10, 10, 10) })],
    );
    const [out] = reparentElement([p], "pg", "a", "g");
    expect(out.layouts[1].placements.a).toEqual(box(20, 20, 20, 20));
  });

  it("leaves an arrangement alone where the new container has no area", () => {
    const p = page(
      [el("g", "group"), el("a")],
      [primary({ g: box(20, 20, 0, 50), a: box(30, 30, 10, 10) }), variant({ g: box(0, 0, 50, 50), a: box(10, 10, 10, 10) })],
    );
    const [out] = reparentElement([p], "pg", "a", "g");
    expect(out.elements.find((e) => e.id === "a")?.parent).toBe("g");
    expect(out.layouts[0].placements.a).toEqual(box(30, 30, 10, 10));
    expect(out.layouts[1].placements.a).toEqual(box(20, 20, 20, 20));
  });

  it("passes over a variant that stores no boxes at all", () => {
    const bare = { id: "portrait", orientation: "portrait", primary: false, inherits: "landscape", hidden: [] } as unknown as Layout;
    const p = page(
      [el("g", "group"), el("a")],
      [primary({ g: box(20, 20, 50, 50), a: box(30, 30, 10, 10) }), bare],
    );
    const [out] = reparentElement([p], "pg", "a", "g");
    expect(out.layouts[0].placements.a).toEqual(box(20, 20, 20, 20));
    expect(out.layouts[1]).toBe(bare);
  });

  it("moves an element with no box anywhere without writing one", () => {
    const p = page([el("g", "group"), el("a")], [primary({ g: box(20, 20, 50, 50) })]);
    const [out] = reparentElement([p], "pg", "a", "g");
    expect(out.elements.find((e) => e.id === "a")?.parent).toBe("g");
    expect(out.layouts[0].placements).toEqual({ g: box(20, 20, 50, 50) });
  });

  it("still moves the element on a page that has no layouts", () => {
    const p = { id: "pg", name: "Page", elements: [el("g", "group"), el("a")] } as unknown as UIPage;
    const [out] = reparentElement([p], "pg", "a", "g");
    expect(out.elements.map((e) => [e.id, e.parent])).toEqual([["g", null], ["a", "g"]]);
    expect(out.layouts).toBeUndefined();
  });

  it("takes an element out to the page at the box the eye saw", () => {
    const p = page(
      [el("g", "group"), el("a", "button", "g")],
      [primary({ g: box(20, 20, 50, 50), a: box(20, 20, 20, 20) })],
    );
    const [out] = reparentElement([p], "pg", "a", null);
    expect(out.layouts[0].placements.a).toEqual(box(30, 30, 10, 10));
  });
});

describe("resolveDropParent: a palette drop, as a box in its container", () => {
  it("measures each axis against the container's own side", () => {
    const p = page([el("g", "group")], [primary({ g: box(20, 10, 50, 20) })]);
    expect(resolveDropParent(p, box(30, 15, 10, 5))).toEqual({ parentId: "g", relative: box(20, 25, 20, 25) });
  });

  it("lands at page level when the container it falls in has no width", () => {
    const p = page([el("g", "group")], [primary({ g: box(30, 10, 0, 50) })]);
    expect(resolveDropParent(p, box(30, 20, 0, 10))).toEqual({ parentId: null, relative: box(30, 20, 0, 10) });
  });
});

describe("alignElements", () => {
  const two = () =>
    page([el("a"), el("b")], [primary({ a: box(10, 10, 20, 10), b: box(50, 40, 10, 30) })]);

  it.each<[AlignAction, Placement, Placement]>([
    ["align-left", box(10, 10, 20, 10), box(10, 40, 10, 30)],
    ["align-center", box(25, 10, 20, 10), box(30, 40, 10, 30)],
    ["align-right", box(40, 10, 20, 10), box(50, 40, 10, 30)],
    ["align-top", box(10, 10, 20, 10), box(50, 10, 10, 30)],
    ["align-middle", box(10, 35, 20, 10), box(50, 25, 10, 30)],
    ["align-bottom", box(10, 60, 20, 10), box(50, 40, 10, 30)],
  ])("%s lines two controls up on the box they span", (action, a, b) => {
    const [out] = alignElements([two()], "pg", ["a", "b"], action);
    expect(out.layouts[0].placements).toEqual({ a, b });
  });

  it.each<[AlignAction, Placement]>([
    ["align-center", box(40, 10, 20, 10)],
    ["align-bottom", box(10, 90, 20, 10)],
  ])("%s on one control lines it up on the page", (action, a) => {
    const p = page([el("a")], [primary({ a: box(10, 10, 20, 10) })]);
    expect(alignElements([p], "pg", ["a"], action)[0].layouts[0].placements.a).toEqual(a);
  });

  it("lines one control in a container up on the page and writes it back against the container", () => {
    const p = page(
      [el("g", "group"), el("kid", "button", "g")],
      [primary({ g: box(0, 0, 50, 50), kid: box(0, 0, 20, 20) })],
    );
    const [out] = alignElements([p], "pg", ["kid"], "align-right");
    expect(out.layouts[0].placements.kid).toEqual(box(180, 0, 20, 20));
  });

  it("writes a control whose container has no area back as a page box", () => {
    const p = page(
      [el("g", "group"), el("kid", "button", "g")],
      [primary({ g: box(40, 40, 0, 20), kid: box(0, 0, 20, 20) })],
    );
    const [out] = alignElements([p], "pg", ["kid"], "align-left");
    expect(out.layouts[0].placements.kid).toEqual(box(0, 40, 0, 4));
  });

  it("writes a control back against a container that is wider than it is tall", () => {
    const p = page(
      [el("g", "group"), el("kid", "button", "g")],
      [primary({ g: box(0, 0, 50, 25), kid: box(0, 0, 20, 40) })],
    );
    const [out] = alignElements([p], "pg", ["kid"], "align-bottom");
    expect(out.layouts[0].placements.kid).toEqual(box(0, 360, 20, 40));
  });

  it("writes a control back as a page box when its container has no height", () => {
    const p = page(
      [el("g", "group"), el("kid", "button", "g")],
      [primary({ g: box(10, 10, 50, 0), kid: box(0, 0, 20, 20) })],
    );
    const [out] = alignElements([p], "pg", ["kid"], "align-left");
    expect(out.layouts[0].placements.kid).toEqual(box(0, 10, 10, 0));
  });

  it("writes a control that names itself as its container back as a page box", () => {
    const p = page([el("self", "button", "self")], [primary({ self: box(10, 10, 20, 10) })]);
    const [out] = alignElements([p], "pg", ["self"], "align-right");
    expect(out.layouts[0].placements.self).toEqual(box(80, 10, 20, 10));
  });

  it("measures a control with no box from the default box and writes it one", () => {
    const p = page([el("a"), el("b")], [primary({ a: box(50, 10, 20, 10) })]);
    const [out] = alignElements([p], "pg", ["a", "b"], "align-right");
    expect(out.layouts[0].placements).toEqual({ a: box(50, 10, 20, 10), b: box(45, 0, 25, 12.5) });
  });

  it("hands back the same page when nothing selected is on it, or everything selected is locked", () => {
    const p = two();
    expect(alignElements([p], "pg", ["nope"], "align-left")[0]).toBe(p);
    const locked = page(
      [el("a", "button", null, { locked: true }), el("b", "button", null, { locked: true })],
      p.layouts,
    );
    expect(alignElements([locked], "pg", ["a", "b"], "align-left")[0]).toBe(locked);
  });

  it("leaves every other page as it was", () => {
    const other = page([el("a")], [primary({ a: box(10, 10, 20, 10) })], "other");
    const out = alignElements([other, two()], "pg", ["a", "b"], "align-left");
    expect(out[0]).toBe(other);
  });
});

describe("distributeElements", () => {
  it("orders by the leading edge, then the far edge", () => {
    const p = page(
      [el("a"), el("c"), el("b"), el("d")],
      [primary({ a: box(0, 0, 10, 10), b: box(30, 0, 10, 10), c: box(30, 0, 20, 10), d: box(90, 0, 10, 10) })],
    );
    const [out] = distributeElements([p], "pg", ["a", "b", "c", "d"], "horizontal");
    expect(out.layouts[0].placements.b.x).toBe(26.6667);
    expect(out.layouts[0].placements.c.x).toBe(53.3333);
  });

  it("puts the nearer far edge first even when the id says otherwise", () => {
    const p = page(
      [el("a"), el("b"), el("c"), el("d")],
      [primary({ a: box(0, 0, 10, 10), b: box(30, 0, 20, 10), c: box(30, 0, 10, 10), d: box(90, 0, 10, 10) })],
    );
    const [out] = distributeElements([p], "pg", ["a", "b", "c", "d"], "horizontal");
    expect(out.layouts[0].placements.c.x).toBe(26.6667);
    expect(out.layouts[0].placements.b.x).toBe(53.3333);
  });

  it.each([
    ["the later id first on the page", ["a", "c", "b", "d"]],
    ["the earlier id first on the page", ["a", "b", "c", "d"]],
  ])("orders two controls on the same edges by id, %s", (_name, order) => {
    const p = page(
      order.map((id) => el(id)),
      [primary({ a: box(0, 0, 10, 10), b: box(30, 0, 10, 10), c: box(30, 0, 10, 10), d: box(90, 0, 10, 10) })],
    );
    const [out] = distributeElements([p], "pg", ["a", "b", "c", "d"], "horizontal");
    expect(out.layouts[0].placements.b.x).toBe(30);
    expect(out.layouts[0].placements.c.x).toBe(60);
  });

  it("carries the run on from where a locked control would have gone", () => {
    const p = page(
      [el("a"), el("b", "button", null, { locked: true }), el("c"), el("d")],
      [primary({ a: box(0, 0, 10, 10), b: box(15, 0, 10, 10), c: box(20, 0, 10, 10), d: box(90, 0, 10, 10) })],
    );
    const [out] = distributeElements([p], "pg", ["a", "b", "c", "d"], "horizontal");
    expect(out.layouts[0].placements.b).toEqual(box(15, 0, 10, 10));
    expect(out.layouts[0].placements.c.x).toBe(60);
  });

  it("hands back the same page when every control between the ends is locked", () => {
    const p = page(
      [el("a"), el("b", "button", null, { locked: true }), el("c")],
      [primary({ a: box(0, 0, 10, 10), b: box(15, 0, 10, 10), c: box(90, 0, 10, 10) })],
    );
    expect(distributeElements([p], "pg", ["a", "b", "c"], "horizontal")[0]).toBe(p);
  });

  it("measures a control with no box from the default box", () => {
    const p = page(
      [el("a"), el("b"), el("c")],
      [primary({ a: box(0, 0, 10, 10), c: box(0, 90, 10, 10) })],
    );
    const [out] = distributeElements([p], "pg", ["a", "b", "c"], "vertical");
    expect(out.layouts[0].placements.b).toEqual(box(0, 43.75, 25, 12.5));
  });

  it("counts what is left once a container's own children are dropped, and leaves other pages alone", () => {
    const other = page([el("a")], [primary({ a: box(0, 0, 10, 10) })], "other");
    const p = page(
      [el("g", "group"), el("kid", "button", "g"), el("c")],
      [primary({ g: box(0, 0, 50, 50), kid: box(0, 0, 10, 10), c: box(90, 0, 10, 10) })],
    );
    const out = distributeElements([other, p], "pg", ["g", "kid", "c"], "horizontal");
    expect(out[0]).toBe(other);
    expect(out[1]).toBe(p);
  });
});

describe("matchSizeElements", () => {
  it("hands back the same page when the first control selected has no box", () => {
    const p = page([el("a"), el("b")], [primary({ b: box(0, 0, 10, 10) })]);
    expect(matchSizeElements([p], "pg", ["a", "b"], "match-both")[0]).toBe(p);
  });

  it("skips a control with no box, and a locked one", () => {
    const p = page(
      [el("a"), el("b"), el("c", "button", null, { locked: true }), el("d")],
      [primary({ a: box(0, 0, 30, 40), c: box(0, 0, 10, 10), d: box(5, 5, 10, 10) })],
    );
    const [out] = matchSizeElements([p], "pg", ["a", "b", "c", "d"], "match-both");
    expect(out.layouts[0].placements).toEqual({
      a: box(0, 0, 30, 40),
      c: box(0, 0, 10, 10),
      d: box(5, 5, 30, 40),
    });
  });

  it("hands back the same page for one control, and leaves other pages alone", () => {
    const other = page([el("a")], [primary({ a: box(0, 0, 10, 10) })], "other");
    const p = page([el("a")], [primary({ a: box(0, 0, 10, 10) })]);
    const out = matchSizeElements([other, p], "pg", ["a"], "match-width");
    expect(out[0]).toBe(other);
    expect(out[1]).toBe(p);
  });
});

describe("duplicatePage", () => {
  const source = (): UIPage =>
    ({
      id: "p1",
      name: "Main",
      snap: { enabled: true, x: 10, y: 10 },
      elements: [el("g", "group"), el("kid", "button", "g"), el("stray", "label", "elsewhere")],
      layouts: [
        primary(
          { kid: box(1, 2, 3, 4), gone: box(9, 9, 9, 9), g: box(5, 6, 7, 8), stray: box(0, 0, 1, 1) },
          { hidden: ["stray", "gone"] },
        ),
        { id: "portrait", orientation: "portrait", primary: false, inherits: "landscape" } as unknown as Layout,
      ],
    }) as UIPage;

  it("hands back the same pages when the page is not there", () => {
    const pages = [source()];
    expect(duplicatePage(pages, "nope")).toBe(pages);
  });

  it("inserts the copy straight after the page, with the next free id and name", () => {
    const pages = [
      source(),
      { id: "p1_copy", name: "Main (Copy)", elements: [], layouts: [] } as unknown as UIPage,
      { id: "after", name: "After", elements: [], layouts: [] } as unknown as UIPage,
    ];
    const out = duplicatePage(pages, "p1");
    expect(out.map((p) => p.id)).toEqual(["p1", "p1_copy_2", "p1_copy", "after"]);
    expect(out[1].name).toBe("Main (Copy 2)");
  });

  it("names each copied control from its type and the copy's id, past every id taken or reserved", () => {
    const out = duplicatePage([source()], "p1", ["group_p1_copy_1"]);
    expect(out[1].elements.map((e) => [e.id, e.parent])).toEqual([
      ["group_p1_copy_2", null],
      ["button_p1_copy_1", "group_p1_copy_2"],
      ["label_p1_copy_1", "elsewhere"],
    ]);
  });

  it("counts on past an id it has just handed out to a control of the same type", () => {
    const two = { id: "p1", name: "Main", elements: [el("a"), el("b")], layouts: [] } as unknown as UIPage;
    expect(duplicatePage([two], "p1")[1].elements.map((e) => e.id)).toEqual(["button_p1_copy_1", "button_p1_copy_2"]);
  });

  it("renames every layout's boxes in their order and its hidden list, keeping ids it does not know", () => {
    const out = duplicatePage([source()], "p1", ["group_p1_copy_1"]);
    const [main, portrait] = out[1].layouts;
    expect(Object.entries(main.placements)).toEqual([
      ["button_p1_copy_1", box(1, 2, 3, 4)],
      ["gone", box(9, 9, 9, 9)],
      ["group_p1_copy_2", box(5, 6, 7, 8)],
      ["label_p1_copy_1", box(0, 0, 1, 1)],
    ]);
    expect(main.hidden).toEqual(["label_p1_copy_1", "gone"]);
    expect(portrait).toEqual({
      id: "portrait", orientation: "portrait", primary: false, inherits: "landscape", placements: {}, hidden: [],
    });
  });

  it("copies the rest of the page deep and leaves the source as it was", () => {
    const src = source();
    const before = JSON.stringify(src);
    const out = duplicatePage([src], "p1");
    expect(out[1].snap).toEqual(src.snap);
    expect(out[1].snap).not.toBe(src.snap);
    expect(JSON.stringify(src)).toBe(before);
    expect(out[0]).toBe(src);
  });

  it("gives a page with no layouts an empty list", () => {
    const bare = { id: "p1", name: "Main", elements: [el("a")] } as unknown as UIPage;
    expect(duplicatePage([bare], "p1")[1].layouts).toEqual([]);
  });
});
