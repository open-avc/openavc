/**
 * The box a move, a resize, a palette drop and a pointerless add produce, read
 * through the helpers file: which pull wins when two are equally near, which
 * guides are drawn, the grid's edge cases, and where auto-place lands at the
 * edges of the page and of the cascade. The Python harness
 * (tests/test_ui_builder_helpers.py) holds the everyday cases.
 */
import { describe, it, expect } from "vitest";
import type { Placement } from "../../api/types";
import { autoPlace, paletteDragPlacement, pointerToPercent, snapMove, snapResize } from "./uiBuilderHelpers";

const OFF = { enabled: false, x: 100 / 12, y: 100 / 8 };
const TWELFTHS = { enabled: true, x: 100 / 12, y: 100 / 8 };
const box = (x: number, y: number, w: number, h: number): Placement => ({ x, y, w, h });

describe("a move's pulls", () => {
  it("takes the first equally near line, in the order edges and lines are listed", () => {
    const others = [box(19, 0, 10, 10), box(31, 0, 10, 10)];
    const r = snapMove(box(30, 60, 10, 10), { snap: OFF, others });
    expect(r).toEqual({ placement: box(29, 60, 10, 10), guidesX: [29], guidesY: [] });
  });

  it("prefers the nearer of two lines, whichever edge reaches it", () => {
    const others = [box(31.2, 0, 10, 10)];
    const r = snapMove(box(20, 60, 10, 10), { snap: OFF, others });
    expect(r.placement.x).toBe(21.2);
    expect(r.guidesX).toEqual([31.2]);
  });

  it("draws one guide per line, however many edges or neighbours sit on it", () => {
    const others = [box(40, 0, 10, 10), box(40, 20, 20, 10), box(10, 0, 30, 10)];
    const r = snapMove(box(40.5, 60, 10, 10), { snap: OFF, others });
    expect(r.placement.x).toBe(40);
    expect(r.guidesX).toEqual([50, 40, 45]);
  });

  it("draws a guide on each axis it is stuck on", () => {
    const r = snapMove(box(0.4, 99.2, 10, 0.5), { snap: OFF, others: [] });
    expect(r).toEqual({ placement: box(0, 99.5, 10, 0.5), guidesX: [0], guidesY: [100] });
  });

  it("takes the first equally near snap increment, and none at all when the increment is not positive", () => {
    const tens = { enabled: true, x: 10, y: 10 };
    expect(snapMove(box(2, 73, 26, 10), { snap: tens }).placement).toEqual(box(0, 75, 26, 10));
    expect(snapMove(box(12.3, 73, 6, 4), { snap: tens }).placement).toEqual(box(14, 70, 6, 4));
    expect(snapMove(box(12.3, 73, 6, 4), { snap: { enabled: true, x: 0, y: -5 } }).placement).toEqual(box(12.3, 73, 6, 4));
  });

  it("snaps to the grid when the page says nothing about whether snap is on", () => {
    const r = snapMove(box(12.3, 73, 6, 4), { snap: { x: 10, y: 10 } as never });
    expect(r.placement).toEqual(box(14, 70, 6, 4));
  });

  it("rounds what it returns to four places, Alt or not", () => {
    expect(snapMove(box(12.123456, 73.98765, 6, 4), { snap: OFF, bypass: true }).placement).toEqual(box(12.1235, 73.9877, 6, 4));
    expect(snapMove(box(12.123456, 73.98765, 6, 4), { snap: OFF }).placement).toEqual(box(12.1235, 73.9877, 6, 4));
  });
});

describe("a resize's pulls", () => {
  it("pulls the north edge and takes the height from it", () => {
    const r = snapResize(box(10, 11, 20, 30), "n", { snap: TWELFTHS });
    expect(r).toEqual({ placement: box(10, 12.5, 20, 28.5), guidesX: [], guidesY: [] });
  });

  it("pulls the west and the north edge of a north-west grip, each on its own axis", () => {
    const others = [box(0, 0, 9, 9)];
    const r = snapResize(box(9.5, 50.6, 20, 20), "nw", { snap: OFF, others });
    expect(r).toEqual({ placement: box(9, 50, 20.5, 20.6), guidesX: [9], guidesY: [50] });
  });

  it("leaves the edges no grip drags alone and only rounds the box", () => {
    expect(snapResize(box(9.12345, 9.5, 20, 20), "", { snap: TWELFTHS })).toEqual({
      placement: box(9.1235, 9.5, 20, 20),
      guidesX: [],
      guidesY: [],
    });
  });
});

