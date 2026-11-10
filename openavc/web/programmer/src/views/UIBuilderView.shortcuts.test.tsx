import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, screen, fireEvent } from "@testing-library/react";

// The UI Builder's keyboard: which key does what, to which selection, and
// when the page leaves a key alone (a dialog is open, preview, typing in a
// field). Plus the page's other listeners: the server's page change in
// preview, a pending focus from elsewhere in the IDE, the first page, and the
// save before the tab closes.

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
import { useNavigationStore } from "../store/navigationStore";
import {
  builder,
  installFetch,
  makeProject,
  master,
  page,
  el,
  pageOf,
  press,
  renderPage,
  resetMocks,
  saves,
  seed,
  seen,
  ui,
  undoLog,
  ws,
} from "../test/uiBuilderHarness";

const call = (fn: () => unknown) => act(() => { fn(); });
const box = (pageId: string, id: string, layout = 0) => pageOf(pageId).layouts[layout].placements[id];
const ids = (pageId: string) => pageOf(pageId).elements.map((e) => e.id);
const selected = (sel: string[], over: Record<string, unknown> = {}) =>
  reseed(makeProject(), { selectedPageId: "main", selectedElementId: sel[0] ?? null, selectedElementIds: sel, ...over });
/** New stores, inside act so a page already drawn has drawn them before the next key. */
const reseed = (...args: Parameters<typeof seed>) => act(() => { seed(...args); });

beforeEach(() => {
  resetMocks();
  installFetch();
  seed();
});

