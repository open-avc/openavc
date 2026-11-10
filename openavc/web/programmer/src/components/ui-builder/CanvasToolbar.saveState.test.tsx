import { describe, it, expect, beforeEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

// What the Builder's toolbar says about saving, and its Save button: the real
// toolbar over the real stores, with the store's save calls replaced by spies
// (test/uiBuilderHarness).

import { CanvasToolbar } from "./CanvasToolbar";
import { useProjectStore } from "../../store/projectStore";
import { makeProject, resetMocks, saves, seed } from "../../test/uiBuilderHarness";

beforeEach(() => {
  resetMocks();
  seed(makeProject(), { selectedPageId: "main" });
});

const IDLE = { dirty: false, saving: false, savePending: false, error: null, conflictDetected: false };

const draw = (state: Record<string, unknown> = {}) => {
  act(() => useProjectStore.setState({ ...IDLE, ...state } as never));
  const p = useProjectStore.getState().project!;
  return render(<CanvasToolbar pages={p.ui.pages} selectedPageId="main" />);
};

const saveButton = () => screen.getByTitle("Save now (Ctrl+S)") as HTMLButtonElement;

describe("the toolbar's save state", () => {
  it("says the save failed, with the reason on hover", () => {
    draw({ error: "Disk full", dirty: true, saving: true, savePending: true });
    const word = screen.getByText("Save failed");
    expect(word.getAttribute("title")).toBe("Disk full");
    expect(word.style.color).toBe("var(--color-error, #d33)");
    expect(screen.queryByText("Saving...")).toBeNull();
  });

  it("leaves a version conflict to its own banner and says what else is true", () => {
    draw({ error: "Someone else saved", conflictDetected: true, dirty: true });
    expect(screen.queryByText("Save failed")).toBeNull();
    expect(screen.getByText("Unsaved")).toBeTruthy();
  });

  it("says Saving..., then Pending..., then Unsaved, then Saved, the first that is true", () => {
    const r = draw({ saving: true, savePending: true, dirty: true });
    expect(screen.getByText("Saving...")).toBeTruthy();
    r.unmount();
    const r2 = draw({ saving: false, savePending: true, dirty: true });
    expect(screen.getByText("Pending...").getAttribute("title")).toBe("Will save shortly");
    r2.unmount();
    const r3 = draw({ savePending: false, dirty: true });
    expect(screen.getByText("Unsaved").style.color).toBe("var(--color-warning)");
    r3.unmount();
    draw({ dirty: false });
    expect(screen.getByText("Saved")).toBeTruthy();
  });
});

describe("the toolbar's Save button", () => {
  it("is off with nothing to save, and on while changes are unsaved or the last save failed", () => {
    const r = draw({ dirty: false, error: null });
    expect(saveButton().disabled).toBe(true);
    expect(saveButton().style.opacity).toBe("0.5");
    r.unmount();
    const r2 = draw({ dirty: true });
    expect(saveButton().disabled).toBe(false);
    expect(saveButton().style.background).toBe("var(--accent-bg)");
    r2.unmount();
    draw({ dirty: false, error: "Disk full", conflictDetected: true });
    expect(saveButton().disabled).toBe(false);
    expect(saveButton().style.opacity).toBe("1");
  });

  it("saves the waiting save now, and saves directly while changes are unsaved and none is running", () => {
    const r = draw({ dirty: true, saving: false });
    fireEvent.click(saveButton());
    expect(saves.flushSave).toHaveBeenCalledTimes(1);
    expect(saves.save).toHaveBeenCalledTimes(1);
    r.unmount();
    const r2 = draw({ dirty: true, saving: true });
    fireEvent.click(saveButton());
    expect(saves.flushSave).toHaveBeenCalledTimes(2);
    expect(saves.save).toHaveBeenCalledTimes(1);
    r2.unmount();
    draw({ dirty: false, saving: false, error: "Disk full" });
    fireEvent.click(saveButton());
    expect(saves.flushSave).toHaveBeenCalledTimes(3);
    expect(saves.save).toHaveBeenCalledTimes(1);
  });

  it("reads the store once, before the flush", () => {
    draw({ dirty: true, saving: false });
    saves.flushSave.mockImplementation(() => useProjectStore.setState({ dirty: false } as never));
    fireEvent.click(saveButton());
    // A flush that leaves nothing unsaved does not stop the direct save.
    expect(saves.save).toHaveBeenCalledTimes(1);
  });
});
