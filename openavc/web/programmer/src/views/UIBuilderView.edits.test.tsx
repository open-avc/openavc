import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, screen, fireEvent, within } from "@testing-library/react";

// The UI Builder page's edits: which undo entry each one leaves (its words and
// the parts of the project it can put back), when a run of quick edits is one
// entry, what the project holds afterwards and what is selected. The children
// are stubs that record their props (test/uiBuilderHarness), so each edit is
// made the way the real child makes it: by calling the page's callback.

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
import {
  builder,
  installFetch,
  makeProject,
  master,
  pageOf,
  press,
  project,
  renderPage,
  resetMocks,
  saves,
  seed,
  seen,
  settle,
  themeFetch,
  toast,
  ui,
  undoLog,
} from "../test/uiBuilderHarness";

const call = (fn: () => unknown) => act(() => { fn(); });
const props = () => seen.PropertiesPanel;
const menu = () => seen.ContextMenu;
const outline = () => seen.OutlinePanel;
const elOf = (pageId: string, id: string) => pageOf(pageId).elements.find((e) => e.id === id) as unknown as Record<string, unknown>;

beforeEach(() => {
  resetMocks();
  installFetch();
  seed();
});

afterEach(() => {
  vi.useRealTimers();
});

// The page's 0.8 s run timer is faked once the page is drawn: React's async
// act waits on a real timeout, so the render goes first.
const drawn = async () => {
  const r = await renderPage(UIBuilderView);
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  return r;
};