describe("nudging with the arrow keys", () => {
  it("moves the selection one snap step, ten with shift, a hair with alt", async () => {
    selected(["btn_1", "lbl_1"]);
    await renderPage(UIBuilderView);
    const right = press("ArrowRight");
    expect(right.defaultPrevented).toBe(true);
    expect(box("main", "btn_1")).toEqual({ x: 15, y: 10, w: 20, h: 10 });
    expect(box("main", "lbl_1")).toEqual({ x: 45, y: 10, w: 20, h: 10 });
    press("ArrowDown", { shift: true });
    expect(box("main", "btn_1")).toEqual({ x: 15, y: 50, w: 20, h: 10 });
    press("ArrowLeft", { alt: true });
    expect(box("main", "btn_1").x).toBeCloseTo(14.75);
    press("ArrowUp", { alt: true, shift: true });
    expect(box("main", "btn_1").y).toBeCloseTo(47.5);
    expect(undoLog()).toEqual([["Nudge right", "pages"]]);
  });

  it("rounds each nudged box the way a drag rounds it", async () => {
    reseed(makeProject({ pages: [page("main", [el("btn_1", "button")], { btn_1: { x: 10.123456, y: 10, w: 20, h: 10 } })] }), { selectedPageId: "main", selectedElementIds: ["btn_1"] });
    await renderPage(UIBuilderView);
    press("ArrowRight");
    const { roundPct } = await import("../components/ui-builder/uiBuilderHelpers");
    const x = box("main", "btn_1").x;
    expect(x).toBe(roundPct(15.123456));
    expect(x).not.toBe(10.123456 + 5);
  });

  it("moves a hair when the page does not snap", async () => {
    reseed(makeProject({ pages: [page("main", [el("btn_1", "button")], { btn_1: { x: 10, y: 10, w: 20, h: 10 } }, { snap: { enabled: false, x: 5, y: 5 } })] }), { selectedPageId: "main", selectedElementIds: ["btn_1"] });
    await renderPage(UIBuilderView);
    press("ArrowUp");
    expect(box("main", "btn_1").y).toBeCloseTo(9.75);
    expect(undoLog()).toEqual([["Nudge up", "pages"]]);
  });

  it("writes the arrangement being authored, and nothing clamps it to the screen", async () => {
    selected(["btn_1"], { activeLayoutId: "main_port" });
    await renderPage(UIBuilderView);
    press("ArrowLeft", { shift: true });
    press("ArrowLeft", { shift: true });
    expect(box("main", "btn_1", 1)).toEqual({ x: -90, y: 10, w: 20, h: 10 });
    expect(box("main", "btn_1", 0)).toEqual({ x: 10, y: 10, w: 20, h: 10 });
  });

  it("leaves a locked selection where it is, and skips ids not on the page", async () => {
    const p = makeProject();
    (p.ui.pages[0].elements[1] as unknown as { locked: boolean }).locked = true;
    reseed(p, { selectedPageId: "main", selectedElementIds: ["btn_1", "lbl_1"] });
    await renderPage(UIBuilderView);
    const ev = press("ArrowRight");
    expect(ev.defaultPrevented).toBe(false);
    expect(undoLog()).toEqual([]);
    reseed(makeProject(), { selectedPageId: "main", selectedElementIds: ["gone", "btn_1"] });
    press("ArrowRight");
    expect(box("main", "btn_1").x).toBe(15);
    reseed(makeProject(), { selectedPageId: "main", selectedElementIds: ["gone"] });
    const none = press("ArrowRight");
    expect(none.defaultPrevented).toBe(true);
    expect(undoLog()).toEqual([]);
  });

  it("writes no box for a selected id that is not on the page", async () => {
    selected(["gone", "btn_1"]);
    await renderPage(UIBuilderView);
    press("ArrowRight");
    expect(box("main", "btn_1").x).toBe(15);
    expect(Object.keys(pageOf("main").layouts[0].placements)).not.toContain("gone");
  });

  it("moves a master's box for the orientation on screen, and not a locked one", async () => {
    reseed(makeProject(), { selectedPageId: "main", selectedMasterElementId: "m_1", activeLayoutId: "main_port" });
    await renderPage(UIBuilderView);
    const ev = press("ArrowRight");
    expect(ev.defaultPrevented).toBe(true);
    expect(ui().master_elements![0].placements).toEqual({ landscape: { x: 10, y: 10, w: 20, h: 10 }, portrait: { x: 55, y: 50, w: 30, h: 8 } });
    press("ArrowRight");
    expect(undoLog()).toEqual([["Edit master element", "master_elements"]]);
    reseed(makeProject({ masters: [master("m_1", { locked: true } as never)] }), { selectedPageId: "main", selectedMasterElementId: "m_1" });
    const locked = press("ArrowRight");
    expect(locked.defaultPrevented).toBe(false);
    expect(ui().master_elements![0].placements.landscape.x).toBe(10);
  });

  it("does nothing for a master id that is not there, or with nothing selected", async () => {
    reseed(makeProject(), { selectedPageId: "main", selectedMasterElementId: "nope" });
    await renderPage(UIBuilderView);
    expect(press("ArrowRight").defaultPrevented).toBe(false);
    reseed(makeProject(), { selectedPageId: "main" });
    expect(press("ArrowRight").defaultPrevented).toBe(false);
    expect(undoLog()).toEqual([]);
  });

  it("leaves a master with no box at all where it is", async () => {
    reseed(makeProject({ masters: [master("m_1", { placements: {} } as never)] }), { selectedPageId: "main", selectedMasterElementId: "m_1" });
    await renderPage(UIBuilderView);
    expect(press("ArrowRight").defaultPrevented).toBe(true);
    expect(ui().master_elements![0].placements).toEqual({});
    expect(undoLog()).toEqual([]);
  });

  it("moves a master's landscape box from a page with no layouts", async () => {
    reseed(makeProject({ pages: [{ ...page("main", [], {}), layouts: [] } as never] }), { selectedPageId: "main", selectedMasterElementId: "m_1" });
    await renderPage(UIBuilderView);
    press("ArrowDown");
    expect(ui().master_elements![0].placements.landscape).toEqual({ x: 10, y: 14, w: 20, h: 10 });
  });
});

