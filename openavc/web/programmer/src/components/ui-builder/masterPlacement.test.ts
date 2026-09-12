import { describe, it, expect } from "vitest";
import { masterPlacement, withMasterPlacement } from "./uiBuilderHelpers";
import type { MasterElement } from "../../api/types";

// A master's box is stored per ORIENTATION, not per layout, and the read falls
// back across the keys while the write must not. Getting that backwards is
// silent in the worst way: the master moves on every page except the one you
// were looking at, and the arrangement you were authoring is the one left
// behind. Three doors move a master -- the canvas gesture, the arrow keys and
// the Layout fields -- and they all write through withMasterPlacement.

const NAV_BAR: MasterElement = {
  id: "nav_bar",
  type: "button",
  label: "Home",
  pages: "*",
  hidden: false,
  placements: { landscape: { x: 0, y: 90, w: 100, h: 10 } },
} as MasterElement;

describe("masterPlacement", () => {
  it("falls back to landscape when this orientation has no box of its own", () => {
    expect(masterPlacement(NAV_BAR, "portrait")).toEqual({ x: 0, y: 90, w: 100, h: 10 });
  });

  it("prefers the orientation's own box once it has one", () => {
    const both = {
      ...NAV_BAR,
      placements: {
        landscape: { x: 0, y: 90, w: 100, h: 10 },
        portrait: { x: 0, y: 93, w: 100, h: 7 },
      },
    };
    expect(masterPlacement(both, "portrait")).toEqual({ x: 0, y: 93, w: 100, h: 7 });
  });

  it("returns null for a master with no box at all", () => {
    expect(masterPlacement({ ...NAV_BAR, placements: {} }, "landscape")).toBeNull();
  });
});

describe("withMasterPlacement", () => {
  it("gives the arrangement its OWN box rather than rewriting the one it borrowed", () => {
    const moved = withMasterPlacement(NAV_BAR, "portrait", { x: 0, y: 80, w: 100, h: 10 });
    // The portrait arrangement now has a box of its own...
    expect(moved.placements.portrait).toEqual({ x: 0, y: 80, w: 100, h: 10 });
    // ...and every page drawn landscape is untouched.
    expect(moved.placements.landscape).toEqual({ x: 0, y: 90, w: 100, h: 10 });
  });

  it("writes the landscape box when landscape is what is being authored", () => {
    const moved = withMasterPlacement(NAV_BAR, "landscape", { x: 5, y: 88, w: 90, h: 10 });
    expect(moved.placements.landscape).toEqual({ x: 5, y: 88, w: 90, h: 10 });
    expect(moved.placements.portrait).toBeUndefined();
  });

  it("rounds, so a pixel drag does not store seventeen decimal places", () => {
    const moved = withMasterPlacement(NAV_BAR, "landscape", {
      x: 12.3456789,
      y: 0,
      w: 25.987654321,
      h: 10,
    });
    expect(moved.placements.landscape).toEqual({ x: 12.3457, y: 0, w: 25.9877, h: 10 });
  });

  it("leaves the master it was handed alone", () => {
    withMasterPlacement(NAV_BAR, "portrait", { x: 1, y: 2, w: 3, h: 4 });
    expect(NAV_BAR.placements).toEqual({ landscape: { x: 0, y: 90, w: 100, h: 10 } });
  });
});
