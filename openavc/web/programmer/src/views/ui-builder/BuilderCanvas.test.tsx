import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen } from "@testing-library/react";

// The canvas column on its own: it follows the Builder store's view settings
// itself, whether or not whatever draws it is drawn again.

vi.mock("../../components/ui-builder/Canvas", async () => (await import("../../test/uiBuilderMocks")).stubs.Canvas);

import { BuilderCanvas } from "./BuilderCanvas";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import { makeProject, seed, seen } from "../../test/uiBuilderHarness";

const call = (fn: () => unknown) => act(() => { fn(); });
const builder = () => useUIBuilderStore.getState();

beforeEach(() => {
  for (const k of Object.keys(seen)) delete seen[k];
  seed(makeProject(), { selectedPageId: "main" });
});

describe("the canvas column", () => {
  it("draws the canvas as the store's view settings change, drawn once by its parent", () => {
    const project = makeProject();
    const page = project.ui.pages[0];
    render(<BuilderCanvas page={page} masters={project.ui.master_elements} />);
    expect(seen.Canvas).toMatchObject({ page, previewMode: false, showGrid: true, zoom: 1, screenWidth: 1024, screenHeight: 600 });
    expect(seen.Canvas.masterElements).toBe(project.ui.master_elements);
    call(() => builder().setPreviewMode(true));
    expect(seen.Canvas.previewMode).toBe(true);
    call(() => builder().selectLayout("main_port"));
    expect(seen.Canvas).toMatchObject({ screenWidth: 600, screenHeight: 1024 });
    call(() => builder().setZoom(2));
    call(() => builder().toggleGrid());
    call(() => builder().setScreenPresetIndex(1));
    expect(seen.Canvas).toMatchObject({ previewMode: true, showGrid: false, zoom: 2, screenWidth: 800, screenHeight: 1280 });
  });

  it("says there are no pages yet, and draws no canvas, without a page", () => {
    render(<BuilderCanvas page={null} masters={undefined} />);
    expect(screen.getByText("No pages yet. Click + to add a page.")).toBeInTheDocument();
    expect(seen.Canvas).toBeUndefined();
  });
});
