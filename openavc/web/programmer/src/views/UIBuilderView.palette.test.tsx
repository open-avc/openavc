import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, screen, fireEvent } from "@testing-library/react";

// Adding a control from the palette, by click and by drag: where it lands
// (the live preview's box, else the same rule worked out at the drop), which
// container adopts it, its id, the undo entry and the selection; the ghost
// drawn off the canvas; and the drag's listeners coming and going.

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
import { usePluginStore } from "../store/pluginStore";
import {
  builder,
  el,
  installFetch,
  makeProject,
  page,
  pageOf,
  press,
  renderPage,
  resetMocks,
  seed,
  seen,
  undoLog,
} from "../test/uiBuilderHarness";

const call = (fn: () => unknown) => act(() => { fn(); });
const ids = (pageId: string) => pageOf(pageId).elements.map((e) => e.id);
const added = (pageId = "main") => pageOf(pageId).elements.at(-1)!;
const boxOf = (pageId: string, id: string, layout = 0) => pageOf(pageId).layouts[layout].placements[id];

/** The canvas the drop measures against: 1000 x 500 px from (100, 50). */
function canvas() {
  const grid = document.createElement("div");
  grid.setAttribute("data-canvas-grid", "");
  grid.getBoundingClientRect = () => ({ left: 100, top: 50, width: 1000, height: 500, right: 1100, bottom: 550, x: 100, y: 50, toJSON: () => ({}) }) as DOMRect;
  document.body.appendChild(grid);
  return grid;
}

/** A palette button the drag starts from, at (10, 20). */
function paletteButton() {
  const item = document.createElement("div");
  item.setAttribute("aria-roledescription", "draggable");
  item.getBoundingClientRect = () => ({ left: 10, top: 20, width: 80, height: 30, right: 90, bottom: 50, x: 10, y: 20, toJSON: () => ({}) }) as DOMRect;
  const label = document.createElement("span");
  item.appendChild(label);
  document.body.appendChild(item);
  return label;
}

const dnd = () => seen.DndContext;
function start(type: string | undefined, at = { x: 30, y: 35 }, opts: { source?: string; altKey?: boolean } = {}) {
  const target = paletteButton();
  call(() => dnd().onDragStart({
    active: { data: { current: { source: opts.source ?? "palette", elementType: type } } },
    activatorEvent: { clientX: at.x, clientY: at.y, target, altKey: !!opts.altKey },
  }));
}
function move(x: number, y: number) {
  call(() => window.dispatchEvent(new MouseEvent("pointermove", { clientX: x, clientY: y })));
}
function end(overCanvas = true, opts: { altKey?: boolean; source?: string; type?: string } = {}) {
  call(() => dnd().onDragEnd({
    active: { data: { current: { source: opts.source ?? "palette", elementType: opts.type ?? "button" } } },
    over: overCanvas ? { id: "canvas-drop" } : null,
    activatorEvent: { altKey: !!opts.altKey },
  }));
}

beforeEach(() => {
  resetMocks();
  installFetch();
  document.body.innerHTML = "";
  seed(makeProject(), { selectedPageId: "main" });
});

