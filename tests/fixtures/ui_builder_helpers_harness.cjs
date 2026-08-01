"use strict";
// Loads the real UI Builder helpers (uiBuilderHelpers.ts, transpiled on the fly
// with the esbuild already in web/programmer/node_modules) and runs pure-logic
// checks for the grid-geometry / id / rename helpers, printing JSON results to
// stdout. Mirrors color_utils_harness.cjs: no build step, and the Python wrapper
// skips when the toolchain is absent rather than failing CI. The helper module
// has only `import type` statements, which esbuild strips, so it loads with no
// runtime imports.
const fs = require("fs");
const path = require("path");

const helpersPath = process.argv[2];
const src = fs.readFileSync(helpersPath, "utf8");

const esbuild = require("esbuild");
const { code } = esbuild.transformSync(src, { loader: "ts", format: "cjs" });
const moduleObj = { exports: {} };
const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
fn(moduleObj.exports, require, moduleObj, helpersPath, path.dirname(helpersPath));
const H = moduleObj.exports;

const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const results = {};

// A 0.8.0 page: an authoring-only snap increment, and geometry in a layout.
const SNAP = { enabled: true, x: 100 / 12, y: 100 / 8 };
const LANDSCAPE = (placements = {}) => ({
  id: "landscape", orientation: "landscape", primary: true, placements, hidden: [],
});

const SNAP_ON = { enabled: true, x: 100 / 12, y: 100 / 8 };
const SNAP_OFF = { enabled: false, x: 100 / 12, y: 100 / 8 };
const near = (a, b, tol = 1e-4) => Math.abs(a - b) < tol;

// --- S-001: the px<->rem boundary between the editors and the panel ---
// Every stored measurement is rem, because the panel's type scale is its own
// size. Nobody designs in rem, so the editors speak px and convert here. Get
// this backwards and a 24px font renders at 336px on real glass.
{
  results.s001_px_to_rem = {
    pass: H.pxToRem(14) === 1 && H.pxToRem(24) === 1.7143 && H.pxToRem(0) === 0,
    detail: {14: H.pxToRem(14), 24: H.pxToRem(24), 0: H.pxToRem(0) },
  };
}
{
  results.s001_rem_to_px = {
    pass: H.remToPx(1) === 14 && H.remToPx(1.7143) === 24 && H.remToPx(0) === 0,
    detail: { 1: H.remToPx(1), "1.7143": H.remToPx(1.7143), 0: H.remToPx(0) },
  };
}
{
  // A value typed in px must survive the round trip unchanged, or every save
  // walks it a little further from what the author asked for.
  const sizes = [8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 44, 64];
  const bad = sizes.filter((px) => H.remToPx(H.pxToRem(px)) !== px);
  results.s001_round_trip_is_stable = { pass: bad.length === 0, detail: bad };
}
{
  // Empty and non-numeric input clears rather than storing NaN.
  results.s001_blank_clears = {
    pass: H.pxToRem("") === null && H.pxToRem(null) === null &&
      H.pxToRem(undefined) === null && H.pxToRem("abc") === null,
    detail: { blank: H.pxToRem(""), nul: H.pxToRem(null), junk: H.pxToRem("abc") },
  };
}
{
  // Only lengths convert. A line height is a multiplier, an opacity is a
  // fraction, a segment count is a count -- converting any of them is a bug.
  const lengths = ["font_size", "border_radius", "border_width", "padding",
    "padding_vertical", "padding_horizontal", "margin", "margin_vertical",
    "margin_horizontal", "letter_spacing", "cell_size", "icon_size",
    "item_height", "thumb_size"];
  const notLengths = ["line_height", "opacity", "background_opacity",
    "meter_segments", "tick_count", "peak_hold_ms", "gauge_width", "arc_angle",
    "font_weight", "transition_duration", "image_opacity"];
  results.s001_only_lengths_convert = {
    pass: lengths.every((k) => H.isRemStyleKey(k)) &&
      notLengths.every((k) => !H.isRemStyleKey(k)),
    detail: {
      missed: lengths.filter((k) => !H.isRemStyleKey(k)),
      overreached: notLengths.filter((k) => H.isRemStyleKey(k)),
    },
  };
}
{
  // The display/store pair is what the editors actually call.
  results.s001_display_and_store = {
    pass: H.displayStyleValue("font_size", 1.7143) === 24 &&
      H.storeStyleValue("font_size", 24) === 1.7143 &&
      H.displayStyleValue("line_height", 1.2) === 1.2 &&
      H.storeStyleValue("line_height", 1.2) === 1.2 &&
      H.displayStyleValue("bg_color", "#fff") === "#fff",
    detail: {
      fontShown: H.displayStyleValue("font_size", 1.7143),
      fontStored: H.storeStyleValue("font_size", 24),
      lineHeight: H.displayStyleValue("line_height", 1.2),
    },
  };
}
{
  // A 1px hairline must not round away to nothing on the way to rem and back.
  results.s001_hairline_survives = {
    pass: H.remToPx(H.pxToRem(1)) === 1 && H.pxToRem(1) > 0,
    detail: { rem: H.pxToRem(1), backToPx: H.remToPx(H.pxToRem(1)) },
  };
}

