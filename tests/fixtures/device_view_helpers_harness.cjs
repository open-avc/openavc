"use strict";
// Loads DeviceView's status-count helper (deviceViewHelpers.ts — zero-import
// pure logic) and checks the status counting. The status-filter chip counts
// used to be computed from ALL devices even when a search was active, so they
// disagreed with the search-narrowed visible list; the fix counts from the
// filtered list, which computeStatusCounts supports by counting exactly the
// list it's handed. Mirrors project_import_harness.cjs. The Python wrapper
// skips when the Node toolchain or esbuild is absent.
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

const LS = {
  "device.d1.connected": true,
  "device.d3.orphaned": true,
  "device.d4.connected": true,
  "device.d4.orphaned": true, // both set -> counted orphaned, not online
};

{
  const r = H.computeStatusCounts([{ id: "d1" }, { id: "d2" }, { id: "d3" }], LS);
  results.l174_counts_by_status = {
    pass: eq(r, { total: 3, online: 1, offline: 1, orphaned: 1 }),
    detail: r,
  };
}
{
  // Orphaned takes precedence over connected.
  const r = H.computeStatusCounts([{ id: "d4" }], LS);
  results.l174_orphaned_precedence = {
    pass: eq(r, { total: 1, online: 0, offline: 0, orphaned: 1 }),
    detail: r,
  };
}
{
  // No live state -> offline.
  const r = H.computeStatusCounts([{ id: "nostate" }], LS);
  results.l174_no_state_is_offline = {
    pass: eq(r, { total: 1, online: 0, offline: 1, orphaned: 0 }),
    detail: r,
  };
}
{
  // THE fix: counting a SUBSET (the search-filtered list) yields the subset's
  // counts — d2 is online in LS but excluded because it isn't in the list.
  const subset = [{ id: "d1" }];
  const r = H.computeStatusCounts(subset, { "device.d1.connected": true, "device.d2.connected": true });
  results.l174_counts_only_the_passed_list = {
    pass: eq(r, { total: 1, online: 1, offline: 0, orphaned: 0 }),
    detail: r,
  };
}
{
  const r = H.computeStatusCounts([], LS);
  results.l174_empty = { pass: eq(r, { total: 0, online: 0, offline: 0, orphaned: 0 }), detail: r };
}

process.stdout.write(JSON.stringify(results));