describe("click to add", () => {
  it("adds the control at the first free spot, named past every page's and master's ids, and selects it", async () => {
    seed(makeProject({ pages: [page("main", [el("button_1", "button")], { button_1: { x: 0, y: 0, w: 15, h: 12 } }), page("other", [el("button_2", "button")], {})] }));
    await renderPage(UIBuilderView);
    call(() => seen.ElementPalette.onAdd("button"));
    expect(ids("main")).toEqual(["button_1", "button_3"]);
    expect(undoLog()).toEqual([["Add button", "pages"]]);
    expect(builder().selectedElementId).toBe("button_3");
    expect(boxOf("main", "button_3")).toEqual({ x: 15, y: 0, w: 25, h: 25 });
  });

  it("finds the free spot among the arrangement on screen's top-level boxes", async () => {
    seed(makeProject({ pages: [page("main", [el("button_1", "button"), el("in", "button", { parent: "button_1" } as never)], { button_1: { x: 0, y: 0, w: 15, h: 12 }, in: { x: 0, y: 0, w: 100, h: 100 } })] }), { activeLayoutId: "main_port" });
    await renderPage(UIBuilderView);
    call(() => seen.ElementPalette.onAdd("label"));
    expect(added().id).toBe("label_1");
    // The child's box is in its container's terms and is not counted; the
    // box goes in the page's primary arrangement.
    expect({ primary: boxOf("main", "label_1", 0), portrait: boxOf("main", "label_1", 1) }).toEqual({ primary: { x: 15, y: 0, w: 25, h: 12.5 }, portrait: undefined });
  });

  it("cascades from the page centre with snap off, one step further for each control on the page", async () => {
    seed(makeProject({ pages: [page("main", [el("a", "label"), el("b", "label")], { a: { x: 0, y: 0, w: 5, h: 5 }, b: { x: 90, y: 90, w: 5, h: 5 } }, { snap: { enabled: false, x: 5, y: 5 } })] }));
    await renderPage(UIBuilderView);
    call(() => seen.ElementPalette.onAdd("label"));
    const first = boxOf("main", "label_1");
    call(() => seen.ElementPalette.onAdd("label"));
    const second = boxOf("main", "label_2");
    expect(second.x - first.x).toBeCloseTo(2.5, 5);
    expect(second.y - first.y).toBeCloseTo(2.5, 5);
    expect(first.x + first.w / 2).toBeCloseTo(55, 5);
  });

  it("names a master's id as taken", async () => {
    seed(makeProject({ masters: [{ ...makeProject().ui.master_elements![0], id: "label_1" }] }));
    await renderPage(UIBuilderView);
    call(() => seen.ElementPalette.onAdd("label"));
    expect(added().id).toBe("label_2");
  });

  it("is off in a page drawn from the author's own file, with the reason", async () => {
    seed(makeProject({ pages: [page("main", [], {}, { render_mode: "custom", custom_file: "room.html" })] }));
    await renderPage(UIBuilderView);
    expect(seen.ElementPalette.disabled).toBe(true);
    expect(seen.ElementPalette.disabledNote).toBe("This page shows a page you wrote, so controls placed here are not drawn.");
  });

  it("stays on for a page that says custom but names no file", async () => {
    seed(makeProject({ pages: [page("main", [], {}, { render_mode: "custom", custom_file: "" })] }));
    await renderPage(UIBuilderView);
    expect(seen.ElementPalette.disabled).toBe(false);
    expect(seen.ElementPalette.disabledNote).toBeUndefined();
  });

  it("adds nothing when the project has no pages, and says how to make one", async () => {
    seed(makeProject({ pages: [] }));
    await renderPage(UIBuilderView);
    call(() => seen.ElementPalette.onAdd("button"));
    expect(undoLog()).toEqual([]);
    expect(screen.getByText("No pages yet. Click + to add a page.")).toBeInTheDocument();
  });
});