// --- H-038: pointerToPercent maps the pointer to the box it fell in ---
// The page carries no padding any more (the gutter died with the grid), so
// this is the whole rect edge to edge and the drop lands under the pointer.
{
  const r = H.pointerToPercent(0, 0, 120);
  results.h038_ptp_origin = { pass: r === 0, detail: r };
}
{
  const r = H.pointerToPercent(60, 0, 120);
  results.h038_ptp_centre = { pass: r === 50, detail: r };
}
{
  // Offset rect: the pointer is measured from the rect's own left edge.
  const r = H.pointerToPercent(130, 100, 120);
  results.h038_ptp_offset_rect = { pass: near(r, 25), detail: r };
}
{
  // Past the far edge is over 100, not clamped — free positioning WARNS
  // rather than prevents, so the number has to survive to be warned about.
  const r = H.pointerToPercent(150, 0, 120);
  results.h038_ptp_past_edge = { pass: r === 125, detail: r };
}
{
  // A zero-width rect can't be divided into; answer 0 rather than NaN.
  const r = H.pointerToPercent(50, 0, 0);
  results.h038_ptp_zero_rect = { pass: r === 0, detail: r };
}

// --- H-038: percentages are stored to 4 decimal places, at every write ---
{
  const r = H.roundPct(100 / 3);
  results.h038_round_precision = { pass: r === 33.3333, detail: r };
}
{
  // The jitter this exists to stop: a px<->% round trip must come back equal,
  // or the save-reconcile diff dirties and arms a phantom autosave.
  const once = H.roundPct((37 / 120) * 100);
  const twice = H.roundPct((once / 100) * 120 / 120 * 100);
  results.h038_round_stable_round_trip = { pass: once === twice, detail: { once, twice } };
}

// --- M-077: snapMove pulls a drag onto the nearest attractive line ---
{
  // Just past a snap increment (8.3333) -> pulled back onto it.
  const r = H.snapMove({ x: 9, y: 13, w: 25, h: 12.5 }, { snap: SNAP_ON });
  results.m077_snap_grid = {
    pass: near(r.placement.x, 8.3333) && near(r.placement.y, 12.5),
    detail: r.placement,
  };
}
{
  // Alt held: the element lands on the EXACT pointer, no attraction at all.
  // This is the affordance that makes "free positioning" actually free.
  const raw = { x: 9.13, y: 13.77, w: 25, h: 12.5 };
  const r = H.snapMove(raw, { snap: SNAP_ON, bypass: true });
  results.m077_snap_bypass_exact = {
    pass: r.placement.x === 9.13 && r.placement.y === 13.77 &&
      r.guidesX.length === 0 && r.guidesY.length === 0,
    detail: r,
  };
}
{
  // A neighbour's left edge is nearer than the snap increment, so being flush
  // with it wins — and the guide that says so is drawn.
  const others = [{ x: 40, y: 0, w: 20, h: 20 }];
  const r = H.snapMove({ x: 40.4, y: 60, w: 10, h: 10 }, { snap: SNAP_ON, others });
  results.m077_snap_element_edge_wins = {
    pass: near(r.placement.x, 40) && r.guidesX.includes(40),
    detail: r,
  };
}
{
  // Element-edge magnetism works with grid snap switched OFF — the two are
  // independent, exactly as the design says.
  const others = [{ x: 40, y: 0, w: 20, h: 20 }];
  // y is deliberately clear of the page centre and thirds AND of the
  // sibling's own top/bottom, all of which are magnetic in their own right.
  const r = H.snapMove({ x: 40.4, y: 42.3, w: 10, h: 10 }, { snap: SNAP_OFF, others });
  results.m077_edge_magnetism_without_grid = {
    pass: near(r.placement.x, 40) && r.placement.y === 42.3,
    detail: r.placement,
  };
}
{
  // The moving box's RIGHT edge can be what sticks, not just its left.
  const others = [{ x: 40, y: 0, w: 20, h: 20 }];
  const r = H.snapMove({ x: 29.7, y: 60, w: 10, h: 10 }, { snap: SNAP_OFF, others });
  results.m077_snap_right_edge = {
    pass: near(r.placement.x, 30) && r.guidesX.includes(40),
    detail: r,
  };
}
{
  // Nothing near, no grid: the box stays exactly where it was put. Both
  // coordinates are clear of the page edges, centre and thirds.
  const r = H.snapMove({ x: 17.3, y: 20.4, w: 10, h: 10 }, { snap: SNAP_OFF, others: [] });
  results.m077_no_attraction_no_move = {
    pass: r.placement.x === 17.3 && r.placement.y === 20.4,
    detail: r.placement,
  };
}

