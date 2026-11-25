import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, waitFor } from "@testing-library/react";
import { DndContext } from "@dnd-kit/core";

// The design canvas is the panel in a frame with no socket of its own, so a
// saved theme changing on the server reaches it as the Builder's count of
// those changes, on the messages the Builder already sends it.

vi.mock("../../api/matrixPreview", () => ({
  resolveProjectMatrices: (project: unknown) => Promise.resolve(project),
}));

import { Canvas } from "./Canvas";
import type { ProjectConfig, UIPage } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";
import { useThemeFilesStore } from "../../store/themeFilesStore";

const PAGE = {
  id: "main",
  name: "Main",
  elements: [],
  layouts: [{ id: "main_land", orientation: "landscape", primary: true, placements: {}, hidden: [] }],
} as unknown as UIPage;

const PROJECT = {
  devices: [], macros: [], variables: [], scripts: [],
  ui: { settings: { theme_id: "hall-look" }, pages: [PAGE], master_elements: [] },
} as unknown as ProjectConfig;

afterEach(() => {
  useProjectStore.setState({ project: null });
  useThemeFilesStore.setState({ version: 0 });
});

describe("the design canvas", () => {
  it("sends the frame the count of saved-theme changes when it moves", async () => {
    useProjectStore.setState({ project: PROJECT });
    const { container } = render(
      <DndContext>
        <Canvas page={PAGE} previewMode={false} showGrid={false} zoom={1} screenWidth={1280} screenHeight={800} />
      </DndContext>,
    );
    const frame = container.querySelector("iframe") as HTMLIFrameElement;
    const posted = vi.spyOn(frame.contentWindow as Window, "postMessage").mockImplementation(() => {});
    const sentCounts = () => posted.mock.calls
      .map(([message]) => message as { type: string; themesVersion?: number })
      .filter((message) => message.type === "openavc:editor-project")
      .map((message) => message.themesVersion);

    act(() => useThemeFilesStore.getState().bump());
    await waitFor(() => expect(sentCounts()).toContain(1));
  });
});
