/**
 * The canvas's two gestures, driven through the canvas itself: a page control
 * and a master element, each moved and resized with the pointer, with Alt,
 * Shift and Escape, and what reaches the project when the pointer lets go.
 *
 * jsdom lays nothing out, so every box is given a size here: the canvas is
 * 1000 x 500 px, and a container named in `FRAMES` is the size given there,
 * which is what a control inside it converts its travel against.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, render } from "@testing-library/react";
import type { MasterElement, Placement, ProjectConfig, UIElement, UIPage } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import { Canvas } from "./Canvas";
import { HANDLE_POSITIONS } from "./CanvasElement";

const W = 1000;
const H = 500;
let FRAMES: Record<string, { width: number; height: number }> = {};

function domRect(width: number, height: number): DOMRect {
  return {
    x: 0, y: 0, left: 0, top: 0, right: width, bottom: height, width, height,
    toJSON: () => ({}),
  } as DOMRect;
}

const SNAP_ON = { enabled: true, x: 100 / 12, y: 100 / 8 };
const SNAP_OFF = { enabled: false, x: 100 / 12, y: 100 / 8 };

type Box = Placement;

function el(id: string, extra: Partial<UIElement> = {}): UIElement {
  return { id, type: "button", ...extra } as UIElement;
}

function makePage(
  elements: UIElement[],
  placements: Record<string, Box>,
  opts: { snap?: typeof SNAP_ON; portrait?: Record<string, Box> } = {},
): UIPage {
  const layouts: unknown[] = [
    { id: "landscape", orientation: "landscape", primary: true, placements, hidden: [] },
  ];
  if (opts.portrait) {
    layouts.push({ id: "portrait", orientation: "portrait", primary: false, placements: opts.portrait, hidden: [] });
  }
  return { id: "p1", name: "P1", elements, snap: opts.snap ?? SNAP_OFF, layouts } as unknown as UIPage;
}

function master(id: string, placements: Record<string, Box>, extra: Partial<MasterElement> = {}): MasterElement {
  return { id, type: "button", pages: "*", placements, hidden: false, ...extra } as MasterElement;
}

let view: ReturnType<typeof render>;
let posted: unknown[] = [];

function load(
  page: UIPage,
  opts: { masters?: MasterElement[]; selected?: string[]; selectedMaster?: string; layout?: string } = {},
) {
  const masters = opts.masters ?? [];
  const project = { ui: { pages: [page], master_elements: masters, settings: {} } } as unknown as ProjectConfig;
  useProjectStore.setState({ project });
  useUIBuilderStore.setState({
    selectedElementIds: opts.selected ?? [],
    selectedMasterElementId: opts.selectedMaster ?? null,
    activeLayoutId: opts.layout ?? null,
    undoStack: [],
    redoStack: [],
  });
  view = render(
    <Canvas page={page} previewMode={false} showGrid={false} zoom={1} screenWidth={W} screenHeight={H} masterElements={masters} />,
  );
  const frame = view.container.querySelector("iframe")!;
  vi.spyOn(frame.contentWindow!, "postMessage").mockImplementation((msg: unknown) => {
    posted.push(msg);
  });
}

function node(id: string): Element {
  return view.container.querySelector(`[data-canvas-element="${id}"]`)!;
}

function masterNode(id: string): Element {
  return view.container.querySelector(`[data-canvas-master="${id}"]`)!;
}

/** A resize grip of a selected box, by the direction it drags. */
function handle(box: Element, direction: string): Element {
  const grips = [...box.children].filter((c) => (c as HTMLElement).style.cursor.endsWith("resize"));
  return grips[Object.keys(HANDLE_POSITIONS).indexOf(direction)];
}

type Mods = { altKey?: boolean; shiftKey?: boolean };

function press(target: Element, mods: Mods = {}) {
  act(() => {
    target.dispatchEvent(new MouseEvent("pointerdown", { clientX: 500, clientY: 250, bubbles: true, button: 0, ...mods }));
  });
}

function drag(dx: number, dy: number, mods: Mods = {}) {
  act(() => {
    document.dispatchEvent(new MouseEvent("pointermove", { clientX: 500 + dx, clientY: 250 + dy, bubbles: true, ...mods }));
  });
}

function key(type: "keydown" | "keyup", k: string, mods: Mods = {}) {
  act(() => {
    document.dispatchEvent(new KeyboardEvent(type, { key: k, bubbles: true, ...mods }));
  });
}

function release() {
  act(() => {
    document.dispatchEvent(new MouseEvent("pointerup", { bubbles: true }));
  });
}

function stored(id: string, layout = "landscape"): Box | undefined {
  const p = useProjectStore.getState().project!.ui.pages[0];
  return p.layouts!.find((l) => l.id === layout)!.placements[id];
}

function storedEl(id: string): UIElement | undefined {
  return useProjectStore.getState().project!.ui.pages[0].elements.find((e) => e.id === id);
}

function storedMaster(id: string, orientation = "landscape"): Box | undefined {
  return useProjectStore.getState().project!.ui.master_elements!.find((m) => m.id === id)!.placements[orientation];
}

