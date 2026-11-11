import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import type { ThemeDefinition, ThemeSummary } from "../../api/restClient";
import { ThemeExistsError } from "../../api/restClient";
import {
  customTheme, builtinTheme, themeById, renderStudio, sectionBody, tokenRow, textIn, SUMMARIES,
} from "../../test/themeStudioFixtures";

const { getTheme, updateTheme, createTheme, deleteTheme, importTheme, preview, calls } = vi.hoisted(() => ({
  getTheme: vi.fn(), updateTheme: vi.fn(), createTheme: vi.fn(), deleteTheme: vi.fn(), importTheme: vi.fn(),
  preview: vi.fn(), calls: [] as string[],
}));
vi.mock("../../api/restClient", async (original) => ({
  ...await original<typeof import("../../api/restClient")>(),
  getTheme, updateTheme, createTheme, deleteTheme, importTheme,
}));
vi.mock("./PanelPreviewFrame", () => ({
  PanelPreviewFrame: (props: unknown) => { preview(props); return null; },
}));

const working = () => (preview.mock.lastCall![0] as { inlineTheme: ThemeDefinition }).inlineTheme;
const json = (v: unknown) => JSON.stringify(v);
const sentTo = (fn: typeof createTheme) => json(fn.mock.lastCall!.at(-1));

/** What was called, in order, across the server and the Builder's callbacks. */
function logged<T extends (...a: never[]) => unknown>(name: string, fn: T): T {
  return ((...a: never[]) => { calls.push(name); return fn(...a); }) as T;
}

function callbacks() {
  return {
    onClose: vi.fn(logged("close", () => {})),
    onChangeTheme: vi.fn(logged("changeTheme", () => {})),
    onClearOverrides: vi.fn(logged("clearOverrides", () => {})),
    onRefreshThemes: vi.fn(logged("refreshThemes", () => {})),
    onThemeSaved: vi.fn(logged("themeSaved", () => {})),
  };
}

async function loaded(props: Parameters<typeof renderStudio>[0] = {}) {
  const cb = callbacks();
  const view = renderStudio({ ...cb, ...props });
  await screen.findByText("Theme Info");
  calls.length = 0;
  return { ...view, cb };
}

function editAccent(value = "#abcdef") {
  fireEvent.change(textIn(tokenRow("Accent")), { target: { value } });
}

const withIds = (...ids: string[]): ThemeSummary[] => [
  ...SUMMARIES, ...ids.map((id) => ({ ...SUMMARIES[1], id, name: id })),
];

/** The custom fixture as the server should receive it: no `_source`. */
function sent(t: ThemeDefinition, patch: Partial<ThemeDefinition>): ThemeDefinition {
  const out = { ...t, ...patch };
  delete out._source;
  return out;
}

beforeEach(() => {
  vi.clearAllMocks();
  calls.length = 0;
  getTheme.mockImplementation(logged("getTheme", async (id: string) => themeById(id)));
  updateTheme.mockImplementation(logged("updateTheme", async () => ({ status: "ok" })));
  createTheme.mockImplementation(logged("createTheme", async () => ({ status: "ok", theme_id: "x" })));
  deleteTheme.mockImplementation(logged("deleteTheme", async () => ({ status: "ok" })));
});
afterEach(() => vi.unstubAllGlobals());

// jsdom's Blob has no text(); a browser's does, and Keep both reads the file with it.
beforeAll(() => {
  if (!Blob.prototype.text) {
    Blob.prototype.text = function (this: Blob) {
      return new Promise<string>((resolve) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result as string);
        reader.readAsText(this);
      });
    };
  }
});

