import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Device logins in an export and an import. Export on a project with a
// login saved on this system asks whether to include them, sealed with a
// passphrase typed twice; a project without one exports straight away. An
// import whose file holds sealed logins asks for the passphrase before the
// project opens: a wrong one is said and can be tried again, and Skip opens
// the project with its devices on Needs login.

const MARKER = "<saved on this system>";

const storeState = {
  project: {
    project: { id: "hall", name: "Hall", description: "" },
    openavc_version: "0.16.0",
    plugins: {},
    devices: [{ id: "proj1", driver: "acme_widget", name: "Projector", config: { mode: "eco" } }],
    connections: { proj1: { host: "192.0.2.10", password: MARKER } } as Record<string, Record<string, unknown>>,
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

const toasts = vi.hoisted(() => ({
  showError: vi.fn(),
  showInfo: vi.fn(),
  showSuccess: vi.fn(),
  showLastingWarning: vi.fn(),
}));
vi.mock("../store/toastStore", () => toasts);

vi.mock("../components/assets/AssetBrowser", () => ({ AssetBrowser: () => null }));
vi.mock("../components/video-streams/VideoStreamsSection", () => ({ VideoStreamsSection: () => null }));

const api = vi.hoisted(() => ({
  exportCurrentProject: vi.fn(async (_passphrase?: string) => {}),
  exportLibraryProject: vi.fn(async (_id: string, _passphrase?: string) => {}),
  importToLibrary: vi.fn(),
  addImportedLogins: vi.fn(),
  skipImportedLogins: vi.fn(async () => ({ status: "skipped" })),
  openFromLibrary: vi.fn(async () => ({ status: "created", project_name: "Hall", needs_login: [] as unknown[] })),
  listLibrary: vi.fn(async () => [] as unknown[]),
}));

vi.mock("../api/restClient", async () => {
  const { ApiError } = await import("../api/errors");
  return {
    ...api,
    ApiError,
    listBackups: async () => [],
    deleteLibraryProject: vi.fn(),
    duplicateLibraryProject: vi.fn(),
    saveToLibrary: vi.fn(),
    createBlankProject: vi.fn(),
    createBackup: vi.fn(),
    restoreBackup: vi.fn(),
    reloadProject: vi.fn(),
    saveProject: vi.fn(),
    ConflictError: class extends Error {},
  };
});

import { ApiError } from "../api/errors";
import { ProjectView } from "./ProjectView";
import { passphraseProblem, sealedDay } from "./projectLoginDialogs";

const PASSPHRASE = "Blue harbour 42";

beforeEach(() => {
  vi.clearAllMocks();
  storeState.dirty = false;
  storeState.project.connections = { proj1: { host: "192.0.2.10", password: MARKER } };
  api.listLibrary.mockResolvedValue([]);
});

/** The Export Project box once it has opened and put focus in itself (a
 *  frame after it appears), so what is typed next lands where it is aimed. */
async function exportBoxOpened() {
  const box = await screen.findByRole("dialog", { name: "Export Project" });
  await waitFor(() =>
    expect(within(box).getByRole("checkbox", { name: "Include logins" })).toHaveFocus());
  return box;
}

async function openExport() {
  render(<ProjectView />);
  await userEvent.click(screen.getByRole("button", { name: /^export$/i }));
  return exportBoxOpened();
}

/** The Export button inside the Export Project box. */
function exportIn(): HTMLElement {
  return within(screen.getByRole("dialog", { name: "Export Project" })).getByRole("button", { name: "Export" });
}

describe("Export with device logins", () => {
  it("exports a project with no saved login straight away", async () => {
    storeState.project.connections = { proj1: { host: "192.0.2.10", password: "" } };
    render(<ProjectView />);
    await userEvent.click(screen.getByRole("button", { name: /^export$/i }));

    await waitFor(() => expect(api.exportCurrentProject).toHaveBeenCalledTimes(1));
    expect(api.exportCurrentProject.mock.calls[0][0]).toBeUndefined();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("asks first, and leaves the logins out unless ticked", async () => {
    await openExport();
    expect(screen.getByRole("checkbox", { name: "Include logins" })).not.toBeChecked();
    expect(screen.queryByLabelText("Passphrase")).toBeNull();

    await userEvent.click(exportIn());

    expect(api.exportCurrentProject).toHaveBeenCalledWith(undefined);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("seals them with the passphrase typed twice", async () => {
    await openExport();
    await userEvent.click(screen.getByRole("checkbox", { name: "Include logins" }));
    expect(screen.getByText(/Anyone with this file and the passphrase can read these logins/)).toBeInTheDocument();
    const exportButton = exportIn();
    expect(exportButton).toBeDisabled();

    await userEvent.type(screen.getByLabelText("Passphrase"), PASSPHRASE);
    expect(exportButton).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Passphrase again"), PASSPHRASE);
    await userEvent.click(exportButton);

    expect(api.exportCurrentProject).toHaveBeenCalledWith(PASSPHRASE);
  });

  it("says when the two passphrases differ, and will not export", async () => {
    await openExport();
    await userEvent.click(screen.getByRole("checkbox", { name: "Include logins" }));
    await userEvent.type(screen.getByLabelText("Passphrase"), PASSPHRASE);
    await userEvent.type(screen.getByLabelText("Passphrase again"), "Blue harbour 24");

    expect(screen.getByRole("alert")).toHaveTextContent("The two passphrases don't match.");
    expect(exportIn()).toBeDisabled();
  });

  it("keeps the box open with the server's sentence when the export is refused", async () => {
    api.exportCurrentProject.mockRejectedValueOnce(
      new ApiError(422, JSON.stringify({ detail: "No logins are saved on this system for this project. Export it without them." })),
    );
    await openExport();
    await userEvent.click(screen.getByRole("checkbox", { name: "Include logins" }));
    await userEvent.type(screen.getByLabelText("Passphrase"), PASSPHRASE);
    await userEvent.type(screen.getByLabelText("Passphrase again"), PASSPHRASE);
    await userEvent.click(exportIn());

    expect(await screen.findByRole("alert")).toHaveTextContent("No logins are saved on this system");
    expect(screen.getByRole("dialog", { name: "Export Project" })).toBeInTheDocument();
  });

  it("asks for a library copy that has a saved login, and passes its id", async () => {
    api.listLibrary.mockResolvedValue([
      { id: "other_room", name: "Other Room", description: "", required_drivers: [], modified: "", has_saved_logins: true },
    ]);
    const { container } = render(<ProjectView />);
    await screen.findByText("Other Room");
    const toggle = container.querySelector<HTMLButtonElement>('[aria-haspopup="menu"]')!;
    await userEvent.click(toggle);
    // The row menu's Export, beside the toolbar's.
    await userEvent.click(screen.getAllByRole("button", { name: "Export" }).at(-1)!);
    await exportBoxOpened();
    await userEvent.click(screen.getByRole("checkbox", { name: "Include logins" }));
    await userEvent.type(screen.getByLabelText("Passphrase"), PASSPHRASE);
    await userEvent.type(screen.getByLabelText("Passphrase again"), PASSPHRASE);
    await userEvent.click(exportIn());

    expect(api.exportLibraryProject).toHaveBeenCalledWith("other_room", PASSPHRASE);
  });
});

describe("the passphrase rules", () => {
  it("wants eight characters and the same twice", () => {
    expect(passphraseProblem("seven77", "seven77")).toBe("Use at least 8 characters.");
    expect(passphraseProblem(PASSPHRASE, "")).toBe("The two passphrases don't match.");
    expect(passphraseProblem(PASSPHRASE, PASSPHRASE)).toBeNull();
  });

  it("names the day a bundle was sealed", () => {
    expect(sealedDay("2026-11-05T14:03:07+00:00")).toContain("2026");
    expect(sealedDay("not a date")).toBe("not a date");
  });
});

/** Choose `file` in the hidden file input the Import button makes, without
 *  waiting for the import to finish (it waits on the passphrase prompt). */
async function chooseImport(file: File) {
  const real = document.createElement.bind(document);
  let input: HTMLInputElement | null = null;
  const spy = vi.spyOn(document, "createElement").mockImplementation(((tag: string) => {
    const el = real(tag);
    if (tag === "input") input = el as HTMLInputElement;
    return el;
  }) as typeof document.createElement);
  render(<ProjectView />);
  await userEvent.click(screen.getByRole("button", { name: /^import$/i }));
  spy.mockRestore();
  const chosen = input as HTMLInputElement | null;
  if (!chosen) throw new Error("no file input was created");
  Object.defineProperty(chosen, "files", { value: [file], configurable: true });
  await act(async () => {
    void chosen.onchange?.(new Event("change"));
  });
}

const zip = () => new File(["PK"], "hall.zip", { type: "application/zip" });

describe("Import of an export with device logins", () => {
  beforeEach(() => {
    api.importToLibrary.mockResolvedValue({
      status: "imported",
      project_id: "hall",
      warnings: [],
      sealed_logins: { sealed_at: "2026-11-05T14:03:07+00:00" },
    });
    api.listLibrary.mockResolvedValue([{ id: "hall", name: "Hall", description: "", required_drivers: [], modified: "" }]);
  });

  it("asks for the passphrase before the project opens, and opens it after", async () => {
    api.addImportedLogins.mockResolvedValue({ status: "added", devices: 1 });
    await chooseImport(zip());

    const prompt = await screen.findByRole("dialog", { name: "Logins" });
    expect(prompt).toHaveTextContent(/This export includes logins, sealed on .*2026\. Enter its passphrase to bring them in, or skip\./);
    expect(api.openFromLibrary).not.toHaveBeenCalled();

    await userEvent.type(screen.getByLabelText("Passphrase"), PASSPHRASE);
    await userEvent.click(screen.getByRole("button", { name: "Bring them in" }));

    expect(api.addImportedLogins).toHaveBeenCalledWith("hall", PASSPHRASE);
    await waitFor(() => expect(api.openFromLibrary).toHaveBeenCalledWith("hall", "Hall", "hall"));
    expect(screen.queryByRole("dialog", { name: "Logins" })).toBeNull();
  });

  it("says a wrong passphrase and lets it be tried again", async () => {
    api.addImportedLogins
      .mockRejectedValueOnce(new ApiError(422, JSON.stringify({ detail: "That passphrase does not open these logins." })))
      .mockResolvedValueOnce({ status: "added", devices: 1 });
    await chooseImport(zip());
    await screen.findByRole("dialog", { name: "Logins" });

    await userEvent.type(screen.getByLabelText("Passphrase"), "Green harbour 42");
    await userEvent.click(screen.getByRole("button", { name: "Bring them in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("That passphrase does not open these logins.");
    expect(api.openFromLibrary).not.toHaveBeenCalled();
    await userEvent.clear(screen.getByLabelText("Passphrase"));
    await userEvent.type(screen.getByLabelText("Passphrase"), PASSPHRASE);
    await userEvent.click(screen.getByRole("button", { name: "Bring them in" }));
    await waitFor(() => expect(api.openFromLibrary).toHaveBeenCalled());
  });

  it("opens the project on Skip and names the devices that need their login", async () => {
    api.openFromLibrary.mockResolvedValueOnce({
      status: "created", project_name: "Hall", needs_login: [{ id: "proj1", name: "Projector" }],
    });
    await chooseImport(zip());
    await screen.findByRole("dialog", { name: "Logins" });

    await userEvent.click(screen.getByRole("button", { name: "Skip" }));

    expect(api.skipImportedLogins).toHaveBeenCalledWith("hall");
    expect(api.addImportedLogins).not.toHaveBeenCalled();
    await waitFor(() => expect(api.openFromLibrary).toHaveBeenCalled());
    await waitFor(() => expect(toasts.showLastingWarning).toHaveBeenCalledWith(expect.stringContaining("Projector")));
  });

  it("asks nothing for an export without logins", async () => {
    api.importToLibrary.mockResolvedValue({ status: "imported", project_id: "hall", warnings: [] });
    await chooseImport(zip());

    await waitFor(() => expect(api.openFromLibrary).toHaveBeenCalled());
    expect(screen.queryByRole("dialog", { name: "Logins" })).toBeNull();
  });
});
