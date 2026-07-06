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

// --- H-038: clampOriginToGrid keeps the full span on-grid ---
{
  const fits = H.clampOriginToGrid(5, 3, 2, 2, 12, 8);
  results.h038_clamp_fits = { pass: eq(fits, { col: 5, row: 3 }), detail: fits };
}
{
  // col 11 + span 3 would reach col 13 on a 12-col grid; clamp to 12-3+1 = 10.
  const r = H.clampOriginToGrid(11, 1, 3, 2, 12, 8);
  results.h038_clamp_overflow_right = { pass: eq(r, { col: 10, row: 1 }), detail: r };
}
{
  // row 8 + span 3 overflows an 8-row grid; clamp to 8-3+1 = 6.
  const r = H.clampOriginToGrid(1, 8, 2, 3, 12, 8);
  results.h038_clamp_overflow_bottom = { pass: eq(r, { col: 1, row: 6 }), detail: r };
}
{
  const r = H.clampOriginToGrid(-2, 0, 3, 2, 12, 8);
  results.h038_clamp_min = { pass: eq(r, { col: 1, row: 1 }), detail: r };
}

// --- M-077: findFreeGridPosition is span- and overlap-aware ---
{
  const r = H.findFreeGridPosition([], 3, 2, 12, 8);
  results.m077_free_empty = { pass: eq(r, { col: 1, row: 1 }), detail: r };
}
{
  // A 3x2 element sits at (1,1); the next 3x2 must skip past it to (4,1),
  // not land on the first free single cell inside it.
  const els = [{ grid_area: { col: 1, row: 1, col_span: 3, row_span: 2 } }];
  const r = H.findFreeGridPosition(els, 3, 2, 12, 8);
  results.m077_free_avoid_overlap = { pass: eq(r, { col: 4, row: 1 }), detail: r };
}
{
  // 4x4 grid, (1,1,3,2) taken — a 3x2 can't fit on rows 1-2, drops to (1,3).
  const els = [{ grid_area: { col: 1, row: 1, col_span: 3, row_span: 2 } }];
  const r = H.findFreeGridPosition(els, 3, 2, 4, 4);
  results.m077_free_drop_down = { pass: eq(r, { col: 1, row: 3 }), detail: r };
}
{
  // Element wider than the grid → clamped (1,1) fallback, never off-grid.
  const r = H.findFreeGridPosition([], 6, 2, 4, 4);
  results.m077_free_too_big_fallback = { pass: eq(r, { col: 1, row: 1 }), detail: r };
}

// --- L-051: pointerToCell excludes the container padding from the cell area ---
{
  const r = H.pointerToCell(0, 0, 120, 0, 12);
  results.l051_ptc_basic = { pass: r === 1, detail: r };
}
{
  // 120px rect, 8px pad → 104px cell area. The centre (60) maps to cell 7.
  const r = H.pointerToCell(60, 0, 120, 8, 12);
  results.l051_ptc_center = { pass: r === 7, detail: r };
}
{
  // x=15 sits in cell 1 once the 8px left pad is removed; the un-padded mapping
  // (x/120*12) would mis-bin it as cell 2.
  const padded = H.pointerToCell(15, 0, 120, 8, 12);
  const unpadded = Math.floor((15 / 120) * 12) + 1;
  results.l051_ptc_pad_corrects_edge = {
    pass: padded === 1 && unpadded === 2,
    detail: { padded, unpadded },
  };
}