// --- M-077: snapResize only attracts the edges the handle drags ---
{
  // An east drag must not pull the west edge along with it.
  const r = H.snapResize({ x: 3.1, y: 10, w: 30.2, h: 20 }, "e", { snap: SNAP_ON });
  results.m077_resize_east_leaves_x = {
    pass: r.placement.x === 3.1 && near(r.placement.x + r.placement.w, 33.3333),
    detail: r.placement,
  };
}
{
  // A west drag moves the origin and takes the width out of it, so the far
  // edge stays put.
  const before = { x: 9.1, y: 10, w: 30, h: 20 };
  const r = H.snapResize(before, "w", { snap: SNAP_ON });
  results.m077_resize_west_holds_far_edge = {
    pass: near(r.placement.x, 8.3333) &&
      near(r.placement.x + r.placement.w, before.x + before.w),
    detail: r.placement,
  };
}
{
  const r = H.snapResize({ x: 0, y: 0, w: 20, h: 26 }, "s", { snap: SNAP_ON });
  results.m077_resize_south = {
    pass: near(r.placement.h, 25) && r.placement.y === 0,
    detail: r.placement,
  };
}
{
  const r = H.snapResize({ x: 3.1, y: 10.4, w: 30.2, h: 20.7 }, "se", { snap: SNAP_ON, bypass: true });
  results.m077_resize_bypass_exact = {
    pass: eq(r.placement, { x: 3.1, y: 10.4, w: 30.2, h: 20.7 }),
    detail: r.placement,
  };
}

// --- L-051: auto-placement for the paths that have no pointer ---
{
  // Click-to-add on an empty page lands at the origin, which is where the old
  // grid put it — so this whole change is invisible at the palette.
  const r = H.autoPlace([], { w: 25, h: 12.5 }, SNAP_ON);
  results.l051_autoplace_empty = { pass: eq(r, { x: 0, y: 0, w: 25, h: 12.5 }), detail: r };
}
{
  // Row-major: a 25%-wide element with the first three cells taken skips past
  // the occupant rather than landing on top of it.
  const taken = [{ x: 0, y: 0, w: 25, h: 12.5 }];
  const r = H.autoPlace(taken, { w: 25, h: 12.5 }, SNAP_ON);
  results.l051_autoplace_skips_occupied = {
    pass: near(r.x, 25) && r.y === 0,
    detail: r,
  };
}
{
  // A full first row pushes the next add down a row, still row-major.
  const taken = [{ x: 0, y: 0, w: 100, h: 12.5 }];
  const r = H.autoPlace(taken, { w: 25, h: 12.5 }, SNAP_ON);
  results.l051_autoplace_next_row = { pass: near(r.y, 12.5) && r.x === 0, detail: r };
}
{
  // With snap off there are no cells to scan, so it falls back to the page
  // centre plus a cascade — the down-right nudge every design tool uses.
  const first = H.autoPlace([], { w: 20, h: 20 }, SNAP_OFF, 0);
  const second = H.autoPlace([], { w: 20, h: 20 }, SNAP_OFF, 1);
  results.l051_autoplace_cascade_when_snap_off = {
    pass: near(first.x, 40) && near(first.y, 40) &&
      second.x > first.x && second.y > first.y,
    detail: { first, second },
  };
}
{
  // Bigger than any free space -> centre fallback, never off the page.
  const taken = [{ x: 0, y: 0, w: 100, h: 100 }];
  const r = H.autoPlace(taken, { w: 50, h: 50 }, SNAP_ON);
  results.l051_autoplace_no_room_falls_back = {
    pass: r.x >= 0 && r.y >= 0 && r.x + r.w <= 100 && r.y + r.h <= 100,
    detail: r,
  };
}

// --- L-051: a drop inside a container adopts into it ---
{
  const page = {
    id: "p1", name: "P1", snap: SNAP_ON,
    elements: [{ id: "box", type: "group" }],
    layouts: [LANDSCAPE({ box: { x: 20, y: 20, w: 40, h: 40 } })],
  };
  // Fully inside -> adopted, and re-expressed relative to the container.
  const inside = H.resolveDropParent(page, { x: 30, y: 30, w: 10, h: 10 });
  results.l051_drop_adopts_when_contained = {
    pass: inside.parentId === "box" && eq(inside.relative, { x: 25, y: 25, w: 25, h: 25 }),
    detail: inside,
  };
}
{
  const page = {
    id: "p1", name: "P1", snap: SNAP_ON,
    elements: [{ id: "box", type: "group" }],
    layouts: [LANDSCAPE({ box: { x: 20, y: 20, w: 40, h: 40 } })],
  };
  // Half in, half out -> stays a page-level peer. Conservative on purpose.
  const partial = H.resolveDropParent(page, { x: 55, y: 30, w: 20, h: 10 });
  results.l051_drop_partial_overlap_not_adopted = {
    pass: partial.parentId === null && eq(partial.relative, { x: 55, y: 30, w: 20, h: 10 }),
    detail: partial,
  };
}
{
  const page = {
    id: "p1", name: "P1", snap: SNAP_ON,
    elements: [{ id: "outer", type: "group" }, { id: "inner", type: "group" }],
    layouts: [LANDSCAPE({
      outer: { x: 0, y: 0, w: 80, h: 80 },
      inner: { x: 10, y: 10, w: 40, h: 40 },
    })],
  };
  // Nested containers -> the innermost (smallest) wins.
  const r = H.resolveDropParent(page, { x: 20, y: 20, w: 10, h: 10 });
  results.l051_drop_innermost_container_wins = { pass: r.parentId === "inner", detail: r };
}

