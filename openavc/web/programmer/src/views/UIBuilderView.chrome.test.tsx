import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, screen, fireEvent, within } from "@testing-library/react";

// The UI Builder page around the canvas: the save banner, the buttons at the
// end of the toolbar, Panel Settings, Validate's report, what preview hides,
// and what the canvas and the Properties panel are handed.

vi.mock("../api/restClient", async (orig) => (await import("../test/uiBuilderMocks")).restModule(orig));
vi.mock("../api/wsClient", async () => (await import("../test/uiBuilderMocks")).wsModule);
vi.mock("../api/commandIssues", async () => (await import("../test/uiBuilderMocks")).commands);
vi.mock("../store/toastStore", async (orig) => (await import("../test/uiBuilderMocks")).toastModule(orig));
vi.mock("@dnd-kit/core", async (orig) => (await import("../test/uiBuilderMocks")).dndKit(orig));
vi.mock("react-resizable-panels", async () => (await import("../test/uiBuilderMocks")).stubs.panels);
vi.mock("../components/ui-builder/ElementPalette", async () => (await import("../test/uiBuilderMocks")).stubs.ElementPalette);
vi.mock("../components/ui-builder/OutlinePanel", async () => (await import("../test/uiBuilderMocks")).stubs.OutlinePanel);
vi.mock("../components/ui-builder/Canvas", async () => (await import("../test/uiBuilderMocks")).stubs.Canvas);
vi.mock("../components/ui-builder/CanvasToolbar", async () => (await import("../test/uiBuilderMocks")).stubs.CanvasToolbar);
vi.mock("../components/ui-builder/PropertiesPanel", async () => (await import("../test/uiBuilderMocks")).stubs.PropertiesPanel);
vi.mock("../components/ui-builder/ContextMenu", async () => (await import("../test/uiBuilderMocks")).stubs.ContextMenu);
vi.mock("../components/ui-builder/ThemeStudio", async () => (await import("../test/uiBuilderMocks")).stubs.ThemeStudio);
vi.mock("../components/ui-builder/StylesheetEditor", async () => (await import("../test/uiBuilderMocks")).stubs.StylesheetEditor);

import { UIBuilderView } from "./UIBuilderView";
import { useProjectStore } from "../store/projectStore";
import {
  builder,
  commands,
  installFetch,
  makeProject,
  page,
  project,
  renderPage,
  resetMocks,
  saves,
  seed,
  seen,
  settle,
  ui,
  undoLog,
} from "../test/uiBuilderHarness";

const call = (fn: () => unknown) => act(() => { fn(); });
const dialog = () => screen.getByRole("dialog", { name: "Panel Settings" });

beforeEach(() => {
  resetMocks();
  installFetch();
  seed();
});

describe("the save banner", () => {
  it("says why the save failed, retries and dismisses", async () => {
    useProjectStore.setState({ error: "disk full" });
    await renderPage(UIBuilderView);
    expect(screen.getByText("Save failed: disk full")).toBeInTheDocument();
    call(() => fireEvent.click(screen.getByRole("button", { name: "Retry" })));
    expect(saves.save).toHaveBeenCalledTimes(1);
    call(() => fireEvent.click(screen.getByRole("button", { name: "Dismiss" })));
    expect(screen.queryByText(/Save failed/)).toBeNull();
    expect(useProjectStore.getState().error).toBeNull();
  });

  it("stays away while a version conflict has its own banner", async () => {
    useProjectStore.setState({ error: "conflict", conflictDetected: true });
    await renderPage(UIBuilderView);
    expect(screen.queryByText(/Save failed/)).toBeNull();
  });
});

describe("the toolbar's own buttons", () => {
  it("opens the shortcuts list the way Ctrl+/ does", async () => {
    await renderPage(UIBuilderView);
    const seenKeys: KeyboardEvent[] = [];
    const listen = (e: KeyboardEvent) => seenKeys.push(e);
    window.addEventListener("keydown", listen);
    call(() => fireEvent.click(screen.getByTitle("Keyboard Shortcuts")));
    window.removeEventListener("keydown", listen);
    expect(seenKeys.map((e) => [e.key, e.ctrlKey])).toEqual([["/", true]]);
  });

  it("hands the toolbar the pages and the page on screen, and none of its buttons in preview", async () => {
    seed(makeProject(), { selectedPageId: "other" });
    await renderPage(UIBuilderView);
    expect(seen.CanvasToolbar.pages.map((p: { id: string }) => p.id)).toEqual(["main", "other"]);
    expect(seen.CanvasToolbar.selectedPageId).toBe("other");
    expect(screen.getByTitle("Open Theme Studio")).toHaveTextContent("Theme");
    call(() => builder().setPreviewMode(true));
    expect(seen.CanvasToolbar.trailing).toBeUndefined();
    expect(screen.queryByTitle("Panel Settings")).toBeNull();
  });
});

