import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, screen, fireEvent } from "@testing-library/react";

// The UI Builder page's edits of a control or a master where the project is
// thin: no page at all, no masters, no macros or variables, an empty
// selection or clipboard. Each says what the edit leaves (its undo entry, the
// project, the selection) when what it works on is missing. The children are
// stubs that record their props (test/uiBuilderHarness).

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
  installFetch,
  makeProject,
  master,
  pageOf,
  press,
  project,
  renderPage,
  resetMocks,
  seed,
  seen,
  toast,
  ui,
  undoLog,
} from "../test/uiBuilderHarness";

const call = (fn: () => unknown) => act(() => { fn(); });
const props = () => seen.PropertiesPanel;
const menu = () => seen.ContextMenu;
const outline = () => seen.OutlinePanel;
const showOutline = () => call(() => fireEvent.click(screen.getByRole("button", { name: "Outline" })));
const useProjectStoreSet = (state: Record<string, unknown>) => useProjectStore.setState(state as never);
const menuOn = (elementId: string, isMaster = false) => ({ contextMenu: { x: 1, y: 2, elementId, isMaster } });

beforeEach(() => {
  resetMocks();
  installFetch();
  seed();
});

describe("with no page to edit", () => {
  const noPages = () => makeProject({ pages: [] });

  it("leaves every control edit undone and the project as it was, from the menu, the outline and the Properties panel", async () => {
    seed(noPages(), { selectedElementIds: ["btn_1"], clipboard: { elements: [{ id: "c", type: "label" }], placements: { c: { x: 0, y: 0, w: 1, h: 1 } } }, ...menuOn("btn_1") });
    await renderPage(UIBuilderView);
    showOutline();
    const was = project();
    const edits: Array<() => void> = [
      () => menu().onDuplicate("btn_1"),
      () => menu().onDelete("btn_1"),
      () => menu().onDuplicateAll(),
      () => menu().onDeleteAll(),
      () => menu().onCopy(["btn_1"]),
      () => menu().onPaste(),
      () => menu().onBringToFront("btn_1"),
      () => menu().onSendToBack("btn_1"),
      () => menu().onPromoteToMaster("btn_1"),
      () => menu().onDemoteFromMaster("m_1"),
      () => outline().onToggleHidden("btn_1"),
      () => outline().onMoveOrder("btn_1", "lbl_1"),
      () => outline().onReparent("btn_1", null),
      () => props().onChange("btn_1", { label: "B" }),
      () => props().onPageChange({ name: "Front" }),
      () => props().onReparent("btn_1", "grp_1"),
      () => seen.ElementPalette.onAdd("button"),
    ];
    for (const edit of edits) call(edit);
    expect(undoLog()).toEqual([]);
    expect(project()).toBe(was);
    expect(builder().clipboard!.elements.map((e) => e.id)).toEqual(["c"]);
    // The two that select after their edit return before they get there.
    expect(builder().selectedElementIds).toEqual(["btn_1"]);
  });

  it("still edits, renames and deletes a master, which no page holds", async () => {
    seed(noPages(), { selectedMasterElementId: "m_1" });
    await renderPage(UIBuilderView);
    call(() => props().onMasterElementChange("m_1", { label: "M" }));
    call(() => props().onRenameElement("m_1", "m_2"));
    expect(builder().selectedMasterElementId).toBe("m_2");
    call(() => props().onDeleteMaster("m_2"));
    expect(undoLog()).toEqual([
      ["Edit master element", "master_elements"],
      ["Rename element m_1 → m_2", "master_elements"],
      ["Delete master element", "master_elements"],
    ]);
    expect(ui().master_elements).toEqual([]);
    expect(builder().selectedMasterElementId).toBeNull();
  });
});