// --- L-051: the 44px touch minimum, as advice rather than a clamp ---
{
  // As a runtime clamp this used to override small percentage heights and
  // shove elements out of their boxes into overlap on every touch panel.
  const small = H.touchTargetWarning({ x: 0, y: 0, w: 2, h: 2 });
  const fine = H.touchTargetWarning({ x: 0, y: 0, w: 25, h: 25 });
  results.l051_touch_warning = {
    pass: !!small && small.axis === "both" && small.widthPx === 26 && fine === null,
    detail: { small, fine },
  };
}
{
  // A child is measured against its CONTAINER's pixels: half a container that
  // is itself a quarter of the page is an eighth of the panel.
  const inContainer = H.touchTargetWarning({ x: 0, y: 0, w: 20, h: 50 }, { width: 200, height: 100 });
  results.l051_touch_warning_container_relative = {
    pass: !!inContainer && inContainer.axis === "width" && inContainer.widthPx === 40,
    detail: inContainer,
  };
}

// --- H-039: duplicateElementInPage avoids reserved (master) ids ---
{
  const pages = [
    {
      id: "p1",
      snap: SNAP, layouts: [LANDSCAPE()],
      elements: [
        { id: "button_1", type: "button", style: {}, bindings: {} },
      ],
    },
  ];
  const withoutReserved = H.duplicateElementInPage(pages, "p1", "button_1");
  const noResId = withoutReserved[0].elements[1].id;
  // master "button_2" reserved → the duplicate must skip to button_3.
  const withReserved = H.duplicateElementInPage(pages, "p1", "button_1", ["button_2"]);
  const resId = withReserved[0].elements[1].id;
  results.h039_dup_reserved_skips_master = {
    pass: noResId === "button_2" && resId === "button_3",
    detail: { noResId, resId },
  };
}

// --- L-052: renameElement preserves untouched-scope array identity ---
function makeProject(macroKey) {
  return {
    pages: [
      {
        id: "p1",
        snap: SNAP, layouts: [LANDSCAPE()],
        elements: [{ id: "btn", type: "button", style: {}, bindings: {} }],
      },
    ],
    masters: [],
    macros: [{ id: "m1", name: "M1", steps: [{ action: "state.set", key: macroKey, value: 1 }] }],
    variables: [{ name: "v1", source_key: "device.x.power" }],
    scripts: [],
  };
}
{
  // Macro/var don't reference btn → those arrays come back by reference, while
  // pages (the renamed element lives there) is a fresh array.
  const p = makeProject("var.unrelated");
  const r = H.renameElement(p.pages, p.masters, p.macros, p.variables, p.scripts, "btn", "btn2");
  results.l052_rename_preserves_untouched = {
    pass:
      r.macros === p.macros &&
      r.variables === p.variables &&
      r.master_elements === p.masters &&
      r.pages !== p.pages &&
      r.pages[0].elements[0].id === "btn2",
    detail: {
      macrosSame: r.macros === p.macros,
      varsSame: r.variables === p.variables,
      mastersSame: r.master_elements === p.masters,
      pagesChanged: r.pages !== p.pages,
      newId: r.pages[0].elements[0].id,
    },
  };
}
{
  // A macro that DOES reference ui.btn.* must produce a fresh macros array, so
  // the guard isn't trivially always-true.
  const p = makeProject("ui.btn.pressed");
  const r = H.renameElement(p.pages, p.masters, p.macros, p.variables, p.scripts, "btn", "btn2");
  const rewritten = r.macros[0].steps[0].key;
  results.l052_rename_rewrites_referencing = {
    pass: r.macros !== p.macros && rewritten === "ui.btn2.pressed",
    detail: { macrosChanged: r.macros !== p.macros, rewritten },
  };
}

