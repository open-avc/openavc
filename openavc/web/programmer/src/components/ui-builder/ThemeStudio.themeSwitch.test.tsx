import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { create } from "zustand";
import { ThemeStudio } from "./ThemeStudio";
import { ThemeExistsError } from "../../api/restClient";
import { SUMMARIES, customTheme, studioProject } from "../../test/themeStudioFixtures";

const { getTheme, createTheme, deleteTheme, importTheme, preview } = vi.hoisted(() => ({
  getTheme: vi.fn(), createTheme: vi.fn(), deleteTheme: vi.fn(), importTheme: vi.fn(), preview: vi.fn(),
}));
vi.mock("../../api/restClient", async (original) => ({
  ...await original<typeof import("../../api/restClient")>(),
  getTheme, createTheme, deleteTheme, importTheme,
}));
vi.mock("./PanelPreviewFrame", () => ({
  PanelPreviewFrame: (props: unknown) => { preview(props); return null; },
}));

// The Builder holds the theme id in its project store, and choosing a theme
// writes the store at once (`chooseTheme`, through the store's own `set`), so
// the Studio is drawn again on the new theme, and its load runs, before the
// action that chose it has finished. These pins drive the Studio that way,
// with React scheduling as a browser does it rather than inside `act`.
const useStore = create<{ id: string }>(() => ({ id: "studio-sample" }));

function Builder() {
  const id = useStore((s) => s.id);
  return (
    <ThemeStudio
      open
      onClose={() => {}}
      themes={SUMMARIES}
      project={studioProject()}
      currentThemeId={id}
      themeOverrides={{}}
      onChangeTheme={(next) => useStore.setState({ id: next })}
      onClearOverrides={() => {}}
      onRefreshThemes={() => {}}
    />
  );
}

const pause = (ms: number) => new Promise((r) => setTimeout(r, ms));
const card = (name: string) => screen.getByText(name, { selector: "div" }).closest("div[style*='position: relative']")!;
const choose = (file: File) =>
  fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [file] } });
const themeFile = () => new File([JSON.stringify({ id: "studio-sample", name: "Sample" })], "x.avctheme");

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

let actEnvironment: unknown;
beforeEach(() => {
  vi.clearAllMocks();
  const g = globalThis as { IS_REACT_ACT_ENVIRONMENT?: unknown };
  actEnvironment = g.IS_REACT_ACT_ENVIRONMENT;
  g.IS_REACT_ACT_ENVIRONMENT = false;
  useStore.setState({ id: "studio-sample" });
  getTheme.mockImplementation(async (id: string) => ({ ...customTheme(), id, name: `Theme ${id}` }));
  createTheme.mockResolvedValue({ status: "ok", theme_id: "x" });
  deleteTheme.mockResolvedValue({ status: "ok" });
  importTheme.mockResolvedValue({ status: "ok", theme_id: "imported", name: "Imported One" });
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: unknown }).IS_REACT_ACT_ENVIRONMENT = actEnvironment;
});

describe("in the Builder, an action that switches theme", () => {
  it.each<[string, string, RegExp, () => Promise<void>]>([
    ["Duplicate", "studio-sample-copy", /Duplicated as/, async () => {
      fireEvent.mouseEnter(card("Sample"));
      fireEvent.click(screen.getByTitle('Duplicate "Sample" as a custom theme'));
    }],
    ["Save as Copy", "theme-studio-sample-copy", /Saved as/, async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save as Copy" }));
    }],
    ["Delete", "dark-default", /Deleted/, async () => {
      fireEvent.click(screen.getByRole("button", { name: "Delete" }));
      fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
    }],
    ["Import", "imported", /Imported/, async () => {
      choose(themeFile());
    }],
    ["Overwrite", "imported", /Imported/, async () => {
      importTheme.mockRejectedValueOnce(new ThemeExistsError("studio-sample", "Sample"));
      choose(themeFile());
      fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Overwrite" }));
    }],
    ["Keep both", "studio-sample-2", /Imported as/, async () => {
      importTheme.mockRejectedValueOnce(new ThemeExistsError("studio-sample", "Sample"));
      choose(themeFile());
      fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Keep both" }));
    }],
  ])("%s: its line is cleared by the load of the theme it chose before it is ever seen", async (_name, id, line, act) => {
    render(<Builder />);
    await screen.findByText("Theme Info");
    await act();
    await waitFor(() => expect(useStore.getState().id).toBe(id), { timeout: 5000 });
    await screen.findByText(`Theme ${id}`, { selector: "strong" });
    const seen: boolean[] = [];
    for (let i = 0; i < 8; i++) {
      seen.push(screen.queryByText(line) !== null);
      await pause(25);
    }
    expect(seen).toEqual(Array(8).fill(false));
  });
});