describe("with a project that has no masters, macros or variables", () => {
  const bare = () => {
    const p = makeProject({ masters: [] });
    delete (p.ui as Record<string, unknown>).master_elements;
    delete (p as Record<string, unknown>).macros;
    delete (p as Record<string, unknown>).variables;
    delete (p as Record<string, unknown>).scripts;
    return p;
  };

  it("renames a control, putting back empty lists for the macros and variables it names none of", async () => {
    seed(bare(), { selectedPageId: "main", selectedElementId: "btn_1", selectedElementIds: ["btn_1"] });
    await renderPage(UIBuilderView);
    call(() => props().onRenameElement("btn_1", "btn_x"));
    expect(undoLog()).toEqual([["Rename element btn_1 → btn_x", "macros,pages,variables"]]);
    const { snapshot } = builder().undoStack[0];
    expect(snapshot.macros).toEqual([]);
    expect(snapshot.variables).toEqual([]);
    expect(project().macros).toEqual([]);
    expect(project().variables).toEqual([]);
    expect(ui().master_elements).toEqual([]);
    expect(pageOf("main").elements.map((e) => e.id)).toContain("btn_x");
    expect(builder().selectedElementId).toBe("btn_x");
    expect(toast.showSuccess).toHaveBeenCalledWith("Renamed to btn_x");
    expect(toast.showInfo).not.toHaveBeenCalled();
  });

  it("refuses a rename onto a page's own id the same way, with no masters to read", async () => {
    seed(bare(), { selectedPageId: "main" });
    await renderPage(UIBuilderView);
    call(() => props().onRenameElement("btn_1", "lbl_1"));
    expect(toast.showError).toHaveBeenCalledTimes(1);
    expect(undoLog()).toEqual([]);
  });

  it("demotes nothing it cannot find, and deletes and edits a master in an empty list", async () => {
    seed(bare(), { selectedPageId: "main", selectedMasterElementId: "m_9" });
    await renderPage(UIBuilderView);
    call(() => props().onDemoteMaster("m_9"));
    call(() => props().onMasterElementChange("m_9", { label: "M" }));
    call(() => props().onDeleteMaster("m_9"));
    expect(undoLog().map((e) => e[0])).toEqual(["Demote from master", "Edit master element", "Delete master element"]);
    for (const entry of builder().undoStack) expect(entry.snapshot.master_elements).toEqual([]);
    expect(ui().master_elements).toEqual([]);
    expect(pageOf("main").elements.map((e) => e.id)).toEqual(["btn_1", "lbl_1", "grp_1", "in_1"]);
    expect(builder().selectedMasterElementId).toBeNull();
  });

  it("duplicates and pastes past every page's ids when there are no masters to avoid", async () => {
    seed(bare(), { selectedPageId: "main", selectedElementIds: ["btn_1", "lbl_1"], ...menuOn("btn_1") });
    await renderPage(UIBuilderView);
    call(() => menu().onDuplicate("btn_1"));
    call(() => menu().onDuplicateAll());
    call(() => menu().onCopy(["btn_1"]));
    call(() => menu().onPaste());
    expect(undoLog().map((e) => e[0])).toEqual(["Duplicate element", "Duplicate 2 elements", "Paste 2 elements"]);
    const ids = pageOf("main").elements.map((e) => e.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids.length).toBe(9);
  });
});

describe("with nothing to act on", () => {
  it("leaves no entry for a Duplicate All with nothing selected", async () => {
    seed(makeProject(), { selectedPageId: "main", selectedElementIds: [], ...menuOn("btn_1") });
    await renderPage(UIBuilderView);
    call(() => menu().onDuplicateAll());
    expect(undoLog()).toEqual([]);
  });

  it("makes an entry that changes nothing for a Duplicate of a control the page does not hold", async () => {
    seed(makeProject(), { selectedPageId: "main", ...menuOn("ghost") });
    await renderPage(UIBuilderView);
    const pages = ui().pages;
    call(() => menu().onDuplicate("ghost"));
    call(() => menu().onDelete("ghost"));
    expect(undoLog()).toEqual([["Duplicate element", "pages"], ["Delete element", "pages"]]);
    expect(pageOf("main").elements.map((e) => e.id)).toEqual(pages[0].elements.map((e) => e.id));
  });

  it("leaves the clipboard as it was when nothing copied is on the page, and pastes nothing from an empty one", async () => {
    const held = { elements: [{ id: "c", type: "label" }], placements: { c: { x: 0, y: 0, w: 1, h: 1 } } };
    seed(makeProject(), { selectedPageId: "main", clipboard: held, ...menuOn("btn_1") });
    await renderPage(UIBuilderView);
    call(() => menu().onCopy(["ghost"]));
    expect(builder().clipboard).toBe(held);
    act(() => builder().setClipboard({ elements: [], placements: {} }));
    call(() => menu().onPaste());
    expect(undoLog()).toEqual([]);
  });

  it("pastes nothing with no clipboard at all", async () => {
    seed(makeProject(), { selectedPageId: "main", clipboard: null, ...menuOn("btn_1") });
    await renderPage(UIBuilderView);
    call(() => menu().onPaste());
    expect(undoLog()).toEqual([]);
  });
});

