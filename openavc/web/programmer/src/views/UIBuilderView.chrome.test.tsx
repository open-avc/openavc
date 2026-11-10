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
  press,
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

/** The columns side by side, left to right: each panel as its size and limits
 *  ("-" for none), each resize handle as "|". */
function columns(): string[] {
  const group = screen.getByTestId("panel-group");
  expect(group.dataset.direction).toBe("horizontal");
  return Array.from(group.children).map((c) => {
    const d = (c as HTMLElement).dataset;
    return c.getAttribute("data-testid") === "panel" ? [d.size, d.min ?? "-", d.max ?? "-"].join("/") : "|";
  });
}

describe("the columns", () => {
  it("draws the palette, the canvas and the Properties panel with a handle between each, at their sizes and limits", async () => {
    await renderPage(UIBuilderView);
    expect(columns()).toEqual(["15/10/25", "|", "60/-/-", "|", "25/15/40"]);
    expect(within(screen.getAllByTestId("panel")[0]).getByTestId("stub-ElementPalette")).toBeInTheDocument();
    expect(within(screen.getAllByTestId("panel")[1]).getByTestId("stub-Canvas")).toBeInTheDocument();
    expect(within(screen.getAllByTestId("panel")[2]).getByTestId("stub-PropertiesPanel")).toBeInTheDocument();
  });

  it("takes the palette and its handle away with Ctrl+E, leaving the canvas its size, and both side panels in preview", async () => {
    await renderPage(UIBuilderView);
    press("e", { ctrl: true });
    expect(columns()).toEqual(["60/-/-", "|", "25/15/40"]);
    press("p", { ctrl: true });
    expect(columns()).toEqual(["100/-/-"]);
    press("e", { ctrl: true });
    expect(columns()).toEqual(["100/-/-"]);
    press("p", { ctrl: true });
    expect(columns()).toEqual(["15/10/25", "|", "60/-/-", "|", "25/15/40"]);
  });

  it("says there are no pages in the canvas's own column, preview or not", async () => {
    seed(makeProject({ pages: [] }));
    await renderPage(UIBuilderView);
    const note = "No pages yet. Click + to add a page.";
    expect(within(screen.getAllByTestId("panel")[1]).getByText(note)).toBeInTheDocument();
    expect(seen.Canvas).toBeUndefined();
    call(() => builder().setPreviewMode(true));
    expect(within(screen.getAllByTestId("panel")[0]).getByText(note)).toBeInTheDocument();
  });

  it("draws nothing else while the project loads, and the whole Builder once it is there", async () => {
    seed(null);
    await renderPage(UIBuilderView);
    expect(screen.queryByTestId("panel-group")).toBeNull();
    expect(seen.CanvasToolbar).toBeUndefined();
    expect(seen.ThemeStudio).toBeUndefined();
    call(() => useProjectStore.setState({ project: makeProject() }));
    expect(screen.queryByText("Loading project...")).toBeNull();
    expect(columns()).toEqual(["15/10/25", "|", "60/-/-", "|", "25/15/40"]);
    expect(seen.CanvasToolbar.pages).toHaveLength(2);
    expect(seen.ThemeStudio.open).toBe(false);
  });
});