describe("a run of quick edits is one undo entry", () => {

  it("keeps two property edits 0.8 s apart as one entry, then starts another", async () => {
    await drawn();
    call(() => props().onChange("btn_1", { label: "A" }));
    call(() => vi.advanceTimersByTime(799));
    call(() => props().onChange("btn_1", { label: "AB" }));
    expect(undoLog()).toEqual([["Edit element", "pages"]]);
    expect(elOf("main", "btn_1").label).toBe("AB");
    // The entry holds the project from before the first edit.
    expect(builder().undoStack[0].snapshot.pages![0].elements[0].label).toBe("btn_1");
    call(() => vi.advanceTimersByTime(800));
    call(() => props().onChange("btn_1", { label: "ABC" }));
    expect(undoLog()).toEqual([["Edit element", "pages"], ["Edit element", "pages"]]);
    expect(saves.debouncedSave).toHaveBeenCalledWith(2000);
    expect(saves.debouncedSave).toHaveBeenCalledTimes(3);
  });

  it("shares one run across the page, a master, the theme and a control", async () => {
    await drawn();
    call(() => props().onPageChange({ name: "Lobby" }));
    call(() => props().onChange("btn_1", { label: "A" }));
    call(() => props().onMasterElementChange("m_1", { label: "M" }));
    call(() => seen.ThemeStudio.onClearOverrides());
    expect(undoLog()).toEqual([["Edit page properties", "pages"]]);
    expect(pageOf("main").name).toBe("Lobby");
    expect(ui().master_elements![0].label).toBe("M");
    expect(ui().settings.theme_overrides).toEqual({});
  });

  it("starts with whichever edit came first: a master, then the theme", async () => {
    await drawn();
    call(() => props().onMasterElementChange("m_1", { label: "M" }));
    call(() => seen.ThemeStudio.onClearOverrides());
    expect(undoLog()).toEqual([["Edit master element", "master_elements"]]);
    call(() => vi.advanceTimersByTime(800));
    call(() => seen.ThemeStudio.onClearOverrides());
    expect(undoLog()).toEqual([["Edit master element", "master_elements"], ["Edit theme overrides", "settings"]]);
  });

  it("ends a run on any structural edit, so the next property edit is its own entry", async () => {
    const structural: Array<[string, () => unknown]> = [
      ["Delete master element", () => props().onDeleteMaster("m_1")],
      ["Change theme", () => props().onThemeChange("paper")],
      ["Rename element btn_1 → big", () => props().onRenameElement("btn_1", "big")],
      ["Edit project stylesheet", () => seen.StylesheetEditor.onSave(".y{}")],
      ["Move out of container", () => props().onReparent("in_1", null)],
    ];
    for (const [words, fire] of structural) {
      vi.useRealTimers();
      seed();
      resetMocks();
      installFetch();
      const r = await drawn();
      // Opening the stylesheet editor leaves the undo history alone.
      call(() => fireEvent.click(screen.getByTitle("Edit the project stylesheet (custom CSS)")));
      call(() => props().onChange("lbl_1", { label: "A" }));
      call(fire);
      call(() => props().onChange("lbl_1", { label: "B" }));
      expect(undoLog().map((e) => e[0]), words).toEqual(["Edit element", words, "Edit element"]);
      r.unmount();
    }
  });

  it("ends a run on a structural edit that leaves the selection as it was", async () => {
    seed(makeProject(), { selectedPageId: "main", contextMenu: { x: 0, y: 0, elementId: "btn_1", isMaster: false } });
    await drawn();
    const steps: Array<[string, () => unknown]> = [
      ["Bring to front", () => menu().onBringToFront("btn_1")],
      ["Promote to master", () => menu().onPromoteToMaster("btn_1")],
    ];
    for (const [words, fire] of steps) {
      call(() => props().onPageChange({ name: "A" }));
      call(fire);
      call(() => props().onPageChange({ name: "B" }));
      expect(undoLog().slice(-3).map((e) => e[0]), words).toEqual(["Edit page properties", words, "Edit page properties"]);
      call(() => vi.advanceTimersByTime(800));
    }
    expect(builder().selectedElementIds).toEqual([]);
  });

  it("ends a run when Panel Settings saves", async () => {
    await drawn();
    call(() => props().onPageChange({ name: "A" }));
    call(() => fireEvent.click(screen.getByTitle("Panel Settings")));
    call(() => fireEvent.change(screen.getByPlaceholderText("Leave empty for no lock"), { target: { value: "1234" } }));
    call(() => fireEvent.click(screen.getByRole("button", { name: "Save" })));
    call(() => props().onPageChange({ name: "B" }));
    expect(undoLog().map((e) => e[0])).toEqual(["Edit page properties", "Edit project settings", "Edit page properties"]);
  });

  it("ends a run when one master is swapped for another", async () => {
    seed(makeProject({ masters: [master("m_1"), master("m_2")] }), { selectedPageId: "main", selectedMasterElementId: "m_1" });
    await drawn();
    call(() => props().onMasterElementChange("m_1", { label: "A" }));
    call(() => builder().selectMasterElement("m_2"));
    call(() => props().onMasterElementChange("m_2", { label: "B" }));
    expect(undoLog().map((e) => e[0])).toEqual(["Edit master element", "Edit master element"]);
  });

  it("ends a run when the selection changes", async () => {
    await drawn();
    call(() => props().onChange("btn_1", { label: "A" }));
    call(() => builder().selectElement("lbl_1"));
    call(() => props().onChange("btn_1", { label: "B" }));
    call(() => builder().selectMasterElement("m_1"));
    call(() => props().onChange("btn_1", { label: "C" }));
    expect(undoLog().map((e) => e[0])).toEqual(["Edit element", "Edit element", "Edit element"]);
  });

  it("ends a run on undo and redo, so the edit after one is undoable alone", async () => {
    await drawn();
    call(() => props().onChange("btn_1", { label: "A" }));
    press("z", { ctrl: true });
    expect(elOf("main", "btn_1").label).toBe("btn_1");
    call(() => props().onChange("btn_1", { label: "B" }));
    expect(undoLog()).toEqual([["Edit element", "pages"]]);
    press("y", { ctrl: true });
    expect(builder().redoStack).toHaveLength(0);
    call(() => props().onChange("btn_1", { label: "C" }));
    expect(undoLog()).toEqual([["Edit element", "pages"], ["Edit element", "pages"]]);
  });

  it("ends each writer's own run 0.8 s after its last edit", async () => {
    seed(makeProject(), { selectedPageId: "main", selectedElementIds: ["btn_1"] });
    await drawn();
    press("ArrowRight");
    call(() => vi.advanceTimersByTime(799));
    press("ArrowRight");
    call(() => vi.advanceTimersByTime(800));
    press("ArrowRight");
    expect(undoLog().map((e) => e[0])).toEqual(["Nudge right", "Nudge right"]);
    call(() => vi.advanceTimersByTime(800));
    call(() => props().onPageChange({ name: "A" }));
    call(() => vi.advanceTimersByTime(799));
    call(() => props().onPageChange({ name: "AB" }));
    call(() => vi.advanceTimersByTime(800));
    call(() => props().onPageChange({ name: "ABC" }));
    call(() => vi.advanceTimersByTime(800));
    call(() => props().onMasterElementChange("m_1", { label: "M" }));
    call(() => vi.advanceTimersByTime(800));
    call(() => props().onMasterElementChange("m_1", { label: "MM" }));
    call(() => vi.advanceTimersByTime(800));
    call(() => seen.ThemeStudio.onClearOverrides());
    call(() => vi.advanceTimersByTime(800));
    call(() => seen.ThemeStudio.onClearOverrides());
    expect(undoLog().map((e) => e[0])).toEqual([
      "Nudge right", "Nudge right",
      "Edit page properties", "Edit page properties",
      "Edit master element", "Edit master element",
      "Edit theme overrides", "Edit theme overrides",
    ]);
  });

  it("does not end a run on a structural edit that leaves a burst behind it (nudge keeps coalescing)", async () => {
    seed(makeProject(), { selectedPageId: "main", selectedElementId: "btn_1", selectedElementIds: ["btn_1"] });
    await drawn();
    press("ArrowRight");
    press("ArrowRight");
    call(() => props().onChange("btn_1", { label: "A" }));
    expect(undoLog()).toEqual([["Nudge right", "pages"]]);
    call(() => vi.advanceTimersByTime(800));
    press("ArrowDown");
    expect(undoLog()).toEqual([["Nudge right", "pages"], ["Nudge down", "pages"]]);
  });
});