describe("preview", () => {
  it("hides the palette and the Properties panel and gives the canvas the width", async () => {
    await renderPage(UIBuilderView);
    expect(screen.getAllByTestId("panel").map((p) => p.dataset.size)).toEqual(["15", "60", "25"]);
    call(() => builder().setPreviewMode(true));
    expect(screen.getAllByTestId("panel").map((p) => p.dataset.size)).toEqual(["100"]);
    expect(seen.Canvas.previewMode).toBe(true);
    expect(screen.queryByTestId("stub-PropertiesPanel")).toBeNull();
  });
});

describe("what the canvas and the Properties panel are handed", () => {
  it("the page, the selection, the screen turned for the arrangement, and the masters", async () => {
    seed(makeProject(), { selectedPageId: "main", selectedElementId: "lbl_1", selectedElementIds: ["lbl_1"], activeLayoutId: "main_port", zoom: 0.5, showGrid: false });
    await renderPage(UIBuilderView);
    expect(seen.Canvas).toMatchObject({ previewMode: false, showGrid: false, zoom: 0.5, screenWidth: 600, screenHeight: 1024 });
    expect(seen.Canvas.page.id).toBe("main");
    expect(seen.Canvas.masterElements).toBe(ui().master_elements);
    expect(seen.PropertiesPanel.element.id).toBe("lbl_1");
    expect(seen.PropertiesPanel.selectedElementIds).toEqual(["lbl_1"]);
    expect(seen.PropertiesPanel.masterElement).toBeNull();
    expect(seen.PropertiesPanel.page.id).toBe("main");
    expect(seen.PropertiesPanel.project).toBe(project());
  });

  it("falls back to the first page when the selected one is gone", async () => {
    seed(makeProject(), { selectedPageId: "deleted" });
    await renderPage(UIBuilderView);
    expect(seen.Canvas.page.id).toBe("main");
    expect(seen.CanvasToolbar.selectedPageId).toBe("main");
  });

  it("waits for the project", async () => {
    seed(null);
    await renderPage(UIBuilderView);
    expect(screen.getByText("Loading project...")).toBeInTheDocument();
    expect(seen.Canvas).toBeUndefined();
  });
});

