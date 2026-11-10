/**
 * The smallest box a control can be drawn in, part by part: the Builder's copy
 * of openavc/ui/control_minimums.py's arithmetic over the measured rules.
 *
 * The parity suite reads these floors through the review's sentences, for the
 * shapes its corpus happens to hold; the Layout panel reads them as numbers.
 * Each case here states the box in full, from the generated rule's own numbers
 * (api/uiMinimums.gen.ts): a crosspoint matrix is 95x84 plus 44+1 px a source
 * and a destination, a list matrix 148x9 plus 44.25+6 px a destination, a tile
 * wall 10x10 plus 120+6 by 91+6 px a tile, a slider 24x54 plus its thumb on
 * both axes, a list 28x50 plus one item's height, a status light 20x20 plus 9
 * for a caption.
 */
import { describe, expect, it } from "vitest";
import type { UIElement } from "../../api/types";
import { controlMinimumBox, controlMinimumPercent, matrixTileGridShape } from "./controlMinimums";
import { reviewPage, starvationFinding } from "./uiBuilderHelpers";

const el = (fields: Record<string, unknown>): UIElement =>
  ({ id: "m1", type: "matrix", ...fields }) as unknown as UIElement;

const crosspoint = (config: Record<string, unknown>, extra: Record<string, unknown> = {}) =>
  el({ matrix_config: config, ...extra });

describe("a repeated part", () => {
  it("adds one cell and its gap per source across and per destination down", () => {
    expect(controlMinimumBox(crosspoint({ sources: { from: { count: 3 } }, destinations: [1, 2] }))).toEqual({
      widthPx: 95 + 3 * 45,
      heightPx: 84 + 2 * 45,
      internals: [{ part: "matrix-cell", widthPx: 44, heightPx: 44 }],
    });
  });

  it("counts a list or a generator by what it resolves to, exclusions out", () => {
    const box = controlMinimumBox(
      crosspoint({ sources: { from: { count: 4 }, exclude: [2] }, destinations: [{ value: 1 }, { label: "x" }] }),
    );
    expect([box?.widthPx, box?.heightPx]).toEqual([95 + 3 * 45, 84 + 1 * 45]);
  });

  it("counts a number written outright, whole only, and nothing for one that is not a positive finite number", () => {
    const size = (sources: unknown) => controlMinimumBox(crosspoint({ sources, destinations: 0 }))?.widthPx;
    expect(size(2.7)).toBe(95 + 2 * 45);
    expect(size("3")).toBe(95 + 3 * 45);
    for (const sources of [0, -1, "abc", "Infinity", null, undefined, true]) {
      expect(size(sources)).toBe(sources === true ? 95 + 45 : 95);
    }
  });

  it("takes an authored cell size, in rem, for both the span and the part it blames", () => {
    expect(
      controlMinimumBox(crosspoint({ sources: [1, 2], destinations: [1] }, { style: { cell_size: 2 } })),
    ).toEqual({
      widthPx: 95 + 2 * (28 + 1),
      heightPx: 84 + 1 * (28 + 1),
      internals: [{ part: "matrix-cell", widthPx: 28, heightPx: 28 }],
    });
    expect(
      controlMinimumBox(crosspoint({ sources: [1], destinations: [1] }, { style: { cell_size: "2" } }))?.internals,
    ).toEqual([{ part: "matrix-cell", widthPx: 44, heightPx: 44 }]);
  });

  it("replaces the declared part with the resolved one, on the axis it repeats along", () => {
    expect(
      controlMinimumBox(el({ matrix_style: "list", matrix_config: { sources: [1, 2, 3], destinations: [1, 2] } })),
    ).toEqual({
      widthPx: 148,
      heightPx: 9 + 2 * 50.25,
      internals: [{ part: "matrix-list-row", widthPx: null, heightPx: 44.25 }],
    });
  });

  it("lays a tile wall out in the grid the panel draws, turned on a portrait screen", () => {
    const wall = el({ matrix_style: "tiles", matrix_config: { destinations: { from: { count: 8 } } } });
    expect(controlMinimumBox(wall)).toEqual({
      widthPx: 10 + 4 * 126,
      heightPx: 10 + 2 * 97,
      internals: [{ part: "matrix-tile", widthPx: 120, heightPx: 91 }],
    });
    expect(controlMinimumBox(wall, null, "portrait")).toMatchObject({
      widthPx: 10 + 2 * 126,
      heightPx: 10 + 4 * 97,
    });
    const five = el({ matrix_style: "tiles", matrix_config: { destinations: [1, 2, 3, 4, 5] } });
    expect(controlMinimumBox(five)).toMatchObject({ widthPx: 10 + 3 * 126, heightPx: 10 + 2 * 97 });
  });

  it("measures a style it does not know as the default crosspoint", () => {
    expect(
      controlMinimumBox(el({ matrix_style: "constructor", matrix_config: { sources: [1], destinations: [1] } })),
    ).toMatchObject({ widthPx: 95 + 45, heightPx: 84 + 45 });
  });
});

