"use strict";
// Loads the project-import orchestration (projectImport.ts — React-free, only
// `import type` deps which esbuild strips) and drives importParsedProject with
// fakes to pin its contract: the parsed project is persisted THROUGH the server
// first, and adopted into the live store (forceReload) ONLY once the server
// accepts it — so a wrong/corrupt file never reaches the store. Mirrors
// project_store_save_harness.cjs. The Python wrapper skips when the Node
// toolchain or esbuild is absent rather than failing the Python-only CI gate.
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

// Build deps over a call log so tests can assert what ran, in what order.
function makeDeps(overrides) {
  const calls = [];
  const deps = {
    getEtag: () => '"1"',
    saveProject: async () => { calls.push("save"); return { status: "saved" }; },
    reloadProject: async () => { calls.push("reload"); return {}; },
    forceReload: async () => { calls.push("forceReload"); },
    isConflict: (e) => Boolean(e && e.__conflict),
    onError: (m) => { calls.push("error:" + m); },
    ...overrides,
  };
  return { deps, calls };
}

async function main() {
  const results = {};

  // Success: save -> reload -> adopt (forceReload), in that order, no error.
  {
    const { deps, calls } = makeDeps({});
    const ok = await H.importParsedProject({ id: "p" }, deps);
    results.m308_success_adopts = {
      pass: ok === true && eq(calls, ["save", "reload", "forceReload"]),
      detail: calls,
    };
  }

  // THE fix: a file the server can't validate (rejected save) is NEVER adopted
  // into the store — forceReload/reload don't run, and an error is surfaced.
  {
    const { deps, calls } = makeDeps({
      saveProject: async () => { calls.push("save"); throw new Error("API 422: bad shape"); },
    });
    const ok = await H.importParsedProject({ oops: true }, deps);
    results.m308_validation_failure_never_adopts = {
      pass: ok === false &&
        !calls.includes("reload") && !calls.includes("forceReload") &&
        calls.includes("save") &&
        calls.some((c) => c.startsWith("error:") && c.includes("isn't a valid OpenAVC project")),
      detail: calls,
    };
  }

  // A 409 conflict is surfaced with its own message and likewise not adopted.
  {
    const { deps, calls } = makeDeps({
      saveProject: async () => {
        calls.push("save");
        const e = new Error("conflict"); e.__conflict = true; throw e;
      },
    });
    const ok = await H.importParsedProject({ id: "p" }, deps);
    results.m308_conflict_message = {
      pass: ok === false && !calls.includes("forceReload") &&
        calls.some((c) => c.startsWith("error:") && c.includes("Another session changed")),
      detail: calls,
    };
  }

  // The etag from the store is passed to the save (optimistic concurrency).
  {
    let sentEtag = "unset";
    const { deps } = makeDeps({
      getEtag: () => '"7"',
      saveProject: async (_p, etag) => { sentEtag = etag; return { status: "saved" }; },
    });
    const ok = await H.importParsedProject({ id: "p" }, deps);
    results.m308_passes_etag_to_save = { pass: ok === true && sentEtag === '"7"', detail: { sentEtag } };
  }

  process.stdout.write(JSON.stringify(results));
}

main().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