describe("Save Changes", () => {
  it("sends the theme without its source, with preview colours, then reloads it", async () => {
    const { cb } = await loaded({ themeOverrides: { accent: "#00ff00" } });
    editAccent();
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    await screen.findByText('Saved "Sample"');
    const edited = customTheme();
    edited.variables.accent = "#abcdef";
    expect(updateTheme.mock.lastCall![0]).toBe("studio-sample");
    expect(sentTo(updateTheme)).toBe(json(sent(edited, { preview_colors: ["#000000", "#303030", "#abcdef", "#ffffff"] })));
    expect(calls).toEqual(["updateTheme", "clearOverrides", "refreshThemes", "getTheme", "themeSaved"]);
    expect(cb.onChangeTheme).not.toHaveBeenCalled();
    expect(json(working())).toBe(json(customTheme()));
    expect(screen.queryByText("Unsaved")).toBeNull();
  });

  it("leaves the project's overrides alone when it has none", async () => {
    await loaded();
    editAccent();
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    await screen.findByText('Saved "Sample"');
    expect(calls).toEqual(["updateTheme", "refreshThemes", "getTheme", "themeSaved"]);
  });

  it("takes the button colour for the preview when the theme has no surface", async () => {
    getTheme.mockResolvedValue({ ...customTheme(), variables: { button_bg: "#123123", accent: "#3366ff" } });
    await loaded();
    editAccent("#000002");
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    await waitFor(() => expect(updateTheme).toHaveBeenCalled());
    expect(updateTheme.mock.lastCall![1].preview_colors).toEqual(["#123123", "#000002"]);
  });

  it("holds every file button while it saves", async () => {
    let finish: () => void = () => {};
    updateTheme.mockReturnValue(new Promise<void>((r) => { finish = r; }));
    await loaded();
    editAccent();
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(screen.getByRole("button", { name: "Save Changes" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save as Copy" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Discard" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Export" })).toBeEnabled();
    await act(async () => finish());
    expect(screen.getByRole("button", { name: "Save as Copy" })).toBeEnabled();
  });

  it.each([
    [new Error("disk full"), "disk full"],
    ["", "Save failed"],
  ])("says why a save failed and keeps the edits (%s)", async (failure, said) => {
    updateTheme.mockRejectedValue(failure);
    const { cb } = await loaded({ themeOverrides: { accent: "#00ff00" } });
    editAccent();
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(await screen.findByText(said)).toBeInTheDocument();
    expect(working().variables.accent).toBe("#abcdef");
    expect(cb.onClearOverrides).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Save Changes" })).toBeEnabled();
  });
});

describe("Save as Custom", () => {
  it("copies a built-in theme under a new name and id, and chooses it", async () => {
    const { cb } = await loaded({ currentThemeId: "dark", themeOverrides: { accent: "#00ff00" } });
    editAccent("#010101");
    fireEvent.click(screen.getByRole("button", { name: "Save as Custom" }));
    await screen.findByText('Saved as "Dark (Custom)"');
    const edited = builtinTheme();
    edited.variables.accent = "#010101";
    expect(sentTo(createTheme)).toBe(json(sent(edited, {
      id: "dark-custom", name: "Dark (Custom)", author: "Custom", preview_colors: ["#010101"],
    })));
    expect(calls).toEqual(["createTheme", "clearOverrides", "changeTheme", "refreshThemes"]);
    expect(cb.onChangeTheme).toHaveBeenCalledWith("dark-custom");
    expect(cb.onThemeSaved).not.toHaveBeenCalled();
  });

  it.each([
    ["Sample", [], "sample-copy", "Sample (Copy)"],
    ["Sample", ["sample-copy"], "sample-copy-2", "Sample (Copy) 2"],
    ["Sample", ["sample-copy", "sample-copy-2"], "sample-copy-3", "Sample (Copy) 3"],
    ["Sample (Copy 2)", [], "sample-copy-2", "Sample (Copy 2)"],
    ["Sample (Custom)", ["sample-custom"], "sample-custom-2", "Sample (Custom) 2"],
    ["  Big Room!  ", [], "big-room-copy", "Big Room! (Copy)"],
  ])("names a copy of %j beside %j as %s / %s", async (name, taken, id, newName) => {
    await loaded({ themes: withIds(...taken) });
    fireEvent.change(sectionBody("Theme Info").querySelector("input")!, { target: { value: name } });
    fireEvent.click(screen.getByRole("button", { name: "Save as Copy" }));
    await waitFor(() => expect(createTheme).toHaveBeenCalled());
    expect(createTheme.mock.lastCall![0]).toMatchObject({ id, name: newName, author: "Custom" });
    expect(await screen.findByText(`Saved as "${newName}"`)).toBeInTheDocument();
  });

  it("cannot be pressed without a name", async () => {
    await loaded();
    fireEvent.change(sectionBody("Theme Info").querySelector("input")!, { target: { value: "   " } });
    expect(screen.getByRole("button", { name: "Save as Copy" })).toBeDisabled();
  });

  it("says why it failed", async () => {
    createTheme.mockRejectedValue(new Error("no room"));
    const { cb } = await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Save as Copy" }));
    expect(await screen.findByText("no room")).toBeInTheDocument();
    expect(cb.onChangeTheme).not.toHaveBeenCalled();
  });
});

describe("Delete", () => {
  it("asks first, then deletes the theme and goes back to the default", async () => {
    const { cb } = await loaded({ themeOverrides: { accent: "#00ff00" } });
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const question = screen.getByRole("alertdialog");
    expect(question).toHaveTextContent('Delete "Sample"? This permanently removes the theme file');
    expect(question).toHaveTextContent("Pages still set to this theme will fall back to dark-default.");
    fireEvent.click(within(question).getByRole("button", { name: "Delete" }));
    await screen.findByText('Deleted "Sample"');
    expect(deleteTheme).toHaveBeenCalledExactlyOnceWith("studio-sample");
    expect(calls).toEqual(["deleteTheme", "clearOverrides", "changeTheme", "refreshThemes"]);
    expect(cb.onChangeTheme).toHaveBeenCalledWith("dark-default");
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("does nothing when the question is cancelled", async () => {
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(deleteTheme).not.toHaveBeenCalled();
  });

  it("says why it failed, after an earlier line", async () => {
    deleteTheme.mockRejectedValue(new Error("locked"));
    const { cb } = await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("locked")).toBeInTheDocument();
    expect(cb.onChangeTheme).not.toHaveBeenCalled();
  });

  it("keeps the line already showing while it deletes", async () => {
    let finish: () => void = () => {};
    deleteTheme.mockReturnValue(new Promise<void>((r) => { finish = r; }));
    await loaded();
    editAccent();
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
    expect(screen.getByText("Reverted to saved values")).toBeInTheDocument();
    await act(async () => finish());
  });
});

describe("Duplicate, from the picker", () => {
  const card = (name: string) => screen.getByText(name, { selector: "div" }).closest("div[style*='position: relative']")!;

  it("copies the theme as the server has it and chooses the copy", async () => {
    const { cb } = await loaded();
    fireEvent.mouseEnter(card("Dark"));
    fireEvent.click(screen.getByTitle('Duplicate "Dark" as a custom theme'));
    await screen.findByText('Duplicated as "Dark (Copy)"');
    expect(getTheme).toHaveBeenLastCalledWith("dark");
    expect(sentTo(createTheme)).toBe(json(sent(builtinTheme(), { id: "dark-copy", name: "Dark (Copy)", author: "Custom" })));
    expect(calls).toEqual(["getTheme", "createTheme", "changeTheme", "refreshThemes"]);
    expect(cb.onChangeTheme).toHaveBeenCalledExactlyOnceWith("dark-copy");
  });

  it.each([
    ["dark", "Dark", ["dark-copy"], "dark-copy-2", "Dark (Copy 2)"],
    ["dark", "Dark", ["dark-copy", "dark-copy-2"], "dark-copy-3", "Dark (Copy 3)"],
    ["dark-copy-2", "Dark (Copy 2)", ["dark-copy", "dark-copy-2"], "dark-copy-3", "Dark (Copy 3)"],
    ["dark-copy", "Dark (Copy)", [], "dark-copy-2", "Dark (Copy 2)"],
  ])("names a copy of %s (%s) beside %j as %s / %s", async (id, name, taken, newId, newName) => {
    getTheme.mockImplementation(async (want: string) => (want === "studio-sample" ? customTheme() : { ...builtinTheme(), id: want, name }));
    const themes = [...withIds(...taken).filter((t) => t.id !== id), { ...SUMMARIES[0], id, name }];
    await loaded({ themes });
    fireEvent.mouseEnter(card(name));
    fireEvent.click(screen.getByTitle(`Duplicate "${name}" as a custom theme`));
    await waitFor(() => expect(createTheme).toHaveBeenCalled());
    expect(createTheme.mock.lastCall![0]).toMatchObject({ id: newId, name: newName });
  });

  it("says why it failed", async () => {
    createTheme.mockRejectedValue(new Error("nope"));
    await loaded();
    fireEvent.mouseEnter(card("Dark"));
    fireEvent.click(screen.getByTitle('Duplicate "Dark" as a custom theme'));
    expect(await screen.findByText("nope")).toBeInTheDocument();
  });
});

describe("a failure that gives no reason", () => {
  const card = (name: string) => screen.getByText(name, { selector: "div" }).closest("div[style*='position: relative']")!;

  it("is said as the step that failed", async () => {
    createTheme.mockRejectedValue("");
    deleteTheme.mockRejectedValue("");
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Save as Copy" }));
    expect(await screen.findByText("Save failed")).toBeInTheDocument();
    fireEvent.mouseEnter(card("Dark"));
    fireEvent.click(screen.getByTitle('Duplicate "Dark" as a custom theme'));
    expect(await screen.findByText("Duplicate failed")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("Delete failed")).toBeInTheDocument();
  });
});

describe("Export", () => {
  it("downloads the working theme as an .avctheme file", async () => {
    const blobs: Blob[] = [];
    const created = vi.fn((b: Blob) => { blobs.push(b); return "blob:theme"; });
    const revoked = vi.fn();
    vi.stubGlobal("URL", Object.assign(Object.create(URL), { createObjectURL: created, revokeObjectURL: revoked }));
    const clicked: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(`${this.download} ${this.href}`);
    });
    await loaded();
    editAccent();
    fireEvent.click(screen.getByRole("button", { name: "Export" }));
    expect(clicked).toEqual(["studio-sample.avctheme blob:theme"]);
    expect(revoked).toHaveBeenCalledWith("blob:theme");
    expect(blobs[0].type).toBe("application/json");
    const edited = customTheme();
    edited.variables.accent = "#abcdef";
    expect(await blobs[0].text()).toBe(JSON.stringify(
      sent(edited, { preview_colors: ["#000000", "#303030", "#abcdef", "#ffffff"] }), null, 2,
    ));
    expect(working()._source).toBe("custom");
    click.mockRestore();
  });
});

// Keep both reads the file before it creates the theme; under a loaded machine that
// takes longer than findBy's default second, so its waits allow five.
describe("Import", () => {
  const fileInput = (c: HTMLElement) => c.querySelector<HTMLInputElement>('input[type="file"]')!;
  const file = (theme: Partial<ThemeDefinition>) => new File([JSON.stringify(theme)], "x.avctheme");

  it("opens the file chooser from the picker's button", async () => {
    const { container } = await loaded();
    const chooser = vi.spyOn(fileInput(container), "click");
    fireEvent.click(screen.getByRole("button", { name: "Import .avctheme" }));
    expect(chooser).toHaveBeenCalledOnce();
    expect(fileInput(container)).toHaveAttribute("accept", ".avctheme,.json");
  });

  it("does nothing when no file was chosen", async () => {
    const { container } = await loaded();
    fireEvent.change(fileInput(container), { target: { files: [] } });
    expect(importTheme).not.toHaveBeenCalled();
  });

  it("says why an import failed", async () => {
    importTheme.mockRejectedValue(new Error("not a theme"));
    const { container, user } = await loaded();
    await user.upload(fileInput(container), file({ id: "x" }));
    expect(await screen.findByText("Import failed: not a theme")).toBeInTheDocument();
    expect(fileInput(container).value).toBe("");
  });

  it("asks before replacing a custom theme, and Cancel leaves it", async () => {
    importTheme.mockRejectedValue(new ThemeExistsError("studio-sample", "Sample"));
    const { container, user, cb } = await loaded();
    await user.upload(fileInput(container), file({ id: "studio-sample", name: "Sample" }));
    const question = await screen.findByRole("alertdialog");
    expect(question).toHaveTextContent('A custom theme named "Sample" already exists.');
    fireEvent.click(within(question).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(importTheme).toHaveBeenCalledOnce();
    expect(cb.onChangeTheme).not.toHaveBeenCalled();
  });

  it("says why an overwrite failed", async () => {
    importTheme.mockRejectedValueOnce(new ThemeExistsError("studio-sample", "Sample"));
    importTheme.mockRejectedValueOnce(new Error("read-only"));
    const { container, user } = await loaded();
    await user.upload(fileInput(container), file({ id: "studio-sample" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Overwrite" }));
    expect(await screen.findByText("Import failed: read-only")).toBeInTheDocument();
    expect(importTheme.mock.lastCall![1]).toBe(true);
  });

  it.each([
    [{ id: "studio-sample", name: "Sample", _source: "custom" }, [], "studio-sample-2", "Sample (2)"],
    [{ id: "studio-sample", name: "Sample (2)" }, ["studio-sample-2"], "studio-sample-3", "Sample (3)"],
    [{ id: "studio-sample" }, ["studio-sample-2", "studio-sample-3"], "studio-sample-4", "studio-sample (4)"],
  ])("keeps both by adding %j under a free id", async (inFile, taken, id, name) => {
    importTheme.mockRejectedValue(new ThemeExistsError("studio-sample", "Sample"));
    const { container, user, cb } = await loaded({ themes: withIds(...taken) });
    await user.upload(fileInput(container), file(inFile));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Keep both" }));
    await screen.findByText(`Imported as "${name}"`, {}, { timeout: 5000 });
    const expected: Record<string, unknown> = { ...inFile, id, name };
    delete expected._source;
    expect(sentTo(createTheme)).toBe(json(expected));
    expect(calls).toEqual(["createTheme", "changeTheme", "refreshThemes"]);
    expect(cb.onChangeTheme).toHaveBeenCalledWith(id);
  });

  it("numbers a kept import past the colliding id even when the theme list does not have it yet", async () => {
    importTheme.mockRejectedValue(new ThemeExistsError("elsewhere", "Elsewhere"));
    const { container, user } = await loaded();
    await user.upload(fileInput(container), file({ id: "elsewhere", name: "Elsewhere" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Keep both" }));
    await screen.findByText('Imported as "Elsewhere (2)"', {}, { timeout: 5000 });
    expect(createTheme.mock.lastCall![0]).toMatchObject({ id: "elsewhere-2" });
  });

  it("says why keeping both failed", async () => {
    importTheme.mockRejectedValue(new ThemeExistsError("studio-sample", "Sample"));
    createTheme.mockRejectedValue(new Error("bad file"));
    const { container, user } = await loaded();
    await user.upload(fileInput(container), file({ id: "studio-sample" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Keep both" }));
    expect(await screen.findByText("Import failed: bad file", {}, { timeout: 5000 })).toBeInTheDocument();
  });
});

describe("choosing another theme in the picker", () => {
  const card = (name: string) => screen.getByText(name, { selector: "div" }).closest("div[style*='position: relative']")!;

  it("switches at once when nothing is unsaved, and not at all for the open theme", async () => {
    const { cb } = await loaded();
    fireEvent.click(card("Sample"));
    expect(cb.onChangeTheme).not.toHaveBeenCalled();
    fireEvent.click(card("Dark"));
    expect(cb.onChangeTheme).toHaveBeenCalledExactlyOnceWith("dark");
  });

  it("asks first when edits are unsaved", async () => {
    const { cb } = await loaded();
    editAccent();
    fireEvent.click(card("Dark"));
    const question = screen.getByRole("alertdialog");
    expect(question).toHaveTextContent('You have unsaved changes to "Sample". Switch theme anyway?');
    fireEvent.click(within(question).getByRole("button", { name: "Keep editing" }));
    expect(cb.onChangeTheme).not.toHaveBeenCalled();
    fireEvent.click(card("Dark"));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Discard and switch" }));
    expect(cb.onChangeTheme).toHaveBeenCalledExactlyOnceWith("dark");
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });
});

describe("closing", () => {
  it("closes at once when nothing is unsaved", async () => {
    const { cb } = await loaded();
    fireEvent.click(screen.getByTitle("Close (Esc)"));
    expect(cb.onClose).toHaveBeenCalledOnce();
  });

  it("offers to save a custom theme's edits, and closes once saved", async () => {
    const { cb } = await loaded();
    editAccent();
    fireEvent.click(screen.getByTitle("Close (Esc)"));
    const question = screen.getByRole("alertdialog");
    expect(question).toHaveTextContent('You have unsaved edits to "Sample".');
    expect(question).toHaveTextContent("Save Changes overwrites this theme. Discard reloads it.");
    fireEvent.click(within(question).getByRole("button", { name: "Save Changes" }));
    await waitFor(() => expect(cb.onClose).toHaveBeenCalledOnce());
    expect(calls).toEqual(["updateTheme", "refreshThemes", "getTheme", "themeSaved", "close"]);
  });

  it("stays open with the reason when that save fails", async () => {
    updateTheme.mockRejectedValue(new Error("disk full"));
    const { cb } = await loaded();
    editAccent();
    fireEvent.click(screen.getByTitle("Close (Esc)"));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Save Changes" }));
    expect(await screen.findByText("disk full")).toBeInTheDocument();
    expect(cb.onClose).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("closes without saving on Discard", async () => {
    const { cb } = await loaded();
    editAccent();
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Discard" }));
    expect(cb.onClose).toHaveBeenCalledOnce();
    expect(updateTheme).not.toHaveBeenCalled();
  });

  it("offers Save as Custom for a built-in theme, which needs a name", async () => {
    const { cb } = await loaded({ currentThemeId: "dark" });
    fireEvent.change(sectionBody("Theme Info").querySelector("input")!, { target: { value: " " } });
    fireEvent.click(screen.getByTitle("Close (Esc)"));
    const question = screen.getByRole("alertdialog");
    expect(question).toHaveTextContent("Built-in themes can't be modified. Save as Custom creates an editable copy.");
    fireEvent.click(within(question).getByRole("button", { name: "Save as Custom" }));
    expect(await screen.findByText("Enter a theme name")).toBeInTheDocument();
    expect(createTheme).not.toHaveBeenCalled();
    expect(cb.onClose).not.toHaveBeenCalled();
  });

  it("closes after saving a built-in theme as a custom one", async () => {
    const { cb } = await loaded({ currentThemeId: "dark" });
    editAccent();
    fireEvent.click(screen.getByTitle("Close (Esc)"));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Save as Custom" }));
    await waitFor(() => expect(cb.onClose).toHaveBeenCalledOnce());
    expect(calls).toEqual(["createTheme", "changeTheme", "refreshThemes", "close"]);
  });
});

describe("the picker", () => {
  it("lists built-in and custom themes apart, marks the open one and shows descriptions", async () => {
    await loaded();
    const picker = screen.getByText("Themes").parentElement!;
    expect(within(picker).getByText("Built-in")).toBeInTheDocument();
    expect(within(picker).getByText("Custom")).toBeInTheDocument();
    expect(within(picker).getByText("A sample theme")).toBeInTheDocument();
    expect(within(picker).queryByText(/No custom themes yet/)).toBeNull();
    const selected = within(picker).getByText("Sample", { selector: "div" }).closest("div[style*='position: relative']")!;
    expect(selected).toHaveStyle({ border: "2px solid var(--accent)" });
  });

  it("explains how to make a custom theme when there is none", async () => {
    await loaded({ themes: [SUMMARIES[0]] });
    expect(screen.getByText(/No custom themes yet\. Hover any theme above and click/)).toBeInTheDocument();
    expect(screen.queryByText("Custom", { selector: "div" })).toBeNull();
  });

  it("shows the duplicate button only while a card is hovered", async () => {
    await loaded();
    const dark = screen.getByText("Dark", { selector: "div" }).closest("div[style*='position: relative']")!;
    expect(screen.queryByTitle('Duplicate "Dark" as a custom theme')).toBeNull();
    fireEvent.mouseEnter(dark);
    expect(screen.getByTitle('Duplicate "Dark" as a custom theme')).toBeInTheDocument();
    fireEvent.mouseLeave(dark);
    expect(screen.queryByTitle('Duplicate "Dark" as a custom theme')).toBeNull();
  });
});

describe("while a file action runs", () => {
  const fileInput = (c: HTMLElement) => c.querySelector<HTMLInputElement>('input[type="file"]')!;
  const card = (name: string) => screen.getByText(name, { selector: "div" }).closest("div[style*='position: relative']")!;
  const upload = async (view: Awaited<ReturnType<typeof loaded>>) =>
    view.user.upload(fileInput(view.container), new File([JSON.stringify({ id: "studio-sample", name: "Sample" })], "x.avctheme"));
  /** A line on show before the action: Discard's. */
  const lineFirst = () => {
    editAccent();
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.getByText("Reverted to saved values")).toBeInTheDocument();
  };
  const held = (fn: typeof createTheme) => {
    let finish: () => void = () => {};
    fn.mockReturnValueOnce(new Promise((r) => { finish = () => r({ status: "ok", theme_id: "x", name: "X" }); }));
    return () => finish();
  };
  const collides = () => importTheme.mockRejectedValueOnce(new ThemeExistsError("studio-sample", "Sample"));

  type Case = [string, boolean, (view: Awaited<ReturnType<typeof loaded>>) => Promise<() => void>];
  const cases: Case[] = [
    ["Save Changes", false, async () => {
      const finish = held(updateTheme);
      lineFirst();
      editAccent("#000003");
      fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
      return finish;
    }],
    ["Save as Copy", false, async () => {
      const finish = held(createTheme);
      lineFirst();
      fireEvent.click(screen.getByRole("button", { name: "Save as Copy" }));
      return finish;
    }],
    ["Delete", true, async () => {
      const finish = held(deleteTheme);
      lineFirst();
      fireEvent.click(screen.getByRole("button", { name: "Delete" }));
      fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
      return finish;
    }],
    ["Duplicate", false, async () => {
      const finish = held(createTheme);
      lineFirst();
      fireEvent.mouseEnter(card("Dark"));
      fireEvent.click(screen.getByTitle('Duplicate "Dark" as a custom theme'));
      await waitFor(() => expect(createTheme).toHaveBeenCalled());
      return finish;
    }],
    ["Import", false, async (view) => {
      const finish = held(importTheme);
      lineFirst();
      await upload(view);
      return finish;
    }],
    ["Overwrite", true, async (view) => {
      collides();
      const finish = held(importTheme);
      await upload(view);
      const question = await screen.findByRole("alertdialog");
      lineFirst();
      fireEvent.click(within(question).getByRole("button", { name: "Overwrite" }));
      return finish;
    }],
    ["Keep both", true, async (view) => {
      collides();
      const finish = held(createTheme);
      await upload(view);
      const question = await screen.findByRole("alertdialog");
      lineFirst();
      fireEvent.click(within(question).getByRole("button", { name: "Keep both" }));
      await waitFor(() => expect(createTheme).toHaveBeenCalled(), { timeout: 5000 });
      return finish;
    }],
  ];

  it.each(cases)("%s holds the file buttons until it ends, and keeps the line already showing: %s", async (_name, keeps, start) => {
    const view = await loaded();
    const finish = await start(view);
    expect(screen.getByRole("button", { name: "Save as Copy" })).toBeDisabled();
    expect(screen.queryByText("Reverted to saved values") !== null).toBe(keeps);
    expect(screen.queryByRole("alertdialog")).toBeNull();
    await act(async () => finish());
    await waitFor(() => expect(screen.getByRole("button", { name: "Save as Copy" })).toBeEnabled());
  });

  it("Save as Custom with a blank name holds nothing", async () => {
    await loaded({ currentThemeId: "dark" });
    fireEvent.change(sectionBody("Theme Info").querySelector("input")!, { target: { value: " " } });
    fireEvent.click(screen.getByTitle("Close (Esc)"));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Save as Custom" }));
    expect(await screen.findByText("Enter a theme name")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Discard" })).toBeEnabled();
  });

  it("an import that collides says nothing and frees the buttons while it asks", async () => {
    const view = await loaded();
    lineFirst();
    collides();
    await upload(view);
    await screen.findByRole("alertdialog");
    expect(screen.queryByText("Reverted to saved values")).toBeNull();
    expect(screen.queryByText(/failed/)).toBeNull();
    expect(screen.getByRole("button", { name: "Save as Copy" })).toBeEnabled();
  });

  it("says what it did only until the theme it chose has loaded, which the Builder does at once", async () => {
    const view = await loaded();
    getTheme.mockImplementation(async (id: string) => (id === "dark-copy" ? { ...builtinTheme(), id, _source: "custom" } : themeById(id)));
    fireEvent.mouseEnter(card("Dark"));
    fireEvent.click(screen.getByTitle('Duplicate "Dark" as a custom theme'));
    await screen.findByText('Duplicated as "Dark (Copy)"');
    view.rerender({ currentThemeId: "dark-copy" });
    await waitFor(() => expect(working().id).toBe("dark-copy"));
    expect(screen.queryByText('Duplicated as "Dark (Copy)"')).toBeNull();
  });
});
