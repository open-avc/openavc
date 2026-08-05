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
{
  // The page thirds are NOT magnetic. On an otherwise empty page with snap
  // off they read as snapping to nothing — the phantom pull this fixes: a
  // release with the centre 0.67% from the 33.33 third used to land ON it.
  const third = H.snapMove(
    { x: 29, y: 20.4, w: 10, h: 10 },  // centre 34, edges 29/39 — near only the third
    { snap: SNAP_OFF, others: [] },
  );
  // Page edges and centre still attract: the same release near the centre line
  // sticks, and the guide that says so is drawn.
  const centre = H.snapMove(
    { x: 45.4, y: 20.4, w: 10, h: 10 },  // centre 50.4
    { snap: SNAP_OFF, others: [] },
  );
  results.m077_thirds_not_magnetic = {
    pass:
      third.placement.x === 29 && third.guidesX.length === 0 &&
      near(centre.placement.x, 45) && centre.guidesX.includes(50),
    detail: { third: third.placement, centre: centre.placement },
  };
}

// --- Palette drag-in lands exactly like a move ---
{
  // paletteDragPlacement is the ONE function both the live preview and the
  // drop call: the element centred under the pointer, then snapped like any
  // move. Parity here is what makes the footprint the guides show BE the
  // landing spot — with the grid, with magnetism, and with Alt held.
  const size = { w: 25, h: 25 };
  const gridded = H.paletteDragPlacement({ x: 50.4, y: 42 }, size, { snap: SNAP_ON, others: [] });
  const viaMove = H.snapMove({ x: 50.4 - 12.5, y: 42 - 12.5, w: 25, h: 25 }, { snap: SNAP_ON, others: [] });
  const bypassed = H.paletteDragPlacement({ x: 30.2, y: 61.7 }, size, { snap: SNAP_ON, bypass: true, others: [] });
  results.palette_drop_is_a_move = {
    pass:
      eq(gridded, viaMove) &&
      near(bypassed.placement.x + size.w / 2, 30.2) &&
      near(bypassed.placement.y + size.h / 2, 61.7) &&
      bypassed.guidesX.length === 0,
    detail: { gridded, viaMove, bypassed },
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
  // is itself a quarter of the page is an eighth of the panel. The height here
  // is deliberately clear of the threshold so this still discriminates ONE
  // axis rather than reporting "both" and passing by accident.
  const inContainer = H.touchTargetWarning({ x: 0, y: 0, w: 20, h: 80 }, { width: 200, height: 100 });
  results.l051_touch_warning_container_relative = {
    pass: !!inContainer && inContainer.axis === "width" && inContainer.widthPx === 40,
    detail: inContainer,
  };
}
{
  // The rule is PHYSICAL. The pixel threshold is derived from it, so nobody can
  // move one and leave the other -- which is how the old 44px constant came to
  // claim 11.2mm while a real 10.1in panel delivered 7.5mm.
  const expectedPx = (H.TOUCH_MIN_MM / 25.4) * H.TOUCH_REFERENCE.pxPerInch;
  const derived = Math.abs(H.TOUCH_MIN_PX - expectedPx) < 1e-9;
  // A control exactly at the minimum must not warn; a hair under must.
  const atMin = (H.TOUCH_MIN_PX / H.TOUCH_REFERENCE.width) * 100;
  const ok = H.touchTargetWarning({ x: 0, y: 0, w: atMin, h: atMin * (H.TOUCH_REFERENCE.width / H.TOUCH_REFERENCE.height) });
  const under = H.touchTargetWarning({ x: 0, y: 0, w: atMin * 0.9, h: atMin * 0.9 });
  // And the millimetre it reports is the millimetre the rule is written in.
  const reported = under ? under.widthMm : null;
  const expectedMm = Math.round(((H.TOUCH_MIN_PX * 0.9) / H.TOUCH_REFERENCE.pxPerInch) * 25.4 * 10) / 10;
  results.l051_touch_minimum_is_physical = {
    pass: derived && ok === null && !!under && reported === expectedMm,
    detail: { TOUCH_MIN_MM: H.TOUCH_MIN_MM, TOUCH_MIN_PX: H.TOUCH_MIN_PX,
              ppi: H.TOUCH_REFERENCE.pxPerInch, atMinWarns: ok, reported, expectedMm },
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
    { id: "b1", type: "button", style: {}, bindings: { do: { press: [{ action: "device.command", device: "real_dev", command: "go" }, { action: "ui.navigate", page: "gone_page" }] } } },
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
              press: [{ action: "ui.navigate", page: "p2" }, { action: "device.command", device: "d1", command: "go" }],
              release: [{ action: "ui.navigate", page: "p2" }],
              hold: { action: "ui.navigate", page: "p2" },  // single-object shape
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
statusScenario("l147_navigate_one_spelling", () => {
  // One spelling: a panel binding's page move is "ui.navigate", the same word
  // the macro step and the WS frame use. The two retired spellings ("navigate"
  // and its "page" alias) are no longer panel actions, so a dead page ref
  // written either old way must NOT badge -- badging it would report a broken
  // reference inside an action the runtime will never execute, which points
  // the author at the page instead of at the actual problem, the action name.
  const canonical = H.actionDanglingRef({ action: "ui.navigate", page: "ghost" }, REF_IDS);
  const retired = H.actionDanglingRef({ action: "navigate", page: "ghost" }, REF_IDS);
  const alias = H.actionDanglingRef({ action: "page", page: "ghost" }, REF_IDS);
  return {
    pass: typeof canonical === "string" && canonical.includes("ghost")
      && retired === null && alias === null,
    detail: { canonical, retired, alias },
  };
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
statusScenario("l147_navigate_incomplete", () => {
  // A page move with no target is Incomplete; with one it is configured.
  return {
    pass: H.actionIncompleteCheck({ action: "ui.navigate" }) === true &&
      H.actionIncompleteCheck({ action: "ui.navigate", page: "main" }) === false,
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
    H.actionDanglingRef({ action: "ui.navigate", page: "main" }, REF_IDS) === null,
    H.actionDanglingRef({ action: "device.command", device: "proj", command: "power_on" }, REF_IDS) === null,
    H.actionIncompleteCheck({ action: "ui.navigate", page: "main" }) === false,
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

// --- A-101: the alignment toolkit reasons in PAGE space ---
// A child's stored percentages are of its container, so 20% wide means two
// different widths on screen depending on where the element lives. Comparing
// them raw lines elements up where the numbers agree instead of where the eye
// sees them, which is the wrong answer as soon as a marquee can sweep a
// container and a page-level control into one selection.

/** A page with a container at 10,10 40x40 and whatever else is passed in. */
const alignPage = (elements, placements) => ({
  id: "pg",
  elements,
  snap: SNAP,
  layouts: [LANDSCAPE(placements)],
});

{
  // The container sits at 10,10 40x40 of the page; its child at 50,50 50x50 of
  // the container is therefore 30,30 20x20 of the page.
  const page = alignPage(
    [{ id: "box", type: "group" }, { id: "kid", type: "button", parent: "box" }],
    { box: { x: 10, y: 10, w: 40, h: 40 }, kid: { x: 50, y: 50, w: 50, h: 50 } },
  );
  const abs = H.absolutePlacements(page);
  results.a101_absolute_flattens_container = {
    pass:
      eq(abs.box, { x: 10, y: 10, w: 40, h: 40 }) &&
      near(abs.kid.x, 30) && near(abs.kid.y, 30) &&
      near(abs.kid.w, 20) && near(abs.kid.h, 20),
    detail: abs,
  };
}
{
  // A parent that points at itself, or a pair pointing at each other, still has
  // to draw -- a hand-edited project must not hang the builder.
  const page = alignPage(
    [{ id: "a", type: "group", parent: "b" }, { id: "b", type: "group", parent: "a" }],
    { a: { x: 10, y: 10, w: 20, h: 20 }, b: { x: 5, y: 5, w: 50, h: 50 } },
  );
  const abs = H.absolutePlacements(page);
  results.a101_absolute_survives_parent_cycle = {
    pass: !!abs.a && !!abs.b && Number.isFinite(abs.a.x) && Number.isFinite(abs.b.x),
    detail: abs,
  };
}
{
  // Align-left across a container boundary: the child ends up at the same
  // SCREEN x as the page-level element, which means a different stored x.
  const page = alignPage(
    [
      { id: "box", type: "group" },
      { id: "kid", type: "button", parent: "box" },
      { id: "free", type: "button" },
    ],
    {
      box: { x: 10, y: 10, w: 40, h: 40 },
      kid: { x: 50, y: 50, w: 50, h: 50 },
      free: { x: 70, y: 10, w: 20, h: 10 },
    },
  );
  const out = H.alignElements([page], "pg", ["kid", "free"], "align-left");
  const abs = H.absolutePlacements(out[0]);
  const stored = out[0].layouts[0].placements;
  results.a101_align_left_across_container = {
    pass: near(abs.kid.x, abs.free.x) && near(abs.kid.x, 30) && !near(stored.kid.x, stored.free.x),
    detail: { absKid: abs.kid.x, absFree: abs.free.x, storedKid: stored.kid.x, storedFree: stored.free.x },
  };
}
{
  // Selecting a container AND its child then aligning must move the container
  // only: the child is a percentage OF it and already travels along. Writing
  // both moves the child twice.
  const page = alignPage(
    [
      { id: "box", type: "group" },
      { id: "kid", type: "button", parent: "box" },
      { id: "free", type: "button" },
    ],
    {
      box: { x: 10, y: 10, w: 40, h: 40 },
      kid: { x: 50, y: 50, w: 50, h: 50 },
      free: { x: 70, y: 70, w: 20, h: 10 },
    },
  );
  const kept = H.topmostSelection(page, ["box", "kid", "free"]);
  const out = H.alignElements([page], "pg", ["box", "kid", "free"], "align-left");
  const stored = out[0].layouts[0].placements;
  results.a101_topmost_selection_drops_children = {
    pass: eq(kept, ["box", "free"]) && eq(stored.kid, { x: 50, y: 50, w: 50, h: 50 }),
    detail: { kept, kid: stored.kid, box: stored.box },
  };
}
{
  // A locked element is a ruler, not a target: it anchors the bounding box and
  // does not move.
  const page = alignPage(
    [
      { id: "pinned", type: "group", locked: true },
      { id: "a", type: "button" },
      { id: "b", type: "button" },
    ],
    {
      pinned: { x: 5, y: 5, w: 20, h: 10 },
      a: { x: 40, y: 20, w: 20, h: 10 },
      b: { x: 60, y: 40, w: 20, h: 10 },
    },
  );
  const out = H.alignElements([page], "pg", ["pinned", "a", "b"], "align-left");
  const stored = out[0].layouts[0].placements;
  results.a101_locked_anchors_but_does_not_move = {
    pass: near(stored.pinned.x, 5) && near(stored.a.x, 5) && near(stored.b.x, 5),
    detail: stored,
  };
}

// --- A-102: distribute evens the GAPS, not the origins ---
// Spacing origins evenly is the obvious implementation and the wrong one: put a
// wide element next to a narrow one and the air between boxes comes out
// visibly uneven, because the eye measures the gap, not the corner-to-corner
// distance.
{
  const page = alignPage(
    [{ id: "a", type: "button" }, { id: "b", type: "button" }, { id: "c", type: "button" }],
    {
      a: { x: 0, y: 0, w: 10, h: 10 },
      b: { x: 20, y: 0, w: 40, h: 10 },
      c: { x: 90, y: 0, w: 10, h: 10 },
    },
  );
  const out = H.distributeElements([page], "pg", ["a", "b", "c"], "horizontal");
  const p = out[0].layouts[0].placements;
  // span 0..100 = 100, widths 10+40+10 = 60, so each of the two gaps is 20.
  const gap1 = p.b.x - (p.a.x + p.a.w);
  const gap2 = p.c.x - (p.b.x + p.b.w);
  // What the old origin-spacing did: b.x would land at 45, leaving gaps of
  // 35 and 5 -- the exact lopsidedness this fixes.
  results.a102_distribute_evens_gaps = {
    pass: near(gap1, 20) && near(gap2, 20) && near(p.b.x, 30),
    detail: { gap1, gap2, bx: p.b.x, oldOriginSpacingWouldBe: 45 },
  };
}
{
  // The outermost two never move -- that is what makes it a redistribution
  // rather than a re-layout.
  const page = alignPage(
    [{ id: "a", type: "button" }, { id: "b", type: "button" }, { id: "c", type: "button" }],
    {
      a: { x: 3, y: 0, w: 10, h: 10 },
      b: { x: 20, y: 0, w: 40, h: 10 },
      c: { x: 77, y: 0, w: 10, h: 10 },
    },
  );
  // Span is edge to edge -- 3 to 87 is 84, less 60 of element leaves 24 to
  // split, so b starts one 12-wide gap after a's far edge at 13. Passing the
  // ids out of order proves the helper sorts rather than trusting the caller.
  const out = H.distributeElements([page], "pg", ["c", "a", "b"], "horizontal");
  const p = out[0].layouts[0].placements;
  results.a102_distribute_pins_first_and_last = {
    pass: near(p.a.x, 3) && near(p.c.x, 77) && near(p.b.x, 25),
    detail: p,
  };
}
{
  // Vertical is the same arithmetic on the other axis, and x is untouched.
  const page = alignPage(
    [{ id: "a", type: "button" }, { id: "b", type: "button" }, { id: "c", type: "button" }],
    {
      a: { x: 5, y: 0, w: 10, h: 10 },
      b: { x: 5, y: 30, w: 10, h: 20 },
      c: { x: 5, y: 90, w: 10, h: 10 },
    },
  );
  const out = H.distributeElements([page], "pg", ["a", "b", "c"], "vertical");
  const p = out[0].layouts[0].placements;
  const gap1 = p.b.y - (p.a.y + p.a.h);
  const gap2 = p.c.y - (p.b.y + p.b.h);
  results.a102_distribute_vertical_evens_gaps = {
    pass: near(gap1, gap2) && near(gap1, 30) && near(p.b.x, 5),
    detail: { gap1, gap2, bx: p.b.x },
  };
}
{
  // Fewer than three has no middle to move, so it is a no-op rather than an
  // error -- the toolbar hides the button, but the helper must agree.
  const page = alignPage(
    [{ id: "a", type: "button" }, { id: "b", type: "button" }],
    { a: { x: 0, y: 0, w: 10, h: 10 }, b: { x: 50, y: 0, w: 10, h: 10 } },
  );
  const out = H.distributeElements([page], "pg", ["a", "b"], "horizontal");
  results.a102_distribute_needs_three = { pass: out[0] === page, detail: out[0] === page };
}

// --- A-103: match size copies the FIRST selected element's rendered box ---
// First, because that is the one whose numbers the Properties panel is showing.
{
  const page = alignPage(
    [{ id: "a", type: "button" }, { id: "b", type: "button" }, { id: "c", type: "button" }],
    {
      a: { x: 0, y: 0, w: 25, h: 12 },
      b: { x: 40, y: 0, w: 10, h: 30 },
      c: { x: 70, y: 0, w: 5, h: 5 },
    },
  );
  const w = H.matchSizeElements([page], "pg", ["a", "b", "c"], "match-width")[0]
    .layouts[0].placements;
  const h = H.matchSizeElements([page], "pg", ["a", "b", "c"], "match-height")[0]
    .layouts[0].placements;
  const both = H.matchSizeElements([page], "pg", ["a", "b", "c"], "match-both")[0]
    .layouts[0].placements;
  results.a103_match_width_height_both = {
    pass:
      near(w.b.w, 25) && near(w.b.h, 30) && near(w.c.w, 25) &&
      near(h.b.h, 12) && near(h.b.w, 10) &&
      near(both.c.w, 25) && near(both.c.h, 12) &&
      near(both.a.w, 25) && near(both.a.h, 12),
    detail: { width: w, height: h, both },
  };
}
{
  // Across a container the match is on RENDERED size: the child of a 40%-wide
  // container needs 62.5% of its parent to draw the same width as a 25%
  // page-level element (25 / 40 * 100).
  const page = alignPage(
    [
      { id: "free", type: "button" },
      { id: "box", type: "group" },
      { id: "kid", type: "button", parent: "box" },
    ],
    {
      free: { x: 0, y: 0, w: 25, h: 10 },
      box: { x: 50, y: 0, w: 40, h: 50 },
      kid: { x: 0, y: 0, w: 10, h: 10 },
    },
  );
  const out = H.matchSizeElements([page], "pg", ["free", "kid"], "match-width");
  const stored = out[0].layouts[0].placements;
  const abs = H.absolutePlacements(out[0]);
  results.a103_match_width_matches_what_is_drawn = {
    pass: near(stored.kid.w, 62.5) && near(abs.kid.w, 25),
    detail: { storedKidW: stored.kid.w, absKidW: abs.kid.w, absFreeW: abs.free.w },
  };
}
{
  // A locked element is still a valid source to match TO, and still never moves.
  const page = alignPage(
    [{ id: "a", type: "button" }, { id: "b", type: "button", locked: true }],
    { a: { x: 0, y: 0, w: 25, h: 12 }, b: { x: 40, y: 0, w: 10, h: 30 } },
  );
  const out = H.matchSizeElements([page], "pg", ["a", "b"], "match-both");
  results.a103_match_skips_locked_target = { pass: out[0] === page, detail: "unchanged" };
}

// --- A-104: the marquee selects what it TOUCHES ---
// Touched, not enclosed. Sweeping a band across a row of buttons should take
// the row; making an integrator lasso every control completely is the kind of
// precision a design tool exists to avoid.
{
  const page = alignPage(
    [
      { id: "box", type: "group" },
      { id: "kid", type: "button", parent: "box" },
      { id: "far", type: "button" },
    ],
    {
      box: { x: 10, y: 10, w: 40, h: 40 },
      kid: { x: 50, y: 50, w: 50, h: 50 },
      far: { x: 80, y: 80, w: 10, h: 10 },
    },
  );
  // A band clipping the container's top-left corner only.
  const grazed = H.elementsIntersectingRect(page, { x: 0, y: 0, w: 15, h: 15 });
  // A band over the child's page-space box (30,30 20x20) but not the far one.
  const overKid = H.elementsIntersectingRect(page, { x: 28, y: 28, w: 5, h: 5 });
  results.a104_marquee_selects_what_it_touches = {
    pass:
      eq(grazed, ["box"]) &&
      overKid.includes("kid") && overKid.includes("box") && !overKid.includes("far"),
    detail: { grazed, overKid },
  };
}
{
  // A band drawn right-to-left / bottom-to-top is the same band.
  const page = alignPage(
    [{ id: "a", type: "button" }],
    { a: { x: 40, y: 40, w: 10, h: 10 } },
  );
  const forward = H.elementsIntersectingRect(page, { x: 30, y: 30, w: 30, h: 30 });
  const backward = H.elementsIntersectingRect(page, { x: 60, y: 60, w: -30, h: -30 });
  results.a104_marquee_normalises_direction = {
    pass: eq(forward, ["a"]) && eq(backward, ["a"]),
    detail: { forward, backward },
  };
}
{
  // Touching edge-to-edge is not touching: a band that stops exactly where an
  // element starts leaves it alone, so a drag in the gap between two rows
  // picks up neither.
  const page = alignPage(
    [{ id: "a", type: "button" }],
    { a: { x: 40, y: 40, w: 10, h: 10 } },
  );
  const flush = H.elementsIntersectingRect(page, { x: 20, y: 20, w: 20, h: 20 });
  results.a104_marquee_excludes_flush_edge = { pass: eq(flush, []), detail: flush };
}

// --- A-105: lock is a project field, not session state ---
// A lock that evaporates on reload is worse than none, because you only find
// out after something has moved.
{
  const page = alignPage(
    [
      { id: "a", type: "button", locked: true },
      { id: "b", type: "button" },
      { id: "c", type: "group" },
    ],
    {
      a: { x: 0, y: 0, w: 10, h: 10 },
      b: { x: 20, y: 0, w: 10, h: 10 },
      c: { x: 40, y: 0, w: 10, h: 10 },
    },
  );
  const masters = [{ id: "m1", type: "label", locked: true }, { id: "m2", type: "label" }];
  const ids = H.lockedIdsFor(page, masters);
  results.a105_locked_ids_span_page_and_masters = {
    pass: ids.has("a") && ids.has("m1") && !ids.has("b") && !ids.has("m2") && ids.size === 2,
    detail: [...ids],
  };
}
{
  // An element with no `locked` key at all -- every project saved before the
  // field existed -- reads as unlocked rather than as undefined-and-therefore-
  // truthy-somewhere.
  const page = alignPage([{ id: "a", type: "button" }], { a: { x: 0, y: 0, w: 10, h: 10 } });
  results.a105_absent_lock_reads_unlocked = {
    pass: H.lockedIdsFor(page, undefined).size === 0 && H.lockedIdsFor(undefined, undefined).size === 0,
    detail: [...H.lockedIdsFor(page, undefined)],
  };
}

// --- A-106: geometry writes go to the layout being authored ---
// Every helper takes an explicit layout id rather than assuming the primary,
// because the layout switcher is coming and a portrait edit that silently
// rewrote the landscape arrangement would be a very quiet bug.
{
  const page = {
    id: "pg",
    elements: [{ id: "a", type: "button" }, { id: "b", type: "button" }],
    snap: SNAP,
    layouts: [
      { id: "landscape", orientation: "landscape", primary: true, placements: {
        a: { x: 10, y: 0, w: 10, h: 10 }, b: { x: 60, y: 0, w: 10, h: 10 } }, hidden: [] },
      { id: "portrait", orientation: "portrait", inherits: "landscape", placements: {
        a: { x: 80, y: 40, w: 10, h: 10 } }, hidden: [] },
    ],
  };
  const out = H.alignElements([page], "pg", ["a", "b"], "align-left", "portrait");
  const landscape = out[0].layouts[0].placements;
  const portrait = out[0].layouts[1].placements;
  results.a106_align_writes_named_layout_only = {
    pass:
      near(landscape.a.x, 10) && near(landscape.b.x, 60) &&
      near(portrait.a.x, 60) && near(portrait.b.x, 60),
    detail: { landscape, portrait },
  };
}
{
  // The inherited box is what a variant edit is measured from: `b` has no
  // portrait entry, so its landscape 60,0 is the one that gets read.
  const page = {
    id: "pg",
    elements: [{ id: "a", type: "button" }, { id: "b", type: "button" }],
    snap: SNAP,
    layouts: [
      { id: "landscape", orientation: "landscape", primary: true, placements: {
        a: { x: 10, y: 0, w: 10, h: 10 }, b: { x: 60, y: 20, w: 30, h: 10 } }, hidden: [] },
      { id: "portrait", orientation: "portrait", inherits: "landscape", placements: {
        a: { x: 80, y: 40, w: 10, h: 10 } }, hidden: [] },
    ],
  };
  const abs = H.absolutePlacements(page, "portrait");
  results.a106_variant_reads_through_inherits = {
    pass: near(abs.a.x, 80) && near(abs.b.x, 60) && near(abs.b.w, 30),
    detail: abs,
  };
}

// --- C-107: the container tree the Outline draws ---
// Containers are real parents, so the panel that lists them has to be a tree or
// the hierarchy is invisible in the one place it should be obvious.
{
  const page = alignPage(
    [
      { id: "outer", type: "group" },
      { id: "inner", type: "group", parent: "outer" },
      { id: "deep", type: "button", parent: "inner" },
      { id: "loose", type: "label" },
    ],
    {
      outer: { x: 0, y: 0, w: 50, h: 50 },
      inner: { x: 0, y: 0, w: 50, h: 50 },
      deep: { x: 0, y: 0, w: 50, h: 50 },
      loose: { x: 60, y: 0, w: 10, h: 10 },
    },
  );
  const rows = H.outlineRows(page.elements);
  results.c107_tree_indents_by_depth = {
    pass: eq(
      rows.map((r) => [r.id, r.depth]),
      [["outer", 0], ["inner", 1], ["deep", 2], ["loose", 0]],
    ),
    detail: rows,
  };
}
{
  // A folded container hides its whole subtree, however deep, and says so.
  const page = alignPage(
    [
      { id: "outer", type: "group" },
      { id: "inner", type: "group", parent: "outer" },
      { id: "deep", type: "button", parent: "inner" },
      { id: "loose", type: "label" },
    ],
    {
      outer: { x: 0, y: 0, w: 50, h: 50 },
      inner: { x: 0, y: 0, w: 50, h: 50 },
      deep: { x: 0, y: 0, w: 50, h: 50 },
      loose: { x: 60, y: 0, w: 10, h: 10 },
    },
  );
  const rows = H.outlineRows(page.elements, { collapsed: ["outer"] });
  const outer = rows.find((r) => r.id === "outer");
  results.c107_collapse_hides_subtree = {
    pass: eq(rows.map((r) => r.id), ["outer", "loose"]) &&
      outer.collapsed === true && outer.hasChildren === true,
    detail: rows,
  };
}
{
  // A search hit three levels down is useless if the containers above it were
  // filtered away, so an ancestor of a match shows even when it doesn't match.
  const page = alignPage(
    [
      { id: "outer", type: "group" },
      { id: "inner", type: "group", parent: "outer" },
      { id: "btn_mute", type: "button", parent: "inner" },
      { id: "loose", type: "label" },
    ],
    {
      outer: { x: 0, y: 0, w: 50, h: 50 },
      inner: { x: 0, y: 0, w: 50, h: 50 },
      btn_mute: { x: 0, y: 0, w: 50, h: 50 },
      loose: { x: 60, y: 0, w: 10, h: 10 },
    },
  );
  // Collapsed too: a search overrides the fold, or the hit stays hidden.
  const rows = H.outlineRows(page.elements, {
    collapsed: ["outer", "inner"],
    matchIds: new Set(["btn_mute"]),
  });
  results.c107_search_keeps_ancestors = {
    pass: eq(rows.map((r) => [r.id, r.depth]), [["outer", 0], ["inner", 1], ["btn_mute", 2]]),
    detail: rows,
  };
}
{
  // Z-order inside a container is position among its SIBLINGS, so the order
  // buttons swap with the neighbour under the same parent -- the row drawn
  // above a child usually belongs to something else entirely.
  const page = alignPage(
    [
      { id: "box", type: "group" },
      { id: "a", type: "button", parent: "box" },
      { id: "b", type: "button", parent: "box" },
      { id: "after", type: "label" },
    ],
    {
      box: { x: 0, y: 0, w: 50, h: 50 },
      a: { x: 0, y: 0, w: 10, h: 10 },
      b: { x: 20, y: 0, w: 10, h: 10 },
      after: { x: 60, y: 0, w: 10, h: 10 },
    },
  );
  const rows = H.outlineRows(page.elements);
  const a = rows.find((r) => r.id === "a");
  const b = rows.find((r) => r.id === "b");
  const box = rows.find((r) => r.id === "box");
  results.c107_order_buttons_pair_siblings = {
    pass:
      a.prevSiblingId === undefined && a.nextSiblingId === "b" &&
      b.prevSiblingId === "a" && b.nextSiblingId === undefined &&
      box.nextSiblingId === "after",
    detail: rows,
  };
}
{
  // A hand-edited parent cycle leaves elements with no path down from a root.
  // The renderer drops them; the Outline is where you would go to fix that.
  const page = alignPage(
    [
      { id: "a", type: "group", parent: "b" },
      { id: "b", type: "group", parent: "a" },
      { id: "ok", type: "label" },
    ],
    { a: { x: 0, y: 0, w: 10, h: 10 }, b: { x: 0, y: 0, w: 10, h: 10 }, ok: { x: 0, y: 0, w: 10, h: 10 } },
  );
  const rows = H.outlineRows(page.elements);
  results.c107_cycle_members_still_listed = {
    pass: rows.length === 3 && rows.some((r) => r.id === "a") && rows.some((r) => r.id === "b"),
    detail: rows,
  };
}

// --- C-108: reparenting must not move anything on screen ---
// A child's percentages are of its parent, so changing the parent without
// recomputing them teleports the element.
{
  const page = alignPage(
    [{ id: "box", type: "group" }, { id: "kid", type: "button" }],
    { box: { x: 10, y: 10, w: 40, h: 40 }, kid: { x: 30, y: 30, w: 20, h: 20 } },
  );
  const before = H.absolutePlacements(page);
  const out = H.reparentElement([page], "pg", "kid", "box");
  const after = H.absolutePlacements(out[0]);
  const stored = out[0].layouts[0].placements.kid;
  results.c108_reparent_keeps_the_pixels = {
    pass:
      near(after.kid.x, before.kid.x) && near(after.kid.y, before.kid.y) &&
      near(after.kid.w, before.kid.w) && near(after.kid.h, before.kid.h) &&
      // and the stored numbers genuinely changed: 30,30 of the page is 50,50
      // of a container that starts at 10,10 and is 40 wide.
      near(stored.x, 50) && near(stored.y, 50) && near(stored.w, 50) && near(stored.h, 50),
    detail: { before: before.kid, after: after.kid, stored },
  };
}
{
  // ...and back out again lands on the number it started with.
  const page = alignPage(
    [{ id: "box", type: "group" }, { id: "kid", type: "button", parent: "box" }],
    { box: { x: 10, y: 10, w: 40, h: 40 }, kid: { x: 50, y: 50, w: 50, h: 50 } },
  );
  const before = H.absolutePlacements(page);
  const out = H.reparentElement([page], "pg", "kid", null);
  const after = H.absolutePlacements(out[0]);
  const stored = out[0].layouts[0].placements.kid;
  const el = out[0].elements.find((e) => e.id === "kid");
  results.c108_reparent_out_to_page_level = {
    pass:
      (el.parent ?? null) === null &&
      near(after.kid.x, before.kid.x) && near(after.kid.y, before.kid.y) &&
      near(stored.x, 30) && near(stored.y, 30) && near(stored.w, 20) && near(stored.h, 20),
    detail: { before: before.kid, after: after.kid, stored, parent: el.parent ?? null },
  };
}
{
  // A container carries its contents, so moving one keeps everything under it
  // exactly where it was drawn without touching a single child's numbers.
  const page = alignPage(
    [
      { id: "outer", type: "group" },
      { id: "box", type: "group" },
      { id: "kid", type: "button", parent: "box" },
    ],
    {
      outer: { x: 50, y: 0, w: 50, h: 100 },
      box: { x: 10, y: 10, w: 40, h: 40 },
      kid: { x: 50, y: 50, w: 50, h: 50 },
    },
  );
  const before = H.absolutePlacements(page);
  const out = H.reparentElement([page], "pg", "box", "outer");
  const after = H.absolutePlacements(out[0]);
  const kidStored = out[0].layouts[0].placements.kid;
  results.c108_children_ride_along_untouched = {
    pass:
      near(after.kid.x, before.kid.x) && near(after.kid.y, before.kid.y) &&
      near(after.kid.w, before.kid.w) && near(after.kid.h, before.kid.h) &&
      eq(kidStored, { x: 50, y: 50, w: 50, h: 50 }),
    detail: { beforeKid: before.kid, afterKid: after.kid, kidStored, box: after.box },
  };
}
{
  // The variant a layout switcher will author has its own boxes, and its own
  // container rect, so the conversion has to be answered once per layout.
  const page = {
    id: "pg",
    snap: SNAP,
    elements: [{ id: "box", type: "group" }, { id: "kid", type: "button" }],
    layouts: [
      { id: "landscape", orientation: "landscape", primary: true, placements: {
        box: { x: 10, y: 10, w: 40, h: 40 }, kid: { x: 30, y: 30, w: 20, h: 20 } }, hidden: [] },
      { id: "portrait", orientation: "portrait", inherits: "landscape", placements: {
        box: { x: 0, y: 0, w: 100, h: 20 }, kid: { x: 50, y: 10, w: 10, h: 5 } }, hidden: [] },
    ],
  };
  const beforeL = H.absolutePlacements(page, "landscape");
  const beforeP = H.absolutePlacements(page, "portrait");
  const out = H.reparentElement([page], "pg", "kid", "box");
  const afterL = H.absolutePlacements(out[0], "landscape");
  const afterP = H.absolutePlacements(out[0], "portrait");
  results.c108_every_layout_reconverted = {
    pass:
      near(afterL.kid.x, beforeL.kid.x) && near(afterL.kid.y, beforeL.kid.y) &&
      near(afterP.kid.x, beforeP.kid.x) && near(afterP.kid.y, beforeP.kid.y) &&
      near(afterP.kid.w, beforeP.kid.w) &&
      !eq(out[0].layouts[0].placements.kid, out[0].layouts[1].placements.kid),
    detail: { beforeP: beforeP.kid, afterP: afterP.kid, storedP: out[0].layouts[1].placements.kid },
  };
}
{
  // Something dropped into a container belongs on top of what is already in
  // there, and array order IS z-order among siblings.
  const page = alignPage(
    [
      { id: "box", type: "group" },
      { id: "first", type: "button", parent: "box" },
      { id: "mover", type: "label" },
    ],
    {
      box: { x: 0, y: 0, w: 50, h: 50 },
      first: { x: 0, y: 0, w: 10, h: 10 },
      mover: { x: 60, y: 60, w: 10, h: 10 },
    },
  );
  const out = H.reparentElement([page], "pg", "mover", "box");
  results.c108_lands_on_top_of_its_new_siblings = {
    pass: eq(out[0].elements.map((e) => e.id), ["box", "first", "mover"]),
    detail: out[0].elements.map((e) => [e.id, e.parent ?? null]),
  };
}

// --- C-109: the cycles the tree makes reachable ---
// A container cannot go inside itself, or inside anything already inside it.
{
  const page = alignPage(
    [
      { id: "outer", type: "group" },
      { id: "inner", type: "group", parent: "outer" },
      { id: "deep", type: "button", parent: "inner" },
      { id: "plain", type: "button" },
    ],
    {
      outer: { x: 0, y: 0, w: 50, h: 50 },
      inner: { x: 0, y: 0, w: 50, h: 50 },
      deep: { x: 0, y: 0, w: 50, h: 50 },
      plain: { x: 60, y: 0, w: 10, h: 10 },
    },
  );
  results.c109_descendants_are_found_at_any_depth = {
    pass: eq([...H.descendantIds(page, "outer")].sort(), ["deep", "inner"]),
    detail: [...H.descendantIds(page, "outer")],
  };
  results.c109_cannot_parent_into_itself_or_its_own = {
    pass:
      H.canReparent(page, "outer", "outer") === false &&
      H.canReparent(page, "outer", "inner") === false &&
      H.canReparent(page, "outer", null) === true &&
      H.canReparent(page, "plain", "inner") === true,
    detail: {
      self: H.canReparent(page, "outer", "outer"),
      descendant: H.canReparent(page, "outer", "inner"),
      out: H.canReparent(page, "outer", null),
      legal: H.canReparent(page, "plain", "inner"),
    },
  };
}
{
  // Refused means refused: the illegal move returns the page untouched rather
  // than half-applying the parent change.
  const page = alignPage(
    [{ id: "outer", type: "group" }, { id: "inner", type: "group", parent: "outer" }],
    { outer: { x: 0, y: 0, w: 50, h: 50 }, inner: { x: 0, y: 0, w: 50, h: 50 } },
  );
  const out = H.reparentElement([page], "pg", "outer", "inner");
  results.c109_illegal_reparent_is_a_no_op = {
    pass: out[0] === page,
    detail: out[0].elements.map((e) => [e.id, e.parent ?? null]),
  };
}
{
  // A non-container is not a container: dropping onto a plain button means
  // "put it beside that", which is also how a child comes back out.
  const page = alignPage(
    [
      { id: "box", type: "group" },
      { id: "kid", type: "button", parent: "box" },
      { id: "peer", type: "button" },
      { id: "mover", type: "label" },
    ],
    {
      box: { x: 0, y: 0, w: 50, h: 50 },
      kid: { x: 0, y: 0, w: 10, h: 10 },
      peer: { x: 60, y: 0, w: 10, h: 10 },
      mover: { x: 60, y: 20, w: 10, h: 10 },
    },
  );
  results.c109_drop_on_container_goes_in = {
    pass: H.outlineDropParent(page, "mover", "box") === "box",
    detail: H.outlineDropParent(page, "mover", "box"),
  };
  results.c109_drop_on_a_peer_joins_its_parent = {
    pass:
      H.outlineDropParent(page, "mover", "kid") === "box" &&
      H.outlineDropParent(page, "kid", "peer") === null &&
      H.outlineDropParent(page, "kid", null) === null,
    detail: {
      ontoChild: H.outlineDropParent(page, "mover", "kid"),
      ontoPageLevelPeer: H.outlineDropParent(page, "kid", "peer"),
      ontoPageZone: H.outlineDropParent(page, "kid", null),
    },
  };
  results.c109_drop_onto_itself_or_its_own_is_refused = {
    pass:
      H.outlineDropParent(page, "box", "box") === undefined &&
      H.outlineDropParent(page, "box", "kid") === undefined &&
      H.outlineDropParent(page, "mover", "nope") === undefined,
    detail: {
      self: H.outlineDropParent(page, "box", "box"),
      own: H.outlineDropParent(page, "box", "kid"),
      unknown: H.outlineDropParent(page, "mover", "nope"),
    },
  };
}
{
  // The picker in the Layout panel is the same door, so it offers the same set.
  const page = alignPage(
    [
      { id: "outer", type: "group" },
      { id: "inner", type: "group", parent: "outer" },
      { id: "other", type: "group" },
    ],
    {
      outer: { x: 0, y: 0, w: 50, h: 50 },
      inner: { x: 0, y: 0, w: 50, h: 50 },
      other: { x: 60, y: 0, w: 20, h: 20 },
    },
  );
  results.c109_picker_hides_self_and_descendants = {
    pass: eq(H.containerChoices(page, "outer").map((c) => c.id), ["other"]),
    detail: H.containerChoices(page, "outer"),
  };
}

// --- C-110: deleting a container still re-homes what was inside it ---
// Children are controls the author wired up; losing six of them to one wrong
// Delete is not a trade anybody wants.
{
  const page = alignPage(
    [
      { id: "outer", type: "group" },
      { id: "box", type: "group", parent: "outer" },
      { id: "kid", type: "button", parent: "box" },
    ],
    {
      outer: { x: 20, y: 20, w: 60, h: 60 },
      box: { x: 10, y: 10, w: 40, h: 40 },
      kid: { x: 50, y: 50, w: 50, h: 50 },
    },
  );
  const before = H.absolutePlacements(page);
  const out = H.removeElementFromPage([page], "pg", "box");
  const after = H.absolutePlacements(out[0]);
  const kid = out[0].elements.find((e) => e.id === "kid");
  results.c110_orphans_rehome_without_moving = {
    pass:
      out[0].elements.length === 2 && kid.parent === "outer" &&
      near(after.kid.x, before.kid.x) && near(after.kid.y, before.kid.y) &&
      near(after.kid.w, before.kid.w) && near(after.kid.h, before.kid.h),
    detail: { before: before.kid, after: after.kid, parent: kid.parent },
  };
}

// --- C-111: a drop lands in the container it looks like it landed in ---
// The canvas hands this a PAGE-space box, so a nested container has to be
// compared in page space too -- its stored numbers are a fraction of its own
// parent and would test the wrong rectangle.
{
  const page = alignPage(
    [
      { id: "outer", type: "group" },
      { id: "inner", type: "group", parent: "outer" },
    ],
    {
      outer: { x: 0, y: 0, w: 50, h: 50 },
      // 50,50 50x50 OF outer = 25,25 25x25 of the page.
      inner: { x: 50, y: 50, w: 50, h: 50 },
    },
  );
  const drop = H.resolveDropParent(page, { x: 30, y: 30, w: 10, h: 10 });
  results.c111_nested_container_adopts_in_page_space = {
    pass:
      drop.parentId === "inner" &&
      near(drop.relative.x, 20) && near(drop.relative.y, 20) &&
      near(drop.relative.w, 40) && near(drop.relative.h, 40),
    detail: drop,
  };
}

// --- C-112: a drag onto a container puts the control in it ---
// The palette drop has always worked this way. A builder where a NEW button
// joins the container but an EXISTING one dragged to the same spot does not is
// a builder that contradicts itself.
const dragPage = () => alignPage(
  [
    { id: "box", type: "group" },
    { id: "kid", type: "button", parent: "box" },
    { id: "loose", type: "button" },
    { id: "other", type: "group" },
  ],
  {
    box: { x: 10, y: 10, w: 40, h: 40 },
    kid: { x: 10, y: 10, w: 20, h: 20 },
    loose: { x: 70, y: 70, w: 10, h: 10 },
    other: { x: 60, y: 10, w: 30, h: 30 },
  },
);
{
  const page = dragPage();
  // Dropped fully inside the container: 20,20 5x5 of the page sits well within
  // the box at 10,10 40x40.
  const target = H.dropTargetFor(page, "loose", { x: 20, y: 20, w: 5, h: 5 });
  results.c112_drag_into_a_container_adopts = { pass: target === "box", detail: target };
}
{
  const page = dragPage();
  // Only clipping the corner is not "inside", so it stays where it was --
  // the same conservative rule the palette drop and the migration use.
  const target = H.dropTargetFor(page, "loose", { x: 45, y: 45, w: 10, h: 10 });
  results.c112_partial_overlap_does_not_adopt = { pass: target === null, detail: target };
}
{
  const page = dragPage();
  // Containers do not clip, so a child deliberately bled over its frame's edge
  // has to stay a child. Ejecting it the moment it crossed the line would make
  // that impossible to author.
  const stillTouching = H.dropTargetFor(page, "kid", { x: 45, y: 20, w: 10, h: 10 });
  const clearAway = H.dropTargetFor(page, "kid", { x: 80, y: 80, w: 10, h: 10 });
  results.c112_bleeding_over_the_edge_stays_inside = {
    pass: stillTouching === "box" && clearAway === null,
    detail: { stillTouching, clearAway },
  };
}
{
  const page = dragPage();
  // Straight from one container into another.
  const target = H.dropTargetFor(page, "kid", { x: 65, y: 15, w: 10, h: 10 });
  results.c112_drag_between_containers = { pass: target === "other", detail: target };
}
{
  const page = alignPage(
    [
      { id: "outer", type: "group" },
      { id: "inner", type: "group", parent: "outer" },
      { id: "loose", type: "button" },
    ],
    {
      outer: { x: 0, y: 0, w: 60, h: 60 },
      inner: { x: 50, y: 50, w: 50, h: 50 },
      other: { x: 0, y: 0, w: 0, h: 0 },
      loose: { x: 90, y: 90, w: 5, h: 5 },
    },
  );
  // inner is 30,30 30x30 of the page. A box inside both wins the innermost.
  const target = H.dropTargetFor(page, "loose", { x: 35, y: 35, w: 5, h: 5 });
  results.c112_innermost_container_wins = { pass: target === "inner", detail: target };
}
{
  const page = dragPage();
  // A container cannot swallow itself, or anything already inside it.
  const self = H.dropTargetFor(page, "box", { x: 12, y: 12, w: 5, h: 5 });
  results.c112_container_never_adopts_into_its_own = { pass: self === null, detail: self };
}
{
  // The whole gesture in one call: the box the drag produced, plus the
  // container it landed in, with the drawn rect unchanged by the adoption.
  const page = dragPage();
  const moved = { loose: { x: 20, y: 20, w: 5, h: 5 } };
  const out = H.commitGesturePlacements([page], "pg", moved);
  const el = out[0].elements.find((e) => e.id === "loose");
  const stored = out[0].layouts[0].placements.loose;
  const abs = H.absolutePlacements(out[0]);
  results.c112_commit_writes_box_and_parent_together = {
    pass:
      el.parent === "box" &&
      near(abs.loose.x, 20) && near(abs.loose.y, 20) &&
      near(abs.loose.w, 5) && near(abs.loose.h, 5) &&
      // 20,20 of the page is 25,25 of a box at 10,10 that is 40 across.
      near(stored.x, 25) && near(stored.y, 25) &&
      near(stored.w, 12.5) && near(stored.h, 12.5),
    detail: { parent: el.parent, stored, drawn: abs.loose },
  };
}
{
  // A drag that changes no parent is just a move.
  const page = dragPage();
  const out = H.commitGesturePlacements([page], "pg", { loose: { x: 75, y: 75, w: 10, h: 10 } });
  const el = out[0].elements.find((e) => e.id === "loose");
  results.c112_commit_leaves_parents_alone_when_nothing_changed = {
    pass: (el.parent ?? null) === null && eq(out[0].layouts[0].placements.loose, { x: 75, y: 75, w: 10, h: 10 }),
    detail: { parent: el.parent ?? null, stored: out[0].layouts[0].placements.loose },
  };
}
{
  // A multi-select drag: everything that ended up inside joins, each converted
  // against the container on its own.
  const page = alignPage(
    [
      { id: "box", type: "group" },
      { id: "a", type: "button" },
      { id: "b", type: "button" },
    ],
    {
      box: { x: 10, y: 10, w: 40, h: 40 },
      a: { x: 70, y: 10, w: 5, h: 5 },
      b: { x: 80, y: 10, w: 5, h: 5 },
    },
  );
  const out = H.commitGesturePlacements([page], "pg", {
    a: { x: 15, y: 15, w: 5, h: 5 },
    b: { x: 25, y: 15, w: 5, h: 5 },
  });
  const abs = H.absolutePlacements(out[0]);
  const parents = Object.fromEntries(out[0].elements.map((e) => [e.id, e.parent ?? null]));
  results.c112_multi_select_drag_adopts_each = {
    pass:
      parents.a === "box" && parents.b === "box" &&
      near(abs.a.x, 15) && near(abs.b.x, 25) &&
      near(abs.a.w, 5) && near(abs.b.w, 5),
    detail: { parents, drawn: { a: abs.a, b: abs.b } },
  };
}
{
  // The palette's drop asks the same question, so a brand-new id -- one the
  // page has never heard of -- has to be answerable.
  const page = dragPage();
  const drop = H.resolveDropParent(page, { x: 20, y: 20, w: 5, h: 5 });
  results.c112_palette_drop_shares_the_rule = {
    pass: drop.parentId === "box" && near(drop.relative.x, 25) && near(drop.relative.w, 12.5),
    detail: drop,
  };
}

// --- V-113: layout variants ---
// One set of controls, one or more arrangements of them. The whole promise is
// that a variant is DELTAS: it stores what you moved and nothing else, so a
// control you never touch follows the primary forever. Every check below is
// really the same check from a different direction.

/** A page with a primary landscape arrangement and nothing else. */
const variantPage = () => ({
  id: "pg",
  name: "Main",
  snap: SNAP,
  elements: [
    { id: "a", type: "button" },
    { id: "b", type: "button" },
    { id: "c", type: "label" },
  ],
  layouts: [
    LANDSCAPE({
      a: { x: 10, y: 10, w: 20, h: 20 },
      b: { x: 40, y: 10, w: 20, h: 20 },
      c: { x: 70, y: 10, w: 20, h: 20 },
    }),
  ],
});

{
  // A new variant is born empty and inheriting, which is what makes it deltas.
  // Ship it with the primary's placements copied in and every one of them
  // becomes a value that stops tracking the primary the moment it is written.
  const page = H.addLayout(variantPage(), "portrait");
  const added = page.layouts[1];
  results.v113_new_variant_is_pure_deltas = {
    pass:
      page.layouts.length === 2 &&
      added.id === "portrait" &&
      added.orientation === "portrait" &&
      added.primary === false &&
      added.inherits === "landscape" &&
      Object.keys(added.placements).length === 0 &&
      eq(added.hidden, []),
    detail: added,
  };
}
{
  // Adding one must not disturb the arrangement it inherits from.
  const before = variantPage();
  const after = H.addLayout(before, "portrait");
  results.v113_adding_leaves_the_primary_alone = {
    pass: eq(after.layouts[0], before.layouts[0]),
    detail: { before: before.layouts[0], after: after.layouts[0] },
  };
}
{
  // Ids stay unique even when an orientation already has an arrangement, so a
  // hand-edited project can never end up with two layouts answering to one id.
  const page = H.addLayout(H.addLayout(variantPage(), "portrait"), "portrait");
  results.v113_layout_ids_stay_unique = {
    pass: page.layouts.length === 3 && page.layouts[2].id === "portrait_2",
    detail: page.layouts.map((l) => l.id),
  };
}
{
  // What the switcher offers: only the shapes this page has no arrangement for.
  const bare = variantPage();
  const withPortrait = H.addLayout(bare, "portrait");
  results.v113_offers_only_missing_orientations = {
    pass:
      eq(H.missingOrientations(bare), ["portrait"]) &&
      eq(H.missingOrientations(withPortrait), []) &&
      eq(H.missingOrientations(undefined), ["landscape", "portrait"]),
    detail: { bare: H.missingOrientations(bare), both: H.missingOrientations(withPortrait) },
  };
}
{
  // THE delta test. Move one control in the variant and exactly one entry is
  // written there. A variant that materialises a row per element is the failure
  // mode this whole model exists to avoid.
  const page = H.addLayout(variantPage(), "portrait");
  const moved = H.withPlacement(page, "a", { x: 5, y: 60, w: 90, h: 10 }, "portrait");
  const variant = moved.layouts[1];
  results.v113_editing_writes_one_entry_only = {
    pass:
      eq(Object.keys(variant.placements), ["a"]) &&
      near(variant.placements.a.x, 5) && near(variant.placements.a.y, 60),
    detail: variant.placements,
  };
}
{
  // The other half: what you did NOT move still resolves through inherits, and
  // the primary's own numbers are untouched by the variant edit.
  const page = H.addLayout(variantPage(), "portrait");
  const moved = H.withPlacement(page, "a", { x: 5, y: 60, w: 90, h: 10 }, "portrait");
  const inPortrait = H.resolvePlacements(moved, "portrait");
  const inLandscape = H.resolvePlacements(moved, "landscape");
  results.v113_untouched_elements_follow_the_primary = {
    pass:
      near(inPortrait.a.x, 5) &&
      near(inPortrait.b.x, 40) && near(inPortrait.c.x, 70) &&
      near(inLandscape.a.x, 10) && near(inLandscape.b.x, 40),
    detail: { portrait: inPortrait, landscape: inLandscape },
  };
}
{
  // A whole multi-select drag in the variant writes only the ids it was handed.
  const page = H.addLayout(variantPage(), "portrait");
  const moved = H.withPlacements(
    page,
    { a: { x: 0, y: 0, w: 10, h: 10 }, b: { x: 0, y: 20, w: 10, h: 10 } },
    "portrait",
  );
  results.v113_multi_edit_still_writes_only_what_moved = {
    pass: eq(Object.keys(moved.layouts[1].placements).sort(), ["a", "b"]),
    detail: Object.keys(moved.layouts[1].placements),
  };
}
{
  // Deleting a variant takes its deltas and nothing else with it.
  const page = H.withPlacement(
    H.addLayout(variantPage(), "portrait"), "a", { x: 5, y: 60, w: 90, h: 10 }, "portrait",
  );
  const gone = H.removeLayout(page, "portrait");
  const primary = gone.layouts[0];
  results.v113_deleting_a_variant_leaves_the_primary = {
    pass:
      gone.layouts.length === 1 &&
      primary.primary === true &&
      eq(Object.keys(primary.placements).sort(), ["a", "b", "c"]) &&
      near(primary.placements.a.x, 10) && near(primary.placements.a.y, 10),
    detail: primary.placements,
  };
}
{
  // The primary is what an unmatched screen falls back to. Remove it and a page
  // has no answer at all, so the request is refused rather than obeyed.
  const page = H.addLayout(variantPage(), "portrait");
  const same = H.removeLayout(page, "landscape");
  results.v113_the_primary_is_not_removable = {
    pass: same.layouts.length === 2 && same.layouts[0].primary === true,
    detail: same.layouts.map((l) => ({ id: l.id, primary: l.primary })),
  };
}
{
  // A chain must never point at a layout that is gone: whatever inherited from
  // the removed one inherits what IT inherited.
  const page = {
    ...variantPage(),
    layouts: [
      LANDSCAPE({ a: { x: 10, y: 10, w: 20, h: 20 } }),
      { id: "mid", orientation: "portrait", inherits: "landscape", placements: { a: { x: 50, y: 50, w: 5, h: 5 } }, hidden: [] },
      { id: "leaf", orientation: "portrait", inherits: "mid", placements: {}, hidden: [] },
    ],
  };
  const gone = H.removeLayout(page, "mid");
  results.v113_removing_a_link_repairs_the_chain = {
    pass:
      gone.layouts.length === 2 &&
      gone.layouts[1].inherits === "landscape" &&
      near(H.resolvePlacements(gone, "leaf").a.x, 10),
    detail: { inherits: gone.layouts[1].inherits, resolved: H.resolvePlacements(gone, "leaf") },
  };
}
{
  // Hiding is per-arrangement and accumulates down the chain, exactly the way
  // the panel runtime unions it. A variant can add a hide; it cannot take back
  // one the layout above it made.
  const page = {
    ...variantPage(),
    layouts: [
      { ...LANDSCAPE({ a: { x: 10, y: 10, w: 20, h: 20 } }), hidden: ["c"] },
      { id: "portrait", orientation: "portrait", inherits: "landscape", placements: {}, hidden: [] },
    ],
  };
  const hidB = H.withHidden(page, "b", true, "portrait");
  results.v113_hidden_unions_down_the_chain = {
    pass:
      eq([...H.resolveHidden(hidB, "portrait")].sort(), ["b", "c"]) &&
      eq([...H.resolveHidden(hidB, "landscape")], ["c"]) &&
      H.isHiddenInLayout(hidB, "b", "portrait") === true &&
      H.isHiddenInLayout(hidB, "c", "portrait") === false,
    detail: {
      portrait: [...H.resolveHidden(hidB, "portrait")],
      landscape: [...H.resolveHidden(hidB, "landscape")],
    },
  };
}
{
  // Hiding writes to the arrangement being authored and nowhere else, and
  // un-hiding is the exact inverse.
  const page = H.addLayout(variantPage(), "portrait");
  const hid = H.withHidden(page, "c", true, "portrait");
  const shown = H.withHidden(hid, "c", false, "portrait");
  results.v113_hiding_touches_one_layout = {
    pass:
      eq(hid.layouts[0].hidden, []) && eq(hid.layouts[1].hidden, ["c"]) &&
      eq(shown.layouts[1].hidden, []),
    detail: { primary: hid.layouts[0].hidden, variant: hid.layouts[1].hidden },
  };
}
{
  // Deleting an element clears it out of every arrangement, hides included, or
  // a deleted id lingers in a list forever.
  const page = H.withHidden(H.addLayout(variantPage(), "portrait"), "c", true, "portrait");
  const gone = H.withoutPlacement(page, "c");
  results.v113_deleting_an_element_clears_every_layout = {
    pass:
      !("c" in gone.layouts[0].placements) &&
      eq(gone.layouts[1].hidden, []),
    detail: { primary: Object.keys(gone.layouts[0].placements), variantHidden: gone.layouts[1].hidden },
  };
}
{
  // A control is born in the PRIMARY whichever arrangement is on screen, so it
  // exists everywhere from the moment it exists at all. Write it into a variant
  // instead and it would have no box in any other layout.
  const page = H.addLayout(variantPage(), "portrait");
  const out = H.addElementToPage([page], "pg", { id: "d", type: "button" }, { x: 1, y: 2, w: 3, h: 4 });
  results.v113_new_controls_are_born_in_the_primary = {
    pass:
      near(out[0].layouts[0].placements.d.x, 1) &&
      !("d" in out[0].layouts[1].placements) &&
      near(H.getPlacement(out[0], "d", "portrait").x, 1),
    detail: {
      primary: out[0].layouts[0].placements.d,
      variant: out[0].layouts[1].placements,
    },
  };
}
{
  // A duplicate is measured against the arrangement on screen -- the copy lands
  // beside the control the author can actually see -- and stored in the primary
  // like any other new control.
  const page = H.withPlacement(
    H.addLayout(variantPage(), "portrait"), "a", { x: 50, y: 50, w: 10, h: 10 }, "portrait",
  );
  const out = H.duplicateElementInPage([page], "pg", "a", [], "portrait");
  const copy = out[0].elements[out[0].elements.length - 1];
  results.v113_duplicate_measures_the_layout_on_screen = {
    pass:
      near(out[0].layouts[0].placements[copy.id].x, 52.5) &&
      near(out[0].layouts[0].placements[copy.id].y, 52.5) &&
      !(copy.id in out[0].layouts[1].placements),
    detail: { id: copy.id, stored: out[0].layouts[0].placements[copy.id] },
  };
}
{
  // The orientation being authored, which is what keys a master's box and turns
  // the canvas.
  const page = H.addLayout(variantPage(), "portrait");
  results.v113_layout_orientation_follows_the_active = {
    pass:
      H.layoutOrientation(page, "portrait") === "portrait" &&
      H.layoutOrientation(page, null) === "landscape" &&
      H.layoutOrientation(page, "nonexistent") === "landscape" &&
      H.layoutOrientation(undefined) === "landscape",
    detail: {
      portrait: H.layoutOrientation(page, "portrait"),
      fallback: H.layoutOrientation(page, "nonexistent"),
    },
  };
}
{
  // A master carries its own orientation-keyed boxes because it renders across
  // pages that can be arranged differently. Same fallback order the panel uses.
  const both = { placements: { landscape: { x: 1, y: 0, w: 10, h: 10 }, portrait: { x: 2, y: 0, w: 10, h: 10 } } };
  const onlyLandscape = { placements: { landscape: { x: 3, y: 0, w: 10, h: 10 } } };
  const odd = { placements: { square: { x: 4, y: 0, w: 10, h: 10 } } };
  results.v113_master_box_is_keyed_by_orientation = {
    pass:
      H.masterPlacement(both, "portrait").x === 2 &&
      H.masterPlacement(both, "landscape").x === 1 &&
      H.masterPlacement(onlyLandscape, "portrait").x === 3 &&
      H.masterPlacement(odd, "portrait").x === 4 &&
      H.masterPlacement({ placements: {} }, "landscape") === null &&
      H.masterPlacement(undefined, "landscape") === null,
    detail: {
      portrait: H.masterPlacement(both, "portrait"),
      fallback: H.masterPlacement(onlyLandscape, "portrait"),
    },
  };
}
{
  // The presets are all landscape. A portrait arrangement gets the same screen
  // turned, because nobody can author a portrait panel on a landscape canvas.
  const preset = { width: 1280, height: 800 };
  const land = H.presetForOrientation(preset, "landscape");
  const port = H.presetForOrientation(preset, "portrait");
  results.v113_preset_turns_for_a_portrait_layout = {
    pass:
      eq(land, { width: 1280, height: 800 }) &&
      eq(port, { width: 800, height: 1280 }) &&
      // Already-portrait input stays put rather than flipping back.
      eq(H.presetForOrientation({ width: 800, height: 1280 }, "portrait"), { width: 800, height: 1280 }) &&
      eq(H.presetForOrientation({ width: 800, height: 1280 }, "landscape"), { width: 1280, height: 800 }),
    detail: { land, port },
  };
}
{
  // vmin is the shorter edge either way, so turning the canvas must not resize
  // a single glyph. This is the claim the whole "type stays stable" story rests
  // on, and it is one line of arithmetic away from being wrong.
  const preset = { width: 1280, height: 800 };
  const land = H.presetForOrientation(preset, "landscape");
  const port = H.presetForOrientation(preset, "portrait");
  results.v113_turning_the_canvas_does_not_resize_type = {
    pass: Math.min(land.width, land.height) === Math.min(port.width, port.height),
    detail: { land: Math.min(land.width, land.height), port: Math.min(port.width, port.height) },
  };
}
{
  // A cycle is reachable by hand-editing a file, and the builder still has to
  // draw something rather than hang.
  const page = {
    ...variantPage(),
    layouts: [
      { id: "landscape", orientation: "landscape", primary: true, placements: { a: { x: 1, y: 1, w: 5, h: 5 } }, hidden: ["a"] },
      { id: "l1", orientation: "portrait", inherits: "l2", placements: {}, hidden: ["b"] },
      { id: "l2", orientation: "portrait", inherits: "l1", placements: {}, hidden: ["c"] },
    ],
  };
  results.v113_cycles_terminate = {
    pass:
      eq([...H.resolveHidden(page, "l1")].sort(), ["b", "c"]) &&
      eq(H.resolvePlacements(page, "l1"), {}) &&
      H.layoutChain(page, "l1").length === 2,
    detail: { hidden: [...H.resolveHidden(page, "l1")], chain: H.layoutChain(page, "l1").map((l) => l.id) },
  };
}
{
  // A drop measured against the variant asks about the variant's rectangles.
  // The same container can sit somewhere else entirely in the primary, and the
  // pointer was over the one on screen.
  const page = {
    id: "pg",
    snap: SNAP,
    elements: [{ id: "box", type: "group" }, { id: "loose", type: "button" }],
    layouts: [
      LANDSCAPE({ box: { x: 0, y: 0, w: 30, h: 30 }, loose: { x: 90, y: 90, w: 5, h: 5 } }),
      { id: "portrait", orientation: "portrait", inherits: "landscape",
        placements: { box: { x: 60, y: 60, w: 30, h: 30 } }, hidden: [] },
    ],
  };
  // 70,70 is inside the container where PORTRAIT puts it and nowhere near
  // where landscape does.
  const inPortrait = H.dropTargetFor(page, "loose", { x: 70, y: 70, w: 5, h: 5 }, "portrait");
  const inLandscape = H.dropTargetFor(page, "loose", { x: 70, y: 70, w: 5, h: 5 }, "landscape");
  results.v113_drops_measure_the_layout_on_screen = {
    pass: inPortrait === "box" && inLandscape === null,
    detail: { portrait: inPortrait, landscape: inLandscape },
  };
}

// --- A-001: duplicatePage carries the geometry through the id renames ---
{
  // Geometry lives on the page's layouts keyed by element id; the copy renames
  // every element, so placements, hidden lists and parent links must follow or
  // the duplicated page loses its entire arrangement.
  const pages = [{
    id: "p1", name: "P1", snap: SNAP,
    elements: [
      { id: "grp_a", type: "group", style: {}, bindings: {} },
      { id: "btn_a", type: "button", parent: "grp_a", style: {}, bindings: {} },
    ],
    layouts: [
      LANDSCAPE({ grp_a: { x: 10, y: 10, w: 50, h: 50 }, btn_a: { x: 20, y: 20, w: 40, h: 20 } }),
      { id: "portrait", orientation: "portrait", inherits: "landscape",
        placements: { grp_a: { x: 5, y: 40, w: 90, h: 40 } }, hidden: ["btn_a"] },
    ],
  }];
  const after = H.duplicatePage(pages, "p1");
  const copy = after[1];
  const grpCopy = copy.elements[0];
  const btnCopy = copy.elements[1];
  const land = copy.layouts.find((l) => l.id === "landscape");
  const port = copy.layouts.find((l) => l.id === "portrait");
  results.a001_duplicate_page_keeps_geometry = {
    pass: grpCopy.id !== "grp_a" && btnCopy.parent === grpCopy.id &&
      !!land.placements[grpCopy.id] && near(land.placements[grpCopy.id].x, 10) &&
      !!land.placements[btnCopy.id] && near(land.placements[btnCopy.id].w, 40) &&
      !!port.placements[grpCopy.id] && near(port.placements[grpCopy.id].y, 40) &&
      port.hidden.length === 1 && port.hidden[0] === btnCopy.id &&
      !(("grp_a") in land.placements) && !(("btn_a") in port.placements),
    detail: { ids: copy.elements.map((e) => e.id), parent: btnCopy.parent, land: land.placements, portHidden: port.hidden },
  };
}

// --- A-002: promoteToMaster converts through page space, per orientation ---
{
  // A container child's stored box is a percentage of the container; a master's
  // is a percentage of the viewport. Promote has to convert -- and a portrait
  // arrangement's position becomes the master's portrait placement instead of
  // being thrown away.
  const pages = [{
    id: "p1", name: "P1", snap: SNAP,
    elements: [
      { id: "grp_a", type: "group", style: {}, bindings: {} },
      { id: "btn_a", type: "button", parent: "grp_a", style: {}, bindings: {} },
    ],
    layouts: [
      LANDSCAPE({ grp_a: { x: 25, y: 25, w: 50, h: 50 }, btn_a: { x: 10, y: 10, w: 50, h: 25 } }),
      { id: "portrait", orientation: "portrait", inherits: "landscape",
        placements: { grp_a: { x: 0, y: 50, w: 100, h: 50 } }, hidden: [] },
    ],
  }];
  const r = H.promoteToMaster(pages, [], "p1", "btn_a");
  const m = r.masterElements[0];
  // Landscape: container 25,25 50x50; child 10%,10% 50%x25% of it -> 30,30 25x12.5.
  // Portrait: container 0,50 100x50 -> child 10,55 50x12.5.
  results.a002_promote_converts_container_child = {
    pass: m.parent == null &&
      near(m.placements.landscape.x, 30) && near(m.placements.landscape.y, 30) &&
      near(m.placements.landscape.w, 25) && near(m.placements.landscape.h, 12.5) &&
      near(m.placements.portrait.x, 10) && near(m.placements.portrait.y, 55) &&
      near(m.placements.portrait.w, 50) && near(m.placements.portrait.h, 12.5),
    detail: m.placements,
  };
}
{
  // Promoting a whole container re-homes its children to page level with the
  // same no-jump conversion a container delete uses.
  const pages = [{
    id: "p1", name: "P1", snap: SNAP,
    elements: [
      { id: "grp_a", type: "group", style: {}, bindings: {} },
      { id: "btn_a", type: "button", parent: "grp_a", style: {}, bindings: {} },
    ],
    layouts: [LANDSCAPE({ grp_a: { x: 25, y: 25, w: 50, h: 50 }, btn_a: { x: 10, y: 10, w: 50, h: 25 } })],
  }];
  const r = H.promoteToMaster(pages, [], "p1", "grp_a");
  const child = r.pages[0].elements[0];
  const box = r.pages[0].layouts[0].placements.btn_a;
  results.a002_promote_group_rehomes_children = {
    pass: r.masterElements.length === 1 && child.id === "btn_a" && child.parent == null &&
      near(box.x, 30) && near(box.y, 30) && near(box.w, 25) && near(box.h, 12.5),
    detail: { parent: child.parent, box },
  };
}

// --- A-003: validateProject checks every arrangement, in the right space ---
{
  // An element pushed out of bounds ONLY in the portrait variant must still be
  // flagged, and a dangling inherits is an error, not a silent collapse.
  const proj = makeValidationProject([
    { id: "b1", type: "button", style: {}, bindings: { do: { press: [{ action: "macro", macro: "real_macro" }] } } },
  ]);
  const page = proj.ui.pages[0];
  page.layouts[0].placements = { b1: { x: 10, y: 10, w: 30, h: 20 } };
  page.layouts.push({
    id: "portrait", orientation: "portrait", inherits: "landscape",
    placements: { b1: { x: 90, y: 90, w: 30, h: 20 } }, hidden: [],
  });
  page.layouts.push({
    id: "broken", orientation: "portrait", inherits: "gone", placements: {}, hidden: [],
  });
  const issues = H.validateProject(proj);
  const oob = issues.filter((i) => /extends beyond/.test(i.message));
  const dangling = issues.filter((i) => /inherits from "gone"/.test(i.message) && i.severity === "error");
  results.a003_validate_sees_variant_geometry = {
    pass: oob.length === 1 && /portrait/.test(oob[0].message) && dangling.length === 1,
    detail: { oob, dangling },
  };
}
{
  // A container child's touch target is judged against the box it sits in --
  // 30% of a small container is a small control whatever the percentage says.
  const proj = makeValidationProject([
    { id: "grp_a", type: "group", style: {}, bindings: {} },
    { id: "b1", type: "button", parent: "grp_a", style: {}, bindings: { do: { press: [{ action: "macro", macro: "real_macro" }] } } },
  ]);
  const page = proj.ui.pages[0];
  // Container 20% x 20% of the page (256x160px at reference); button 30% x 30%
  // of it = ~77x48px -> under the touch minimum on width.
  page.layouts[0].placements = {
    grp_a: { x: 0, y: 0, w: 20, h: 20 },
    b1: { x: 0, y: 0, w: 30, h: 30 },
  };
  const issues = H.validateProject(proj);
  const touch = issues.filter((i) => /Small touch target/.test(i.message) && i.elementId === "b1");
  results.a003_validate_touch_uses_container_space = {
    pass: touch.length === 1 && !/15-inch/.test(touch[0].message),
    detail: touch,
  };
}

process.stdout.write(JSON.stringify(results));
