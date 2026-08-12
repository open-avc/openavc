import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Program > Export used to write the store to a Blob client-side, which meant
// the most obvious way to move a room to another machine shipped the settings
// and left every custom control, driver and plugin behind. It goes through the
// server bundle now, and its Import takes that bundle back.

const storeState = {
  project: {
    project: { id: "test_room", name: "Test Room", description: "" },
    openavc_version: "0.9.0",
    plugins: {},
  },
  dirty: false,
  saving: false,
  etag: "1",
  save: vi.fn(),
  updateProject: vi.fn(),
  forceReload: vi.fn(async () => {}),
};

vi.mock("../store/projectStore", () => ({
  useProjectStore: Object.assign(
    (selector: (s: typeof storeState) => unknown) => selector(storeState),
    { getState: () => storeState },
  ),
}));

const showError = vi.fn();
const showInfo = vi.fn();
const showSuccess = vi.fn();
vi.mock("../store/toastStore", () => ({
  showError: (m: string) => showError(m),
  showInfo: (m: string) => showInfo(m),
  showSuccess: (m: string) => showSuccess(m),
}));

// The two heavy children fetch on mount and have nothing to do with transfer.
vi.mock("../components/assets/AssetBrowser", () => ({
  AssetBrowser: () => null,
}));
vi.mock("../components/video-streams/VideoStreamsSection", () => ({
  VideoStreamsSection: () => null,
}));

const exportCurrentProject = vi.fn(async () => {});
const importToLibrary = vi.fn(async () => ({
  status: "imported",
  project_id: "test_room",
  installed_drivers: ["acme_matrix"],
  installed_plugins: ["acme_widget"],
  warnings: [],
}));
const openFromLibrary = vi.fn(async () => ({ status: "created", project_name: "Test Room" }));
const listLibrary = vi.fn(async () => [
  { id: "test_room", name: "Test Room", description: "", modified: "" },
]);

vi.mock("../api/restClient", () => ({
  exportCurrentProject: () => exportCurrentProject(),
  importToLibrary: (f: File) => importToLibrary(f),
  openFromLibrary: (a: string, b: string, c?: string) => openFromLibrary(a, b, c),
  listLibrary: () => listLibrary(),
  listBackups: async () => [],
  exportLibraryProject: vi.fn(),
  deleteLibraryProject: vi.fn(),
  duplicateLibraryProject: vi.fn(),
  saveToLibrary: vi.fn(),
  createBlankProject: vi.fn(),
  createBackup: vi.fn(),
  restoreBackup: vi.fn(),
  reloadProject: vi.fn(),
  saveProject: vi.fn(),
  ConflictError: class extends Error {},
}));

import { ProjectView } from "./ProjectView";

/** Grab the hidden <input type=file> the handler makes, and hand it a file. */
function fileInputCapture() {
  const real = document.createElement.bind(document);
  const created: HTMLInputElement[] = [];
  vi.spyOn(document, "createElement").mockImplementation(((tag: string) => {
    const el = real(tag);
    if (tag === "input") created.push(el as HTMLInputElement);
    return el;
  }) as typeof document.createElement);
  return {
    async choose(file: File) {
      const input = created.find((el) => el.type === "file");
      if (!input) throw new Error("no file input was created");
      Object.defineProperty(input, "files", { value: [file], configurable: true });
      await act(async () => {
        await input.onchange?.(new Event("change"));
      });
      return input;
    },
    get inputs() {
      return created;
    },
  };
}

describe("Program > Export / Import", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    storeState.dirty = false;
  });

  it("exports through the server so the bundle carries the whole room", async () => {
    render(<ProjectView />);
    await userEvent.click(screen.getByRole("button", { name: /export/i }));
    await waitFor(() => expect(exportCurrentProject).toHaveBeenCalledTimes(1));
    expect(showError).not.toHaveBeenCalled();
  });

  it("refuses to export unsaved edits rather than shipping the wrong project", async () => {
    // The bundle is built on the server from what is on disk. Exporting a
    // dirty project silently would hand over the last save, not the screen.
    storeState.dirty = true;
    render(<ProjectView />);
    await userEvent.click(screen.getByRole("button", { name: /export/i }));
    await waitFor(() => expect(showError).toHaveBeenCalled());
    expect(exportCurrentProject).not.toHaveBeenCalled();
    expect(showError.mock.calls[0][0]).toMatch(/save the project first/i);
  });

  it("takes a bundle back: into the library, then opened as the room", async () => {
    const capture = fileInputCapture();
    render(<ProjectView />);
    await userEvent.click(screen.getByRole("button", { name: /^import$/i }));

    const input = await capture.choose(
      new File([new Uint8Array([0x50, 0x4b])], "test_room.zip", { type: "application/zip" }),
    );
    expect(input.accept).toContain(".zip");

    await waitFor(() => expect(importToLibrary).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(openFromLibrary).toHaveBeenCalledTimes(1));
    expect(openFromLibrary.mock.calls[0][0]).toBe("test_room");
    expect(storeState.forceReload).toHaveBeenCalled();
    // What arrived with it gets said out loud, including the plugins.
    expect(showSuccess.mock.calls[0][0]).toMatch(/acme_widget/);
  });
});
