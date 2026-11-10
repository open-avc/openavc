import { describe, it, expect } from "vitest";
import { demoteFromMaster, promoteToMaster } from "./uiBuilderHelpers";
import { DEFAULT_PLACEMENT } from "./pageGeometry";
import type { Layout, MasterElement, Placement, UIElement, UIPage } from "../../api/types";

// A control made a master element, and a master element put back on a page:
// the box it keeps in each orientation, the id it keeps when the shared ui.<id>
// namespace already holds it, and what comes back when there is nothing to do.
// Every answer below is what the Builder gives today.

const el = (id: string, type = "button", parent: string | null = null, extra: object = {}): UIElement =>
  ({ id, type, bindings: {}, parent, ...extra }) as unknown as UIElement;

const box = (x: number, y: number, w: number, h: number): Placement => ({ x, y, w, h });

const layout = (id: string, orientation: string | undefined, placements: Record<string, Placement>, extra: Partial<Layout> = {}): Layout =>
  ({ id, orientation, placements, hidden: [], ...extra }) as Layout;

function page(elements: UIElement[], layouts: Layout[] | undefined, id = "pg"): UIPage {
  return { id, name: "Page", elements, layouts } as UIPage;
}

const master = (id: string, placements?: Record<string, Placement>, extra: object = {}): MasterElement =>
  ({ id, type: "button", bindings: {}, pages: "*", hidden: false, placements, ...extra }) as unknown as MasterElement;

const selfBound = (id: string) => ({
  show: { look: { source: "state", key: `ui.${id}.value`, condition: { equals: true } } },
});

describe("promoteToMaster: the box a master keeps in each orientation", () => {
  it("takes the primary's box over an earlier arrangement of the same orientation", () => {
    const pages = [page([el("a")], [
      layout("wide", "landscape", { a: box(1, 1, 10, 10) }),
      layout("main", "landscape", { a: box(2, 2, 20, 20) }, { primary: true }),
    ])];
    const out = promoteToMaster(pages, [], "pg", "a");
    expect(out.masterElements[0].placements).toEqual({ landscape: box(2, 2, 20, 20) });
  });

  it("keeps the first of two arrangements of an orientation when neither is the primary", () => {
    const pages = [page([el("a")], [
      layout("main", "landscape", { a: box(2, 2, 20, 20) }, { primary: true }),
      layout("tall", "portrait", { a: box(3, 3, 30, 30) }),
      layout("tall_2", "portrait", { a: box(4, 4, 40, 40) }),
    ])];
    const out = promoteToMaster(pages, [], "pg", "a");
    expect(out.masterElements[0].placements).toEqual({
      landscape: box(2, 2, 20, 20),
      portrait: box(3, 3, 30, 30),
    });
  });

  it("files an arrangement with no orientation under landscape", () => {
    const pages = [page([el("a")], [layout("main", undefined, { a: box(5, 6, 7, 8) }, { primary: true })])];
    const out = promoteToMaster(pages, [], "pg", "a");
    expect(out.masterElements[0].placements).toEqual({ landscape: box(5, 6, 7, 8) });
  });

  it("rounds the page-space box", () => {
    const pages = [page([el("g", "group"), el("a", "button", "g")], [
      layout("main", "landscape", { g: box(0, 0, 33.333, 50), a: box(10, 10, 33.333, 20) }, { primary: true }),
    ])];
    const out = promoteToMaster(pages, [], "pg", "a");
    expect(out.masterElements[0].placements).toEqual({ landscape: box(3.3333, 5, 11.1109, 10) });
  });

  it("gives a page with no arrangements the default box under landscape, as a copy", () => {
    const pages = [page([el("a")], undefined)];
    const out = promoteToMaster(pages, [], "pg", "a");
    const placements = out.masterElements[0].placements as Record<string, Placement>;
    expect(placements).toEqual({ landscape: DEFAULT_PLACEMENT });
    expect(placements.landscape).not.toBe(DEFAULT_PLACEMENT);
  });

  it("gives a portrait primary its box under portrait", () => {
    const pages = [page([el("a")], [layout("main", "portrait", { a: box(1, 2, 3, 4) }, { primary: true })])];
    const out = promoteToMaster(pages, [], "pg", "a");
    expect(out.masterElements[0].placements).toEqual({ portrait: box(1, 2, 3, 4) });
  });
});

