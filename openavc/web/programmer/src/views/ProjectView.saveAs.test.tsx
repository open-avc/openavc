import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Save As prefills the running project's own id, so re-saving a room you saved
// earlier -- the common use of the button -- answered "Library project
// 'test_room' already exists" and left the dialog open. The only ways through
// were a second copy under a new id or deleting the saved one first.

const storeState = {
  project: {
    project: { id: "test_room", name: "Test Room", description: "A room" },
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
vi.mock("../store/toastStore", () => ({
  showError: (m: string) => showError(m),
  showInfo: vi.fn(),
  showSuccess: vi.fn(),
}));

vi.mock("../components/assets/AssetBrowser", () => ({ AssetBrowser: () => null }));
vi.mock("../components/video-streams/VideoStreamsSection", () => ({
  VideoStreamsSection: () => null,
}));

const saveToLibrary = vi.fn(async () => ({ status: "created", project_id: "test_room" }));
const replaceInLibrary = vi.fn(async () => ({ status: "replaced", project_id: "test_room" }));
const listLibrary = vi.fn(async () => [
  {
    id: "test_room", name: "Test Room", description: "", modified: "2026-09-01T10:00:00",
    created: "2026-01-04T09:00:00", device_count: 4, page_count: 2, macro_count: 0,
    script_count: 0, required_drivers: [],
  },
]);

vi.mock("../api/restClient", () => ({
  listLibrary: () => listLibrary(),
  saveToLibrary: (d: unknown) => saveToLibrary(d),
  replaceInLibrary: (id: string, d: unknown) => replaceInLibrary(id, d),
  listBackups: async () => [],
  exportCurrentProject: vi.fn(),
  importToLibrary: vi.fn(),
  openFromLibrary: vi.fn(),
  exportLibraryProject: vi.fn(),
  deleteLibraryProject: vi.fn(),
  duplicateLibraryProject: vi.fn(),
  createBlankProject: vi.fn(),
  createBackup: vi.fn(),
  restoreBackup: vi.fn(),
  reloadProject: vi.fn(),
  saveProject: vi.fn(),
  ConflictError: class extends Error {},
}));

import { ProjectView } from "./ProjectView";

async function openSaveAs() {
  render(<ProjectView />);
  await waitFor(() => expect(listLibrary).toHaveBeenCalled());
  await userEvent.click(screen.getByRole("button", { name: "Save As" }));
  return within(screen.getByRole("dialog", { name: "Save to Library" }));
}

beforeEach(() => {
  saveToLibrary.mockClear();
  replaceInLibrary.mockClear();
  showError.mockClear();
});

describe("Save As, on an id the library already holds", () => {
  it("says what is there rather than waiting to refuse", async () => {
    const dialog = await openSaveAs();
    expect(dialog.getByText(/Replaces .*Test Room/)).toBeTruthy();
  });

  it("offers Replace, and replaces", async () => {
    const dialog = await openSaveAs();
    const replace = dialog.getByRole("button", { name: "Replace" });
    await userEvent.click(replace);
    await waitFor(() => expect(replaceInLibrary).toHaveBeenCalledTimes(1));
    expect(replaceInLibrary).toHaveBeenCalledWith(
      "test_room", { name: "Test Room", description: "A room" },
    );
    expect(saveToLibrary).not.toHaveBeenCalled();
    expect(showError).not.toHaveBeenCalled();
  });
});

describe("Save As, on an id that is free", () => {
  it("saves, and says nothing about replacing", async () => {
    const dialog = await openSaveAs();
    const idField = dialog.getByPlaceholderText("e.g. my_boardroom");
    await userEvent.clear(idField);
    await userEvent.type(idField, "second_room");
    expect(dialog.queryByText(/Replaces /)).toBeNull();

    await userEvent.click(dialog.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(saveToLibrary).toHaveBeenCalledTimes(1));
    expect(saveToLibrary).toHaveBeenCalledWith({
      id: "second_room", name: "Test Room", description: "A room",
    });
    expect(replaceInLibrary).not.toHaveBeenCalled();
  });

  it("recognises the id the server will actually use", async () => {
    // The server lowercases and folds punctuation to underscores before it
    // looks, so "Test Room" is the same shelf as "test_room" and typing it
    // must not walk back into the refusal this fixes.
    const dialog = await openSaveAs();
    const idField = dialog.getByPlaceholderText("e.g. my_boardroom");
    await userEvent.clear(idField);
    await userEvent.type(idField, "Test Room");
    expect(dialog.getByText(/Replaces .*Test Room/)).toBeTruthy();
  });
});