describe("Validate", () => {
  it("reports no issues", async () => {
    const clean = { id: "lbl", type: "label", text: "Hi", style: {} } as never;
    seed(makeProject({ pages: [page("main", [clean], { lbl: { x: 10, y: 10, w: 20, h: 10 } })], masters: [] }));
    await renderPage(UIBuilderView);
    await act(async () => { await seen.CanvasToolbar.onValidate(); });
    expect(commands.commandIssues).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Project Validation (No Issues)")).toBeInTheDocument();
    expect(screen.getByText("All bindings, pages, devices, and macros are valid.")).toBeInTheDocument();
    call(() => fireEvent.click(screen.getByRole("button", { name: "×" })));
    expect(screen.queryByText(/Project Validation/)).toBeNull();
  });

  it("lists each issue with its place, and goes to the control when one is clicked", async () => {
    await renderPage(UIBuilderView);
    await act(async () => { await seen.CanvasToolbar.onValidate(); });
    const heading = screen.getByText(/^Project Validation \(\d+ issues\)$/);
    const count = Number(/\((\d+)/.exec(heading.textContent!)![1]);
    expect(count).toBeGreaterThan(3);
    expect(screen.getAllByText(/^(WARN|ERR)$/)).toHaveLength(count);
    expect(screen.getAllByText("Interactive element has no action").length).toBeGreaterThan(1);
    // A master's issue names no page: clicking it goes nowhere and keeps the report.
    call(() => fireEvent.click(screen.getAllByText("Master Elements > m_1")[0]));
    expect(heading).toBeInTheDocument();
    const row = screen.getByText("Other > btn_9");
    call(() => fireEvent.click(row));
    expect(builder().selectedPageId).toBe("other");
    expect(builder().selectedElementId).toBe("btn_9");
    expect(screen.queryByText(/Project Validation/)).toBeNull();
  });

  it("asks the platform about each chosen command's parameters", async () => {
    commands.commandIssues.mockResolvedValueOnce(new Map());
    await renderPage(UIBuilderView);
    await act(async () => { await seen.CanvasToolbar.onValidate(); });
    expect(Array.isArray(commands.commandIssues.mock.calls[0][0])).toBe(true);
  });
});

describe("Panel Settings", () => {
  const open = () => call(() => fireEvent.click(screen.getByTitle("Panel Settings")));

  it("saves the edited settings as one entry", async () => {
    await renderPage(UIBuilderView);
    open();
    const d = within(dialog());
    expect(d.getByRole("button", { name: "Save" })).toBeDisabled();
    call(() => fireEvent.change(d.getByPlaceholderText("Leave empty for no lock"), { target: { value: "12a3456789" } }));
    expect((d.getByPlaceholderText("Leave empty for no lock") as HTMLInputElement).value).toBe("123456");
    call(() => fireEvent.click(d.getByLabelText("Show a message when a control fails")));
    call(() => fireEvent.click(d.getByRole("button", { name: "Save" })));
    expect(undoLog()).toEqual([["Edit project settings", "settings"]]);
    expect(ui().settings).toMatchObject({ lock_code: "123456", show_error_messages: false });
    expect(screen.queryByRole("dialog", { name: "Panel Settings" })).toBeNull();
  });

  it("closes without a word when nothing changed, and asks before throwing changes away", async () => {
    await renderPage(UIBuilderView);
    open();
    call(() => fireEvent.click(within(dialog()).getByRole("button", { name: "Cancel" })));
    expect(screen.queryByRole("dialog", { name: "Panel Settings" })).toBeNull();
    open();
    call(() => fireEvent.change(within(dialog()).getByPlaceholderText("#2196F3"), { target: { value: "#00ff00" } }));
    call(() => fireEvent.click(within(dialog()).getByRole("button", { name: "Cancel" })));
    expect(screen.getByText("Discard unsaved settings changes?")).toBeInTheDocument();
    call(() => fireEvent.click(screen.getByRole("button", { name: "Discard" })));
    expect(screen.queryByRole("dialog", { name: "Panel Settings" })).toBeNull();
    expect(undoLog()).toEqual([]);
    expect(ui().settings.accent_color).toBe("#ff0000");
  });

  it("shows the transition and stagger settings only when they apply", async () => {
    await renderPage(UIBuilderView);
    open();
    const d = within(dialog());
    expect(d.queryByText("Transition Duration")).toBeNull();
    const selects = d.getAllByRole("combobox");
    const transition = selects.find((s) => within(s).queryByText("Slide Left"))!;
    call(() => fireEvent.change(transition, { target: { value: "fade" } }));
    expect(d.getByText("Transition Duration")).toBeInTheDocument();
    const entry = selects.find((s) => within(s).queryByText("Stagger"))!;
    expect(d.queryByText("Stagger Style")).toBeNull();
    call(() => fireEvent.change(entry, { target: { value: "stagger" } }));
    expect(d.getByText("Stagger Style")).toBeInTheDocument();
    expect(d.getByText("Stagger Delay")).toBeInTheDocument();
    const idle = selects.find((s) => within(s).queryByText("Other (other)"))!;
    call(() => fireEvent.change(idle, { target: { value: "other" } }));
    call(() => fireEvent.click(d.getByRole("button", { name: "Save" })));
    expect(ui().settings).toMatchObject({ page_transition: "fade", element_entry: "stagger", idle_page: "other" });
  });
});

describe("the edit the toolbar's Settings button makes", () => {
  it("leaves the theme alone", async () => {
    await renderPage(UIBuilderView);
    call(() => fireEvent.click(screen.getByTitle("Panel Settings")));
    call(() => fireEvent.change(within(dialog()).getByPlaceholderText("#2196F3"), { target: { value: "#00ff00" } }));
    call(() => fireEvent.click(within(dialog()).getByRole("button", { name: "Save" })));
    await settle();
    expect(ui().settings).toMatchObject({ accent_color: "#00ff00", theme_id: "dark-default" });
  });
});
