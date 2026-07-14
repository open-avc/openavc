"use strict";
// Loads the project-store save loop (projectStoreSave.ts — React-free pure
// async logic, only `import type` deps which esbuild strips) and drives
// runSaveWithRetry with fakes to pin its async contract: a caller that awaits
// it must see the FINAL underlying write, not just the first failed attempt,
// and a persistent failure must stop after the retry budget instead of looping
// forever. Mirrors ui_builder_helpers_harness.cjs. The Python wrapper skips
// when the Node toolchain or esbuild is absent rather than failing the
// Python-only CI gate.
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

// Build a deps object over a mutable fake state, recording backoff sleeps.
function makeDeps(overrides) {
  const state = {
    saving: false, savePending: false, dirty: false,
    etag: '"1"', revision: 1, conflictDetected: false, error: null,
  };
  const sleeps = [];
  const deps = {
    getProject: () => ({ id: "p" }),
    getEtag: () => state.etag,
    saveProject: async () => ({ etag: '"2"' }),
    isConflict: (e) => Boolean(e && e.__conflict),
    conflictMessage: (e) => e.message,
    setState: (patch) => Object.assign(state, patch),
    sleep: async (ms) => { sleeps.push(ms); },
    ...overrides,
  };
  return { deps, state, sleeps };
}

async function main() {
  const results = {};

  // Success on the first try: one write, no backoff.
  {
    let calls = 0;
    const { deps, state, sleeps } = makeDeps({
      saveProject: async () => { calls++; return { etag: '"5"' }; },
    });
    const outcome = await H.runSaveWithRetry(deps);
    results.m307_success_first_try = {
      pass: outcome === "saved" && calls === 1 && sleeps.length === 0 &&
        state.etag === '"5"' && state.revision === 5 && state.error === null,
      detail: { outcome, calls, sleeps, etag: state.etag, revision: state.revision },
    };
  }

  // THE contract: two transient failures then success — the promise resolves
  // only AFTER the third (successful) write. Old code resolved at calls===1.
  {
    let calls = 0;
    const { deps, state, sleeps } = makeDeps({
      saveProject: async () => { calls++; if (calls < 3) throw new Error("network"); return { etag: '"2"' }; },
    });
    const outcome = await H.runSaveWithRetry(deps);
    const callsAtResolve = calls;  // read right after the awaited promise settled
    results.m307_retries_then_succeeds = {
      pass: outcome === "saved" && callsAtResolve === 3 &&
        eq(sleeps, [1000, 2000]) && state.error === null && state.saving === false,
      detail: { outcome, callsAtResolve, sleeps, error: state.error },
    };
  }

  // Persistent failure stops after the retry budget (1 initial + 2 retries) and
  // resolves "failed" — it does NOT loop forever (if it did, this awaits hangs).
  {
    let calls = 0;
    const { deps, state, sleeps } = makeDeps({
      saveProject: async () => { calls++; throw new Error("down"); },
    });
    const outcome = await H.runSaveWithRetry(deps);
    results.m307_persistent_failure_stops = {
      pass: outcome === "failed" && calls === 3 && eq(sleeps, [1000, 2000]) &&
        typeof state.error === "string" && state.saving === false,
      detail: { outcome, calls, sleeps, error: state.error },
    };
  }

  // A version conflict is never retried — one write, no backoff.
  {
    let calls = 0;
    const { deps, state, sleeps } = makeDeps({
      saveProject: async () => {
        calls++;
        const e = new Error("conflict!"); e.__conflict = true; throw e;
      },
    });
    const outcome = await H.runSaveWithRetry(deps);
    results.m307_conflict_no_retry = {
      pass: outcome === "conflict" && calls === 1 && sleeps.length === 0 &&
        state.conflictDetected === true && state.error === "conflict!",
      detail: { outcome, calls, conflictDetected: state.conflictDetected },
    };
  }

  // Editing during the save keeps the store dirty so the caller re-saves.
  {
    let projCalls = 0;
    const A = { id: "A" }, B = { id: "B" };
    const { deps, state } = makeDeps({
      getProject: () => { projCalls++; return projCalls === 1 ? A : B; },
    });
    const outcome = await H.runSaveWithRetry(deps);
    results.m307_edit_during_save_keeps_dirty = {
      pass: outcome === "saved" && state.dirty === true,
      detail: { outcome, dirty: state.dirty },
    };
  }

  // ETag "0" is a real revision (the engine boots at revision 0 before the
  // first save) — it must parse to 0, not collapse to null.
  {
    const { deps, state } = makeDeps({
      saveProject: async () => ({ etag: '"0"' }),
    });
    const outcome = await H.runSaveWithRetry(deps);
    results.revision_zero_survives_save = {
      pass: outcome === "saved" && state.revision === 0 && state.etag === '"0"',
      detail: { outcome, revision: state.revision, etag: state.etag },
    };
  }

  // A non-numeric ETag still falls back to revision null.
  {
    const { deps, state } = makeDeps({
      saveProject: async () => ({ etag: '"abc"' }),
    });
    const outcome = await H.runSaveWithRetry(deps);
    results.revision_non_numeric_etag_null = {
      pass: outcome === "saved" && state.revision === null,
      detail: { outcome, revision: state.revision },
    };
  }

  // No project loaded → no-op, nothing written.
  {
    let calls = 0;
    const { deps } = makeDeps({
      getProject: () => null,
      saveProject: async () => { calls++; return { etag: '"2"' }; },
    });
    const outcome = await H.runSaveWithRetry(deps);
    results.m307_noop_when_no_project = { pass: outcome === "noop" && calls === 0, detail: { outcome, calls } };
  }

  process.stdout.write(JSON.stringify(results));
}

main().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