function undoLabels(): string[] {
  return useUIBuilderStore.getState().undoStack.map((u) => u.description);
}

/** The guide lines drawn right now: vertical ones by left %, horizontal by top %. */
function guides(): { x: string[]; y: string[] } {
  const lines = [...view.container.querySelectorAll("div")].filter(
    (d) => d.style.background === "rgba(33, 150, 243, 0.9)",
  );
  return {
    x: lines.filter((d) => d.style.width === "1px").map((d) => d.style.left),
    y: lines.filter((d) => d.style.height === "1px").map((d) => d.style.top),
  };
}

beforeEach(() => {
  FRAMES = {};
  posted = [];
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    const id = this.getAttribute?.("data-canvas-element");
    const f = id ? FRAMES[id] : undefined;
    return f ? domRect(f.width, f.height) : domRect(W, H);
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

const A = { x: 10, y: 10, w: 20, h: 20 };

describe("moving a page control", () => {
  it("travels by the pointer's distance as a share of the canvas, with snap off", () => {
    load(makePage([el("a")], { a: A }));
    press(node("a"));
    drag(153, 0);
    release();
    expect(stored("a")).toEqual({ x: 25.3, y: 10, w: 20, h: 20 });
    expect(undoLabels()).toEqual(["Move element"]);
  });

  it("lands on the snap increment nearest any of its edges or its centre", () => {
    load(makePage([el("a")], { a: A }, { snap: SNAP_ON }));
    press(node("a"));
    drag(153, 0);
    release();
    expect(stored("a")).toEqual({ x: 25, y: 12.5, w: 20, h: 20 });
  });

  it("lands on the exact pointer with Alt held", () => {
    load(makePage([el("a")], { a: A }, { snap: SNAP_ON }));
    press(node("a"));
    drag(153, 0, { altKey: true });
    release();
    expect(stored("a")).toEqual({ x: 25.3, y: 10, w: 20, h: 20 });
  });

  it("keeps to the axis that has travelled further with Shift held, x on a tie", () => {
    load(makePage([el("a"), el("b"), el("c")], { a: A, b: { x: 10, y: 60, w: 10, h: 10 }, c: { x: 70, y: 60, w: 10, h: 10 } }));
    press(node("a"));
    drag(153, 40, { shiftKey: true });
    release();
    expect(stored("a")).toEqual({ x: 25.3, y: 10, w: 20, h: 20 });

    press(node("b"));
    drag(40, 60, { shiftKey: true });
    release();
    expect(stored("b")).toEqual({ x: 10, y: 72, w: 10, h: 10 });

    press(node("c"));
    drag(55, 55, { shiftKey: true });
    release();
    expect(stored("c")).toEqual({ x: 75.5, y: 60, w: 10, h: 10 });
  });

  it("sticks to a sibling's edge when that is nearer than 1.5%, and draws the guide while dragging", () => {
    load(makePage([el("a"), el("b")], { a: A, b: { x: 62, y: 60, w: 20, h: 20 } }));
    press(node("a"));
    drag(314, 0);
    expect(guides()).toEqual({ x: ["62%"], y: [] });
    release();
    expect(stored("a")).toEqual({ x: 42, y: 10, w: 20, h: 20 });
    expect(guides()).toEqual({ x: [], y: [] });
  });

  it("is not pulled toward a control inside a container, whose box is of the container", () => {
    load(
      makePage([el("a"), el("g", { type: "group" }), el("k", { parent: "g" })], {
        a: A,
        g: { x: 0, y: 70, w: 100, h: 30 },
        k: { x: 66, y: 0, w: 20, h: 20 },
      }),
    );
    press(node("a"));
    drag(354, 0);
    release();
    expect(stored("a")).toEqual({ x: 45.4, y: 10, w: 20, h: 20 });
  });

  it("carries the rest of the selection by the same distance in pixels, each in its own container's percentages", () => {
    FRAMES = { g: { width: 500, height: 250 } };
    load(
      makePage([el("a"), el("g", { type: "group" }), el("k", { parent: "g" })], {
        a: A,
        g: { x: 50, y: 50, w: 50, h: 50 },
        k: { x: 10, y: 10, w: 20, h: 20 },
      }),
      { selected: ["a", "k"] },
    );
    press(node("a"));
    drag(103, 26);
    release();
    expect(stored("a")).toEqual({ x: 20.3, y: 15.2, w: 20, h: 20 });
    expect(stored("k")).toEqual({ x: 30.6, y: 20.4, w: 20, h: 20 });
    expect(stored("g")).toEqual({ x: 50, y: 50, w: 50, h: 50 });
  });

  it("moves the container when a control inside a selected container is grabbed", () => {
    load(
      makePage([el("g", { type: "group" }), el("h", { type: "group", parent: "g" }), el("k", { parent: "h" })], {
        g: { x: 10, y: 10, w: 50, h: 50 },
        h: { x: 0, y: 0, w: 50, h: 50 },
        k: { x: 10, y: 10, w: 20, h: 20 },
      }),
      { selected: ["g", "k"] },
    );
    press(node("k"));
    drag(103, 0);
    release();
    expect(stored("g")).toEqual({ x: 20.3, y: 10, w: 50, h: 50 });
    expect(stored("h")).toEqual({ x: 0, y: 0, w: 50, h: 50 });
    expect(stored("k")).toEqual({ x: 10, y: 10, w: 20, h: 20 });
  });

  it("moves a control inside a container in the container's own percentages, and it stays there", () => {
    FRAMES = { g: { width: 500, height: 250 } };
    load(
      makePage([el("g", { type: "group" }), el("k", { parent: "g" })], {
        g: { x: 50, y: 50, w: 50, h: 50 },
        k: { x: 10, y: 10, w: 20, h: 20 },
      }),
    );
    press(node("k"));
    drag(53, 13);
    release();
    expect(storedEl("k")!.parent).toBe("g");
    expect(stored("k")).toEqual({ x: 20.6, y: 15.2, w: 20, h: 20 });
  });

  it("refuses to move a control whose container has no area to measure against", () => {
    FRAMES = { g: { width: 0, height: 250 } };
    load(
      makePage([el("g", { type: "group" }), el("k", { parent: "g" })], {
        g: { x: 50, y: 50, w: 50, h: 50 },
        k: { x: 10, y: 10, w: 20, h: 20 },
      }),
    );
    press(node("k"));
    drag(53, 13);
    release();
    expect(stored("k")).toEqual({ x: 10, y: 10, w: 20, h: 20 });
    expect(undoLabels()).toEqual([]);
  });

  it("leaves a locked control in a selection where it is", () => {
    load(makePage([el("a", { locked: true }), el("b")], { a: A, b: { x: 60, y: 60, w: 10, h: 10 } }), {
      selected: ["a", "b"],
    });
    press(node("b"));
    drag(-123, 0);
    release();
    expect(stored("a")).toEqual(A);
    expect(stored("b")).toEqual({ x: 47.7, y: 60, w: 10, h: 10 });
  });

  it("lights up the container it would land in, then lands in it", () => {
    load(
      makePage([el("a"), el("g", { type: "group" })], { a: A, g: { x: 40, y: 40, w: 50, h: 50 } }),
    );
    press(node("a"));
    drag(400, 200);
    expect((node("g") as HTMLElement).style.outline).toBe("2px dashed var(--accent)");
    release();
    expect(storedEl("a")!.parent).toBe("g");
    expect(stored("a")).toEqual({ x: 20, y: 20, w: 40, h: 40 });
  });

  it("lights up no container while a control moves within its own, and measures where it lands in page space", () => {
    FRAMES = { g: { width: 500, height: 250 } };
    load(
      makePage([el("h", { type: "group" }), el("g", { type: "group" }), el("k", { parent: "g" })], {
        h: { x: 0, y: 0, w: 50, h: 50 },
        g: { x: 50, y: 50, w: 50, h: 50 },
        k: { x: 10, y: 10, w: 20, h: 20 },
      }),
    );
    press(node("k"));
    drag(53, 13);
    expect((node("g") as HTMLElement).style.outline).not.toBe("2px dashed var(--accent)");
    expect((node("h") as HTMLElement).style.outline).not.toBe("2px dashed var(--accent)");
    release();
    expect(storedEl("k")!.parent).toBe("g");
  });

  it("shows a live box while dragging and says where it landed to the panel", () => {
    load(makePage([el("a")], { a: A }));
    press(node("a"));
    drag(153, 0);
    expect((node("a") as HTMLElement).style.left).toBe("25.3%");
    release();
    expect(posted.at(-1)).toEqual({ type: "openavc:editor-placements", placements: { a: { x: 25.3, y: 10, w: 20, h: 20 } } });
  });
});

describe("a page control in an arrangement other than the primary", () => {
  const PORT_A = { x: 50, y: 50, w: 20, h: 20 };

  it("moves from its box in the arrangement on screen, writes that one and leaves the primary", () => {
    load(makePage([el("a")], { a: A }, { portrait: { a: PORT_A } }), { layout: "portrait" });
    press(node("a"));
    drag(103, 0);
    release();
    expect(stored("a", "portrait")).toEqual({ x: 60.3, y: 50, w: 20, h: 20 });
    expect(stored("a")).toEqual(A);
    expect(undoLabels()).toEqual(["Move element"]);
  });

  it("lands in a container where the arrangement on screen draws it, not where the primary does", () => {
    // In portrait the group is at the top left, where the control is dragged
    // to; in the primary it is at the bottom right.
    load(
      makePage([el("a"), el("g", { type: "group" })], { a: { x: 60, y: 60, w: 10, h: 10 }, g: { x: 50, y: 50, w: 50, h: 50 } }, {
        portrait: { a: { x: 60, y: 60, w: 10, h: 10 }, g: { x: 0, y: 0, w: 50, h: 50 } },
      }),
      { layout: "portrait" },
    );
    press(node("a"));
    drag(-500, -250);
    release();
    expect(storedEl("a")!.parent).toBe("g");
    expect(stored("a", "portrait")).toEqual({ x: 20, y: 20, w: 20, h: 20 });
  });

  it("resizes its box in the arrangement on screen and leaves the primary", () => {
    load(makePage([el("a")], { a: A }, { portrait: { a: PORT_A } }), { layout: "portrait", selected: ["a"] });
    press(handle(node("a"), "e"));
    drag(100, 0);
    release();
    expect(stored("a", "portrait")).toEqual({ x: 50, y: 50, w: 30, h: 20 });
    expect(stored("a")).toEqual(A);
    expect(undoLabels()).toEqual(["Resize element"]);
  });
});

describe("the marquee", () => {
  /** A band from the canvas's centre, `dx` and `dy` pixels across. */
  function sweep(dx: number, dy: number) {
    const overlay = view.container.querySelector("[data-canvas-grid]")!;
    press(overlay);
    drag(dx, dy);
    release();
  }

  // a sits top left in the primary and at the centre in portrait; the band
  // runs from the centre, 50%, to 65% across and down.
  const twoArrangements = () =>
    makePage([el("a"), el("b")], { a: A, b: { x: 80, y: 0, w: 10, h: 10 } }, {
      portrait: { a: { x: 60, y: 60, w: 10, h: 10 } },
    });

  it("takes what it touches where the arrangement on screen draws it", () => {
    load(twoArrangements(), { layout: "portrait" });
    sweep(150, 75);
    expect(useUIBuilderStore.getState().selectedElementIds).toEqual(["a"]);
  });

  it("in the primary, the same band touches nothing and clears the selection", () => {
    load(twoArrangements(), { selected: ["b"] });
    sweep(150, 75);
    expect(useUIBuilderStore.getState().selectedElementIds).toEqual([]);
  });

  it("leaves a locked control out, and with Shift adds what it touches to the selection, once each", () => {
    load(
      makePage([el("a", { locked: true }), el("b"), el("c"), el("d")], {
        a: { x: 55, y: 55, w: 5, h: 5 },
        b: { x: 58, y: 52, w: 5, h: 5 },
        c: { x: 0, y: 0, w: 5, h: 5 },
        d: { x: 62, y: 62, w: 5, h: 5 },
      }),
      { selected: ["c", "b"] },
    );
    const overlay = view.container.querySelector("[data-canvas-grid]")!;
    press(overlay, { shiftKey: true });
    drag(150, 75);
    release();
    expect(useUIBuilderStore.getState().selectedElementIds).toEqual(["c", "b", "d"]);
  });
});

describe("the hit boxes", () => {
  it("nest inside their container, and a control naming itself or nothing sits at page level", () => {
    load(
      makePage(
        [el("g", { type: "group" }), el("k", { parent: "g" }), el("j", { parent: "g" }), el("s", { parent: "s" }), el("o", { parent: "gone" })],
        {},
      ),
    );
    expect(node("k").parentElement).toBe(node("g"));
    expect(node("j").parentElement).toBe(node("g"));
    const top = node("g").parentElement;
    expect(node("s").parentElement).toBe(top);
    expect(node("o").parentElement).toBe(top);
  });

  it("are not drawn on a page that hands the screen to its own markup", () => {
    const page = { ...makePage([el("a")], { a: A }), render_mode: "custom", custom_file: "x.html" } as UIPage;
    load(page);
    expect(view.container.querySelector("[data-canvas-element]")).toBeNull();
  });

  it("are drawn on a page that says custom and names no file, which still draws its controls", () => {
    const page = { ...makePage([el("a")], { a: A }), render_mode: "custom" } as UIPage;
    load(page);
    expect(view.container.querySelector("[data-canvas-element]")).not.toBeNull();
  });
});

describe("resizing a page control", () => {
  function resize(direction: string, dx: number, dy: number, opts: { a?: Box; extra?: Partial<UIElement>; mods?: Mods; snap?: typeof SNAP_ON } = {}) {
    load(makePage([el("a", opts.extra)], { a: opts.a ?? A }, { snap: opts.snap }), { selected: ["a"] });
    press(handle(node("a"), direction));
    drag(dx, dy, opts.mods);
    release();
    return stored("a");
  }

  it("moves only the edges the grip drags", () => {
    expect(resize("e", 100, 30)).toEqual({ x: 10, y: 10, w: 30, h: 20 });
    expect(resize("w", 50, 30)).toEqual({ x: 15, y: 10, w: 15, h: 20 });
    expect(resize("n", 30, 50)).toEqual({ x: 10, y: 20, w: 20, h: 10 });
    expect(resize("s", 30, 60)).toEqual({ x: 10, y: 10, w: 20, h: 32 });
    expect(resize("se", 60, 60)).toEqual({ x: 10, y: 10, w: 26, h: 32 });
    expect(resize("nw", 60, 60)).toEqual({ x: 16, y: 22, w: 14, h: 8 });
    expect(undoLabels()).toEqual(["Resize element"]);
  });

  it("draws the guide a dragged edge lands on, down and across", () => {
    load(makePage([el("a")], { a: A }), { selected: ["a"] });
    press(handle(node("a"), "s"));
    drag(0, 98);
    expect(guides()).toEqual({ x: [], y: ["50%"] });
    release();
    press(handle(node("a"), "e"));
    drag(198, 0);
    expect(guides()).toEqual({ x: ["50%"], y: [] });
    release();
  });

  it("snaps only the dragged edges, and Alt sets the snap aside", () => {
    expect(resize("se", 60, 60, { snap: SNAP_ON })).toEqual({ x: 10, y: 10, w: 23.3333, h: 27.5 });
    expect(resize("se", 60, 60, { snap: SNAP_ON, mods: { altKey: true } })).toEqual({ x: 10, y: 10, w: 26, h: 32 });
  });

  it("never makes a box under 1% wide or tall, and a west or north grip keeps travelling", () => {
    expect(resize("e", -300, 0)).toEqual({ x: 10, y: 10, w: 1, h: 20 });
    expect(resize("w", 300, 0)).toEqual({ x: 40, y: 10, w: 1, h: 20 });
    expect(resize("n", 0, 300)).toEqual({ x: 10, y: 70, w: 20, h: 1 });
  });

  it("holds an aspect-locked control's ratio in pixels, driven by the side the grip moves", () => {
    const lock = { aspect_lock: 2 } as Partial<UIElement>;
    expect(resize("e", 100, 0, { extra: lock })).toEqual({ x: 10, y: 10, w: 30, h: 30 });
    expect(resize("s", 0, 100, { extra: lock })).toEqual({ x: 10, y: 10, w: 40, h: 40 });
    expect(resize("n", 0, 50, { extra: lock })).toEqual({ x: 10, y: 20, w: 10, h: 10 });
    expect(resize("w", 100, 0, { extra: lock })).toEqual({ x: 20, y: 10, w: 10, h: 10 });
    expect(resize("nw", 100, 0, { extra: lock })).toEqual({ x: 20, y: 20, w: 10, h: 10 });
    expect(resize("sw", 100, 0, { extra: lock })).toEqual({ x: 20, y: 10, w: 10, h: 10 });
  });

  it("holds the ratio it started with on Shift, but an aspect lock wins", () => {
    const tall = { x: 10, y: 10, w: 20, h: 40 };
    expect(resize("e", 100, 0, { a: tall, mods: { shiftKey: true } })).toEqual({ x: 10, y: 10, w: 30, h: 60 });
    expect(resize("e", 100, 0, { a: tall, extra: { aspect_lock: 2 } as Partial<UIElement>, mods: { shiftKey: true } })).toEqual(
      { x: 10, y: 10, w: 30, h: 30 },
    );
    expect(resize("e", 100, 0, { a: { x: 10, y: 10, w: 20, h: 0 }, mods: { shiftKey: true } })).toEqual(
      { x: 10, y: 10, w: 30, h: 1 },
    );
  });

  it("holds a ratio that is not 1 the right way up, and leaves a box with no height to its grips", () => {
    expect(resize("e", 100, 0, { mods: { shiftKey: true } })).toEqual({ x: 10, y: 10, w: 30, h: 30 });
    expect(resize("s", 0, 100, { a: { x: 10, y: 10, w: 20, h: 0 }, mods: { shiftKey: true } })).toEqual(
      { x: 10, y: 10, w: 20, h: 20 },
    );
  });

  it("rounds the box a ratio sets to four places, in what it saves and what it tells the panel", () => {
    expect(resize("e", 100, 0, { extra: { aspect_lock: 7 } as Partial<UIElement> })).toEqual({ x: 10, y: 10, w: 30, h: 8.5714 });
    expect(posted.at(-1)).toEqual({ type: "openavc:editor-placements", placements: { a: { x: 10, y: 10, w: 30, h: 8.5714 } } });
  });

  it("keeps the side the ratio sets at 1% or more", () => {
    expect(resize("e", 10, 0, { extra: { aspect_lock: 100 } as Partial<UIElement> })).toEqual({ x: 10, y: 10, w: 21, h: 1 });
    expect(resize("n", 0, 50, { extra: { aspect_lock: 0.01 } as Partial<UIElement> })).toEqual({ x: 10, y: 20, w: 1, h: 10 });
  });

  it("holds the starting ratio on Shift when the aspect lock is 0", () => {
    expect(resize("e", 100, 0, { extra: { aspect_lock: 0 } as Partial<UIElement>, mods: { shiftKey: true } })).toEqual(
      { x: 10, y: 10, w: 30, h: 30 },
    );
  });

  it("ignores an aspect lock that is not a positive number", () => {
    expect(resize("e", 100, 0, { extra: { aspect_lock: 0 } as Partial<UIElement> })).toEqual({ x: 10, y: 10, w: 30, h: 20 });
    expect(resize("e", 100, 0, { extra: { aspect_lock: "2" } as unknown as Partial<UIElement> })).toEqual(
      { x: 10, y: 10, w: 30, h: 20 },
    );
  });

  it("never moves a control into a container, however it ends up", () => {
    load(makePage([el("a"), el("g", { type: "group" })], { a: { x: 45, y: 45, w: 20, h: 20 }, g: { x: 40, y: 40, w: 50, h: 50 } }), {
      selected: ["a"],
    });
    press(handle(node("a"), "w"));
    drag(30, 0);
    expect((node("g") as HTMLElement).style.outline).not.toBe("2px dashed var(--accent)");
    release();
    expect(storedEl("a")!.parent).toBeUndefined();
    expect(stored("a")).toEqual({ x: 48, y: 45, w: 17, h: 20 });
  });
});

describe("a page gesture's keys and its end", () => {
  it("Escape puts the box back, saves nothing, and tells the panel where it was", () => {
    load(makePage([el("a")], { a: A }));
    press(node("a"));
    drag(153, 0);
    key("keydown", "Escape");
    expect((node("a") as HTMLElement).style.left).toBe("10%");
    expect(posted.at(-1)).toEqual({ type: "openavc:editor-placements", placements: { a: A } });
    release();
    drag(300, 0);
    expect(stored("a")).toEqual(A);
    expect(undoLabels()).toEqual([]);
  });

  it("pressing or letting go of Alt re-runs the last pointer position at once", () => {
    load(makePage([el("a")], { a: A }, { snap: SNAP_ON }));
    press(node("a"));
    drag(153, 0);
    expect((node("a") as HTMLElement).style.left).toBe("25%");
    key("keydown", "Alt", { altKey: true });
    expect((node("a") as HTMLElement).style.left).toBe("25.3%");
    key("keyup", "Alt");
    expect((node("a") as HTMLElement).style.left).toBe("25%");
    key("keydown", "Alt", { altKey: true });
    release();
    expect(stored("a")).toEqual({ x: 25.3, y: 10, w: 20, h: 20 });
  });

  it("pressing Shift re-runs the last pointer position on one axis", () => {
    load(makePage([el("a")], { a: A }));
    press(node("a"));
    drag(153, 40);
    key("keydown", "Shift", { shiftKey: true });
    release();
    expect(stored("a")).toEqual({ x: 25.3, y: 10, w: 20, h: 20 });
  });

  it("a key before the pointer has moved, or any other key, changes nothing", () => {
    load(makePage([el("a")], { a: A }));
    press(node("a"));
    key("keydown", "Alt", { altKey: true });
    drag(153, 0);
    key("keydown", "x");
    release();
    expect(stored("a")).toEqual({ x: 25.3, y: 10, w: 20, h: 20 });
  });

  it("saves nothing when the box ends where it started", () => {
    load(makePage([el("a")], { a: A }));
    press(node("a"));
    release();
    press(node("a"));
    drag(153, 0);
    drag(0, 0);
    release();
    expect(stored("a")).toEqual(A);
    expect(undoLabels()).toEqual([]);
    expect(posted.at(-1)).toEqual({ type: "openavc:editor-placements", placements: { a: A } });
  });

  it("stops following the pointer once it lets go", () => {
    load(makePage([el("a")], { a: A }));
    press(node("a"));
    drag(153, 0);
    release();
    drag(400, 0);
    key("keydown", "Alt", { altKey: true });
    release();
    expect(stored("a")).toEqual({ x: 25.3, y: 10, w: 20, h: 20 });
    expect(undoLabels()).toEqual(["Move element"]);
  });

  it("draws nothing more once it lets go, whatever the pointer or Alt does next", () => {
    load(makePage([el("a")], { a: A }, { snap: SNAP_ON }));
    press(node("a"));
    drag(153, 0);
    key("keydown", "Alt", { altKey: true });
    release();
    const sent = posted.length;
    key("keyup", "Alt");
    drag(400, 0);
    // No live box comes back: the hit box draws the page the canvas was handed.
    expect((node("a") as HTMLElement).style.left).toBe("10%");
    expect(posted.length).toBe(sent);
    expect(stored("a")).toEqual({ x: 25.3, y: 10, w: 20, h: 20 });
  });

  it("a new press ends the gesture still running", () => {
    load(makePage([el("a"), el("b")], { a: A, b: { x: 60, y: 60, w: 10, h: 10 } }));
    press(node("a"));
    drag(153, 0);
    press(node("b"));
    drag(-123, 0);
    release();
    expect(stored("a")).toEqual(A);
    expect(stored("b")).toEqual({ x: 47.7, y: 60, w: 10, h: 10 });
    expect(posted.at(-1)).toEqual({ type: "openavc:editor-placements", placements: { b: { x: 47.7, y: 60, w: 10, h: 10 } } });
    expect(undoLabels()).toEqual(["Move element"]);
  });

  it("a move pressed after an unfinished resize saves as a move, landing in a container", () => {
    load(
      makePage([el("a"), el("b"), el("g", { type: "group" })], {
        a: A,
        b: { x: 5, y: 60, w: 10, h: 10 },
        g: { x: 40, y: 40, w: 50, h: 50 },
      }),
      { selected: ["a"] },
    );
    press(handle(node("a"), "e"));
    drag(50, 0);
    press(node("b"));
    drag(450, 0);
    release();
    expect(storedEl("b")!.parent).toBe("g");
    expect(undoLabels()).toEqual(["Move element"]);
  });

  it("stops following the pointer when the canvas goes away mid-drag", () => {
    load(makePage([el("a")], { a: A }));
    press(node("a"));
    drag(153, 0);
    act(() => view.unmount());
    drag(300, 0);
    release();
    expect(stored("a")).toEqual(A);
    expect(undoLabels()).toEqual([]);
  });
});

describe("moving and resizing a master element", () => {
  const M = { x: 70, y: 70, w: 20, h: 20 };

  function loadMaster(m: MasterElement, opts: { elements?: UIElement[]; placements?: Record<string, Box>; masters?: MasterElement[]; snap?: typeof SNAP_ON; layout?: string; portrait?: Record<string, Box> } = {}) {
    load(makePage(opts.elements ?? [], opts.placements ?? {}, { snap: opts.snap, portrait: opts.portrait }), {
      masters: [m, ...(opts.masters ?? [])],
      selectedMaster: m.id,
      layout: opts.layout,
    });
  }

  it("travels by the pointer's distance as a share of the screen", () => {
    loadMaster(master("m", { landscape: M }));
    press(masterNode("m"));
    drag(-103, 0);
    release();
    expect(storedMaster("m")).toEqual({ x: 59.7, y: 70, w: 20, h: 20 });
    expect(undoLabels()).toEqual(["Move master element"]);
  });

  it("snaps, sets the snap aside with Alt, and keeps to one axis with Shift", () => {
    loadMaster(master("m", { landscape: M }), { snap: SNAP_ON });
    press(masterNode("m"));
    drag(-103, 0);
    release();
    expect(storedMaster("m")).toEqual({ x: 58.3333, y: 67.5, w: 20, h: 20 });

    loadMaster(master("m", { landscape: M }), { snap: SNAP_ON });
    press(masterNode("m"));
    drag(-103, 0, { altKey: true });
    release();
    expect(storedMaster("m")).toEqual({ x: 59.7, y: 70, w: 20, h: 20 });

    loadMaster(master("m", { landscape: M }));
    press(masterNode("m"));
    drag(-103, -40, { shiftKey: true });
    release();
    expect(storedMaster("m")).toEqual({ x: 59.7, y: 70, w: 20, h: 20 });

    loadMaster(master("m", { landscape: M }));
    press(masterNode("m"));
    drag(-40, -60, { shiftKey: true });
    release();
    expect(storedMaster("m")).toEqual({ x: 70, y: 58, w: 20, h: 20 });

    loadMaster(master("m", { landscape: M }));
    press(masterNode("m"));
    drag(-55, -55, { shiftKey: true });
    release();
    expect(storedMaster("m")).toEqual({ x: 64.5, y: 70, w: 20, h: 20 });
  });

  it("sticks to another master and to the page's top-level controls, never to one inside a container", () => {
    loadMaster(master("m", { landscape: M }), {
      masters: [master("n", { landscape: { x: 34, y: 0, w: 12, h: 10 } })],
    });
    press(masterNode("m"));
    drag(-371, 0);
    expect(guides().x).toEqual(["34%"]);
    release();
    expect(storedMaster("m")).toEqual({ x: 34, y: 70, w: 20, h: 20 });

    loadMaster(master("m", { landscape: M }), {
      elements: [el("g", { type: "group" }), el("k", { parent: "g" })],
      placements: { g: { x: 40, y: 0, w: 40, h: 40 }, k: { x: 10, y: 0, w: 20, h: 20 } },
    });
    press(masterNode("m"));
    drag(-384, 0);
    release();
    expect(storedMaster("m")).toEqual({ x: 31.6, y: 70, w: 20, h: 20 });

    loadMaster(master("m", { landscape: M }), {
      elements: [el("b")],
      placements: { b: { x: 34, y: 0, w: 12, h: 10 } },
    });
    press(masterNode("m"));
    drag(-371, 0);
    release();
    expect(storedMaster("m")).toEqual({ x: 34, y: 70, w: 20, h: 20 });
  });

  it("is not pulled toward a master that is not on this page", () => {
    loadMaster(master("m", { landscape: M }), {
      masters: [master("n", { landscape: { x: 34, y: 0, w: 12, h: 10 } }, { pages: ["other"] })],
    });
    press(masterNode("m"));
    drag(-371, 0);
    release();
    expect(storedMaster("m")).toEqual({ x: 32.9, y: 70, w: 20, h: 20 });
  });

  it("resizes from its grips the way a page control does, against the screen", () => {
    const cases: [string, number, number, Partial<MasterElement>, Mods, Box][] = [
      ["e", -100, 0, {}, {}, { x: 70, y: 70, w: 10, h: 20 }],
      ["w", -100, 0, {}, {}, { x: 60, y: 70, w: 30, h: 20 }],
      ["n", 0, -50, {}, {}, { x: 70, y: 60, w: 20, h: 30 }],
      ["s", 0, -50, {}, {}, { x: 70, y: 70, w: 20, h: 10 }],
      ["e", -300, 0, {}, {}, { x: 70, y: 70, w: 1, h: 20 }],
      ["w", 300, 0, {}, {}, { x: 100, y: 70, w: 1, h: 20 }],
      ["e", -100, 0, { aspect_lock: 2 }, {}, { x: 70, y: 70, w: 10, h: 10 }],
      ["s", 0, -50, { aspect_lock: 2 }, {}, { x: 70, y: 70, w: 10, h: 10 }],
      ["n", 0, 50, { aspect_lock: 2 }, {}, { x: 70, y: 80, w: 10, h: 10 }],
      ["nw", 100, 0, { aspect_lock: 2 }, {}, { x: 80, y: 80, w: 10, h: 10 }],
      ["w", 100, 0, { aspect_lock: 2 }, {}, { x: 80, y: 70, w: 10, h: 10 }],
      ["e", -100, 0, {}, { shiftKey: true }, { x: 70, y: 70, w: 10, h: 10 }],
      ["e", -100, 0, { aspect_lock: 4 }, { shiftKey: true }, { x: 70, y: 70, w: 10, h: 5 }],
      ["e", -195, 0, { aspect_lock: 100 }, {}, { x: 70, y: 70, w: 1, h: 1 }],
      ["e", -100, 0, { aspect_lock: 3 }, {}, { x: 70, y: 70, w: 10, h: 6.6667 }],
    ];
    for (const [direction, dx, dy, extra, mods, want] of cases) {
      loadMaster(master("m", { landscape: M }, extra));
      press(handle(masterNode("m"), direction));
      drag(dx, dy, mods);
      release();
      expect({ direction, extra, mods, box: storedMaster("m") }).toEqual({ direction, extra, mods, box: want });
    }
    expect(undoLabels()).toEqual(["Resize master element"]);
  });

  it("tells the panel the rounded box a ratio sets", () => {
    loadMaster(master("m", { landscape: M }, { aspect_lock: 3 }));
    press(handle(masterNode("m"), "e"));
    drag(-100, 0);
    release();
    expect(posted.at(-1)).toEqual({ type: "openavc:editor-placements", placements: { m: { x: 70, y: 70, w: 10, h: 6.6667 } } });
  });

  it("writes the arrangement on screen and leaves the other alone", () => {
    loadMaster(master("m", { landscape: M, portrait: { x: 10, y: 10, w: 20, h: 20 } }), {
      layout: "portrait",
      portrait: {},
    });
    press(masterNode("m"));
    drag(103, 0);
    release();
    expect(storedMaster("m", "portrait")).toEqual({ x: 20.3, y: 10, w: 20, h: 20 });
    expect(storedMaster("m", "landscape")).toEqual(M);
  });

  it("Escape puts it back, and a box that ends where it started saves nothing", () => {
    loadMaster(master("m", { landscape: M }));
    press(masterNode("m"));
    drag(-103, 0);
    key("keydown", "Escape");
    expect(posted.at(-1)).toEqual({ type: "openavc:editor-placements", placements: { m: M } });
    release();
    press(masterNode("m"));
    drag(-103, 0);
    drag(0, 0);
    release();
    expect(storedMaster("m")).toEqual(M);
    expect(undoLabels()).toEqual([]);
    expect(posted.at(-1)).toEqual({ type: "openavc:editor-placements", placements: { m: M } });
  });

  it("keeps to one axis the moment Shift goes down mid-drag", () => {
    loadMaster(master("m", { landscape: M }));
    press(masterNode("m"));
    drag(-103, -40);
    key("keydown", "Shift", { shiftKey: true });
    release();
    expect(storedMaster("m")).toEqual({ x: 59.7, y: 70, w: 20, h: 20 });
  });

  it("draws nothing more once it lets go, whatever the pointer or Alt does next", () => {
    loadMaster(master("m", { landscape: M }), { snap: SNAP_ON });
    press(masterNode("m"));
    drag(-103, 0);
    key("keydown", "Alt", { altKey: true });
    release();
    const sent = posted.length;
    key("keyup", "Alt");
    drag(-300, 0);
    expect((masterNode("m") as HTMLElement).style.left).toBe("70%");
    expect(posted.length).toBe(sent);
  });

  it("re-runs the last pointer position when Alt or Shift changes, and stops following once it lets go", () => {
    loadMaster(master("m", { landscape: M }), { snap: SNAP_ON });
    press(masterNode("m"));
    key("keydown", "Shift", { shiftKey: true });
    key("keydown", "x");
    drag(-103, 0);
    key("keydown", "Alt", { altKey: true });
    key("keyup", "Alt");
    key("keydown", "Alt", { altKey: true });
    release();
    drag(-300, 0);
    key("keydown", "Shift", { shiftKey: true });
    release();
    expect(storedMaster("m")).toEqual({ x: 59.7, y: 70, w: 20, h: 20 });
    expect(undoLabels()).toEqual(["Move master element"]);
  });
});