describe("deleting with Delete or Backspace", () => {
  it("deletes one selected control and clears the selection", async () => {
    selected(["btn_1"]);
    await renderPage(UIBuilderView);
    expect(press("Delete").defaultPrevented).toBe(true);
    expect(ids("main")).not.toContain("btn_1");
    expect(undoLog()).toEqual([["Delete element", "pages"]]);
    expect(builder().selectedElementIds).toEqual([]);
  });

  it("deletes a selection of several as one entry", async () => {
    selected(["btn_1", "lbl_1"]);
    await renderPage(UIBuilderView);
    press("Backspace");
    expect(ids("main")).toEqual(["grp_1", "in_1"]);
    expect(undoLog()).toEqual([["Delete 2 elements", "pages"]]);
    expect(builder().selectedElementIds).toEqual([]);
  });

  it("deletes the selected master, but never a locked one or a locked control", async () => {
    reseed(makeProject(), { selectedPageId: "main", selectedMasterElementId: "m_1" });
    await renderPage(UIBuilderView);
    press("Delete");
    expect(ui().master_elements).toEqual([]);
    expect(undoLog()).toEqual([["Delete master element", "master_elements"]]);
    reseed(makeProject({ masters: [master("m_1", { locked: true } as never)] }), { selectedPageId: "main", selectedMasterElementId: "m_1" });
    expect(press("Delete").defaultPrevented).toBe(false);
    expect(ui().master_elements).toHaveLength(1);
    const p = makeProject();
    (p.ui.pages[0].elements[0] as unknown as { locked: boolean }).locked = true;
    reseed(p, { selectedPageId: "main", selectedElementIds: ["btn_1", "lbl_1"] });
    press("Delete");
    expect(ids("main")).toContain("lbl_1");
    expect(undoLog()).toEqual([]);
  });

  it("leaves a key typed in a field to the field", async () => {
    selected(["btn_1"]);
    await renderPage(UIBuilderView);
    const input = document.createElement("input");
    document.body.appendChild(input);
    for (const tag of ["input", "textarea", "select"]) {
      const target = tag === "input" ? input : document.body.appendChild(document.createElement(tag));
      expect(press("Delete", {}, target).defaultPrevented).toBe(false);
      expect(press("Escape", {}, target).defaultPrevented).toBe(false);
      expect(press("ArrowRight", {}, target).defaultPrevented).toBe(false);
      // Ctrl shortcuts are left alone in a field too.
      expect(press("z", { ctrl: true }, target).defaultPrevented).toBe(false);
      expect(press("d", { ctrl: true }, target).defaultPrevented).toBe(false);
    }
    expect(ids("main")).toContain("btn_1");
    expect(builder().selectedElementIds).toEqual(["btn_1"]);
  });

  it("still nudges, deselects and deletes on a Ctrl chord typed in a field", async () => {
    selected(["btn_1"]);
    await renderPage(UIBuilderView);
    const input = document.body.appendChild(document.createElement("input"));
    expect(press("ArrowRight", { ctrl: true }, input).defaultPrevented).toBe(true);
    expect(box("main", "btn_1").x).toBe(15);
    expect(press("Delete", { meta: true }, input).defaultPrevented).toBe(true);
    expect(ids("main")).not.toContain("btn_1");
    selected(["lbl_1"]);
    expect(press("Escape", { ctrl: true }, input).defaultPrevented).toBe(true);
    expect(builder().selectedElementIds).toEqual([]);
  });
});