describe("structural edits", () => {
  it("promotes a control to a master and back, each one entry over pages and masters", async () => {
    seed(makeProject(), { selectedPageId: "main", selectedElementId: "btn_1", selectedElementIds: ["btn_1"], contextMenu: { x: 1, y: 2, elementId: "btn_1", isMaster: false } });
    await renderPage(UIBuilderView);
    call(() => menu().onPromoteToMaster("btn_1"));
    expect(undoLog()).toEqual([["Promote to master", "master_elements,pages"]]);
    expect(pageOf("main").elements.map((e) => e.id)).not.toContain("btn_1");
    expect(ui().master_elements!.map((m) => m.id)).toEqual(["m_1", "btn_1"]);
    expect(builder().selectedElementIds).toEqual([]);
    call(() => builder().selectMasterElement("btn_1"));
    call(() => props().onDemoteMaster("btn_1"));
    expect(undoLog()[1]).toEqual(["Demote from master", "master_elements,pages"]);
    expect(pageOf("main").elements.map((e) => e.id)).toContain("btn_1");
    expect(builder().selectedMasterElementId).toBeNull();
  });

  it("deletes a master as one entry over the masters alone", async () => {
    seed(makeProject(), { selectedPageId: "main", selectedMasterElementId: "m_1" });
    await renderPage(UIBuilderView);
    call(() => props().onDeleteMaster("m_1"));
    expect(undoLog()).toEqual([["Delete master element", "master_elements"]]);
    expect(ui().master_elements).toEqual([]);
    expect(builder().selectedMasterElementId).toBeNull();
  });

  it("renames a control everywhere it is named, keeps it selected and says so", async () => {
    seed(makeProject(), { selectedPageId: "main", selectedElementId: "btn_1", selectedElementIds: ["btn_1"] });
    await renderPage(UIBuilderView);
    call(() => props().onRenameElement("btn_1", "power"));
    expect(undoLog()).toEqual([["Rename element btn_1 → power", "macros,pages,variables"]]);
    expect(elOf("main", "power")).toBeTruthy();
    expect(JSON.stringify(project().macros)).toContain("ui.power.label");
    expect(JSON.stringify(project().variables)).toContain("ui.power.value");
    expect(builder().selectedElementId).toBe("power");
    expect(toast.showSuccess).toHaveBeenCalledWith("Renamed to power");
    expect(toast.showInfo).not.toHaveBeenCalled();
  });

  it("puts back only what a rename changed, and warns about scripts it cannot read", async () => {
    seed(makeProject({ scripts: [{ id: "s1", file: "s1.py" }] }), { selectedPageId: "main", selectedMasterElementId: "m_1" });
    await renderPage(UIBuilderView);
    call(() => props().onRenameElement("m_1", "header"));
    expect(undoLog()).toEqual([["Rename element m_1 → header", "master_elements"]]);
    expect(builder().selectedMasterElementId).toBe("header");
    expect(toast.showInfo).toHaveBeenCalledWith('If any scripts reference "ui.m_1.*", update them manually.');
  });

  it("refuses a rename onto a taken id with the reason, and ignores one to itself", async () => {
    await renderPage(UIBuilderView);
    call(() => props().onRenameElement("btn_1", "lbl_1"));
    expect(toast.showError).toHaveBeenCalledTimes(1);
    expect(String(toast.showError.mock.calls[0][0])).toMatch(/lbl_1/);
    call(() => props().onRenameElement("btn_1", "m_1"));
    expect(toast.showError).toHaveBeenCalledTimes(2);
    call(() => props().onRenameElement("btn_1", "btn_1"));
    expect(undoLog()).toEqual([]);
    expect(toast.showSuccess).not.toHaveBeenCalled();
  });

  it("changes the theme, choosing light or dark from its background and clearing the old theme's tuning", async () => {
    await renderPage(UIBuilderView);
    call(() => props().onThemeChange("paper"));
    expect(undoLog()).toEqual([["Change theme", "settings"]]);
    expect(builder().undoStack[0].snapshot.settings!.theme_id).toBe("dark-default");
    expect(ui().settings).toMatchObject({ theme_id: "paper", theme: "light", accent_color: "", font_family: "", theme_overrides: {} });
    // toMatchObject takes any object for {}: the old theme's overrides are gone.
    expect(ui().settings.theme_overrides).toEqual({});
    call(() => props().onThemeChange("dark-default"));
    expect(ui().settings.theme).toBe("dark");
    // No background to read: the id says.
    call(() => props().onThemeChange("light-ish"));
    expect(ui().settings.theme).toBe("light");
    call(() => props().onThemeChange("minimal"));
    expect(ui().settings.theme).toBe("light");
    call(() => props().onThemeChange("unknown-theme"));
    expect(ui().settings.theme).toBe("dark");
  });

  it("goes by the id when the theme's background is not a colour it can read", async () => {
    const { api, THEMES } = await import("../test/uiBuilderHarness");
    api.listThemes.mockResolvedValue([
      ...THEMES,
      { ...THEMES[0], id: "light-sky", variables: { panel_bg: "sky gradient" } },
      { ...THEMES[0], id: "night", variables: { panel_bg: "night gradient" } },
    ]);
    await renderPage(UIBuilderView);
    call(() => props().onThemeChange("light-sky"));
    expect(ui().settings.theme).toBe("light");
    call(() => props().onThemeChange("night"));
    expect(ui().settings.theme).toBe("dark");
  });

  it("hands the Properties panel no theme changer while the Theme Studio is open", async () => {
    await renderPage(UIBuilderView);
    expect(typeof props().onThemeChange).toBe("function");
    call(() => fireEvent.click(screen.getByTitle("Open Theme Studio")));
    expect(seen.ThemeStudio.open).toBe(true);
    expect(props().onThemeChange).toBeUndefined();
    call(() => seen.ThemeStudio.onChangeTheme("paper"));
    expect(ui().settings.theme_id).toBe("paper");
    call(() => seen.ThemeStudio.onClose());
    expect(seen.ThemeStudio.open).toBe(false);
    expect(typeof props().onThemeChange).toBe("function");
  });

  it("saves the project stylesheet as one entry and says so", async () => {
    await renderPage(UIBuilderView);
    expect(seen.StylesheetEditor).toBeUndefined();
    call(() => fireEvent.click(screen.getByTitle("Edit the project stylesheet (custom CSS)")));
    expect(seen.StylesheetEditor).toMatchObject({ previewPageId: "main", panelWidth: 1024, panelHeight: 600 });
    call(() => seen.StylesheetEditor.onSave(".brand{color:red}"));
    expect(undoLog()).toEqual([["Edit project stylesheet", "custom_css"]]);
    expect(builder().undoStack[0].snapshot.custom_css).toBe(".x{}");
    expect(ui().custom_css).toBe(".brand{color:red}");
    expect(toast.showSuccess).toHaveBeenCalledWith("Project stylesheet saved");
  });

  it("opens the stylesheet from the Properties panel too, and closes it", async () => {
    await renderPage(UIBuilderView);
    call(() => props().onOpenStylesheet());
    expect(screen.getByTestId("stub-StylesheetEditor")).toBeInTheDocument();
    call(() => seen.StylesheetEditor.onClose());
    expect(screen.queryByTestId("stub-StylesheetEditor")).toBeNull();
  });

  it("moves a control into a container and selects it", async () => {
    await renderPage(UIBuilderView);
    call(() => props().onReparent("btn_1", "grp_1"));
    expect(undoLog()).toEqual([["Move into container", "pages"]]);
    expect(elOf("main", "btn_1").parent).toBe("grp_1");
    expect(builder().selectedElementId).toBe("btn_1");
    call(() => fireEvent.click(screen.getByRole("button", { name: "Outline" })));
    call(() => outline().onReparent("btn_1", null));
    expect(undoLog()[1]).toEqual(["Move out of container", "pages"]);
    expect(elOf("main", "btn_1").parent ?? null).toBeNull();
  });
});

