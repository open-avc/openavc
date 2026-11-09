import { describe, it, expect } from "vitest";
import { addLayout, addPage } from "./uiBuilderHelpers";
import type { UIPage } from "../../api/types";

// What a new page and a new arrangement are called, and what they start as.

const named = (id: string, name: string): UIPage =>
  ({ id, name, elements: [], layouts: [] }) as unknown as UIPage;

describe("addPage", () => {
  it("starts a project's first page as page_1, Page 1, on the 12x8 ruler with one landscape arrangement", () => {
    expect(addPage([])).toEqual([
      {
        id: "page_1",
        name: "Page 1",
        snap: { enabled: true, x: 100 / 12, y: 100 / 8 },
        elements: [],
        layouts: [{ id: "landscape", orientation: "landscape", primary: true, placements: {}, hidden: [] }],
      },
    ]);
  });

  it("counts the id past every page id taken, and the name on from the id's number", () => {
    const pages = [named("page_1", "Lobby"), named("x", "Page 2")];
    const added = addPage(pages).at(-1)!;
    expect([added.id, added.name]).toEqual(["page_2", "Page 3"]);
  });

  it("adds an overlay on a coarser ruler with its box as a share of the screen", () => {
    expect(addPage([named("overlay_1", "Overlay 1")], "overlay").at(-1)).toEqual({
      id: "overlay_2",
      name: "Overlay 2",
      snap: { enabled: true, x: 25, y: 25 },
      elements: [],
      layouts: [{ id: "landscape", orientation: "landscape", primary: true, placements: {}, hidden: [] }],
      page_type: "overlay",
      overlay: {
        width: 31.25,
        height: 37.5,
        position: "center",
        backdrop: "dim",
        dismiss_on_backdrop: true,
        animation: "fade",
      },
    });
  });

  it("adds a sidebar that slides in from the right", () => {
    const added = addPage([], "sidebar").at(-1)!;
    expect([added.id, added.name, added.page_type]).toEqual(["sidebar_1", "Sidebar 1", "sidebar"]);
    expect(added.overlay).toEqual({
      width: 25,
      side: "right",
      backdrop: "dim",
      dismiss_on_backdrop: true,
      animation: "slide-left",
    });
  });
});

describe("addLayout", () => {
  it("names a second arrangement of one orientation with a count, and a third with the next", () => {
    const base = {
      id: "pg",
      name: "Page",
      elements: [],
      layouts: [{ id: "landscape", orientation: "landscape", primary: true, placements: {}, hidden: [] }],
    } as unknown as UIPage;
    const once = addLayout(base, "portrait");
    const thrice = addLayout(addLayout(once, "portrait"), "portrait");
    expect(thrice.layouts.map((l) => [l.id, l.inherits])).toEqual([
      ["landscape", undefined],
      ["portrait", "landscape"],
      ["portrait_2", "landscape"],
      ["portrait_3", "landscape"],
    ]);
  });

  it("starts a page with no arrangements with one that inherits nothing", () => {
    const bare = { id: "pg", name: "Page", elements: [] } as unknown as UIPage;
    expect(addLayout(bare, "portrait").layouts).toEqual([
      { id: "portrait", orientation: "portrait", primary: false, inherits: null, placements: {}, hidden: [] },
    ]);
  });
});