// --- H-039: duplicateElementInPage avoids reserved (master) ids ---
{
  const pages = [
    {
      id: "p1",
      grid: { columns: 12, rows: 8 },
      elements: [
        { id: "button_1", type: "button", grid_area: { col: 1, row: 1, col_span: 3, row_span: 2 }, style: {}, bindings: {} },
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
        grid: { columns: 12, rows: 8 },
        elements: [{ id: "btn", type: "button", grid_area: { col: 1, row: 1, col_span: 3, row_span: 2 }, style: {}, bindings: {} }],
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
      pages: [{ id: "p1", name: "Page 1", grid: { columns: 12, rows: 8 }, elements }],
      master_elements: [],
      settings: {},
    },
    devices: [{ id: "real_dev" }],
    macros: [{ id: "real_macro", name: "M", steps: [] }],
  };
}
const AREA = { col: 1, row: 1, col_span: 2, row_span: 1 };
{
  // Array-shaped do.press binding to a deleted device must be flagged.
  const proj = makeValidationProject([
    { id: "b1", type: "button", grid_area: AREA, style: {}, bindings: { do: { press: [{ action: "device.command", device: "ghost_dev", command: "go" }] } } },
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
    { id: "b1", type: "button", grid_area: AREA, style: {}, bindings: { do: { press: [{ action: "device.command", device: "real_dev", command: "go" }, { action: "navigate", page: "gone_page" }] } } },
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
    { id: "s1", type: "select", grid_area: AREA, style: {}, bindings: { do: { change: [{ action: "macro", macro: "ghost_macro" }] } } },
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
    { id: "b1", type: "button", grid_area: AREA, style: {}, bindings: { do: { press: { action: "device.command", device: "ghost_dev", command: "go" } } } },
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
    { id: "b1", type: "button", grid_area: AREA, style: {}, bindings: { do: { press: [{ action: "device.command", device: "real_dev", command: "go" }, { action: "macro", macro: "real_macro" }] } } },
  ]);
  const issues = H.validateProject(proj).filter((i) => i.severity === "error");
  results.h086_validate_valid_refs_pass = { pass: issues.length === 0, detail: issues };
}

// --- H-086: removePage scrubs navigate actions in do action lists ---
{
  const pages = [
    {
      id: "p1", name: "P1", grid: { columns: 12, rows: 8 },
      elements: [
        {
          id: "b1", type: "button", grid_area: AREA, style: {},
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
    { id: "p2", name: "P2", grid: { columns: 12, rows: 8 }, elements: [] },
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
      id: "p1", name: "P1", grid: { columns: 12, rows: 8 },
      elements: [
        {
          id: "btn_x", type: "button", grid_area: AREA, style: {},
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
      id: "p1", name: "P1", grid: { columns: 12, rows: 8 },
      elements: [
        { id: "btn_a", type: "button", grid_area: AREA, style: {}, bindings: { show: { look: { source: "state", key: "ui.btn_a.value", condition: { equals: true }, style_active: {}, style_inactive: {} } } } },
        { id: "lbl_b", type: "label", grid_area: { ...AREA, row: 2 }, style: {}, bindings: { show: { value: { source: "state", key: "ui.btn_a.value" } } } },
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
    { id: "p1", name: "P1", grid: { columns: 12, rows: 8 }, elements: [
      { id: "btn_a", type: "button", grid_area: AREA, style: {}, bindings: {} },
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
    { id: "shared_btn", type: "button", pages: "*", grid_area: AREA, style: {}, bindings: { show: { look: { source: "state", key: "ui.shared_btn.value", condition: { equals: true }, style_active: {}, style_inactive: {} } } } },
  ];
  const pages = [
    { id: "p1", name: "P1", grid: { columns: 12, rows: 8 }, elements: [
      { id: "shared_btn", type: "button", grid_area: AREA, style: {}, bindings: {} },
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
  const masters = [{ id: "solo_btn", type: "button", pages: "*", grid_area: AREA, style: {}, bindings: {} }];
  const pages = [{ id: "p1", name: "P1", grid: { columns: 12, rows: 8 }, elements: [] }];
  const r = H.demoteFromMaster(pages, masters, "solo_btn", "p1");
  results.m144_demote_no_collision_keeps_id = {
    pass: r.pages[0].elements.length === 1 && r.pages[0].elements[0].id === "solo_btn",
    detail: r.pages[0].elements.map((e) => e.id),
  };
}
{
  // Promote when a master already holds the id -> promoted copy renamed.
  const masters = [{ id: "dup_btn", type: "button", pages: "*", grid_area: AREA, style: {}, bindings: {} }];
  const pages = [
    { id: "p1", name: "P1", grid: { columns: 12, rows: 8 }, elements: [
      { id: "dup_btn", type: "button", grid_area: AREA, style: {}, bindings: { show: { look: { source: "state", key: "ui.dup_btn.value", condition: { equals: true }, style_active: {}, style_inactive: {} } } } },
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
    { id: "p1", name: "P1", grid: { columns: 12, rows: 8 }, elements: [
      { id: "lone_btn", type: "button", grid_area: AREA, style: {}, bindings: {} },
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
      id: "s1", type: "select", grid_area: AREA, style: {},
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

// --- L-088: findOutOfBoundsIds flags spans beyond the grid ---
{
  const grid = { columns: 12, rows: 8 };
  const els = [
    { id: "ok", type: "button", grid_area: { col: 1, row: 1, col_span: 3, row_span: 2 } },
    { id: "off_right", type: "button", grid_area: { col: 11, row: 1, col_span: 3, row_span: 1 } },
    { id: "off_bottom", type: "button", grid_area: { col: 1, row: 8, col_span: 1, row_span: 2 } },
    { id: "edge_fit", type: "button", grid_area: { col: 10, row: 7, col_span: 3, row_span: 2 } },
  ];
  const flagged = [...H.findOutOfBoundsIds(els, grid)].sort();
  results.l088_out_of_bounds_ids = {
    pass: eq(flagged, ["off_bottom", "off_right"]),
    detail: flagged,
  };
}

// --- M-231: grid shrink clamps element areas instead of stranding them ---
{
  // 12x8 grid shrinking to 6x4: the out-of-bounds element is pulled inside
  // (position clamps into range, span shrinks to fit — the inspector's
  // rules); the in-bounds element keeps its identity.
  try {
    const els = [
      { id: "a", grid_area: { col: 5, row: 5, col_span: 4, row_span: 2 } },
      { id: "b", grid_area: { col: 1, row: 1, col_span: 2, row_span: 2 } },
    ];
    const r = H.clampElementsToGrid(els, 6, 4);
    results.m231_shrink_clamps_out_of_bounds = {
      pass:
        eq(r[0].grid_area, { col: 5, row: 4, col_span: 2, row_span: 1 }) &&
        r[1] === els[1],
      detail: r,
    };
  } catch (e) {
    results.m231_shrink_clamps_out_of_bounds = { pass: false, detail: String(e) };
  }
}
{
  // Everything already fits -> the SAME array back (identity), so callers can
  // cheaply detect "nothing to clamp" (and undo diffs stay minimal).
  try {
    const els = [{ id: "a", grid_area: { col: 1, row: 1, col_span: 3, row_span: 2 } }];
    const r = H.clampElementsToGrid(els, 12, 8);
    results.m231_identity_when_fits = { pass: r === els, detail: { same: r === els } };
  } catch (e) {
    results.m231_identity_when_fits = { pass: false, detail: String(e) };
  }
}
{
  // Wider than the whole new grid -> the span itself shrinks (an origin-only
  // clamp could never bring a full-width element back in bounds).
  try {
    const els = [{ id: "w", grid_area: { col: 1, row: 1, col_span: 12, row_span: 1 } }];
    const r = H.clampElementsToGrid(els, 8, 8);
    results.m231_span_shrinks_to_grid = {
      pass: eq(r[0].grid_area, { col: 1, row: 1, col_span: 8, row_span: 1 }),
      detail: r,
    };
  } catch (e) {
    results.m231_span_shrinks_to_grid = { pass: false, detail: String(e) };
  }
}

// --- L-142: page-delete scrub returns the ORIGINAL arrays when untouched ---
{
  // Nothing references the deleted page -> both arrays come back by
  // identity, so the changed-only undo snapshot actually skips them (the
  // old .map() always allocated, making the caller's !== guard dead code).
  const pages = [{ id: "p1", elements: [] }, { id: "p2", elements: [] }];
  const masters = [{ id: "m1", pages: "*", grid_area: { col: 1, row: 1, col_span: 1, row_span: 1 } }];
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

process.stdout.write(JSON.stringify(results));
