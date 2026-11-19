import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act, within } from "@testing-library/react";
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
const showLastingWarning = vi.fn();
vi.mock("../store/toastStore", () => ({
  showError: (m: string) => showError(m),
  showInfo: (m: string) => showInfo(m),
  showSuccess: (m: string) => showSuccess(m),
  showLastingWarning: (m: string) => showLastingWarning(m),
}));

// The two heavy children fetch on mount and have nothing to do with transfer.
vi.mock("../components/assets/AssetBrowser", () => ({
  AssetBrowser: () => null,
}));
vi.mock("../components/video-streams/VideoStreamsSection", () => ({
  VideoStreamsSection: () => null,
}));

const exportCurrentProject = vi.fn(async () => {});
const importToLibrary = vi.fn(async (_f: File) => ({
  status: "imported",
  project_id: "test_room",
  installed_drivers: ["acme_matrix"],
  installed_plugins: ["acme_widget"],
  warnings: [] as string[],
}));
const openFromLibrary = vi.fn(async (_id: string, _mode: string, _name?: string): Promise<{
  status: string; project_name: string; warnings?: string[];
}> => ({ status: "created", project_name: "Test Room" }));
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

// The capture below spies on document.createElement; a second spy over the
// first would call itself.
afterEach(() => {
  vi.restoreAllMocks();
});

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
    expect(showLastingWarning).not.toHaveBeenCalled();
  });

  it("says which driver a bundle left out, and why, until it is dismissed", async () => {
    const refused =
      "Driver 'acme_matrix' came with this project but was not installed: This driver requires " +
      "OpenAVC 99.0.0 or later. You are running 0.1.0. Please update OpenAVC first. (used by: d1)";
    importToLibrary.mockResolvedValueOnce({
      status: "imported", project_id: "test_room", installed_drivers: [], installed_plugins: [],
      warnings: [refused],
    });
    const capture = fileInputCapture();
    render(<ProjectView />);
    await userEvent.click(screen.getByRole("button", { name: /^import$/i }));
    await capture.choose(new File([new Uint8Array([0x50, 0x4b])], "test_room.zip", { type: "application/zip" }));

    await waitFor(() => expect(showLastingWarning).toHaveBeenCalledWith(refused));
    expect(showSuccess.mock.calls[0][0]).not.toContain("acme_matrix");
  });
});

describe("Opening a library copy", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    storeState.dirty = false;
  });

  it("says which driver it carried was left out, and why, until it is dismissed", async () => {
    const refused =
      "Driver 'acme_matrix' came with this project but was not installed: Its file did not load.";
    listLibrary.mockResolvedValueOnce([
      { id: "starter_room", name: "Starter Room", description: "", modified: "" },
    ]);
    openFromLibrary.mockResolvedValueOnce({
      status: "created", project_name: "Starter Room", warnings: [refused],
    });
    render(<ProjectView />);
    await userEvent.click(await screen.findByText("Starter Room"));
    const dialog = await screen.findByRole("dialog", { name: "Open Project" });
    await userEvent.click(within(dialog).getByRole("button", { name: /^open$/i }));

    await waitFor(() => expect(openFromLibrary).toHaveBeenCalledWith("starter_room", "Starter Room", undefined));
    await waitFor(() => expect(showLastingWarning).toHaveBeenCalledWith(refused));
  });

  it("says nothing more when everything it carried came in", async () => {
    listLibrary.mockResolvedValueOnce([
      { id: "starter_room", name: "Starter Room", description: "", modified: "" },
    ]);
    openFromLibrary.mockResolvedValueOnce({ status: "created", project_name: "Starter Room", warnings: [] });
    render(<ProjectView />);
    await userEvent.click(await screen.findByText("Starter Room"));
    const dialog = await screen.findByRole("dialog", { name: "Open Project" });
    await userEvent.click(within(dialog).getByRole("button", { name: /^open$/i }));

    await waitFor(() => expect(storeState.forceReload).toHaveBeenCalled());
    expect(showLastingWarning).not.toHaveBeenCalled();
  });
});
