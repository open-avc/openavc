"use strict";
// Loads DeviceView's status-count helper (deviceViewHelpers.ts, pure logic,
// bundled with the presence reader it imports) and checks the status
// counting. The status-filter chip counts used to be computed from ALL
// devices even when a search was active, so they disagreed with the
// search-narrowed visible list; the fix counts from the filtered list, which
// computeStatusCounts supports by counting exactly the list it's handed. Each
// device is counted by its presence word. The Python wrapper skips when the
// Node toolchain or esbuild is absent.
const path = require("path");

const helpersPath = process.argv[2];

const esbuild = require("esbuild");
const built = esbuild.buildSync({
  entryPoints: [helpersPath], bundle: true, format: "cjs", platform: "node", write: false,
});
const code = built.outputFiles[0].text;
const moduleObj = { exports: {} };
const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
fn(moduleObj.exports, require, moduleObj, helpersPath, path.dirname(helpersPath));
const H = moduleObj.exports;

const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const results = {};

const NONE = { online: 0, offline: 0, restarting: 0, paused: 0, asleep: 0, disabled: 0, waiting_for_driver: 0 };
const counts = (total, some) => ({ total, ...NONE, ...some });

const LS = {
  "device.d1.connected": true,
  "device.d1.presence": "online",
  "device.d2.presence": "offline",
  "device.d3.orphaned": true,
  "device.d3.presence": "waiting_for_driver",
  // The flags say connected; the word the server publishes decides.
  "device.d4.connected": true,
  "device.d4.orphaned": true,
  "device.d4.presence": "waiting_for_driver",
};

{
  const r = H.computeStatusCounts([{ id: "d1" }, { id: "d2" }, { id: "d3" }], LS);
  results.l174_counts_by_status = {
    pass: eq(r, counts(3, { online: 1, offline: 1, waiting_for_driver: 1 })),
    detail: r,
  };
}
{
  // A device waiting for its driver is counted so, whatever else it says.
  const r = H.computeStatusCounts([{ id: "d4" }], LS);
  results.l174_orphaned_precedence = {
    pass: eq(r, counts(1, { waiting_for_driver: 1 })),
    detail: r,
  };
}
{
  // No word published yet -> offline.
  const r = H.computeStatusCounts([{ id: "nostate" }], LS);
  results.l174_no_state_is_offline = {
    pass: eq(r, counts(1, { offline: 1 })),
    detail: r,
  };
}
{
  // THE fix: counting a SUBSET (the search-filtered list) yields the subset's
  // counts -- d2 is online in the state but excluded because it isn't in the list.
  const subset = [{ id: "d1" }];
  const r = H.computeStatusCounts(subset, {
    "device.d1.presence": "online", "device.d2.presence": "online",
  });
  results.l174_counts_only_the_passed_list = {
    pass: eq(r, counts(1, { online: 1 })),
    detail: r,
  };
}
{
  const r = H.computeStatusCounts([], LS);
  results.l174_empty = { pass: eq(r, counts(0, {})), detail: r };
}
{
  // A device put to sleep on purpose is counted under Asleep, not Offline.
  const r = H.computeStatusCounts([{ id: "tv" }, { id: "amp" }], {
    "device.tv.presence": "asleep", "device.amp.presence": "offline",
  });
  results.l174_asleep_is_not_offline = {
    pass: eq(r, counts(2, { offline: 1, asleep: 1 })),
    detail: r,
  };
}

process.stdout.write(JSON.stringify(results));