// --- H-086: validateProject handles do.<interaction> action lists ---
function makeValidationProject(elements) {
  return {
    ui: {
      pages: [{ id: "p1", name: "Page 1", snap: SNAP, layouts: [LANDSCAPE()], elements }],
      master_elements: [],
      settings: {},
    },
    devices: [{ id: "real_dev" }],
    macros: [{ id: "real_macro", name: "M", steps: [] }],
  };
}
{
  // Array-shaped do.press binding to a deleted device must be flagged.
  const proj = makeValidationProject([
    { id: "b1", type: "button", style: {}, bindings: { do: { press: [{ action: "device.command", device: "ghost_dev", command: "go" }] } } },
  ]);
  const issues = H.validateProject(proj).filter((i) => i.severity === "error");
  results.h086_validate_array_device = {
    pass: issues.length === 1 && /ghost_dev/.test(issues[0].message),
    detail: issues,
  };
}
{
  // Second action in the array is checked too (navigate to deleted page).
  const proj = makeValidationProject([
    { id: "b1", type: "button", style: {}, bindings: { do: { press: [{ action: "device.command", device: "real_dev", command: "go" }, { action: "navigate", page: "gone_page" }] } } },
  ]);
  const issues = H.validateProject(proj).filter((i) => i.severity === "error");
  results.h086_validate_array_navigate = {
    pass: issues.length === 1 && /gone_page/.test(issues[0].message),
    detail: issues,
  };
}
{
  // do.change: array-shaped macro action to a deleted macro.
  const proj = makeValidationProject([
    { id: "s1", type: "select", style: {}, bindings: { do: { change: [{ action: "macro", macro: "ghost_macro" }] } } },
  ]);
  const issues = H.validateProject(proj).filter((i) => i.severity === "error");
  results.h086_validate_array_change_macro = {
    pass: issues.length === 1 && /ghost_macro/.test(issues[0].message),
    detail: issues,
  };
}
{
  // A do.<interaction> holding a single action object (not an array) is still validated.
  const proj = makeValidationProject([
    { id: "b1", type: "button", style: {}, bindings: { do: { press: { action: "device.command", device: "ghost_dev", command: "go" } } } },
  ]);
  const issues = H.validateProject(proj).filter((i) => i.severity === "error");
  results.h086_validate_legacy_object = {
    pass: issues.length === 1 && /ghost_dev/.test(issues[0].message),
    detail: issues,
  };
}
{
  // Valid references in do action lists produce NO false positives.
  const proj = makeValidationProject([
    { id: "b1", type: "button", style: {}, bindings: { do: { press: [{ action: "device.command", device: "real_dev", command: "go" }, { action: "macro", macro: "real_macro" }] } } },
  ]);
  const issues = H.validateProject(proj).filter((i) => i.severity === "error");
  results.h086_validate_valid_refs_pass = { pass: issues.length === 0, detail: issues };
}

// --- H-086: removePage scrubs navigate actions in do action lists ---
{
  const pages = [
    {
      id: "p1", name: "P1", snap: SNAP, layouts: [LANDSCAPE()],
      elements: [
        {
          id: "b1", type: "button", style: {},
          bindings: {
            do: {
              press: [{ action: "navigate", page: "p2" }, { action: "device.command", device: "d1", command: "go" }],
              release: [{ action: "navigate", page: "p2" }],
              hold: { action: "navigate", page: "p2" },  // single-object shape
            },
          },
        },
      ],
    },
    { id: "p2", name: "P2", snap: SNAP, layouts: [LANDSCAPE()], elements: [] },
  ];
  const after = H.removePage(pages, "p2");
  const d = after[0].elements[0].bindings.do;
  results.h086_removepage_scrubs_arrays = {
    pass:
      !!d && Array.isArray(d.press) && d.press.length === 1 && d.press[0].action === "device.command" &&
      !("release" in d) && !("hold" in d),
    detail: d,
  };
}

// --- M-143: duplicate rewrites self-referencing ui.<id> bindings ---
{
  const pages = [
    {
      id: "p1", name: "P1", snap: SNAP, layouts: [LANDSCAPE()],
      elements: [
        {
          id: "btn_x", type: "button", style: {},
          bindings: { show: { look: { source: "state", key: "ui.btn_x.value", condition: { equals: true }, style_active: {}, style_inactive: {} } } },
        },
      ],
    },
  ];
  const after = H.duplicateElementInPage(pages, "p1", "btn_x");
  const dup = after[0].elements[1];
  const orig = after[0].elements[0];
  results.m143_duplicate_rewrites_self_ref = {
    pass: dup.id !== "btn_x" && dup.bindings.show.look.key === `ui.${dup.id}.value` &&
      orig.bindings.show.look.key === "ui.btn_x.value",
    detail: { dupId: dup.id, dupKey: dup.bindings.show.look.key, origKey: orig.bindings.show.look.key },
  };
}
{
  // duplicatePage rewrites self-refs AND sibling refs to the copied siblings.
  const pages = [
    {
      id: "p1", name: "P1", snap: SNAP, layouts: [LANDSCAPE()],
      elements: [
        { id: "btn_a", type: "button", style: {}, bindings: { show: { look: { source: "state", key: "ui.btn_a.value", condition: { equals: true }, style_active: {}, style_inactive: {} } } } },
        { id: "lbl_b", type: "label", style: {}, bindings: { show: { value: { source: "state", key: "ui.btn_a.value" } } } },
      ],
    },
  ];
  const after = H.duplicatePage(pages, "p1");
  const copy = after[1];
  const aCopy = copy.elements[0];
  const bCopy = copy.elements[1];
  results.m143_duplicate_page_rewrites_sibling_refs = {
    pass: aCopy.bindings.show.look.key === `ui.${aCopy.id}.value` &&
      bCopy.bindings.show.value.key === `ui.${aCopy.id}.value` &&
      pages[0].elements[1].bindings.show.value.key === "ui.btn_a.value",
    detail: { aCopyId: aCopy.id, aKey: aCopy.bindings.show.look.key, bKey: bCopy.bindings.show.value.key },
  };
}
{
  // duplicatePage respects reserved (master) ids when naming copies.
  const pages = [
    { id: "p1", name: "P1", snap: SNAP, layouts: [LANDSCAPE()], elements: [
      { id: "btn_a", type: "button", style: {}, bindings: {} },
    ] },
  ];
  const after = H.duplicatePage(pages, "p1", ["button_p1_copy_1"]);
  const copyEl = after[1].elements[0];
  results.m143_duplicate_page_respects_reserved = {
    pass: copyEl.id === "button_p1_copy_2",
    detail: copyEl.id,
  };
}