describe("the toolbar's view settings, changed once the page is drawn", () => {
  it("reach the canvas: the zoom, the grid, the screen, the arrangement and preview", async () => {
    seed(makeProject(), { selectedPageId: "main" });
    await renderPage(UIBuilderView);
    expect(seen.Canvas).toMatchObject({ previewMode: false, showGrid: true, zoom: 1, screenWidth: 1024, screenHeight: 600 });
    call(() => builder().setZoom(0.7));
    expect(seen.Canvas.zoom).toBe(0.7);
    call(() => builder().toggleGrid());
    expect(seen.Canvas.showGrid).toBe(false);
    call(() => builder().setScreenPresetIndex(3));
    expect(seen.Canvas).toMatchObject({ screenWidth: 1920, screenHeight: 1080 });
    call(() => builder().selectLayout("main_port"));
    expect(seen.Canvas).toMatchObject({ screenWidth: 1080, screenHeight: 1920 });
    call(() => builder().setPreviewMode(true));
    expect(seen.Canvas).toMatchObject({ previewMode: true, showGrid: false, zoom: 0.7, screenWidth: 1080, screenHeight: 1920 });
  });

  it("reach both previews: a preset chosen later sizes the Theme Studio and the stylesheet, as chosen", async () => {
    seed(makeProject(), { selectedPageId: "main", activeLayoutId: "main_port" });
    await renderPage(UIBuilderView);
    expect(seen.ThemeStudio).toMatchObject({ panelWidth: 1024, panelHeight: 600 });
    call(() => builder().setScreenPresetIndex(2));
    expect(seen.ThemeStudio).toMatchObject({ panelWidth: 1024, panelHeight: 768 });
    fireEvent.click(screen.getByTitle("Edit the project stylesheet (custom CSS)"));
    expect(seen.StylesheetEditor).toMatchObject({ panelWidth: 1024, panelHeight: 768 });
    call(() => builder().setScreenPresetIndex(3));
    expect(seen.StylesheetEditor).toMatchObject({ panelWidth: 1920, panelHeight: 1080 });
    expect(seen.ThemeStudio).toMatchObject({ panelWidth: 1920, panelHeight: 1080 });
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

describe("Validate's report, row by row", () => {
  it("lists what the platform answered about a chosen command, against the step that chose it", async () => {
    const p = makeProject();
    p.macros = [{ id: "m2", name: "Startup", steps: [{ action: "device.command", device: "amp", command: "set_fader", params: {} }] }] as never;
    seed(p);
    commands.commandIssues.mockImplementationOnce(async (actions: unknown[]) => new Map([[actions[0], ["'set_fader': 'channel' is required"]]]));
    await renderPage(UIBuilderView);
    await act(async () => { await seen.CanvasToolbar.onValidate(); });
    const row = screen.getByText("'set_fader': 'channel' is required").parentElement!.parentElement!;
    expect(row.textContent).toBe(`ERR'set_fader': 'channel' is requiredMacro "Startup" > device.command`);
  });

  it("counts one issue in the singular, marks an error, and goes to a page-wide issue's page", async () => {
    const clean = { id: "lbl", type: "label", text: "Hi", style: {} } as never;
    seed(
      makeProject({
        pages: [
          page("main", [clean], { lbl: { x: 10, y: 10, w: 20, h: 10 } }),
          page("other", [], {}, { grant: { devices: ["gone"] } } as never),
        ],
        masters: [],
      }),
      { selectedPageId: "main", selectedElementId: "lbl", selectedElementIds: ["lbl"] },
    );
    await renderPage(UIBuilderView);
    await act(async () => { await seen.CanvasToolbar.onValidate(); });
    expect(screen.getByText("Project Validation (1 issue)")).toBeInTheDocument();
    const tag = screen.getByText("ERR");
    const row = tag.parentElement!;
    expect(tag.style.color).toBe("var(--color-error)");
    expect(row.style.background).toBe("rgba(244, 67, 54, 0.08)");
    expect(row.style.border).toBe("1px solid rgba(244, 67, 54, 0.2)");
    expect(row.style.cursor).toBe("pointer");
    expect(row.textContent).toBe('ERRGranted device "gone" not found, so the page shown here cannot read or control itOther > page');
    call(() => fireEvent.click(row));
    expect(builder().selectedPageId).toBe("other");
    expect(builder().selectedElementId).toBeNull();
    expect(screen.queryByText(/Project Validation/)).toBeNull();
  });

  it("marks a warning in its own colour, and a row with no page as one that goes nowhere", async () => {
    await renderPage(UIBuilderView);
    await act(async () => { await seen.CanvasToolbar.onValidate(); });
    const warn = screen.getAllByText("WARN")[0];
    expect(warn.style.color).toBe("rgb(255, 152, 0)");
    expect(warn.parentElement!.style.background).toBe("rgba(255, 152, 0, 0.08)");
    expect(warn.parentElement!.style.border).toBe("1px solid rgba(255, 152, 0, 0.2)");
    const masterRow = screen.getAllByText("Master Elements > m_1")[0].parentElement!.parentElement!;
    expect(masterRow.style.cursor).toBe("default");
  });
});

describe("Panel Settings, field by field", () => {
  const open = () => call(() => fireEvent.click(screen.getByTitle("Panel Settings")));
  const field = (label: string) => within(dialog()).getByText(label).parentElement!;
  const selectIn = (label: string) => field(label).querySelector("select")!;
  const optionsOf = (label: string) => [...selectIn(label).options].map((o) => [o.value, o.text]);
  const boxIn = (label: string) => field(label).querySelector("input")! as HTMLInputElement;
  const typeInto = (box: HTMLInputElement, value: string) => {
    call(() => fireEvent.focus(box));
    call(() => fireEvent.change(box, { target: { value } }));
    call(() => fireEvent.blur(box));
  };

  it("says what each setting is for, in order", async () => {
    seed(makeProject({ settings: { page_transition: "fade", element_entry: "stagger" } }));
    await renderPage(UIBuilderView);
    open();
    expect(dialog().textContent).toBe(
      "Panel Settings" +
      "Accent Color" +
      "Font FamilyInter (Default)System UIRobotoSegoe UIMonospace" +
      "Lock Code (PIN)4-6 digit PIN to lock the touch panel. Leave empty to disable." +
      "Idle Timeout (seconds)Return to idle page after this many seconds of inactivity. 0 = disabled." +
      "Idle PageMain (main)Other (other)" +
      "Show a message when a control failsA press that does not reach its device puts the reason across the " +
      "panel for about five seconds. Turn this off in a space that shows its own status." +
      "Page TransitionNone (instant)FadeSlide LeftSlide RightSlide UpScale" +
      "Transition Durationms" +
      "Element Entry AnimationNone (instant)Fade InFade UpScale InStagger" +
      "Stagger StyleFadeFade UpScaleHow each element animates in as the stagger sweeps across the page." +
      "Stagger Delayms per element" +
      "CancelSave",
    );
  });

  it("offers each choice under the value the panel reads", async () => {
    await renderPage(UIBuilderView);
    open();
    expect(optionsOf("Font Family")).toEqual([
      ["Inter, system-ui, sans-serif", "Inter (Default)"],
      ["system-ui, sans-serif", "System UI"],
      ["'Roboto', sans-serif", "Roboto"],
      ["'Segoe UI', sans-serif", "Segoe UI"],
      ["monospace", "Monospace"],
    ]);
    expect(optionsOf("Idle Page")).toEqual([["main", "Main (main)"], ["other", "Other (other)"]]);
    expect(optionsOf("Page Transition")).toEqual([
      ["none", "None (instant)"], ["fade", "Fade"], ["slide-left", "Slide Left"],
      ["slide-right", "Slide Right"], ["slide-up", "Slide Up"], ["scale", "Scale"],
    ]);
    expect(optionsOf("Element Entry Animation")).toEqual([
      ["none", "None (instant)"], ["fade", "Fade In"], ["fade-up", "Fade Up"], ["scale", "Scale In"], ["stagger", "Stagger"],
    ]);
    call(() => fireEvent.change(selectIn("Element Entry Animation"), { target: { value: "stagger" } }));
    expect(optionsOf("Stagger Style")).toEqual([["fade", "Fade"], ["fade-up", "Fade Up"], ["scale", "Scale"]]);
  });

  it("shows a value of its own where the project leaves a setting blank", async () => {
    seed(makeProject({ settings: { accent_color: "", page_transition: undefined, page_transition_duration: undefined, element_entry: undefined } }));
    await renderPage(UIBuilderView);
    open();
    const accent = field("Accent Color").querySelectorAll("input");
    expect((accent[0] as HTMLInputElement).value).toBe("#2196f3");
    expect((accent[1] as HTMLInputElement).value).toBe("");
    expect(selectIn("Page Transition").value).toBe("none");
    expect(selectIn("Element Entry Animation").value).toBe("none");
    expect(within(dialog()).getByLabelText("Show a message when a control fails")).toBeChecked();
    // A blank transition is none, so it has no duration to set.
    expect(within(dialog()).queryByText("Transition Duration")).toBeNull();
    call(() => fireEvent.change(selectIn("Page Transition"), { target: { value: "slide-up" } }));
    call(() => fireEvent.change(selectIn("Element Entry Animation"), { target: { value: "stagger" } }));
    expect(boxIn("Transition Duration").value).toBe("200");
    expect(selectIn("Stagger Style").value).toBe("fade-up");
    expect(boxIn("Stagger Delay").value).toBe("30");
  });

  it("gives each number box its step, and the boxes with a unit their narrow width", async () => {
    seed(makeProject({ settings: { page_transition: "fade", element_entry: "stagger" } }));
    await renderPage(UIBuilderView);
    open();
    const boxes = ["Idle Timeout (seconds)", "Transition Duration", "Stagger Delay"].map(boxIn);
    expect(boxes.map((b) => b.getAttribute("step"))).toEqual([null, "50", "10"]);
    expect(boxes.map((b) => b.style.width)).toEqual(["100%", "80px", "80px"]);
  });

  it("keeps the colour picker and the colour's text in step, and saves the font chosen", async () => {
    await renderPage(UIBuilderView);
    open();
    const [picker, text] = [...field("Accent Color").querySelectorAll("input")] as HTMLInputElement[];
    expect(picker.value).toBe("#ff0000");
    call(() => fireEvent.change(picker, { target: { value: "#00ff00" } }));
    expect(text.value).toBe("#00ff00");
    call(() => fireEvent.change(selectIn("Font Family"), { target: { value: "system-ui, sans-serif" } }));
    call(() => fireEvent.click(within(dialog()).getByRole("button", { name: "Save" })));
    expect(ui().settings).toMatchObject({ accent_color: "#00ff00", font_family: "system-ui, sans-serif" });
  });

  it("saves the numbers typed into its boxes, held to each box's range", async () => {
    seed(makeProject({ settings: { page_transition: "fade", element_entry: "stagger" } }));
    await renderPage(UIBuilderView);
    open();
    typeInto(boxIn("Idle Timeout (seconds)"), "-5");
    expect(boxIn("Idle Timeout (seconds)").value).toBe("0");
    typeInto(boxIn("Idle Timeout (seconds)"), "45");
    typeInto(boxIn("Transition Duration"), "5000");
    expect(boxIn("Transition Duration").value).toBe("1000");
    typeInto(boxIn("Stagger Delay"), "1");
    expect(boxIn("Stagger Delay").value).toBe("10");
    typeInto(boxIn("Stagger Delay"), "500");
    expect(boxIn("Stagger Delay").value).toBe("200");
    typeInto(boxIn("Transition Duration"), "5");
    expect(boxIn("Transition Duration").value).toBe("50");
    call(() => fireEvent.change(selectIn("Stagger Style"), { target: { value: "scale" } }));
    call(() => fireEvent.click(within(dialog()).getByRole("button", { name: "Save" })));
    expect(ui().settings).toMatchObject({
      idle_timeout_seconds: 45, page_transition_duration: 50, element_stagger_ms: 200, element_stagger_style: "scale",
    });
  });

  it("asks before throwing changes away when Escape closes it, and closes at once when nothing changed", async () => {
    await renderPage(UIBuilderView);
    open();
    call(() => fireEvent.keyDown(document, { key: "Escape" }));
    expect(screen.queryByRole("dialog", { name: "Panel Settings" })).toBeNull();
    open();
    call(() => fireEvent.click(within(dialog()).getByLabelText("Show a message when a control fails")));
    call(() => fireEvent.keyDown(document, { key: "Escape" }));
    expect(screen.getByText("Discard unsaved settings changes?")).toBeInTheDocument();
    call(() => fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" })));
    expect(screen.queryByText("Discard unsaved settings changes?")).toBeNull();
    expect(within(dialog()).getByLabelText("Show a message when a control fails")).not.toBeChecked();
    call(() => fireEvent.click(within(dialog()).getByRole("button", { name: "Save" })));
    expect(ui().settings.show_error_messages).toBe(false);
  });
});