describe("the context menu", () => {
  const openMenu = (over: Record<string, unknown> = {}) =>
    seed(makeProject(), {
      selectedPageId: "main",
      selectedElementId: "btn_1",
      selectedElementIds: ["btn_1"],
      contextMenu: { x: 10, y: 20, elementId: "btn_1", isMaster: false },
      ...over,
    });

  it("is drawn only while one is open, where it was asked for", async () => {
    const first = await renderPage(UIBuilderView);
    expect(seen.ContextMenu).toBeUndefined();
    first.unmount();
    openMenu({ selectedElementIds: ["btn_1", "lbl_1"], clipboard: { elements: [], placements: {} } });
    await renderPage(UIBuilderView);
    expect(menu()).toMatchObject({ x: 10, y: 20, elementId: "btn_1", isMaster: false, multiSelectCount: 2, hasClipboard: true });
    call(() => menu().onClose());
    expect(builder().contextMenu).toBeNull();
  });

  it("duplicates and deletes one control", async () => {
    openMenu();
    await renderPage(UIBuilderView);
    call(() => menu().onDuplicate("btn_1"));
    expect(undoLog()).toEqual([["Duplicate element", "pages"]]);
    expect(pageOf("main").elements.map((e) => e.id)).toEqual(["btn_1", "lbl_1", "grp_1", "in_1", "button_1"]);
    call(() => menu().onDelete("btn_1"));
    expect(undoLog()[1]).toEqual(["Delete element", "pages"]);
    expect(pageOf("main").elements.map((e) => e.id)).not.toContain("btn_1");
    expect(builder().selectedElementIds).toEqual([]);
  });

  it("deletes and duplicates the whole selection, counting what is copied", async () => {
    openMenu({ selectedElementIds: ["btn_1", "grp_1", "in_1"] });
    await renderPage(UIBuilderView);
    call(() => menu().onDuplicateAll());
    // The child goes with its container, so two copies, not three.
    expect(undoLog()).toEqual([["Duplicate 2 elements", "pages"]]);
    call(() => menu().onDeleteAll());
    expect(undoLog()[1]).toEqual(["Delete 3 elements", "pages"]);
    const ids = pageOf("main").elements.map((e) => e.id);
    expect(ids).not.toContain("btn_1");
    expect(ids).not.toContain("grp_1");
    expect(builder().selectedElementIds).toEqual([]);
  });

  it("duplicates a one-control selection under the one-control words", async () => {
    openMenu({ selectedElementIds: ["grp_1", "in_1"] });
    await renderPage(UIBuilderView);
    call(() => menu().onDuplicateAll());
    expect(undoLog()).toEqual([["Duplicate element", "pages"]]);
  });

  it("copies the selection when several are selected, else what was clicked, then pastes", async () => {
    openMenu({ selectedElementIds: ["btn_1", "lbl_1"] });
    await renderPage(UIBuilderView);
    call(() => menu().onCopy(["btn_1"]));
    expect(builder().clipboard!.elements.map((e) => e.id)).toEqual(["btn_1", "lbl_1"]);
    expect(undoLog()).toEqual([]);
    call(() => menu().onPaste());
    expect(undoLog()).toEqual([["Paste 2 elements", "pages"]]);
    expect(pageOf("main").elements.length).toBe(6);
  });

  it("copies what was clicked when one or none is selected, and pastes it as one", async () => {
    openMenu({ selectedElementIds: ["btn_1"] });
    await renderPage(UIBuilderView);
    call(() => menu().onCopy(["lbl_1"]));
    expect(builder().clipboard!.elements.map((e) => e.id)).toEqual(["lbl_1"]);
    call(() => menu().onPaste());
    expect(undoLog()).toEqual([["Paste element", "pages"]]);
  });

  it("brings a control to the front and sends it to the back", async () => {
    openMenu();
    await renderPage(UIBuilderView);
    call(() => menu().onBringToFront("btn_1"));
    expect(pageOf("main").elements.map((e) => e.id).at(-1)).toBe("btn_1");
    call(() => menu().onSendToBack("btn_1"));
    expect(pageOf("main").elements[0].id).toBe("btn_1");
    expect(undoLog().map((e) => e[0])).toEqual(["Bring to front", "Send to back"]);
  });

  it("demotes and deletes a master from its menu", async () => {
    openMenu({ contextMenu: { x: 0, y: 0, elementId: "m_1", isMaster: true }, selectedElementIds: [] });
    await renderPage(UIBuilderView);
    call(() => menu().onDemoteFromMaster("m_1"));
    expect(undoLog()).toEqual([["Demote from master", "master_elements,pages"]]);
    expect(ui().master_elements).toEqual([]);
  });

  it("deletes a master from its menu", async () => {
    openMenu({ contextMenu: { x: 0, y: 0, elementId: "m_1", isMaster: true }, selectedElementIds: [] });
    await renderPage(UIBuilderView);
    call(() => menu().onDeleteMaster("m_1"));
    expect(undoLog()).toEqual([["Delete master element", "master_elements"]]);
  });
});