// --- M-144: promote/demote rename on ui.<id> namespace collision ---
{
  // Demote onto a page that already has an element with the master's id.
  const masters = [
    { id: "shared_btn", type: "button", pages: "*", style: {}, bindings: { show: { look: { source: "state", key: "ui.shared_btn.value", condition: { equals: true }, style_active: {}, style_inactive: {} } } } },
  ];
  const pages = [
    { id: "p1", name: "P1", snap: SNAP, layouts: [LANDSCAPE()], elements: [
      { id: "shared_btn", type: "button", style: {}, bindings: {} },
    ] },
  ];
  const r = H.demoteFromMaster(pages, masters, "shared_btn", "p1");
  const els = r.pages[0].elements;
  const demoted = els[1];
  results.m144_demote_collision_renamed = {
    pass: els.length === 2 && demoted.id !== "shared_btn" &&
      demoted.bindings.show.look.key === `ui.${demoted.id}.value` &&
      r.masterElements.length === 0,
    detail: { ids: els.map((e) => e.id), key: demoted.bindings.show.look.key },
  };
}
{
  // No collision -> id is kept.
  const masters = [{ id: "solo_btn", type: "button", pages: "*", style: {}, bindings: {} }];
  const pages = [{ id: "p1", name: "P1", snap: SNAP, layouts: [LANDSCAPE()], elements: [] }];
  const r = H.demoteFromMaster(pages, masters, "solo_btn", "p1");
  results.m144_demote_no_collision_keeps_id = {
    pass: r.pages[0].elements.length === 1 && r.pages[0].elements[0].id === "solo_btn",
    detail: r.pages[0].elements.map((e) => e.id),
  };
}
{
  // Promote when a master already holds the id -> promoted copy renamed.
  const masters = [{ id: "dup_btn", type: "button", pages: "*", style: {}, bindings: {} }];
  const pages = [
    { id: "p1", name: "P1", snap: SNAP, layouts: [LANDSCAPE()], elements: [
      { id: "dup_btn", type: "button", style: {}, bindings: { show: { look: { source: "state", key: "ui.dup_btn.value", condition: { equals: true }, style_active: {}, style_inactive: {} } } } },
    ] },
  ];
  const r = H.promoteToMaster(pages, masters, "p1", "dup_btn");
  const promoted = r.masterElements[1];
  results.m144_promote_collision_renamed = {
    pass: r.masterElements.length === 2 && promoted.id !== "dup_btn" &&
      promoted.bindings.show.look.key === `ui.${promoted.id}.value`,
    detail: { ids: r.masterElements.map((m) => m.id), key: promoted.bindings.show.look.key },
  };
}
{
  // Promote without collision keeps the id.
  const pages = [
    { id: "p1", name: "P1", snap: SNAP, layouts: [LANDSCAPE()], elements: [
      { id: "lone_btn", type: "button", style: {}, bindings: {} },
    ] },
  ];
  const r = H.promoteToMaster(pages, [], "p1", "lone_btn");
  results.m144_promote_no_collision_keeps_id = {
    pass: r.masterElements.length === 1 && r.masterElements[0].id === "lone_btn",
    detail: r.masterElements.map((m) => m.id),
  };
}

// --- L-087: validateProject recurses into value_map per-option actions ---
{
  const proj = makeValidationProject([
    {
      id: "s1", type: "select", style: {},
      bindings: {
        do: {
          change: [{
            action: "value_map",
            map: {
              a: { action: "device.command", device: "ghost_dev", command: "go" },
              b: { action: "macro", macro: "ghost_macro" },
              c: { action: "value_map", map: { d: { action: "macro", macro: "ghost_nested" } } },
              e: { action: "device.command", device: "real_dev", command: "ok" },
            },
          }],
        },
      },
    },
  ]);
  const issues = H.validateProject(proj).filter((i) => i.severity === "error");
  results.l087_value_map_recursion = {
    pass: issues.length === 3 &&
      issues.some((i) => /ghost_dev/.test(i.message)) &&
      issues.some((i) => /ghost_macro/.test(i.message)) &&
      issues.some((i) => /ghost_nested/.test(i.message)),
    detail: issues,
  };
}