describe("promoteToMaster: what the master is, and what the page keeps", () => {
  it("drops the parent link and is shown on every page, not hidden", () => {
    const pages = [page([el("g", "group"), el("a", "button", "g", { label: "Go" })], [
      layout("main", "landscape", { g: box(0, 0, 100, 100), a: box(0, 0, 10, 10) }, { primary: true }),
    ])];
    const out = promoteToMaster(pages, [], "pg", "a");
    expect(out.masterElements[0]).toEqual({
      id: "a", type: "button", bindings: {}, label: "Go", parent: null, pages: "*",
      placements: { landscape: box(0, 0, 10, 10) }, hidden: false,
    });
    expect(out.pages[0].elements.map((e) => e.id)).toEqual(["g"]);
    expect(out.pages[0].layouts![0].placements).toEqual({ g: box(0, 0, 100, 100) });
  });

  it("adds the master after the ones already there", () => {
    const pages = [page([el("a")], [layout("main", "landscape", {}, { primary: true })])];
    const out = promoteToMaster(pages, [master("m1")], "pg", "a");
    expect(out.masterElements.map((m) => m.id)).toEqual(["m1", "a"]);
  });

  it("keeps an id only its own page held", () => {
    const pages = [page([el("a", "button", null, { bindings: selfBound("a") })], [layout("main", "landscape", {}, { primary: true })])];
    const out = promoteToMaster(pages, [], "pg", "a");
    expect(out.masterElements[0].id).toBe("a");
    expect(out.masterElements[0].bindings).toEqual(selfBound("a"));
  });

  it("renames a control whose id another page holds, and rewrites its own references", () => {
    const pages = [
      page([el("a", "button", null, { bindings: selfBound("a") })], [layout("main", "landscape", {}, { primary: true })]),
      page([el("a", "label")], [layout("main", "landscape", {}, { primary: true })], "other"),
    ];
    const out = promoteToMaster(pages, [], "pg", "a");
    expect(out.masterElements[0].id).toBe("button_1");
    expect(out.masterElements[0].bindings).toEqual(selfBound("button_1"));
  });

  it("names a renamed control clear of every page and every master", () => {
    const pages = [
      page([el("a")], [layout("main", "landscape", {}, { primary: true })]),
      page([el("a", "label"), el("button_1")], [layout("main", "landscape", {}, { primary: true })], "other"),
    ];
    const out = promoteToMaster(pages, [master("button_2")], "pg", "a");
    expect(out.masterElements.map((m) => m.id)).toEqual(["button_2", "button_3"]);
  });

  it.each([
    ["no such page", "nope", "a"],
    ["no such control on the page", "pg", "nope"],
  ])("hands back the same pages and masters for %s", (_name, pageId, elementId) => {
    const pages = [page([el("a")], [layout("main", "landscape", {}, { primary: true })])];
    const masters = [master("m1")];
    const out = promoteToMaster(pages, masters, pageId, elementId);
    expect(out.pages).toBe(pages);
    expect(out.masterElements).toBe(masters);
  });
});

describe("demoteFromMaster: the box a control comes back with", () => {
  const target = () => page([], [layout("tall", "portrait", {}), layout("main", "landscape", {}, { primary: true })]);

  it.each([
    ["the landscape box first", { portrait: box(1, 1, 1, 1), landscape: box(2, 2, 2, 2) }, box(2, 2, 2, 2)],
    ["the portrait box with no landscape one", { other: box(3, 3, 3, 3), portrait: box(1, 1, 1, 1) }, box(1, 1, 1, 1)],
    ["the first box stored, with neither", { other: box(3, 3, 3, 3), more: box(4, 4, 4, 4) }, box(3, 3, 3, 3)],
    ["the default box with none stored", {}, DEFAULT_PLACEMENT],
    ["the default box with no placements at all", undefined, DEFAULT_PLACEMENT],
  ])("takes %s, into the target page's primary arrangement", (_name, placements, expected) => {
    const out = demoteFromMaster([target()], [master("m", placements as Record<string, Placement> | undefined)], "m", "pg");
    expect(out.pages[0].layouts![1].placements).toEqual({ m: expected });
    expect(out.pages[0].layouts![0].placements).toEqual({});
  });

  it("writes the default box as a copy", () => {
    const out = demoteFromMaster([target()], [master("m")], "m", "pg");
    expect(out.pages[0].layouts![1].placements.m).not.toBe(DEFAULT_PLACEMENT);
  });

  it("strips the master-only fields and keeps the rest", () => {
    const out = demoteFromMaster([target()], [master("m", { landscape: box(0, 0, 5, 5) }, { label: "Go", parent: "g" })], "m", "pg");
    expect(out.pages[0].elements).toEqual([{ id: "m", type: "button", bindings: {}, label: "Go", parent: "g" }]);
    expect(out.masterElements).toEqual([]);
  });

  it("leaves the other pages as they were", () => {
    const other = page([el("x")], [layout("main", "landscape", {}, { primary: true })], "other");
    const out = demoteFromMaster([other, target()], [master("m")], "m", "pg");
    expect(out.pages[0]).toBe(other);
  });

  it("renames a master whose id a control on another page holds, and rewrites its own references", () => {
    const other = page([el("m", "label")], [layout("main", "landscape", {}, { primary: true })], "other");
    const out = demoteFromMaster([target(), other], [master("m", undefined, { bindings: selfBound("m") })], "m", "pg");
    expect(out.pages[0].elements[0].id).toBe("button_1");
    expect(out.pages[0].elements[0].bindings).toEqual(selfBound("button_1"));
  });

  it("names a renamed control clear of the remaining masters", () => {
    const other = page([el("m", "label")], [layout("main", "landscape", {}, { primary: true })], "other");
    const out = demoteFromMaster([target(), other], [master("m"), master("button_1")], "m", "pg");
    expect(out.pages[0].elements[0].id).toBe("button_2");
    expect(out.masterElements.map((m) => m.id)).toEqual(["button_1"]);
  });

  it("drops every master sharing the id, and puts back the first", () => {
    const masters = [master("m", undefined, { label: "first" }), master("m", undefined, { label: "second" }), master("n")];
    const out = demoteFromMaster([target()], masters, "m", "pg");
    expect(out.masterElements.map((m) => m.id)).toEqual(["n"]);
    expect(out.pages[0].elements).toEqual([{ id: "m", type: "button", bindings: {}, label: "first" }]);
  });

  it("hands back the same pages and masters when the master is not there", () => {
    const pages = [target()];
    const masters = [master("m")];
    const out = demoteFromMaster(pages, masters, "nope", "pg");
    expect(out.pages).toBe(pages);
    expect(out.masterElements).toBe(masters);
  });
});