describe("the outline", () => {
  const showOutline = async () => {
    await renderPage(UIBuilderView);
    call(() => fireEvent.click(screen.getByRole("button", { name: "Outline" })));
  };

  it("hides a control in the arrangement being authored, and shows it again", async () => {
    seed(makeProject(), { selectedPageId: "main", activeLayoutId: "main_port" });
    await showOutline();
    expect(outline().hiddenElementIds).toEqual(new Set());
    call(() => outline().onToggleHidden("btn_1"));
    expect(undoLog()).toEqual([["Hide in this layout", "pages"]]);
    expect(pageOf("main").layouts[1].hidden).toEqual(["btn_1"]);
    expect(pageOf("main").layouts[0].hidden).toEqual([]);
    expect(outline().hiddenElementIds).toEqual(new Set(["btn_1"]));
    call(() => outline().onToggleHidden("btn_1"));
    expect(undoLog()[1]).toEqual(["Show in this layout", "pages"]);
    expect(pageOf("main").layouts[1].hidden).toEqual([]);
  });

  it("selects one, or adds to the selection with shift, and selects a master", async () => {
    seed(makeProject(), { selectedPageId: "main" });
    await showOutline();
    call(() => outline().onSelectElement("btn_1", false));
    expect(builder().selectedElementIds).toEqual(["btn_1"]);
    call(() => outline().onSelectElement("lbl_1", true));
    expect(builder().selectedElementIds).toEqual(["btn_1", "lbl_1"]);
    call(() => outline().onSelectMasterElement("m_1"));
    expect(builder().selectedMasterElementId).toBe("m_1");
  });

  it("swaps a control with its neighbour, and locks one", async () => {
    seed(makeProject(), { selectedPageId: "main" });
    await showOutline();
    call(() => outline().onMoveOrder("btn_1", "lbl_1"));
    expect(undoLog()).toEqual([["Reorder element", "pages"]]);
    expect(pageOf("main").elements.slice(0, 2).map((e) => e.id)).toEqual(["lbl_1", "btn_1"]);
    call(() => outline().onToggleLock("btn_1"));
    expect(elOf("main", "btn_1").locked).toBe(true);
    expect(outline().lockedElementIds).toEqual(new Set(["btn_1"]));
  });

  it("hands the outline the page, masters, selection and collapsed rows", async () => {
    seed(makeProject(), { selectedPageId: "main", selectedElementIds: ["btn_1"], collapsedOutlineIds: ["grp_1"] });
    await showOutline();
    expect(outline().page.id).toBe("main");
    expect(outline().masterElements.map((m: { id: string }) => m.id)).toEqual(["m_1"]);
    expect(outline().selectedElementIds).toEqual(["btn_1"]);
    expect(outline().collapsedIds).toEqual(["grp_1"]);
    call(() => outline().onToggleCollapse("grp_1"));
    expect(builder().collapsedOutlineIds).toEqual([]);
  });
});