// --- L-088: findOutOfBoundsIds flags boxes hanging outside their parent ---
{
  const page = {
    id: "p1", name: "P1", snap: SNAP,
    elements: [
      { id: "ok", type: "button" },
      { id: "off_right", type: "button" },
      { id: "off_bottom", type: "button" },
      { id: "edge_fit", type: "button" },
      // A child is a percentage of its CONTAINER, so the same 0..100 test
      // covers it -- only the box it is measured against changes.
      { id: "kid_out", type: "button", parent: "box" },
      { id: "kid_in", type: "button", parent: "box" },
      { id: "box", type: "group" },
    ],
    layouts: [LANDSCAPE({
      ok: { x: 0, y: 0, w: 25, h: 25 },
      off_right: { x: 85, y: 0, w: 25, h: 12.5 },
      off_bottom: { x: 0, y: 90, w: 10, h: 25 },
      edge_fit: { x: 75, y: 75, w: 25, h: 25 },
      kid_out: { x: 80, y: 10, w: 40, h: 20 },
      kid_in: { x: 10, y: 10, w: 40, h: 20 },
      box: { x: 0, y: 0, w: 50, h: 50 },
    })],
  };
  const flagged = [...H.findOutOfBoundsIds(page)].sort();
  results.l088_out_of_bounds_ids = {
    pass: eq(flagged, ["kid_out", "off_bottom", "off_right"]),
    detail: flagged,
  };
}

// --- M-231: changing the snap increment moves NOTHING ---
// The old clampElementsToGrid rewrote every element to fit a shrinking grid.
// It has no successor, and this is the assertion that says so: the increment
// is a ruler now, so it can change to anything -- or switch off -- and the
// page is untouched. This is the regression that used to happen silently.
{
  const before = {
    id: "p1", name: "P1", snap: SNAP,
    elements: [{ id: "a", type: "button" }, { id: "b", type: "button" }],
    layouts: [LANDSCAPE({
      a: { x: 33.3333, y: 50, w: 33.3333, h: 25 },
      b: { x: 0, y: 0, w: 16.6667, h: 25 },
    })],
  };
  const denser = { ...before, snap: { enabled: true, x: 100 / 5, y: 100 / 3 } };
  const off = { ...before, snap: { enabled: false, x: 100 / 12, y: 100 / 8 } };
  results.m231_snap_change_moves_nothing = {
    pass:
      eq(H.getPlacement(denser, "a"), H.getPlacement(before, "a")) &&
      eq(H.getPlacement(denser, "b"), H.getPlacement(before, "b")) &&
      eq(H.getPlacement(off, "a"), H.getPlacement(before, "a")),
    detail: { a: H.getPlacement(denser, "a"), b: H.getPlacement(denser, "b") },
  };
}
{
  // clampElementsToGrid is gone on purpose; nothing may quietly resurrect it.
  results.m231_no_clamp_successor = {
    pass: H.clampElementsToGrid === undefined && H.clampOriginToGrid === undefined,
    detail: {
      clampElementsToGrid: typeof H.clampElementsToGrid,
      clampOriginToGrid: typeof H.clampOriginToGrid,
    },
  };
}

// --- L-142: page-delete scrub returns the ORIGINAL arrays when untouched ---
{
  // Nothing references the deleted page -> both arrays come back by
  // identity, so the changed-only undo snapshot actually skips them (the
  // old .map() always allocated, making the caller's !== guard dead code).
  const pages = [{ id: "p1", elements: [] }, { id: "p2", elements: [] }];
  const masters = [{ id: "m1", pages: "*", }];
  const macros = [{ id: "mac1", triggers: [{ conditions: [{ key: "var.x", value: "1" }] }] }];
  const r = H.removePageAndScrubRefs(pages, "p2", masters, macros);
  results.l142_scrub_identity_when_untouched = {
    pass: r.masterElements === masters && r.macros === macros,
    detail: { mastersSame: r.masterElements === masters, macrosSame: r.macros === macros },
  };
}
{
  // References exist -> new scrubbed arrays (the guard must still detect
  // real changes).
  const pages = [{ id: "p1", elements: [] }, { id: "p2", elements: [] }];
  const masters = [{ id: "m1", pages: ["p1", "p2"] }];
  const macros = [{ id: "mac1", triggers: [{ conditions: [{ key: "system.current_page", value: "p2" }] }] }];
  const r = H.removePageAndScrubRefs(pages, "p2", masters, macros);
  results.l142_scrub_new_when_changed = {
    pass:
      r.masterElements !== masters && eq(r.masterElements[0].pages, ["p1"]) &&
      r.macros !== macros && eq(r.macros[0].triggers[0].conditions, []),
    detail: r,
  };
}

