import { describe, it, expect, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// The toolbar's arrange tools as an author uses them: the selection on the page
// being edited, read and written in the arrangement on screen. The real toolbar
// over the real stores (test/uiBuilderHarness).

import { CanvasToolbar } from "./CanvasToolbar";
import { useProjectStore } from "../../store/projectStore";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import { el, makeProject, page, resetMocks, seed } from "../../test/uiBuilderHarness";
import type { UIPage } from "../../api/types";

const LANDSCAPE = {
  a: { x: 10, y: 10, w: 10, h: 10 },
  b: { x: 30, y: 20, w: 10, h: 10 },
  c: { x: 70, y: 30, w: 10, h: 10 },
};
const PORTRAIT = {
  a: { x: 5, y: 40, w: 20, h: 10 },
  b: { x: 35, y: 50, w: 10, h: 12 },
  c: { x: 80, y: 60, w: 10, h: 10 },
};

function threeControls(): UIPage {
  const p = page("main", [el("a", "button"), el("b", "button"), el("c", "button")], LANDSCAPE);
  return { ...p, layouts: [p.layouts![0], { ...p.layouts![1], placements: PORTRAIT }] } as UIPage;
}

const pages = () => useProjectStore.getState().project!.ui.pages;
const boxes = (layoutId: string) => pages()[0].layouts!.find((l) => l.id === layoutId)!.placements;
const undoNames = () => useUIBuilderStore.getState().undoStack.map((e) => e.description);

function showToolbar(selected: string[], activeLayoutId: string | null, selectedPageId: string | null = "main") {
  seed(makeProject({ pages: [threeControls(), page("other", [el("z", "button")], { z: { x: 0, y: 0, w: 10, h: 10 } })] }), {
    selectedPageId,
    selectedElementIds: selected,
    activeLayoutId,
  });
  render(<CanvasToolbar pages={pages()} selectedPageId={selectedPageId} />);
}

const click = (title: string) => fireEvent.click(screen.getByTitle(title));

beforeEach(() => {
  resetMocks();
});

describe("the arrange tools in a portrait arrangement", () => {
  it("align reads and writes the portrait boxes and leaves the landscape ones", () => {
    showToolbar(["a", "b"], "main_port");
    click("Align left");
    expect(boxes("main_port").b).toEqual({ ...PORTRAIT.b, x: 5 });
    expect(boxes("main_land")).toEqual(LANDSCAPE);
    expect(undoNames()).toEqual(["Align align-left"]);
  });

  it("distribute evens the portrait gaps", () => {
    showToolbar(["a", "b", "c"], "main_port");
    click("Distribute horizontally (equal gaps between elements)");
    expect(boxes("main_port").b).toEqual({ ...PORTRAIT.b, x: 47.5 });
    expect(boxes("main_land")).toEqual(LANDSCAPE);
    expect(undoNames()).toEqual(["Distribute horizontally"]);
  });

  it("match size takes the first selected control's portrait size", () => {
    showToolbar(["a", "b"], "main_port");
    click("Match width and height to the first element selected");
    expect(boxes("main_port").b).toEqual({ ...PORTRAIT.b, w: 20, h: 10 });
    expect(boxes("main_land")).toEqual(LANDSCAPE);
    expect(undoNames()).toEqual(["Match size"]);
  });
});

describe("the arrange tools in the primary arrangement", () => {
  it("align, distribute and match size write the landscape boxes", () => {
    showToolbar(["a", "b", "c"], null);
    click("Align top");
    expect(boxes("main_land")).toEqual({ a: LANDSCAPE.a, b: { ...LANDSCAPE.b, y: 10 }, c: { ...LANDSCAPE.c, y: 10 } });
    click("Distribute horizontally (equal gaps between elements)");
    expect(boxes("main_land").b.x).toBe(40);
    click("Match height to the first element selected");
    expect(boxes("main_port")).toEqual(PORTRAIT);
    expect(undoNames()).toEqual(["Align align-top", "Distribute horizontally", "Match height"]);
  });
});

describe("with no page on screen", () => {
  it("offers none of the arrange tools, whatever is selected", () => {
    showToolbar(["a", "b", "c"], "main_port", null);
    expect(screen.queryByTitle("Align left")).toBeNull();
    expect(screen.queryByTitle("Distribute vertically (equal gaps between elements)")).toBeNull();
    expect(screen.queryByTitle("Match width to the first element selected")).toBeNull();
    expect(undoNames()).toEqual([]);
  });
});