describe("dragging a control in from the palette", () => {
  it("shows the snapped box under the pointer and drops it exactly there", async () => {
    canvas();
    await renderPage(UIBuilderView);
    start("button");
    expect(builder().activeDragSource).toBe("palette");
    move(600, 300);
    const preview = builder().paletteDragPreview!;
    expect(preview.label).toBe("button");
    expect(preview.adoptInto).toBeNull();
    expect(preview.placement.x + preview.placement.w / 2).toBeCloseTo(50, 0);
    end();
    expect(undoLog()).toEqual([["Add button", "pages"]]);
    expect(added().id).toBe("button_1");
    expect(boxOf("main", "button_1")).toEqual(preview.placement);
    expect(builder().selectedElementId).toBe("button_1");
    expect(builder().paletteDragPreview).toBeNull();
    expect(builder().activeDragSource).toBeNull();
  });

  it("drops a control inside a container into it, in the container's terms", async () => {
    canvas();
    await renderPage(UIBuilderView);
    start("button");
    // The group is at 50..90 x 50..90 percent: 600..1000 px across, 300..500 down.
    move(800, 400);
    expect(builder().paletteDragPreview!.adoptInto).toBe("grp_1");
    end();
    const dropped = added();
    expect((dropped as unknown as { parent: string }).parent).toBe("grp_1");
    const rel = boxOf("main", dropped.id);
    expect(rel.x).toBeGreaterThan(0);
    expect(rel.x + rel.w).toBeLessThanOrEqual(100);
  });

  it("clears the preview when the pointer leaves the canvas, and adds nothing dropped off it", async () => {
    canvas();
    await renderPage(UIBuilderView);
    start("button");
    move(600, 300);
    expect(builder().paletteDragPreview).not.toBeNull();
    move(50, 300);
    expect(builder().paletteDragPreview).toBeNull();
    move(600, 300);
    move(600, 551);
    expect(builder().paletteDragPreview).toBeNull();
    move(600, 49);
    expect(builder().paletteDragPreview).toBeNull();
    end(false);
    expect(undoLog()).toEqual([]);
    expect(builder().activeDragSource).toBeNull();
  });

  it("works the box out at the drop when the pointer never moved, from where the drag started", async () => {
    canvas();
    await renderPage(UIBuilderView);
    start("button", { x: 600, y: 300 });
    end();
    const placed = boxOf("main", added().id);
    expect(placed.x + placed.w / 2).toBeCloseTo(50, 0);
    expect(placed.y + placed.h / 2).toBeCloseTo(50, 0);
  });

  it("works it out from the last pointer when the preview was cleared, honouring Alt on the start", async () => {
    canvas();
    await renderPage(UIBuilderView);
    start("button", { x: 600, y: 300 });
    move(613, 307);
    builder().setPaletteDragPreview(null);
    end(true, { altKey: true });
    const placed = boxOf("main", added().id);
    // Alt: exactly under the pointer, no snap.
    expect(placed.x + placed.w / 2).toBeCloseTo(51.3, 5);
    expect(placed.y + placed.h / 2).toBeCloseTo(51.4, 5);
  });

  it("follows Alt pressed and released mid-drag without the pointer moving", async () => {
    canvas();
    await renderPage(UIBuilderView);
    start("button");
    move(613, 307);
    const snapped = builder().paletteDragPreview!.placement;
    press("Alt", { alt: true });
    const free = builder().paletteDragPreview!.placement;
    expect(free.x + free.w / 2).toBeCloseTo(51.3, 5);
    expect(free).not.toEqual(snapped);
    const up = new KeyboardEvent("keyup", { key: "Alt", altKey: false, bubbles: true });
    call(() => window.dispatchEvent(up));
    expect(builder().paletteDragPreview!.placement).toEqual(snapped);
    end();
    expect(boxOf("main", added().id)).toEqual(snapped);
  });

  it("drops where the preview showed even when Alt, held at the start, was let go", async () => {
    canvas();
    await renderPage(UIBuilderView);
    start("button", { x: 30, y: 35 }, { altKey: true });
    move(613, 307);
    const shown = builder().paletteDragPreview!.placement;
    end(true, { altKey: true });
    expect(boxOf("main", added().id)).toEqual(shown);
    // Snapped, not free: the start's Alt is not what the preview used.
    expect(shown.x + shown.w / 2).not.toBeCloseTo(51.3, 5);
  });

  it("snaps to the page's own controls, never to a control's box inside its container", async () => {
    canvas();
    seed(
      makeProject({
        pages: [page(
          "main",
          [el("card", "group"), el("inner", "button", { parent: "card" } as never)],
          { card: { x: 0, y: 0, w: 20, h: 20 }, inner: { x: 43, y: 43, w: 10, h: 10 } },
          { snap: { enabled: false, x: 5, y: 5 } },
        )],
      }),
      { selectedPageId: "main" },
    );
    const helpers = await import("../components/ui-builder/uiBuilderHelpers");
    await renderPage(UIBuilderView);
    start("label");
    move(100 + 10 * 46.5, 50 + 5 * 46.5);
    const shown = builder().paletteDragPreview!.placement;
    const size = { w: shown.w, h: shown.h };
    const ctx = (others: Array<{ x: number; y: number; w: number; h: number }>) => ({ snap: { enabled: false, x: 5, y: 5 }, bypass: false, others });
    const topLevel = helpers.paletteDragPlacement({ x: 46.5, y: 46.5 }, size, ctx([{ x: 0, y: 0, w: 20, h: 20 }])).placement;
    const everything = helpers.paletteDragPlacement({ x: 46.5, y: 46.5 }, size, ctx([{ x: 0, y: 0, w: 20, h: 20 }, { x: 43, y: 43, w: 10, h: 10 }])).placement;
    expect(everything).not.toEqual(topLevel);
    expect(shown).toEqual(topLevel);
    builder().setPaletteDragPreview(null);
    end(true, { type: "label" });
    expect(boxOf("main", added().id)).toEqual(topLevel);
  });

  it("names a dropped control past the masters' ids too", async () => {
    canvas();
    seed(makeProject({ masters: [{ ...makeProject().ui.master_elements![0], id: "button_1" }] }), { selectedPageId: "main" });
    await renderPage(UIBuilderView);
    start("button");
    move(600, 300);
    end();
    expect(added().id).toBe("button_2");
  });

  it("lets go of the pointer when the drag is cancelled or ends", async () => {
    canvas();
    await renderPage(UIBuilderView);
    const listening = () => {
      const adds = addSpy.mock.calls.filter((c) => c[0] === "pointermove").length;
      const removes = removeSpy.mock.calls.filter((c) => c[0] === "pointermove").length;
      return adds - removes;
    };
    const addSpy = vi.spyOn(window, "addEventListener");
    const removeSpy = vi.spyOn(window, "removeEventListener");
    start("button");
    expect(listening()).toBe(1);
    call(() => dnd().onDragCancel());
    expect(listening()).toBe(0);
    start("button");
    start("button");
    expect(listening()).toBe(1);
    end();
    expect(listening()).toBe(0);
    addSpy.mockRestore();
    removeSpy.mockRestore();
  });

  it("sizes the ghost to the landing box and centres it under the pointer", async () => {
    canvas();
    await renderPage(UIBuilderView);
    start("level_meter", { x: 30, y: 35 });
    const ghost = screen.getByText("level meter");
    const style = ghost.style;
    const w = parseFloat(style.width);
    const h = parseFloat(style.height);
    expect(w).toBeGreaterThan(0);
    // Pointer at (20, 15) inside the button: the ghost's centre goes there.
    expect(style.transform).toBe(`translate(${20 - w / 2}px, ${15 - h / 2}px)`);
    move(600, 300);
    expect(screen.queryByText("level meter")).toBeNull();
    move(50, 300);
    expect(screen.getByText("level meter")).toBeInTheDocument();
  });

  it("centres the ghost on its corner when the drag did not start on a palette button", async () => {
    canvas();
    await renderPage(UIBuilderView);
    call(() => dnd().onDragStart({
      active: { data: { current: { source: "palette", elementType: "label" } } },
      activatorEvent: { clientX: 30, clientY: 35, target: document.body },
    }));
    const ghost = screen.getByText("label");
    const w = parseFloat(ghost.style.width);
    const h = parseFloat(ghost.style.height);
    expect(ghost.style.transform).toBe(`translate(${-w / 2}px, ${-h / 2}px)`);
  });

  it("draws the ghost at its default size before there is a canvas to size it", async () => {
    await renderPage(UIBuilderView);
    start("label");
    const ghost = screen.getByText("label");
    expect([ghost.style.width, ghost.style.height]).toEqual(["120px", "60px"]);
  });

  it("keeps the last drag's ghost size and grab for a drag the canvas cannot measure", async () => {
    const grid = canvas();
    await renderPage(UIBuilderView);
    start("level_meter", { x: 30, y: 35 });
    const first = screen.getByText("level meter").style;
    const [w, h, transform] = [first.width, first.height, first.transform];
    expect(w).not.toBe("120px");
    call(() => dnd().onDragCancel());
    grid.remove();
    start("label", { x: 70, y: 70 });
    const second = screen.getByText("label").style;
    expect([second.width, second.height, second.transform]).toEqual([w, h, transform]);
  });

  it("sizes a plugin control from what its plugin declares", async () => {
    canvas();
    usePluginStore.setState({ extensions: { ...usePluginStore.getState().extensions, panel_elements: [{ plugin_id: "acme", type: "dial", default_size: { w: 2, h: 2 } }] } } as never);
    await renderPage(UIBuilderView);
    start("plugin:acme:dial");
    // The ghost names what the control will be.
    expect(screen.getByTestId("drag-overlay")).toHaveTextContent(/^plugin$/);
    move(600, 300);
    const box = builder().paletteDragPreview!.placement;
    end(true, { type: "plugin:acme:dial" });
    expect(boxOf("main", added().id)).toEqual(box);
    expect(added().type).toBe("plugin");
  });

  it("draws no ghost and adds nothing for a drag that did not come from the palette", async () => {
    canvas();
    await renderPage(UIBuilderView);
    start(undefined, { x: 600, y: 300 }, { source: "canvas" });
    expect(builder().activeDragSource).toBe("canvas");
    expect(screen.getByTestId("drag-overlay")).toBeEmptyDOMElement();
    move(600, 300);
    expect(builder().paletteDragPreview).toBeNull();
    end(true, { source: "canvas" });
    expect(undoLog()).toEqual([]);
  });

  it("adds nothing without a canvas to measure", async () => {
    await renderPage(UIBuilderView);
    start("button", { x: 600, y: 300 });
    end();
    expect(undoLog()).toEqual([]);
  });

  it("stops following the pointer once the drag is cancelled", async () => {
    canvas();
    await renderPage(UIBuilderView);
    start("button");
    move(600, 300);
    call(() => dnd().onDragCancel());
    expect(builder().paletteDragPreview).toBeNull();
    expect(builder().activeDragSource).toBeNull();
    move(610, 300);
    expect(builder().paletteDragPreview).toBeNull();
    end();
    expect(undoLog()).toEqual([]);
  });

  it("stops following the pointer when the page goes away mid-drag", async () => {
    canvas();
    const r = await renderPage(UIBuilderView);
    start("button");
    r.unmount();
    move(600, 300);
    expect(builder().paletteDragPreview).toBeNull();
  });

  it("tells the drag library to drop on the pointer and not to scroll", async () => {
    await renderPage(UIBuilderView);
    expect(dnd().autoScroll).toBe(false);
    expect(dnd().collisionDetection.name).toBe("pointerWithin");
  });
});

describe("the left panel", () => {
  it("switches between the palette and the outline", async () => {
    await renderPage(UIBuilderView);
    expect(screen.getByTestId("stub-ElementPalette")).toBeInTheDocument();
    call(() => fireEvent.click(screen.getByRole("button", { name: "Outline" })));
    expect(screen.queryByTestId("stub-ElementPalette")).toBeNull();
    expect(screen.getByTestId("stub-OutlinePanel")).toBeInTheDocument();
    call(() => fireEvent.click(screen.getByRole("button", { name: "Elements" })));
    expect(screen.getByTestId("stub-ElementPalette")).toBeInTheDocument();
  });
});
