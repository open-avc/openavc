import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ProjectConfig } from "../api/types";

const { getProject, listAssets, listBackups } = vi.hoisted(() => ({
  getProject: vi.fn(),
  listAssets: vi.fn(),
  listBackups: vi.fn(),
}));

vi.mock("../api/restClient", () => ({
  getProject,
  listAssets,
  listBackups,
  listLibrary: async () => [],
  getAssetUrl: (name: string) => `/assets/${name}`,
}));

import { useProjectStore } from "../store/projectStore";
import { ProjectView } from "./ProjectView";

const project = () => ({
  project: { id: "test_space", name: "Test Space", description: "" },
  openavc_version: "0.13.0",
  ui: { pages: [] },
  plugins: {},
} as unknown as ProjectConfig);

beforeEach(() => {
  vi.resetAllMocks();
  useProjectStore.setState({
    project: project(), dirty: false, saving: false, loadGeneration: 0,
    error: null, conflictDetected: false,
  });
  getProject.mockImplementation(async () => project());
  listAssets.mockResolvedValue({ assets: [] });
  listBackups.mockResolvedValue([]);
});

async function emptyProgram() {
  render(<ProjectView />);
  await screen.findByText("No assets uploaded yet.");
  await screen.findByText(/No backups yet/);
}

function updatedResources() {
  listAssets.mockResolvedValue({ assets: [
    { name: "logo.png", size: 100, type: "image", extension: "png",
      used_by: ["master element 'brand_logo'"] },
  ] });
  listBackups.mockResolvedValue([
    { filename: "backups/backup.zip", reason: "Before opening 'Test Space'",
      timestamp: "2026-01-01T12:00:00Z", size: 100, format: "zip" },
  ]);
}

describe("project resource refresh", () => {
  it.each(["forceReload", "load"] as const)(
    "refreshes assets and backups after %s even when the project ID is unchanged",
    async (method) => {
      await emptyProgram();
      updatedResources();
      await act(async () => { await useProjectStore.getState()[method](); });
      expect(await screen.findByRole("img", { name: "logo.png" })).toBeVisible();
      expect(await screen.findByText("Before opening 'Test Space'")).toBeVisible();
      expect(screen.queryByText(/No backups yet/)).toBeNull();
      expect(screen.queryByText("unused")).toBeNull();
    },
  );

  it("marks only an asset confirmed unused, while preserving unsaved page references", async () => {
    listAssets.mockResolvedValue({ assets: [
      { name: "spare.png", size: 100, type: "image", extension: "png", used_by: [] },
      { name: "unknown.png", size: 100, type: "image", extension: "png" },
      { name: "draft.png", size: 100, type: "image", extension: "png", used_by: [] },
    ] });
    const p = project();
    p.ui.pages = [{ id: "main", elements: [{ id: "draft", src: "assets://draft.png" }] }] as ProjectConfig["ui"]["pages"];
    useProjectStore.setState({ project: p });
    render(<ProjectView />);
    await screen.findByRole("img", { name: "spare.png" });
    expect(screen.getAllByText("unused")).toHaveLength(1);
    expect(screen.getByText("unused").closest("div")?.textContent).toContain("100 B");
    const spareCard = screen.getByRole("img", { name: "spare.png" }).parentElement;
    expect(spareCard?.textContent).toContain("unused");
  });

  it("keeps the accepted project's resources after an unsaved or failed reload", async () => {
    await emptyProgram();
    updatedResources();
    await act(async () => {
      useProjectStore.getState().updateProject({ description: "unsaved" });
      expect(await useProjectStore.getState().load()).toBe(false);
    });
    getProject.mockRejectedValue(new Error("unavailable"));
    await act(async () => { await useProjectStore.getState().forceReload(); });
    await waitFor(() => expect(useProjectStore.getState().loading).toBe(false));
    expect(screen.queryByRole("img", { name: "logo.png" })).toBeNull();
    expect(listAssets).toHaveBeenCalledTimes(1);
    expect(listBackups).toHaveBeenCalledTimes(1);
  });
});
