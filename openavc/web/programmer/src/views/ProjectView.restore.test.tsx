import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Restoring a backup used to happen in silence: the confirm dialog closed and
// the row's Restore button, though disabled, kept its full-strength styling and
// the word "Restore", so nothing on screen said the click had landed. The only
// evidence the restore happened at all was a toast that lives three seconds,
// which is easy to miss and impossible to go back to. The dialog also named the
// zip path rather than the backup the user had just pressed.

const storeState = {
  project: {
    project: { id: "test_room", name: "Test Room", description: "A room" },
    openavc_version: "0.13.0",
    plugins: {},
  },
  dirty: false,
  saving: false,
  etag: "1",
  loadGeneration: 0,
  save: vi.fn(),
  updateProject: vi.fn(),
  forceReload: vi.fn(async () => { storeState.loadGeneration += 1; }),
};

vi.mock("../store/projectStore", () => ({
  useProjectStore: Object.assign(
    (selector: (s: typeof storeState) => unknown) => selector(storeState),
    { getState: () => storeState },
  ),
}));

const showSuccess = vi.fn();
const showError = vi.fn();
vi.mock("../store/toastStore", () => ({
  showError: (m: string) => showError(m),
  showInfo: vi.fn(),
  showSuccess: (m: string) => showSuccess(m),
}));

vi.mock("../components/assets/AssetBrowser", () => ({ AssetBrowser: () => null }));
vi.mock("../components/video-streams/VideoStreamsSection", () => ({
  VideoStreamsSection: () => null,
}));

const BACKUP = {
  filename: "backups/backup_20260901_002543_manual_backup.zip",
  reason: "Manual backup",
  timestamp: "2026-09-01T00:25:43+00:00",
  project_name: "Test Room",
  size: 8192,
  format: "zip",
};

/** The row's own words for BACKUP, built the way the view builds them. */
const BACKUP_LABEL = `Manual backup, ${new Date(BACKUP.timestamp).toLocaleString()}`;

const listBackups = vi.fn(async () => [BACKUP]);
const restoreBackup = vi.fn(async (_filename: string) => ({ status: "restored" }));

vi.mock("../api/restClient", () => ({
  listLibrary: async () => [],
  listBackups: () => listBackups(),
  restoreBackup: (f: string) => restoreBackup(f),
  saveToLibrary: vi.fn(),
  replaceInLibrary: vi.fn(),
  exportCurrentProject: vi.fn(),
  importToLibrary: vi.fn(),
  openFromLibrary: vi.fn(),
  exportLibraryProject: vi.fn(),
  deleteLibraryProject: vi.fn(),
  duplicateLibraryProject: vi.fn(),
  createBlankProject: vi.fn(),
  createBackup: vi.fn(),
  reloadProject: vi.fn(),
  saveProject: vi.fn(),
  ConflictError: class extends Error {},
}));

import { ProjectView } from "./ProjectView";

/** Render, wait for the backups list, and open the restore confirm dialog.
 *
 * Waits for the ROW, not for the call: `listBackups` having been called says
 * nothing about its promise having resolved and the list having rendered, and
 * `getByRole` does not retry. On a loaded CI box the click landed in that gap
 * and every test in the file failed with "Unable to find an accessible element
 * with the role button and name Restore".
 */
async function openRestoreDialog() {
  render(<ProjectView />);
  await userEvent.click(await screen.findByRole("button", { name: "Restore" }));
  return within(await screen.findByRole("alertdialog", { name: "Restore Backup" }));
}

beforeEach(() => {
  listBackups.mockClear();
  restoreBackup.mockClear();
  restoreBackup.mockImplementation(async () => ({ status: "restored" }));
  showSuccess.mockClear();
  showError.mockClear();
  storeState.forceReload.mockClear();
  storeState.loadGeneration = 0;
});

describe("the restore confirm dialog", () => {
  it("names the backup the way its row does, not the zip path", async () => {
    const dialog = await openRestoreDialog();

    expect(dialog.getByText(new RegExp(BACKUP_LABEL))).toBeTruthy();
    expect(dialog.queryByText(/\.zip/)).toBeNull();
    expect(dialog.queryByText(/backups\//)).toBeNull();
  });
});

describe("restoring a backup", () => {
  it("says so on the row for as long as it runs", async () => {
    let finish: () => void = () => {};
    restoreBackup.mockImplementation(
      () => new Promise((resolve) => { finish = () => resolve({ status: "restored" }); }),
    );

    const dialog = await openRestoreDialog();
    await userEvent.click(dialog.getByRole("button", { name: "Restore" }));

    // In flight: the row says what is happening and cannot be pressed again.
    const rowButton = await screen.findByRole("button", { name: "Restoring..." });
    expect(rowButton).toBeDisabled();

    finish();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Restore" })).toBeEnabled(),
    );
  });

  it("names the backup it restored when it finishes", async () => {
    const dialog = await openRestoreDialog();
    await userEvent.click(dialog.getByRole("button", { name: "Restore" }));

    await waitFor(() => expect(showSuccess).toHaveBeenCalledTimes(1));
    expect(showSuccess).toHaveBeenCalledWith(`Restored from ${BACKUP_LABEL}.`);
    expect(restoreBackup).toHaveBeenCalledWith(BACKUP.filename);
    expect(storeState.forceReload).toHaveBeenCalledTimes(1);
    // The list is re-read, so the "Before restore" the server just wrote shows
    // up without leaving the view: once on mount, once after the restore.
    expect(listBackups).toHaveBeenCalledTimes(2);
    expect(showError).not.toHaveBeenCalled();
  });

  it("leaves the row usable again when the restore is refused", async () => {
    restoreBackup.mockImplementation(async () => {
      throw new Error("Backup 'x' not found");
    });

    const dialog = await openRestoreDialog();
    await userEvent.click(dialog.getByRole("button", { name: "Restore" }));

    await waitFor(() => expect(showError).toHaveBeenCalledWith("Backup 'x' not found"));
    expect(showSuccess).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Restore" })).toBeEnabled();
  });
});
