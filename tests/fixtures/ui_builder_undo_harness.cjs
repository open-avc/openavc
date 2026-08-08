"use strict";
// Loads the REAL UI Builder + project zustand stores (bundled together on
// the fly with the esbuild already in openavc/web/programmer/node_modules — zustand
// stores work headless via getState/setState) and exercises the UI Builder
// undo/redo paths end to end: rollback patch application, dirty marking,
// save scheduling through the shared project-store debounce, and selection
// after rollback. The project store's debouncedSave is replaced with a spy
// via setState, so no timers or network are involved.
// argv[2] = the programmer src/store directory containing uiBuilderStore.ts
// and projectStore.ts.
// Mirrors trigger_helpers_harness.cjs / icon_picker_harness.cjs. The Python
// wrapper skips when the Node toolchain or esbuild is absent rather than
// failing the Python-only CI gate.
const path = require("path");

const storeDir = process.argv[2];

// The API layer computes its base path from window.location at module load;
// give the bundle a minimal browser-shaped global.
globalThis.window = { location: { pathname: "/programmer/" } };

const esbuild = require("esbuild");
const built = esbuild.buildSync({
  stdin: {
    contents:
      'export { useUIBuilderStore } from "./uiBuilderStore";\n' +
      'export { useProjectStore } from "./projectStore";\n',
    resolveDir: storeDir,
    loader: "ts",
  },
  bundle: true,
  format: "cjs",
  platform: "node",
  write: false,
  logLevel: "silent",
});
const code = built.outputFiles[0].text;
const moduleObj = { exports: {} };
const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
fn(moduleObj.exports, require, moduleObj, path.join(storeDir, "_harness_entry.ts"), storeDir);
const { useUIBuilderStore, useProjectStore } = moduleObj.exports;

const el = (id) => ({ id, type: "button", x: 0, y: 0, width: 100, height: 40, properties: {} });
const makeProject = (pages) => ({
  project: { name: "harness" },
  ui: { pages, settings: {}, master_elements: [], page_groups: [] },
  macros: [],
  variables: [],
});

let saveCalls = [];
function reset(project, selection) {
  saveCalls = [];
  useProjectStore.setState({
    project,
    dirty: false,
    debouncedSave: (delay) => saveCalls.push(delay ?? "default"),
  });
  useUIBuilderStore.setState({
    undoStack: [],
    redoStack: [],
    selectedPageId: "page1",
    selectedElementIds: [],
    selectedElementId: null,
    selectedMasterElementId: null,
    ...selection,
  });
}

const U = () => useUIBuilderStore.getState();
const P = () => useProjectStore.getState();
const pageIds = () => P().project.ui.pages.map((p) => p.elements.map((e) => e.id).join(",")).join("|");

const results = {};

// --- Undo applies the rollback patch and marks the project dirty ---
{
  const pre = [{ id: "page1", elements: [el("A")] }];
  const post = [{ id: "page1", elements: [el("A"), el("B")] }];
  reset(makeProject(post));
  U().pushUndo({ pages: pre }, "add B");
  U().undo();
  results.undo_applies_patch_and_marks_dirty = {
    pass: pageIds() === "A" && P().dirty === true,
    detail: { pages: pageIds(), dirty: P().dirty },
  };
}

// --- The H-137 bug: undo/redo must arm the shared save debounce ---
// (update() only sets dirty; flushSave and the beforeunload handler no-op
// with no pending timer, so without this the undone state never persists.)
{
  const pre = [{ id: "page1", elements: [el("A")] }];
  const post = [{ id: "page1", elements: [el("A"), el("B")] }];
  reset(makeProject(post));
  U().pushUndo({ pages: pre }, "add B");
  U().undo();
  results.undo_schedules_save = {
    pass: saveCalls.length === 1 && saveCalls[0] === 100,
    detail: { saveCalls },
  };

  saveCalls = [];
  U().redo();
  results.redo_schedules_save = {
    pass: saveCalls.length === 1 && saveCalls[0] === 100 && pageIds() === "A,B",
    detail: { saveCalls, pages: pageIds() },
  };
}

// --- The L-106 bug: redo of an add/paste re-selects the re-created element ---
{
  const pre = [{ id: "page1", elements: [el("A")] }];
  const post = [{ id: "page1", elements: [el("A"), el("B")] }];
  reset(makeProject(post), { selectedElementIds: ["B"], selectedElementId: "B" });
  U().pushUndo({ pages: pre }, "add B");
  U().undo();
  // Existing repair behavior: the undone element can't stay selected.
  results.undo_of_add_drops_stale_selection = {
    pass: U().selectedElementIds.length === 0 && U().selectedElementId === null,
    detail: { ids: U().selectedElementIds },
  };
  U().redo();
  results.redo_of_add_restores_selection = {
    pass:
      U().selectedElementId === "B" &&
      U().selectedElementIds.join(",") === "B" &&
      U().selectedPageId === "page1",
    detail: {
      selectedElementId: U().selectedElementId,
      selectedElementIds: U().selectedElementIds,
      selectedPageId: U().selectedPageId,
    },
  };
}

// --- Same class: undo of a delete re-selects the restored element ---
{
  const withB = [{ id: "page1", elements: [el("A"), el("B")] }];
  const without = [{ id: "page1", elements: [el("A")] }];
  reset(makeProject(without));
  U().pushUndo({ pages: withB }, "delete B");
  U().undo();
  results.undo_of_delete_restores_selection = {
    pass: U().selectedElementId === "B" && pageIds() === "A,B",
    detail: { selectedElementId: U().selectedElementId, pages: pageIds() },
  };
}

// --- Guard: a rollback that re-creates nothing keeps a valid selection ---
{
  const moved = [{ id: "page1", elements: [{ ...el("A"), x: 50 }] }];
  const orig = [{ id: "page1", elements: [el("A")] }];
  reset(makeProject(moved), { selectedElementIds: ["A"], selectedElementId: "A" });
  U().pushUndo({ pages: orig }, "move A");
  U().undo();
  results.rollback_keeps_valid_selection = {
    pass: U().selectedElementId === "A" && U().selectedElementIds.join(",") === "A",
    detail: { selectedElementId: U().selectedElementId },
  };
}

// --- Undo of a page delete selects the restored page, not its elements ---
{
  const both = [
    { id: "page1", elements: [el("A")] },
    { id: "page2", elements: [el("C"), el("D")] },
  ];
  const onlyOne = [{ id: "page1", elements: [el("A")] }];
  reset(makeProject(onlyOne));
  U().pushUndo({ pages: both }, "delete page2");
  U().undo();
  results.undo_of_page_delete_selects_page = {
    pass: U().selectedPageId === "page2" && U().selectedElementIds.length === 0,
    detail: { selectedPageId: U().selectedPageId, ids: U().selectedElementIds },
  };
}

// --- Redo of a master-element add re-selects the re-created master ---
{
  const project = makeProject([{ id: "page1", elements: [] }]);
  project.ui.master_elements = [{ id: "M1", type: "button" }];
  reset(project, { selectedMasterElementId: "M1" });
  U().pushUndo({ master_elements: [] }, "add master");
  U().undo();
  const droppedAfterUndo = U().selectedMasterElementId === null;
  U().redo();
  results.redo_of_master_add_restores_selection = {
    pass: droppedAfterUndo && U().selectedMasterElementId === "M1",
    detail: { droppedAfterUndo, selectedMasterElementId: U().selectedMasterElementId },
  };
}

process.stdout.write(JSON.stringify(results));