describe("resetting element styles to the theme", () => {
  it("asks first, then drops each control's style keys its theme defines, as one entry", async () => {
    themeFetch.byId["dark-default"] = {
      variables: {},
      element_defaults: { label: { bg_color: "#000", text_color: "#fff" } },
    };
    await renderPage(UIBuilderView);
    call(() => seen.ThemeStudio.onResetElementStyles());
    expect(screen.getByText("Reset element styles to theme defaults?")).toBeInTheDocument();
    call(() => fireEvent.click(screen.getByRole("button", { name: "Cancel" })));
    expect(screen.queryByText("Reset element styles to theme defaults?")).toBeNull();
    expect(undoLog()).toEqual([]);
    call(() => seen.ThemeStudio.onResetElementStyles());
    call(() => fireEvent.click(screen.getByRole("button", { name: "Reset Styles" })));
    expect(undoLog()).toEqual([["Reset element styles to theme", "pages"]]);
    expect(elOf("main", "lbl_1").style).toEqual({ font_size: 2 });
    // A type the theme says nothing about is left exactly as it was.
    expect(elOf("main", "btn_1").style).toEqual({});
    expect(pageOf("other").elements[0]).toBe(builder().undoStack[0].snapshot.pages![1].elements[0]);
    expect(toast.showSuccess).toHaveBeenCalledWith("Element styles reset to theme defaults");
  });
});

