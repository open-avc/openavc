import { describe, it, expect, beforeEach } from "vitest";
import { useUIBuilderStore } from "./uiBuilderStore";
import type { ProjectConfig } from "../api/types";

/**
 * The wiring the UI Builder actually runs when the project is refetched:
 * useWebSocket hands the store the project from before and after the
 * refetch, and the store decides whether the history survives. Tested
 * through the real store rather than the pure rule, because the bug was
 * that the caller cleared unconditionally and never asked.
 */

const project = (over: Record<string, unknown> = {}): ProjectConfig =>
  ({
    devices: [],
    macros: [],
    variables: [],
    ...over,
    ui: {
      pages: [{ id: "main", name: "Main", elements: [] }],
      settings: { theme: "dark" },
      ...((over.ui as Record<string, unknown>) ?? {}),
    },
  }) as unknown as ProjectConfig;

const pagesOf = (p: ProjectConfig) => p.ui.pages;

describe("clearUndoHistoryIfStale", () => {
  beforeEach(() => {
    useUIBuilderStore.setState({ undoStack: [], redoStack: [] });
  });

  it("keeps an edit undoable when something else touched the project", () => {
    const before = project();
    useUIBuilderStore.getState().pushUndo({ pages: pagesOf(before) }, "Edit element");

    // A device wrote its learned config back. ui is untouched.
    const cleared = useUIBuilderStore
      .getState()
      .clearUndoHistoryIfStale(before, project({ devices: [{ id: "projector_1" }] }));

    expect(cleared).toBe(false);
    expect(useUIBuilderStore.getState().undoStack).toHaveLength(1);
    expect(useUIBuilderStore.getState().undoStack[0].description).toBe("Edit element");
  });

  it("drops both stacks when the UI changed elsewhere, and says it did", () => {
    const before = project();
    useUIBuilderStore.getState().pushUndo({ pages: pagesOf(before) }, "Edit element");
    useUIBuilderStore.setState({
      redoStack: [{ description: "Move element", snapshot: { pages: pagesOf(before) } }],
    });

    const cleared = useUIBuilderStore
      .getState()
      .clearUndoHistoryIfStale(before, project({ ui: { pages: [] } }));

    // The return value is what puts the message on screen. A history that
    // disappears with nothing said is the half of this bug the user never
    // sees coming.
    expect(cleared).toBe(true);
    expect(useUIBuilderStore.getState().undoStack).toEqual([]);
    expect(useUIBuilderStore.getState().redoStack).toEqual([]);
  });

  it("weighs the redo stack, not just the undo stack", () => {
    const before = project();
    useUIBuilderStore.setState({
      redoStack: [{ description: "Change theme", snapshot: { settings: before.ui.settings } }],
    });

    const cleared = useUIBuilderStore
      .getState()
      .clearUndoHistoryIfStale(before, project({ ui: { settings: { theme: "light" } } }));

    expect(cleared).toBe(true);
    expect(useUIBuilderStore.getState().redoStack).toEqual([]);
  });

  it("says nothing when there was no history to lose", () => {
    const cleared = useUIBuilderStore
      .getState()
      .clearUndoHistoryIfStale(project(), project({ ui: { pages: [] } }));

    expect(cleared).toBe(false);
  });

  it("says nothing on the first load, when there is no project to compare", () => {
    // A refetch with nothing before it reads as stale, which is right for a
    // stack that exists. With no stack it must still stay quiet, or the
    // first connect of every session announces a loss that never happened.
    expect(useUIBuilderStore.getState().clearUndoHistoryIfStale(null, project())).toBe(false);
  });
});