describe("what a menu edit hands on", () => {
  it("deletes and duplicates the control clicked, not the one selected", async () => {
    seed(makeProject(), { selectedPageId: "main", selectedElementId: "lbl_1", selectedElementIds: ["lbl_1"], ...menuOn("btn_1") });
    await renderPage(UIBuilderView);
    call(() => menu().onDuplicate("btn_1"));
    expect(pageOf("main").elements.map((e) => e.id)).toEqual(["btn_1", "lbl_1", "grp_1", "in_1", "button_1"]);
    call(() => menu().onDelete("btn_1"));
    expect(pageOf("main").elements.map((e) => e.id)).toEqual(["lbl_1", "grp_1", "in_1", "button_1"]);
    expect(builder().selectedElementIds).toEqual([]);
  });

  it("duplicates a container with its contents, and a copy avoids every master's id", async () => {
    seed(makeProject({ masters: [master("button_1"), master("group_1")] }), { selectedPageId: "main", ...menuOn("grp_1") });
    await renderPage(UIBuilderView);
    call(() => menu().onDuplicate("grp_1"));
    const els = pageOf("main").elements;
    expect(els.map((e) => e.id)).toEqual(["btn_1", "lbl_1", "grp_1", "in_1", "group_2", "button_2"]);
    expect((els[5] as unknown as { parent: string }).parent).toBe("group_2");
  });

  it("duplicates and pastes a selection past every master's id too", async () => {
    seed(makeProject({ masters: [master("button_1"), master("label_1")] }), { selectedPageId: "main", selectedElementIds: ["btn_1", "lbl_1"], ...menuOn("btn_1") });
    await renderPage(UIBuilderView);
    call(() => menu().onDuplicateAll());
    expect(pageOf("main").elements.map((e) => e.id).slice(4)).toEqual(["button_2", "label_2"]);
    call(() => menu().onCopy(["btn_1"]));
    call(() => menu().onPaste());
    expect(pageOf("main").elements.map((e) => e.id).slice(6)).toEqual(["button_3", "label_3"]);
  });

  it("hands the menu the selection's size and whether the clipboard holds anything", async () => {
    seed(makeProject(), { selectedPageId: "main", selectedElementIds: ["btn_1", "lbl_1", "in_1"], clipboard: null, ...menuOn("btn_1") });
    await renderPage(UIBuilderView);
    expect(menu()).toMatchObject({ multiSelectCount: 3, hasClipboard: false, isMaster: false });
  });
});