describe("a tile wall's grid", () => {
  it("is as square as the count allows, wider than tall in landscape", () => {
    expect(matrixTileGridShape(8)).toEqual([4, 2]);
    expect(matrixTileGridShape(8, "portrait")).toEqual([2, 4]);
    expect(matrixTileGridShape(9)).toEqual([3, 3]);
    expect(matrixTileGridShape(15)).toEqual([5, 3]);
    expect(matrixTileGridShape(3)).toEqual([3, 1]);
    expect(matrixTileGridShape(1, "portrait")).toEqual([1, 1]);
  });

  it("is empty for no tiles", () => {
    expect(matrixTileGridShape(0)).toEqual([0, 0]);
    expect(matrixTileGridShape(-3, "portrait")).toEqual([0, 0]);
  });
});

describe("a conditional part", () => {
  it("is added only where the matrix asks for it", () => {
    const base = { sources: [1], destinations: [1] };
    const size = (config: Record<string, unknown>, extra: Record<string, unknown> = {}) => {
      const box = controlMinimumBox(crosspoint({ ...base, ...config }, extra));
      return [box?.widthPx, box?.heightPx];
    };
    expect(size({})).toEqual([140, 129]);
    expect(size({}, { label: "Main" })).toEqual([140, 129 + 40]);
    expect(size({}, { label: "  " })).toEqual([140, 129]);
    expect(size({ presets: [{ name: "a" }] })).toEqual([140, 129 + 50]);
    expect(size({ presets: [] })).toEqual([140, 129]);
    expect(size({ presets: { a: 1 } })).toEqual([140, 129]);
    expect(size({ show_lock: true })).toEqual([140 + 45, 129]);
    expect(size({ show_lock: "yes" })).toEqual([140, 129]);
    const mute = { bindings: { do: { mute_route: { action: "macro" } } } };
    expect(size({}, mute)).toEqual([140 + 45, 129]);
    expect(size({ show_mute: false }, mute)).toEqual([140, 129]);
    expect(size({ show_mute: true })).toEqual([140, 129]);
  });

  it("takes each style's own size for the same part", () => {
    const list = el({
      matrix_style: "list",
      label: "Main",
      matrix_config: { destinations: [1], show_lock: true },
      bindings: { do: { mute_route: { action: "macro" } } },
    });
    expect(controlMinimumBox(list)).toMatchObject({ widthPx: 148 + 49 + 37, heightPx: 9 + 50.25 + 40 });
  });
});