describe("the other keys", () => {
  it("deselects on Escape", async () => {
    selected(["btn_1"]);
    await renderPage(UIBuilderView);
    expect(press("Escape").defaultPrevented).toBe(true);
    expect(builder().selectedElementId).toBeNull();
  });

  it("toggles preview with Ctrl+P or Cmd+P, and in preview ignores the rest", async () => {
    selected(["btn_1"]);
    await renderPage(UIBuilderView);
    expect(press("p", { ctrl: true }).defaultPrevented).toBe(true);
    expect(builder().previewMode).toBe(true);
    call(() => builder().selectElement("btn_1"));
    expect(press("Delete").defaultPrevented).toBe(false);
    expect(press("Escape").defaultPrevented).toBe(false);
    expect(press("z", { ctrl: true }).defaultPrevented).toBe(false);
    expect(ids("main")).toContain("btn_1");
    press("p", { meta: true });
    expect(builder().previewMode).toBe(false);
  });

  it("hides and shows the palette with Ctrl+E, preview or not", async () => {
    await renderPage(UIBuilderView);
    expect(screen.getByTestId("stub-ElementPalette")).toBeInTheDocument();
    expect(press("e", { ctrl: true }).defaultPrevented).toBe(true);
    expect(screen.queryByTestId("stub-ElementPalette")).toBeNull();
    expect(builder().previewMode).toBe(false);
    press("p", { ctrl: true });
    press("e", { meta: true });
    press("p", { ctrl: true });
    expect(screen.getByTestId("stub-ElementPalette")).toBeInTheDocument();
  });

  it("undoes with Ctrl+Z and redoes with Ctrl+Shift+Z or Ctrl+Y", async () => {
    selected(["btn_1"]);
    await renderPage(UIBuilderView);
    press("Delete");
    expect(press("z", { ctrl: true }).defaultPrevented).toBe(true);
    expect(ids("main")).toContain("btn_1");
    expect(press("z", { ctrl: true, shift: true }).defaultPrevented).toBe(true);
    expect(ids("main")).not.toContain("btn_1");
    press("z", { meta: true });
    expect(press("y", { ctrl: true }).defaultPrevented).toBe(true);
    expect(ids("main")).not.toContain("btn_1");
  });

  it("saves now on Ctrl+S: the pending save, and a direct one while changes are unsaved", async () => {
    await renderPage(UIBuilderView);
    expect(press("s", { ctrl: true }).defaultPrevented).toBe(true);
    expect(saves.flushSave).toHaveBeenCalledTimes(1);
    expect(saves.save).not.toHaveBeenCalled();
    useProjectStore.setState({ dirty: true, saving: true });
    press("s", { meta: true });
    expect(saves.save).not.toHaveBeenCalled();
    useProjectStore.setState({ dirty: true, saving: false });
    press("s", { ctrl: true });
    expect(saves.save).toHaveBeenCalledTimes(1);
    expect(press("s", { ctrl: true, shift: true }).defaultPrevented).toBe(false);
    expect(saves.flushSave).toHaveBeenCalledTimes(3);
  });

  it("copies, pastes and duplicates with Ctrl+C, Ctrl+V and Ctrl+D", async () => {
    selected(["btn_1"]);
    await renderPage(UIBuilderView);
    expect(press("v", { ctrl: true }).defaultPrevented).toBe(false);
    expect(press("c", { ctrl: true }).defaultPrevented).toBe(true);
    expect(builder().clipboard!.elements.map((e) => e.id)).toEqual(["btn_1"]);
    expect(press("v", { ctrl: true }).defaultPrevented).toBe(true);
    expect(undoLog()).toEqual([["Paste element", "pages"]]);
    expect(press("d", { ctrl: true }).defaultPrevented).toBe(true);
    expect(undoLog()[1]).toEqual(["Duplicate element", "pages"]);
    call(() => builder().selectElements(["btn_1", "lbl_1"]));
    press("d", { meta: true });
    expect(undoLog()[2]).toEqual(["Duplicate 2 elements", "pages"]);
    call(() => builder().selectElement(null));
    expect(press("c", { ctrl: true }).defaultPrevented).toBe(false);
    expect(press("d", { ctrl: true }).defaultPrevented).toBe(false);
  });

  it("ignores a key it has no use for, whatever its name", async () => {
    selected(["btn_1"]);
    await renderPage(UIBuilderView);
    // A throw inside the listener reaches the window, not the test.
    const thrown: unknown[] = [];
    const onError = (e: ErrorEvent) => { thrown.push(e.error); e.preventDefault(); };
    window.addEventListener("error", onError);
    for (const key of ["toString", "constructor", "__proto__", "hasOwnProperty", "Arrow", "q"]) {
      expect(press(key).defaultPrevented, key).toBe(false);
      expect(press(key, { ctrl: true }).defaultPrevented, key).toBe(false);
    }
    window.removeEventListener("error", onError);
    expect(thrown).toEqual([]);
    expect(box("main", "btn_1").x).toBe(10);
    expect(undoLog()).toEqual([]);
  });

  it("stands down while a dialog is open", async () => {
    selected(["btn_1"]);
    await renderPage(UIBuilderView);
    call(() => fireEvent.click(screen.getByTitle("Panel Settings")));
    for (const [key, mods] of [["Delete", {}], ["ArrowRight", {}], ["p", { ctrl: true }], ["e", { ctrl: true }], ["z", { ctrl: true }], ["Escape", {}]] as const) {
      press(key, mods);
    }
    // Escape went to the dialog, which closed.
    expect(screen.queryByText("Panel Settings", { selector: "h3" })).toBeNull();
    expect(builder().selectedElementIds).toEqual(["btn_1"]);
    expect(builder().previewMode).toBe(false);
    expect(screen.getByTestId("stub-ElementPalette")).toBeInTheDocument();
    expect(box("main", "btn_1").x).toBe(10);
    expect(ids("main")).toContain("btn_1");
  });
});

