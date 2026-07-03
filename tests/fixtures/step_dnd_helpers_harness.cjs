"use strict";
// Loads the macro step drag-reorder helpers (stepDndHelpers.ts — React-free
// pure logic) bundled on the fly with the esbuild already in
// web/programmer/node_modules, and replays the drag flows that used to break
// when the sortable ids rendered for each step row came from a different id
// space (`step-${index}`) than the SortableContext items (stable per-object
// ids): the drag after a reorder moved the WRONG step, and any drag after
// switching macros silently no-oped.
// Mirrors trigger_helpers_harness.cjs. The Python wrapper skips when the
// Node toolchain or esbuild is absent rather than failing the Python-only
// CI gate.
const path = require("path");

const helpersPath = process.argv[2];

const esbuild = require("esbuild");
const built = esbuild.buildSync({
  entryPoints: [helpersPath],
  bundle: true,
  format: "cjs",
  platform: "node",
  write: false,
  logLevel: "silent",
});
const code = built.outputFiles[0].text;
const moduleObj = { exports: {} };
const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
fn(moduleObj.exports, require, moduleObj, helpersPath, path.dirname(helpersPath));
const H = moduleObj.exports;

const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const results = {};

// The ids each rendered step row registers with useSortable. The fixed
// editor renders id={stepIds[i]} — the same array handed to
// SortableContext — so this is the identity function. A pre-fix scratch
// module exports the old index formula instead, which is what made these
// scenarios red.
const renderIds = H.renderIds ? H.renderIds.bind(H) : (ids) => ids;

const names = (steps) => steps.map((s) => s.name);

// --- The drag AFTER a reorder must move the step the user dragged ---
{
  const idMap = new WeakMap();
  const counter = { current: 0 };
  let steps = [{ name: "A" }, { name: "B" }, { name: "C" }];

  // First render + first drag: pull C (row 2) up to the top. Id spaces
  // coincide on a fresh editor, so this worked even before the fix.
  let ids = H.getStepIds(steps, idMap, counter);
  let rids = renderIds(ids);
  const first = H.applyStepReorder(steps, ids, rids[2], rids[0]);
  const firstOk = first !== null && eq(names(first.steps), ["C", "A", "B"]);
  steps = first ? first.steps : steps;

  // Re-render, then drag A (now row 1) down one slot onto B. With the old
  // index-formula render ids this resolved against the permuted stable ids
  // and moved B instead.
  ids = H.getStepIds(steps, idMap, counter);
  rids = renderIds(ids);
  const second = H.applyStepReorder(steps, ids, rids[1], rids[2]);
  results.second_drag_after_reorder_moves_dragged_step = {
    pass: firstOk && second !== null && eq(names(second.steps), ["C", "B", "A"]),
    detail: {
      firstOk,
      after_second: second ? names(second.steps) : null,
    },
  };
}

// --- Dragging still works after switching to another macro ---
{
  const idMap = new WeakMap();
  const counter = { current: 0 };

  // Editor showed a first macro (mints ids 0..2; the editor instance and
  // its refs survive a macro switch because MacroView renders it unkeyed).
  H.getStepIds([{ name: "A" }, { name: "B" }, { name: "C" }], idMap, counter);

  // Switch to a second macro and drag its first step down. The old render
  // ids restarted at step-0 while the stable ids continued at step-3, so
  // the two spaces shared no entries and every drag returned null.
  const steps = [{ name: "D" }, { name: "E" }];
  const ids = H.getStepIds(steps, idMap, counter);
  const rids = renderIds(ids);
  const moved = H.applyStepReorder(steps, ids, rids[0], rids[1]);
  results.drag_after_macro_switch_still_reorders = {
    pass: moved !== null && eq(names(moved.steps), ["E", "D"]),
    detail: moved ? names(moved.steps) : null,
  };
}

// --- Ids are stable per step object across a reorder (keys don't churn) ---
{
  const idMap = new WeakMap();
  const counter = { current: 0 };
  const a = { name: "A" };
  const b = { name: "B" };
  const c = { name: "C" };
  const before = H.getStepIds([a, b, c], idMap, counter);
  const after = H.getStepIds([c, a, b], idMap, counter);
  results.ids_follow_step_objects_across_reorder = {
    pass: eq(after, [before[2], before[0], before[1]]) && counter.current === 3,
    detail: { before, after, minted: counter.current },
  };
}

// --- Duplicated and pasted steps (fresh objects) get fresh unique ids ---
{
  const idMap = new WeakMap();
  const counter = { current: 0 };
  const original = { name: "A" };
  const steps = [original];
  H.getStepIds(steps, idMap, counter);
  // duplicateStep shallow-copies; pasting deep-clones the clipboard on
  // every read. Either way: new object, new id.
  const withCopies = [original, { ...original }, { name: "A" }, { name: "A" }];
  const ids = H.getStepIds(withCopies, idMap, counter);
  results.copied_steps_get_unique_ids = {
    pass: new Set(ids).size === 4 && ids[0] === "step-0",
    detail: ids,
  };
}

// --- Dropping a step on itself or on an unknown id changes nothing ---
{
  const idMap = new WeakMap();
  const counter = { current: 0 };
  const steps = [{ name: "A" }, { name: "B" }];
  const ids = H.getStepIds(steps, idMap, counter);
  const self = H.applyStepReorder(steps, ids, ids[0], ids[0]);
  const unknown = H.applyStepReorder(steps, ids, "step-99", ids[1]);
  results.self_and_unknown_drops_are_noops = {
    pass: self === null && unknown === null,
    detail: { self, unknown },
  };
}

// --- Expanded step tracking across a move ---
{
  const checks = [
    // The expanded step itself moved: follow it.
    H.adjustExpandedAfterMove(0, 0, 2) === 2,
    // A step moved from above the expanded one to at/below it: shift up.
    H.adjustExpandedAfterMove(1, 0, 2) === 0,
    // A step moved from below the expanded one to at/above it: shift down.
    H.adjustExpandedAfterMove(1, 2, 0) === 2,
    // Move entirely below the expanded step: unchanged.
    H.adjustExpandedAfterMove(0, 1, 2) === 0,
    // Nothing expanded stays that way.
    H.adjustExpandedAfterMove(null, 0, 1) === null,
  ];
  results.expanded_step_follows_moves = {
    pass: checks.every(Boolean),
    detail: checks,
  };
}

process.stdout.write(JSON.stringify(results));