describe("the theme the Properties panel shows", () => {
  it("hands over every type's effective defaults, built from the theme's defaults and variables", async () => {
    themeFetch.byId["dark-default"] = {
      variables: { button_bg: "#b1", button_text: "#b2", button_border: "#b3", accent: "#ac", surface: "#su", panel_text: "#pt", border_radius: 6, font_family: "Inter" },
      element_defaults: {
        button: { bg_color: "#own" },
        label: { text_color: "#lbl" },
        clock: { border_radius: 2 },
      },
    };
    await renderPage(UIBuilderView);
    const d = props().themeDefaults;
    expect(d.button).toEqual({ bg_color: "#own", text_color: "#b2", border_color: "#b3", border_radius: 6, font_family: "Inter" });
    expect(d.page_nav).toEqual({ bg_color: "#b1", text_color: "#b2", border_color: "#b3", accent_color: "#ac", track_color: "#su", border_radius: 6, font_family: "Inter" });
    expect(d.keypad).toMatchObject({ bg_color: "#b1", accent_color: "#ac", track_color: "#su" });
    expect(d.camera_preset).toEqual({ bg_color: "#b1", text_color: "#b2", border_color: "#b3", border_radius: 6, font_family: "Inter" });
    expect(d.slider).toEqual({ accent_color: "#ac", track_color: "#su", text_color: "#pt", border_radius: 6, font_family: "Inter" });
    expect(d.label).toEqual({ text_color: "#lbl", border_radius: 6, font_family: "Inter" });
    expect(d.clock).toEqual({ border_radius: 2, text_color: "#pt", font_family: "Inter" });
    expect(d.status_led).toEqual({ text_color: "#pt", border_radius: 6, font_family: "Inter" });
    expect(Object.keys(d).sort()).toEqual([
      "button", "camera_preset", "clock", "fader", "gauge", "group", "keypad", "label", "level_meter", "list",
      "matrix", "page_nav", "select", "slider", "status_led", "text_input",
    ]);
    expect(themeFetch.calls).toEqual(["/api/themes/dark-default"]);
  });

  it("fills only what each variable gives, and leaves a type with nothing out", async () => {
    themeFetch.byId["dark-default"] = { variables: { button_text: "#b2" } };
    await renderPage(UIBuilderView);
    const d = props().themeDefaults;
    expect(d.button).toEqual({ text_color: "#b2" });
    expect(d.slider).toEqual({});
    expect(d.label).toBeUndefined();
  });

  it("asks the server for the default theme when none is chosen, through the tunnel", async () => {
    const p = makeProject({ settings: { theme_id: "" } });
    seed(p);
    const { api } = await import("../test/uiBuilderHarness");
    api.getTunnelPrefix.mockImplementation(() => "/t/abc");
    themeFetch.byId["dark-default"] = { variables: { accent: "#1" } };
    await renderPage(UIBuilderView);
    expect(themeFetch.calls).toEqual(["/t/abc/api/themes/dark-default"]);
  });

  it("shows no defaults when the theme cannot be read", async () => {
    themeFetch.byId["dark-default"] = { variables: { accent: "#1" } };
    const r = await renderPage(UIBuilderView);
    expect(props().themeDefaults.slider).toBeDefined();
    r.unmount();
    themeFetch.fail = true;
    await renderPage(UIBuilderView);
    expect(props().themeDefaults).toEqual({});
  });

  it("clears what it showed when the next theme cannot be read", async () => {
    themeFetch.byId["dark-default"] = { variables: { accent: "#1" } };
    await renderPage(UIBuilderView);
    expect(props().themeDefaults.slider).toEqual({ accent_color: "#1" });
    themeFetch.fail = true;
    call(() => props().onThemeChange("paper"));
    await settle();
    expect(props().themeDefaults).toEqual({});
  });

  it("treats a theme the server does not have as one with nothing in it", async () => {
    await renderPage(UIBuilderView);
    expect(Object.keys(props().themeDefaults).sort()).toEqual(["button", "camera_preset", "fader", "keypad", "page_nav", "select", "slider", "text_input"]);
    expect(props().themeDefaults.button).toEqual({});
  });

  it("reads the theme again when it changes and when the Theme Studio saves it", async () => {
    themeFetch.byId["dark-default"] = { variables: {} };
    themeFetch.byId.paper = { variables: { accent: "#p" } };
    await renderPage(UIBuilderView);
    call(() => props().onThemeChange("paper"));
    await settle();
    expect(props().themeDefaults.slider).toEqual({ accent_color: "#p" });
    call(() => seen.ThemeStudio.onThemeSaved());
    await settle();
    expect(themeFetch.calls).toEqual(["/api/themes/dark-default", "/api/themes/paper", "/api/themes/paper"]);
  });

  it("hands the theme list to both, reads it again on request, and says when it cannot", async () => {
    await renderPage(UIBuilderView);
    const { api } = await import("../test/uiBuilderHarness");
    expect(props().themes.map((t: { id: string }) => t.id)).toEqual(["dark-default", "paper", "light-ish", "minimal"]);
    expect(seen.ThemeStudio.themes).toBe(props().themes);
    expect(api.listThemes).toHaveBeenCalledTimes(1);
    api.listThemes.mockRejectedValueOnce(new Error("x"));
    call(() => seen.ThemeStudio.onRefreshThemes());
    await settle();
    expect(api.listThemes).toHaveBeenCalledTimes(2);
    expect(toast.showError).toHaveBeenCalledWith("Failed to load themes");
  });

  it("hands the Theme Studio the theme in use, its overrides and the screen's own size", async () => {
    seed(makeProject(), { selectedPageId: "main", activeLayoutId: "main_port", screenPresetIndex: 1 });
    const first = await renderPage(UIBuilderView);
    expect(seen.ThemeStudio).toMatchObject({ currentThemeId: "dark-default", themeOverrides: { accent: "#123456" } });
    // Not the arrangement's turned screen: the preset as it is.
    expect(seen.ThemeStudio.panelWidth).toBeGreaterThan(seen.ThemeStudio.panelHeight);
    expect(seen.Canvas.screenWidth).toBeLessThan(seen.Canvas.screenHeight);
    first.unmount();
    seed(makeProject({ settings: { theme_id: "", theme_overrides: undefined } }));
    await renderPage(UIBuilderView);
    expect(seen.ThemeStudio).toMatchObject({ currentThemeId: "dark-default", themeOverrides: {} });
    // toMatchObject takes undefined for {}: none set is an empty set, not none.
    expect(seen.ThemeStudio.themeOverrides).toEqual({});
  });
});

describe("the master element edit", () => {
  it("writes the master's change and leaves the page alone", async () => {
    seed(makeProject({ masters: [master("m_1"), master("m_2")] }), { selectedPageId: "main", selectedMasterElementId: "m_2" });
    await renderPage(UIBuilderView);
    expect(props().masterElement.id).toBe("m_2");
    call(() => props().onMasterElementChange("m_2", { label: "Two" }));
    expect(ui().master_elements!.map((m) => m.label)).toEqual(["m_1", "Two"]);
    expect(undoLog()).toEqual([["Edit master element", "master_elements"]]);
  });
});

