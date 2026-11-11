import { describe, it, expect, beforeEach } from "vitest";
import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";

// The run of quick edits against the Builder's other writers: the toolbar's
// own Undo and Redo buttons and the lock, each of which reaches the undo
// history without going through the page. A quick edit after any of them is
// undoable on its own. The real hook, the real toolbar, the real stores
// (test/uiBuilderHarness).

import { CanvasToolbar } from "../../components/ui-builder/CanvasToolbar";
import { builder, el, makeProject, page, pageOf, resetMocks, seed } from "../../test/uiBuilderHarness";
import { useProjectStore } from "../../store/projectStore";
import { projectEdit, useEditRun } from "./useEditRun";

const project = () => useProjectStore.getState().project!;
const label = () => pageOf("main").elements.find((e) => e.id === "btn_1")!.label;
const undoNames = () => builder().undoStack.map((e) => e.description);

function relabel(run: ReturnType<typeof useEditRun>, text: string) {
  const pages = project().ui.pages.map((p) => ({
    ...p,
    elements: p.elements.map((e) => (e.id === "btn_1" ? { ...e, label: text } : e)),
  }));
  act(() => run.join(projectEdit(project(), { pages }, "Edit element")));
}

function showBuilder() {
  seed(
    makeProject({ pages: [page("main", [el("btn_1", "button", { label: "Start" })], { btn_1: { x: 0, y: 0, w: 10, h: 10 } })] }),
    { selectedPageId: "main" },
  );
  render(<CanvasToolbar pages={project().ui.pages} selectedPageId="main" />);
  return renderHook(() => useEditRun()).result.current;
}

const clickTitled = (start: string) =>
  act(() => { fireEvent.click(screen.getByTitle((t) => t.startsWith(start))); });

beforeEach(() => {
  resetMocks();
});

describe("the run of quick edits and the Builder's other writers", () => {
  it("ends on the toolbar's Undo button, so the edit after it is undone alone", () => {
    const run = showBuilder();
    relabel(run, "A");
    clickTitled("Undo Edit element");
    expect(label()).toBe("Start");
    relabel(run, "B");
    expect(undoNames()).toEqual(["Edit element"]);
    clickTitled("Undo Edit element");
    expect(label()).toBe("Start");
  });

  it("ends on the toolbar's Redo button, so the edit after it is undone alone", () => {
    const run = showBuilder();
    relabel(run, "A");
    clickTitled("Undo Edit element");
    clickTitled("Redo Edit element");
    expect(label()).toBe("A");
    relabel(run, "B");
    expect(undoNames()).toEqual(["Edit element", "Edit element"]);
    clickTitled("Undo Edit element");
    expect(label()).toBe("A");
  });

  it("ends when another writer pushes an entry of its own: the lock", () => {
    const run = showBuilder();
    relabel(run, "A");
    act(() => builder().toggleLock("btn_1"));
    relabel(run, "B");
    expect(undoNames()).toEqual(["Edit element", "Lock element", "Edit element"]);
    clickTitled("Undo Edit element");
    expect(label()).toBe("A");
  });
});