// --- L-146 / L-147: Broken/Incomplete checks match what the runtime runs ---
const REF_IDS = {
  deviceIds: new Set(["proj"]),
  macroIds: new Set(["all_on"]),
  pageIds: new Set(["main"]),
};
const statusScenario = (name, fnBody) => {
  try {
    results[name] = fnBody();
  } catch (e) {
    results[name] = { pass: false, detail: String(e) };
  }
};
statusScenario("l147_page_alias_dangling", () => {
  // engine.py accepts "page" as an alias for "navigate"; a dead page ref via
  // the alias must badge Broken like the canonical spelling does.
  const d = H.actionDanglingRef({ action: "page", page: "ghost" }, REF_IDS);
  return { pass: typeof d === "string" && d.includes("ghost"), detail: d };
});
statusScenario("l147_value_map_dangling_descends", () => {
  // value_map branches run real actions — a dangling device ref inside an
  // option branch (array or single-dict form) must surface.
  const arr = H.actionDanglingRef(
    { action: "value_map", map: { hdmi1: [{ action: "device.command", device: "ghost_dev", command: "input" }] } },
    REF_IDS,
  );
  const single = H.actionDanglingRef(
    { action: "value_map", map: { hdmi1: { action: "macro", macro: "ghost_macro" } } },
    REF_IDS,
  );
  return {
    pass: !!arr && arr.includes("ghost_dev") && !!single && single.includes("ghost_macro"),
    detail: { arr, single },
  };
});
statusScenario("l146_script_call_incomplete", () => {
  // engine.py only emits script.call when `function` is set — an empty
  // function must badge Incomplete, a filled one must not.
  const empty = H.actionIncompleteCheck({ action: "script.call", function: "" });
  const filled = H.actionIncompleteCheck({ action: "script.call", function: "do_thing" });
  return { pass: empty === true && filled === false, detail: { empty, filled } };
});
statusScenario("l147_page_alias_incomplete", () => {
  return {
    pass: H.actionIncompleteCheck({ action: "page" }) === true &&
      H.actionIncompleteCheck({ action: "page", page: "main" }) === false,
    detail: null,
  };
});
statusScenario("l147_value_map_branch_incomplete", () => {
  // An option branch missing its command is half-finished even though the
  // map itself is non-empty.
  const broken = H.actionIncompleteCheck({ action: "value_map", map: { a: { action: "device.command", device: "proj" } } });
  const ok = H.actionIncompleteCheck({ action: "value_map", map: { a: { action: "device.command", device: "proj", command: "input" } } });
  const emptyMap = H.actionIncompleteCheck({ action: "value_map", map: {} });
  return { pass: broken === true && ok === false && emptyMap === true, detail: { broken, ok, emptyMap } };
});
statusScenario("l147_valid_actions_clean", () => {
  // Guard: fully-configured actions with live refs stay unbadged.
  const checks = [
    H.actionDanglingRef({ action: "navigate", page: "main" }, REF_IDS) === null,
    H.actionDanglingRef({ action: "device.command", device: "proj", command: "power_on" }, REF_IDS) === null,
    H.actionIncompleteCheck({ action: "navigate", page: "main" }) === false,
    H.actionIncompleteCheck({ action: "macro", macro: "all_on" }) === false,
  ];
  return { pass: checks.every(Boolean), detail: checks };
});

// --- M-306: swapElementsInOrder swaps by id (visible-neighbour z-order move) ---
// The OutlinePanel z-order buttons pass the moving element's VISIBLE (filtered)
// neighbour, so a reorder swaps what the user sees adjacent — not a hidden
// full-list neighbour, which the old direction-based adjacent move did.
const pageWith = (ids) => [{ id: "pg", elements: ids.map((id) => ({ id })) }];
const idsOf = (pages) => pages[0].elements.map((e) => e.id);
{
  // Adjacent (unfiltered) case: B up past A -> [B, A, C].
  const r = H.swapElementsInOrder(pageWith(["A", "B", "C"]), "pg", "B", "A");
  results.m306_swap_adjacent = { pass: eq(idsOf(r), ["B", "A", "C"]), detail: idsOf(r) };
}
{
  // THE fix: x is hidden by a search filter between A and B in the full list.
  // Moving A against its VISIBLE neighbour B swaps A<->B, so x keeps its slot.
  // The OLD adjacent move ("down") would have swapped A<->x instead.
  const full = ["A", "x", "B"];
  const oldAdjacent = (() => { const e = [...full]; [e[0], e[1]] = [e[1], e[0]]; return e; })();
  const r = H.swapElementsInOrder(pageWith(full), "pg", "A", "B");
  results.m306_swap_visible_neighbor_skips_hidden = {
    pass: eq(oldAdjacent, ["x", "A", "B"]) && eq(idsOf(r), ["B", "x", "A"]),
    detail: { oldAdjacent, now: idsOf(r) },
  };
}
{
  // No-op guards: unknown neighbour or same id leaves order untouched.
  const same = H.swapElementsInOrder(pageWith(["A", "B"]), "pg", "A", "A");
  const missing = H.swapElementsInOrder(pageWith(["A", "B"]), "pg", "A", "ghost");
  results.m306_swap_noop_unknown_or_same = {
    pass: eq(idsOf(same), ["A", "B"]) && eq(idsOf(missing), ["A", "B"]),
    detail: { same: idsOf(same), missing: idsOf(missing) },
  };
}
{
  // Only the named page is touched.
  const pages = [
    { id: "pg", elements: [{ id: "A" }, { id: "B" }] },
    { id: "other", elements: [{ id: "A" }, { id: "B" }] },
  ];
  const r = H.swapElementsInOrder(pages, "pg", "A", "B");
  results.m306_swap_scoped_to_page = {
    pass:
      eq(r[0].elements.map((e) => e.id), ["B", "A"]) &&
      eq(r[1].elements.map((e) => e.id), ["A", "B"]),
    detail: { pg: r[0].elements.map((e) => e.id), other: r[1].elements.map((e) => e.id) },
  };
}

process.stdout.write(JSON.stringify(results));