describe("a scaling part", () => {
  it("grows a slider on both axes with its thumb, from the element, else the theme, else the default", () => {
    const slider = (fields: Record<string, unknown> = {}) => ({ id: "s", type: "slider", ...fields }) as unknown as UIElement;
    expect(controlMinimumBox(slider())).toEqual({
      widthPx: 24 + 44,
      heightPx: 54 + 44,
      internals: [{ part: "slider thumb", widthPx: 44, heightPx: 44 }],
    });
    expect(controlMinimumBox(slider({ thumb_size: 4 }), { thumb_size: 2 })).toMatchObject({ widthPx: 24 + 56, heightPx: 54 + 56 });
    expect(controlMinimumBox(slider({ thumb_size: null }), { thumb_size: 2 })).toMatchObject({ widthPx: 24 + 28 });
    expect(controlMinimumBox(slider(), { thumb_size: "3" })).toMatchObject({ widthPx: 24 + 42 });
    expect(controlMinimumBox(slider(), {})).toMatchObject({ widthPx: 24 + 44 });
  });

  it("grows a list down only, by an item height the theme does not set", () => {
    const list = (fields: Record<string, unknown> = {}) => ({ id: "l", type: "list", ...fields }) as unknown as UIElement;
    expect(controlMinimumBox(list({ item_height: 5 }), { item_height: 1 })).toEqual({
      widthPx: 28,
      heightPx: 50 + 70,
      internals: [{ part: "list-item", widthPx: null, heightPx: 70 }],
    });
    expect(controlMinimumBox(list(), { item_height: 1 })).toMatchObject({ heightPx: 50 + 44 });
  });
});

describe("the rest of the floor", () => {
  it("widens a status light for a caption it draws", () => {
    const led = (label?: string) => ({ id: "d", type: "status_led", label }) as unknown as UIElement;
    expect(controlMinimumBox(led("Online"))).toEqual({
      widthPx: 29,
      heightPx: 20,
      internals: [{ part: "led-dot", widthPx: 20, heightPx: 20 }],
    });
    expect(controlMinimumBox(led(" "))).toMatchObject({ widthPx: 20 });
    expect(controlMinimumBox(led())).toMatchObject({ widthPx: 20 });
  });

  it("has nothing to say about a type with no fixed parts, or one named after an object's own keys", () => {
    for (const type of ["button", "label", "constructor", "toString", "__proto__"]) {
      expect(controlMinimumBox({ id: "x", type } as unknown as UIElement)).toBeNull();
    }
  });

  it("is a percentage of the box the element sits in, axis by axis", () => {
    const led = { id: "d", type: "status_led" } as unknown as UIElement;
    expect(controlMinimumPercent(led, { width: 400, height: 80 })).toEqual({ w: 5, h: 25 });
    expect(controlMinimumPercent({ id: "b", type: "button" } as unknown as UIElement, { width: 1, height: 1 })).toBeNull();
  });
});

describe("a box short of its floor", () => {
  const seat = (width: number, height: number) => ({
    boxPx: { width, height },
    parentPx: { width: 1280, height: 800 },
    parentName: "the page",
    ancestors: [],
    orientation: "landscape",
  });
  const fader = { id: "f1", type: "fader" } as unknown as UIElement;

  it("says each short axis with its numbers and the parts that do not shrink on it", () => {
    expect(starvationFinding(fader, seat(80, 100))?.message).toContain(
      "80px wide, needs 88px (fader-handle is 44px, fader-scale is 42px); 100px tall, needs 175px (fader-handle is 44px)",
    );
  });

  it("is not short within half a pixel of the floor", () => {
    expect(starvationFinding(fader, seat(87.5, 174.5))).toBeNull();
    expect(starvationFinding(fader, seat(87.49, 175))?.message).toContain("87px wide, needs 88px");
  });

  it("is reviewed against the floor the page's own orientation draws", () => {
    const wall = {
      id: "wall",
      type: "matrix",
      matrix_style: "tiles",
      matrix_config: { destinations: { from: { count: 8, route_key: "var.r*" } } },
      bindings: { do: { route: { action: "macro", macro: "m" } } },
    };
    const page = (orientation: string) => ({
      id: "p",
      name: "P",
      elements: [wall],
      layouts: [{
        id: orientation, orientation, primary: true, hidden: [],
        placements: { wall: { x: 0, y: 0, w: 41, h: 30 } },
      }],
    });
    const says = (orientation: string) =>
      reviewPage(page(orientation) as never).filter((f) => f.kind === "too_small_for_contents").map((f) => f.message);
    expect(says("landscape")).toEqual([]);
    expect(says("portrait").join(" ")).toContain("384px tall, needs 398px");
  });
});
