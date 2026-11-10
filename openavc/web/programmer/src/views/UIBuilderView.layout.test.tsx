import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, screen, fireEvent } from "@testing-library/react";

// The UI Builder page's layout as its children live through it: which of them
// stay mounted (and so keep their own state) through preview, Ctrl+E, a dialog,
// the canvas menu and a page switch, and which are drawn fresh each time.

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
  mounts,
  page,
  press,
  renderPage,
  resetMocks,
  seed,
  seen,
  themeFetch,
  ui,
  undoLog,
} from "../test/uiBuilderHarness";

const call = (fn: () => unknown) => act(() => { fn(); });
const count = (name: string) => [mounts[name]?.mounted ?? 0, mounts[name]?.unmounted ?? 0];
const click = (title: string) => call(() => fireEvent.click(screen.getByTitle(title)));

beforeEach(() => {
  resetMocks();
  installFetch();
  seed();
});

/** Every way the page changes around the canvas, one after the other. */
async function everyChange() {
  press("p", { ctrl: true });
  press("p", { ctrl: true });
  press("e", { ctrl: true });
  press("e", { ctrl: true });
  click("Open Theme Studio");
  call(() => seen.ThemeStudio.onClose());
  click("Edit the project stylesheet (custom CSS)");
  call(() => seen.StylesheetEditor.onClose());
  click("Panel Settings");
  call(() => fireEvent.click(screen.getByRole("button", { name: "Cancel" })));
  call(() => builder().setContextMenu({ x: 1, y: 2, elementId: "btn_1", isMaster: false }));
  call(() => seen.ContextMenu.onClose());
  call(() => seen.ThemeStudio.onResetElementStyles());
  call(() => fireEvent.click(screen.getByRole("button", { name: "Cancel" })));
  await act(async () => { await seen.CanvasToolbar.onValidate(); });
  call(() => fireEvent.click(screen.getByRole("button", { name: "×" })));
  call(() => builder().selectPage("other"));
}