describe("a palette drop", () => {
  it("centres the new control under the pointer and pulls it like a move", () => {
    const r = paletteDragPlacement({ x: 50.6, y: 30 }, { w: 20, h: 10 }, { snap: OFF, others: [] });
    expect(r).toEqual({ placement: box(40, 25, 20, 10), guidesX: [50], guidesY: [] });
  });

  it("measures the pointer across the box it fell in, and a box with no width as 0", () => {
    expect(pointerToPercent(250, 200, 100)).toBe(50);
    expect(pointerToPercent(250, 200, Number.NaN)).toBe(0);
    expect(pointerToPercent(250, 200, -100)).toBe(0);
  });
});

describe("where a control lands with no pointer", () => {
  it("fits a cell flush with the page's far edge on a twelfths grid", () => {
    const row = Array.from({ length: 11 }, (_, c) => box((c * 100) / 12, 0, 100 / 12, 12.5));
    expect(autoPlace(row, { w: 100 / 12, h: 12.5 }, TWELFTHS)).toEqual(box(91.6667, 0, 8.3333, 12.5));
    expect(autoPlace([box(0, 0, 75, 12.5)], { w: 25, h: 12.5 }, TWELFTHS)).toEqual(box(75, 0, 25, 12.5));
    expect(autoPlace([box(0, 0, 76, 12.5)], { w: 25, h: 12.5 }, TWELFTHS)).toEqual(box(0, 12.5, 25, 12.5));
  });

  it("lands in a cell that only touches an occupied box", () => {
    expect(autoPlace([box(0, 0, 25, 100)], { w: 25, h: 12.5 }, TWELFTHS)).toEqual(box(25, 0, 25, 12.5));
  });

  it("grows a box under 1% to 1%", () => {
    expect(autoPlace([], { w: 0, h: 0.5 }, TWELFTHS)).toEqual(box(0, 0, 1, 1));
    expect(autoPlace([], { w: -3, h: 0 }, OFF)).toEqual(box(49.5, 49.5, 1, 1));
  });

  it("scans whole cells only, and one cell when the increment is wider than the page", () => {
    const thirties = { enabled: true, x: 30, y: 30 };
    expect(autoPlace([box(0, 0, 60, 30)], { w: 30, h: 30 }, thirties)).toEqual(box(60, 0, 30, 30));
    const wide = { enabled: true, x: 150, y: 150 };
    expect(autoPlace([], { w: 10, h: 10 }, wide)).toEqual(box(0, 0, 10, 10));
    expect(autoPlace([box(0, 0, 10, 10)], { w: 10, h: 10 }, wide, 1)).toEqual(box(47.5, 47.5, 10, 10));
  });

  it("cascades from the page centre when there is no grid to scan", () => {
    expect(autoPlace([], { w: 20, h: 20 }, { enabled: true, x: 0, y: 10 })).toEqual(box(40, 40, 20, 20));
    expect(autoPlace([], { w: 20, h: 20 }, { enabled: true, x: 10, y: 0 }, 2)).toEqual(box(45, 45, 20, 20));
  });

  it("cascades 2.5% a step and wraps after eight steps", () => {
    expect(autoPlace([], { w: 20, h: 10 }, OFF, 3)).toEqual(box(47.5, 52.5, 20, 10));
    expect(autoPlace([], { w: 20, h: 10 }, OFF, 8)).toEqual(box(40, 45, 20, 10));
    expect(autoPlace([], { w: 20, h: 10 }, OFF, 11)).toEqual(box(47.5, 52.5, 20, 10));
  });

  it("keeps a cascaded box on the page", () => {
    expect(autoPlace([], { w: 90, h: 95 }, OFF, 3)).toEqual(box(10, 5, 90, 95));
    expect(autoPlace([], { w: 120, h: 30 }, OFF, 0)).toEqual(box(0, 35, 120, 30));
  });

  it("falls back to the cascade, at the count it is given, when no cell is free", () => {
    expect(autoPlace([box(0, 0, 100, 100)], { w: 20, h: 20 }, TWELFTHS, 2)).toEqual(box(45, 45, 20, 20));
  });
});