describe("the toolbar's own buttons and the save banner, as drawn", () => {
  it("docks Theme, Stylesheet, Settings and the shortcuts list at the toolbar's end, in that order, alike but the last", async () => {
    seed(makeProject(), { selectedPageId: "main" });
    await renderPage(UIBuilderView);
    const buttons = Array.from(screen.getByTestId("stub-CanvasToolbar").querySelectorAll("button"));
    expect(buttons.map((b) => [b.title, b.textContent])).toEqual([
      ["Open Theme Studio", " Theme"],
      ["Edit the project stylesheet (custom CSS)", " Stylesheet"],
      ["Panel Settings", " Settings"],
      ["Keyboard Shortcuts", "?"],
    ]);
    for (const b of buttons.slice(0, 3)) {
      expect(b.style.padding).toBe("3px var(--space-md)");
      expect(b.style.background).toBe("var(--bg-hover)");
      expect(b.style.color).toBe("var(--text-secondary)");
      expect(b.querySelector("svg")).not.toBeNull();
    }
    const help = buttons[3];
    expect([help.style.width, help.style.height, help.style.fontWeight, help.style.justifyContent]).toEqual(["24px", "24px", "700", "center"]);
    expect(help.querySelector("svg")).toBeNull();
    call(() => fireEvent.click(buttons[2]));
    expect(screen.getByRole("dialog", { name: "Panel Settings" })).toBeTruthy();
  });

  it("draws the save failure across the top in the error colour, Retry the stronger of its two buttons", async () => {
    seed(makeProject(), { selectedPageId: "main" });
    act(() => useProjectStoreSet({ error: "disk full" }));
    await renderPage(UIBuilderView);
    const banner = screen.getByText("Save failed: disk full").parentElement as HTMLElement;
    expect(banner.style.background).toBe("rgba(244, 67, 54, 0.15)");
    expect(banner.style.color).toBe("var(--color-error)");
    expect(banner.style.borderBottom).toBe("1px solid var(--color-error)");
    const retry = screen.getByRole("button", { name: "Retry" }) as HTMLButtonElement;
    const dismiss = screen.getByRole("button", { name: "Dismiss" }) as HTMLButtonElement;
    expect([retry.style.padding, retry.style.fontWeight, retry.style.border, retry.style.color]).toEqual(["2px 10px", "600", "1px solid var(--color-error)", "var(--color-error)"]);
    expect([dismiss.style.padding, dismiss.style.fontWeight, dismiss.style.border, dismiss.style.color]).toEqual(["2px 10px", "", "1px solid var(--border-color)", "var(--text-secondary)"]);
    expect(banner.nextElementSibling).toBe(screen.getByTestId("stub-CanvasToolbar"));
  });
});

describe("the left panel", () => {
  it("keeps the Outline tab through preview and through Ctrl+E", async () => {
    seed(makeProject(), { selectedPageId: "main" });
    await renderPage(UIBuilderView);
    showOutline();
    expect(screen.getByTestId("stub-OutlinePanel")).toBeTruthy();
    call(() => builder().setPreviewMode(true));
    expect(screen.queryByTestId("stub-OutlinePanel")).toBeNull();
    call(() => builder().setPreviewMode(false));
    expect(screen.getByTestId("stub-OutlinePanel")).toBeTruthy();
    press("e", { ctrl: true });
    expect(screen.queryByTestId("stub-OutlinePanel")).toBeNull();
    press("e", { ctrl: true });
    expect(screen.getByTestId("stub-OutlinePanel")).toBeTruthy();
    call(() => fireEvent.click(screen.getByRole("button", { name: "Elements" })));
    expect(screen.getByTestId("stub-ElementPalette")).toBeTruthy();
    expect(screen.queryByTestId("stub-OutlinePanel")).toBeNull();
  });

  it("marks the tab on screen in the accent colour, and the other muted", async () => {
    seed(makeProject(), { selectedPageId: "main" });
    await renderPage(UIBuilderView);
    const tab = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;
    expect(tab("Elements").style.color).toBe("var(--accent)");
    expect(tab("Elements").style.borderBottom).toBe("2px solid var(--accent)");
    expect(tab("Outline").style.color).toBe("var(--text-muted)");
    expect(tab("Outline").style.borderBottom).toBe("2px solid transparent");
    showOutline();
    expect(tab("Outline").style.color).toBe("var(--accent)");
    expect(tab("Elements").style.color).toBe("var(--text-muted)");
  });

  it("closes the palette on a page the author wrote, with a note saying why, and not on one that names no file", async () => {
    const custom = makeProject();
    custom.ui.pages[0] = { ...custom.ui.pages[0], render_mode: "custom", custom_file: "main.html" } as never;
    custom.ui.pages[1] = { ...custom.ui.pages[1], render_mode: "custom" } as never;
    seed(custom, { selectedPageId: "main" });
    await renderPage(UIBuilderView);
    expect(seen.ElementPalette.disabled).toBe(true);
    expect(seen.ElementPalette.disabledNote).toBe("This page shows a page you wrote, so controls placed here are not drawn.");
    call(() => builder().selectPage("other"));
    expect(seen.ElementPalette.disabled).toBe(false);
    expect(seen.ElementPalette.disabledNote).toBeUndefined();
  });
});