describe("what stays mounted", () => {
  it("keeps the canvas, the toolbar and the Theme Studio through every change around them", async () => {
    await renderPage(UIBuilderView);
    expect([count("Canvas"), count("CanvasToolbar"), count("ThemeStudio")]).toEqual([[1, 0], [1, 0], [1, 0]]);
    await everyChange();
    expect(seen.Canvas.page.id).toBe("other");
    expect([count("Canvas"), count("CanvasToolbar"), count("ThemeStudio")]).toEqual([[1, 0], [1, 0], [1, 0]]);
  });

  it("draws the Properties panel again after preview, and keeps it through Ctrl+E, the dialogs and the menu", async () => {
    await renderPage(UIBuilderView);
    expect(count("PropertiesPanel")).toEqual([1, 0]);
    press("p", { ctrl: true });
    expect(count("PropertiesPanel")).toEqual([1, 1]);
    press("p", { ctrl: true });
    expect(count("PropertiesPanel")).toEqual([2, 1]);
    press("e", { ctrl: true });
    press("e", { ctrl: true });
    click("Open Theme Studio");
    call(() => seen.ThemeStudio.onClose());
    click("Edit the project stylesheet (custom CSS)");
    call(() => seen.StylesheetEditor.onClose());
    call(() => builder().setContextMenu({ x: 1, y: 2, elementId: "btn_1", isMaster: false }));
    call(() => seen.ContextMenu.onClose());
    call(() => builder().selectPage("other"));
    expect(count("PropertiesPanel")).toEqual([2, 1]);
  });

  it("draws the palette again after preview and after Ctrl+E, and keeps it through the dialogs and the menu", async () => {
    await renderPage(UIBuilderView);
    expect(count("ElementPalette")).toEqual([1, 0]);
    press("p", { ctrl: true });
    press("p", { ctrl: true });
    expect(count("ElementPalette")).toEqual([2, 1]);
    press("e", { ctrl: true });
    press("e", { ctrl: true });
    expect(count("ElementPalette")).toEqual([3, 2]);
    click("Open Theme Studio");
    call(() => seen.ThemeStudio.onClose());
    click("Panel Settings");
    call(() => fireEvent.click(screen.getByRole("button", { name: "Cancel" })));
    call(() => builder().setContextMenu({ x: 1, y: 2, elementId: "btn_1", isMaster: false }));
    call(() => seen.ContextMenu.onClose());
    expect(count("ElementPalette")).toEqual([3, 2]);
  });

  it("draws the stylesheet editor fresh each time it opens, from the toolbar or the Properties panel", async () => {
    await renderPage(UIBuilderView);
    expect(count("StylesheetEditor")).toEqual([0, 0]);
    click("Edit the project stylesheet (custom CSS)");
    expect(count("StylesheetEditor")).toEqual([1, 0]);
    call(() => seen.StylesheetEditor.onClose());
    expect(count("StylesheetEditor")).toEqual([1, 1]);
    call(() => seen.PropertiesPanel.onOpenStylesheet());
    expect(count("StylesheetEditor")).toEqual([2, 1]);
    // Opening it again while it is open draws nothing new.
    call(() => seen.PropertiesPanel.onOpenStylesheet());
    expect(count("StylesheetEditor")).toEqual([2, 1]);
  });

  it("keeps one canvas menu while it moves to another control, and lets it go when closed", async () => {
    await renderPage(UIBuilderView);
    call(() => builder().setContextMenu({ x: 1, y: 2, elementId: "btn_1", isMaster: false }));
    call(() => builder().setContextMenu({ x: 30, y: 40, elementId: "m_1", isMaster: true }));
    expect(count("ContextMenu")).toEqual([1, 0]);
    expect(seen.ContextMenu).toMatchObject({ x: 30, y: 40, elementId: "m_1", isMaster: true });
    call(() => seen.ContextMenu.onClose());
    expect(count("ContextMenu")).toEqual([1, 1]);
    expect(screen.queryByTestId("stub-ContextMenu")).toBeNull();
  });

  it("draws the Theme Studio closed from the start, with the stylesheet, the question and the report all closed", async () => {
    await renderPage(UIBuilderView);
    expect(seen.ThemeStudio.open).toBe(false);
    expect(screen.queryByTestId("stub-StylesheetEditor")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });
});

describe("the dialogs one over another", () => {
  it("asks the reset question over the open Theme Studio, and leaves the Studio open after either answer", async () => {
    themeFetch.byId["dark-default"] = { variables: {}, element_defaults: { label: { bg_color: "#000" } } };
    await renderPage(UIBuilderView);
    click("Open Theme Studio");
    call(() => seen.ThemeStudio.onResetElementStyles());
    expect(screen.getByText("Reset element styles to theme defaults?")).toBeInTheDocument();
    expect(seen.ThemeStudio.open).toBe(true);
    call(() => fireEvent.click(screen.getByRole("button", { name: "Reset Styles" })));
    expect(screen.queryByText("Reset element styles to theme defaults?")).toBeNull();
    expect(seen.ThemeStudio.open).toBe(true);
    expect(undoLog()).toEqual([["Reset element styles to theme", "pages"]]);
    call(() => seen.ThemeStudio.onResetElementStyles());
    call(() => fireEvent.click(screen.getByRole("button", { name: "Cancel" })));
    expect(seen.ThemeStudio.open).toBe(true);
  });

  it("keeps a dialog open across preview, which only the canvas's surroundings follow", async () => {
    await renderPage(UIBuilderView);
    click("Edit the project stylesheet (custom CSS)");
    click("Open Theme Studio");
    press("p", { ctrl: true });
    expect(seen.ThemeStudio.open).toBe(true);
    expect(screen.getByTestId("stub-StylesheetEditor")).toBeInTheDocument();
    expect(count("StylesheetEditor")).toEqual([1, 0]);
  });

  it("previews the stylesheet on the screen as the preset has it, not turned for the arrangement", async () => {
    seed(makeProject(), { selectedPageId: "main", activeLayoutId: "main_port", screenPresetIndex: 1 });
    await renderPage(UIBuilderView);
    click("Edit the project stylesheet (custom CSS)");
    expect(seen.StylesheetEditor.panelWidth).toBeGreaterThan(seen.StylesheetEditor.panelHeight);
    expect(seen.StylesheetEditor.panelWidth).toBe(seen.ThemeStudio.panelWidth);
    expect(seen.StylesheetEditor.panelHeight).toBe(seen.ThemeStudio.panelHeight);
    expect(seen.Canvas.screenWidth).toBeLessThan(seen.Canvas.screenHeight);
  });

  it("previews the stylesheet on the page on screen, and the next page once it changes", async () => {
    seed(makeProject(), { selectedPageId: "other" });
    await renderPage(UIBuilderView);
    click("Edit the project stylesheet (custom CSS)");
    expect(seen.StylesheetEditor.previewPageId).toBe("other");
    call(() => builder().selectPage("main"));
    expect(seen.StylesheetEditor.previewPageId).toBe("main");
  });
});

describe("the order the dialogs stack in", () => {
  it("draws the Theme Studio, the stylesheet, the reset question, Validate's report and Panel Settings in that order, each later one over the one before", async () => {
    await renderPage(UIBuilderView);
    click("Edit the project stylesheet (custom CSS)");
    click("Open Theme Studio");
    call(() => seen.ThemeStudio.onResetElementStyles());
    await act(async () => { await seen.CanvasToolbar.onValidate(); });
    click("Panel Settings");
    const drawn = [
      screen.getByTestId("stub-ThemeStudio"),
      screen.getByTestId("stub-StylesheetEditor"),
      screen.getByRole("alertdialog"),
      screen.getByText(/^Project Validation/),
      screen.getByRole("dialog", { name: "Panel Settings" }),
    ];
    for (let i = 1; i < drawn.length; i++) {
      expect(drawn[i - 1].compareDocumentPosition(drawn[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });
});

describe("each dialog on its own", () => {
  it("opens only the dialog asked for, from each of its openers", async () => {
    await renderPage(UIBuilderView);
    const shown = () => [
      seen.ThemeStudio.open,
      !!screen.queryByTestId("stub-StylesheetEditor"),
      !!screen.queryByRole("dialog", { name: "Panel Settings" }),
      !!screen.queryByText("Reset element styles to theme defaults?"),
    ];
    click("Open Theme Studio");
    expect(shown()).toEqual([true, false, false, false]);
    call(() => seen.ThemeStudio.onClose());
    click("Edit the project stylesheet (custom CSS)");
    expect(shown()).toEqual([false, true, false, false]);
    call(() => seen.StylesheetEditor.onClose());
    call(() => seen.PropertiesPanel.onOpenStylesheet());
    expect(shown()).toEqual([false, true, false, false]);
    call(() => seen.StylesheetEditor.onClose());
    click("Panel Settings");
    expect(shown()).toEqual([false, false, true, false]);
    call(() => fireEvent.click(screen.getByRole("button", { name: "Cancel" })));
    expect(shown()).toEqual([false, false, false, false]);
  });

  it("draws both previews at the chosen preset's own size", async () => {
    seed(makeProject(), { selectedPageId: "main", activeLayoutId: "main_port", screenPresetIndex: 1 });
    await renderPage(UIBuilderView);
    click("Edit the project stylesheet (custom CSS)");
    expect(seen.ThemeStudio).toMatchObject({ panelWidth: 1280, panelHeight: 800 });
    expect(seen.StylesheetEditor).toMatchObject({ panelWidth: 1280, panelHeight: 800 });
    expect(seen.Canvas).toMatchObject({ screenWidth: 800, screenHeight: 1280 });
  });
});

describe("a project with no pages", () => {
  it("previews the stylesheet on no page", async () => {
    seed(makeProject({ pages: [] }));
    await renderPage(UIBuilderView);
    click("Edit the project stylesheet (custom CSS)");
    expect(seen.StylesheetEditor.previewPageId).toBeNull();
  });
});

describe("resetting styles on a control with no style of its own", () => {
  it("gives it an empty style where its theme defines defaults, and leaves one the theme says nothing about alone", async () => {
    themeFetch.byId["dark-default"] = { variables: {}, element_defaults: { button: { bg_color: "#000" } } };
    const bare = { id: "bare", type: "button", label: "B" } as never;
    const plain = { id: "plain", type: "clock", label: "C" } as never;
    seed(makeProject({ pages: [page("main", [bare, plain], { bare: { x: 0, y: 0, w: 10, h: 10 }, plain: { x: 20, y: 0, w: 10, h: 10 } })] }));
    await renderPage(UIBuilderView);
    call(() => seen.ThemeStudio.onResetElementStyles());
    call(() => fireEvent.click(screen.getByRole("button", { name: "Reset Styles" })));
    const els = ui().pages[0].elements as unknown as Array<Record<string, unknown>>;
    expect(els[0].style).toEqual({});
    expect("style" in els[1]).toBe(false);
  });
});

describe("a project with no masters list", () => {
  it("hands the canvas no list at all, and the outline and the keys an empty one", async () => {
    const p = makeProject();
    delete (p.ui as { master_elements?: unknown }).master_elements;
    seed(p, { selectedPageId: "main" });
    await renderPage(UIBuilderView);
    expect(seen.Canvas.masterElements).toBeUndefined();
    call(() => fireEvent.click(screen.getByRole("button", { name: "Outline" })));
    expect(seen.OutlinePanel.masterElements).toEqual([]);
    expect(seen.PropertiesPanel.masterElement).toBeNull();
    expect(ui().master_elements).toBeUndefined();
  });
});
