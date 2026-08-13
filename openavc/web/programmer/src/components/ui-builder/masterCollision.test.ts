import { describe, it, expect } from "vitest";
import { reviewWarningsByElement } from "./uiBuilderHelpers";
import type { MasterElement, UIPage } from "../../api/types";

// The canvas badge is the surface this had to reach. A master is not in
// `page.elements`, so the review cannot see one unless the canvas hands it over
// -- and the way this breaks is not an exception, it is silence: the badge
// simply stops appearing and the page looks clean. The parity corpus pins the
// sentence; this pins the delivery.

const NAV_BAR: MasterElement = {
  id: "nav_bar",
  type: "button",
  label: "Home",
  pages: "*",
  hidden: false,
  // 203 x 63px at the origin, on a 1280 x 800 reference.
  placements: { landscape: { x: 0, y: 0, w: 15.859375, h: 7.875 } },
} as MasterElement;

const PAGE = {
  id: "main",
  name: "Main",
  elements: [
    { id: "vid_wide", type: "image", src: "assets://wall.png" },
    { id: "nav_clear", type: "button", label: "Go" },
  ],
  layouts: [
    {
      id: "landscape",
      orientation: "landscape",
      primary: true,
      hidden: [],
      placements: {
        // Starts 51px in, so it lies across most of the bar.
        vid_wide: { x: 3.984375, y: 0, w: 48.75, h: 20 },
        nav_clear: { x: 20, y: 25, w: 20, h: 12 },
      },
    },
  ],
} as unknown as UIPage;

describe("a control dragged onto a master element", () => {
  it("badges the control, and names the master it buried", () => {
    const warnings = reviewWarningsByElement(PAGE, "landscape", undefined, [NAV_BAR]);
    expect(warnings.get("vid_wide")).toBe(
      "vid_wide (image) is drawn over the master element nav_bar (button), which " +
        "draws on every page and sits behind a page's own controls. Move vid_wide off " +
        "it, or stop nav_bar drawing on main. vid_wide covers 152x63px of nav_bar, " +
        "75% of it.",
    );
  });

  it("says nothing about a control beside the master rather than on it", () => {
    const warnings = reviewWarningsByElement(PAGE, "landscape", undefined, [NAV_BAR]);
    expect(warnings.has("nav_clear")).toBe(false);
  });
});
