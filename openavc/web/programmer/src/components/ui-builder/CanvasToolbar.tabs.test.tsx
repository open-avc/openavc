import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// What a page tab in the Builder's toolbar says about its page: the real
// toolbar over the real stores (test/uiBuilderHarness).

import { CanvasToolbar } from "./CanvasToolbar";
import { useProjectStore } from "../../store/projectStore";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import { makeProject, resetMocks, seed } from "../../test/uiBuilderHarness";

beforeEach(() => {
  resetMocks();
  seed(makeProject(), { selectedPageId: "main" });
});

describe("a page tab", () => {
  it("says a page shows one the author wrote only when it names a file, and counts the controls otherwise", () => {
    const p = useProjectStore.getState().project!;
    const pages = [
      { ...p.ui.pages[0], render_mode: "custom", custom_file: "main.html" },
      { ...p.ui.pages[1], render_mode: "custom" },
    ] as typeof p.ui.pages;
    render(<CanvasToolbar pages={pages} selectedPageId="main" />);
    expect(screen.getByTitle("Main: shows a page you wrote. Double-click to rename, right-click for more.")).toBeTruthy();
    expect(screen.getByTitle("Other: 1 element. Double-click to rename, right-click for more.")).toBeTruthy();
  });
});

describe("the screen preset list", () => {
  it("says the size the canvas draws: the preset, turned for a portrait arrangement", () => {
    const { rerender } = render(<CanvasToolbar pages={useProjectStore.getState().project!.ui.pages} selectedPageId="main" />);
    const list = () => screen.getByTitle(/^Canvas: /);
    expect(list().title).toBe("Canvas: 1024x600");
    useUIBuilderStore.setState({ activeLayoutId: "main_port", screenPresetIndex: 1 });
    rerender(<CanvasToolbar pages={useProjectStore.getState().project!.ui.pages} selectedPageId="main" />);
    expect(list().title).toBe("Canvas: 800x1280");
  });
});