// Each edit's undo entry holds exactly the parts of the project it replaces,
// as they were, and the edit leaves every other part as the same object: an
// entry that named less than its edit wrote would leave that part changed on
// undo, and one that named more would put back what nobody touched.
describe("what each edit's undo entry holds", () => {
  type Part = "pages" | "settings" | "master_elements" | "custom_css" | "page_groups";
  const UI_PARTS: Part[] = ["pages", "settings", "master_elements", "custom_css", "page_groups"];
  const resetTheme = () => {
    themeFetch.byId["dark-default"] = { variables: {}, element_defaults: { label: { bg_color: "#000" } } };
  };
  const cases: { name: string; parts: Part[]; setup?: () => void; edit: () => void }[] = [
    { name: "a control deleted", parts: ["pages"], edit: () => menu().onDelete("btn_1") },
    { name: "a control promoted", parts: ["pages", "master_elements"], edit: () => menu().onPromoteToMaster("btn_1") },
    { name: "a master demoted", parts: ["pages", "master_elements"], edit: () => props().onDemoteMaster("m_1") },
    { name: "a master deleted", parts: ["master_elements"], edit: () => props().onDeleteMaster("m_1") },
    { name: "a master edited", parts: ["master_elements"], edit: () => props().onMasterElementChange("m_1", { label: "M" }) },
    { name: "a control edited", parts: ["pages"], edit: () => props().onChange("btn_1", { label: "B" }) },
    { name: "the page edited", parts: ["pages"], edit: () => props().onPageChange({ name: "Front" }) },
    { name: "the theme changed", parts: ["settings"], edit: () => props().onThemeChange("paper") },
    { name: "the theme's overrides cleared", parts: ["settings"], edit: () => seen.ThemeStudio.onClearOverrides() },
    { name: "the stylesheet saved", parts: ["custom_css"], edit: () => { fireEvent.click(screen.getByTitle("Edit the project stylesheet (custom CSS)")); } },
    {
      name: "the styles reset to the theme",
      parts: ["pages"],
      setup: resetTheme,
      edit: () => {
        seen.ThemeStudio.onResetElementStyles();
      },
    },
    { name: "Panel Settings saved", parts: ["settings"], edit: () => { fireEvent.click(screen.getByTitle("Panel Settings")); } },
  ];

  it.each(cases)("$name", async ({ parts, setup, edit, name }) => {
    setup?.();
    seed(makeProject(), { selectedPageId: "main", selectedElementId: "btn_1", selectedElementIds: ["btn_1"], contextMenu: { x: 1, y: 2, elementId: "btn_1", isMaster: false } });
    await renderPage(UIBuilderView);
    const was = project();
    call(edit);
    // The two that open a dialog first finish the edit inside it.
    if (name === "the stylesheet saved") call(() => seen.StylesheetEditor.onSave(".y{}"));
    if (name === "the styles reset to the theme") call(() => fireEvent.click(screen.getByRole("button", { name: "Reset Styles" })));
    if (name === "Panel Settings saved") {
      const d = within(screen.getByRole("dialog", { name: "Panel Settings" }));
      call(() => fireEvent.change(d.getByPlaceholderText("#2196F3"), { target: { value: "#00ff00" } }));
      call(() => fireEvent.click(d.getByRole("button", { name: "Save" })));
    }
    expect(builder().undoStack).toHaveLength(1);
    const { snapshot } = builder().undoStack[0];
    expect(Object.keys(snapshot).sort()).toEqual([...parts].sort());
    for (const part of UI_PARTS) {
      if (parts.includes(part)) {
        expect(snapshot[part], part).toBe(was.ui[part]);
        expect(ui()[part], part).not.toBe(was.ui[part]);
      } else {
        expect(ui()[part], part).toBe(was.ui[part]);
      }
    }
    expect(project().macros).toBe(was.macros);
    expect(project().variables).toBe(was.variables);
    expect(Object.keys(ui()).sort()).toEqual(Object.keys(was.ui).sort());
  });

  it("puts back no masters as an empty list, and no stylesheet as an empty one", async () => {
    const bare = makeProject();
    delete (bare.ui as Record<string, unknown>).master_elements;
    delete (bare.ui as Record<string, unknown>).custom_css;
    seed(bare, { selectedPageId: "main", contextMenu: { x: 1, y: 2, elementId: "btn_1", isMaster: false } });
    await renderPage(UIBuilderView);
    call(() => menu().onPromoteToMaster("btn_1"));
    expect(builder().undoStack[0].snapshot.master_elements).toEqual([]);
    expect(ui().master_elements!.map((m) => m.id)).toEqual(["btn_1"]);
    call(() => fireEvent.click(screen.getByTitle("Edit the project stylesheet (custom CSS)")));
    call(() => seen.StylesheetEditor.onSave(".y{}"));
    expect(builder().undoStack[1].snapshot).toEqual({ custom_css: "" });
    expect(ui().custom_css).toBe(".y{}");
    call(() => builder().undo());
    expect(ui().custom_css).toBe("");
    call(() => builder().undo());
    expect(ui().master_elements).toEqual([]);
    expect(pageOf("main").elements.map((e) => e.id)).toContain("btn_1");
  });
});