describe("the page's own listeners", () => {
  it("follows the server's page change in preview only, and stops listening after", async () => {
    await renderPage(UIBuilderView);
    expect(ws.listeners).toHaveLength(0);
    press("p", { ctrl: true });
    expect(ws.listeners).toHaveLength(1);
    call(() => ws.listeners[0]({ type: "ui.navigate", page_id: "other" }));
    expect(builder().selectedPageId).toBe("other");
    call(() => ws.listeners[0]({ type: "ui.navigate" }));
    call(() => ws.listeners[0]({ type: "state.update", page_id: "main" }));
    expect(builder().selectedPageId).toBe("other");
    press("p", { ctrl: true });
    expect(ws.listeners).toHaveLength(0);
    expect(ws.unsubscribed).toBe(1);
  });

  it("selects the first page when none is", async () => {
    await renderPage(UIBuilderView);
    expect(builder().selectedPageId).toBe("main");
    expect(seen.Canvas.page.id).toBe("main");
  });

  it("opens on a control another screen pointed at, on its page", async () => {
    reseed(makeProject(), { selectedPageId: "main" });
    useNavigationStore.setState({ pendingFocus: { type: "element", id: "btn_9", detail: "page:other" } } as never);
    const frames: FrameRequestCallback[] = [];
    const raf = vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => { frames.push(cb); return 0; });
    await renderPage(UIBuilderView);
    call(() => frames.splice(0).forEach((f) => f(0)));
    expect(builder().selectedPageId).toBe("other");
    expect(builder().selectedElementId).toBe("btn_9");
    expect(useNavigationStore.getState().pendingFocus).toBeNull();
    raf.mockRestore();
  });

  it("lands on the first page instead when no page was selected yet (as it does today)", async () => {
    useNavigationStore.setState({ pendingFocus: { type: "element", id: "btn_9", detail: "page:other" } } as never);
    const frames: FrameRequestCallback[] = [];
    const raf = vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => { frames.push(cb); return 0; });
    await renderPage(UIBuilderView);
    call(() => frames.splice(0).forEach((f) => f(0)));
    // The first-page rule runs after the focus with the page it saw at mount.
    expect(builder().selectedPageId).toBe("main");
    expect(builder().selectedElementId).toBe("btn_9");
    raf.mockRestore();
  });

  it("selects a control pointed at with no page named, on the page already open", async () => {
    reseed(makeProject(), { selectedPageId: "main" });
    useNavigationStore.setState({ pendingFocus: { type: "element", id: "lbl_1" } } as never);
    const frames: FrameRequestCallback[] = [];
    const raf = vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => { frames.push(cb); return 0; });
    await renderPage(UIBuilderView);
    expect(builder().selectedElementId).toBeNull();
    call(() => frames.splice(0).forEach((f) => f(0)));
    expect(builder().selectedPageId).toBe("main");
    expect(builder().selectedElementId).toBe("lbl_1");
    raf.mockRestore();
  });

  it("ignores a pending focus that is not a control, and one with no page", async () => {
    reseed(makeProject(), { selectedPageId: "other" });
    useNavigationStore.setState({ pendingFocus: { type: "device", id: "proj" } } as never);
    await renderPage(UIBuilderView);
    expect(builder().selectedPageId).toBe("other");
    expect(builder().selectedElementId).toBeNull();
  });

  it("ignores a pending focus that is not a control even when it names a page, once its frame has run", async () => {
    reseed(makeProject(), { selectedPageId: "other" });
    useNavigationStore.setState({ pendingFocus: { type: "device", id: "btn_1", detail: "page:main" } } as never);
    const frames: FrameRequestCallback[] = [];
    const raf = vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => { frames.push(cb); return 0; });
    await renderPage(UIBuilderView);
    call(() => frames.splice(0).forEach((f) => f(0)));
    expect(builder().selectedPageId).toBe("other");
    expect(builder().selectedElementId).toBeNull();
    expect(useNavigationStore.getState().pendingFocus).toBeNull();
    raf.mockRestore();
  });

  it("flushes the pending save before the tab unloads, until it is gone", async () => {
    const r = await renderPage(UIBuilderView);
    window.dispatchEvent(new Event("beforeunload"));
    expect(saves.flushSave).toHaveBeenCalledTimes(1);
    r.unmount();
    window.dispatchEvent(new Event("beforeunload"));
    expect(saves.flushSave).toHaveBeenCalledTimes(1);
  });

  it("stops hearing keys once it is gone", async () => {
    selected(["btn_1"]);
    const r = await renderPage(UIBuilderView);
    r.unmount();
    press("Delete");
    expect(ids("main")).toContain("btn_1");
  });
});
