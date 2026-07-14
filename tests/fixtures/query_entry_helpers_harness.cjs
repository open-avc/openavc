"use strict";
// Bundles queryEntryHelpers.ts (the shared shape model for polling.queries /
// on_connect entries) with the esbuild in web/programmer/node_modules and
// exercises buildQueryEntry + the shape readers. This covers the write-back
// logic the render harness can't reach (renderToStaticMarkup fires no events):
// how the editor folds send/each_child/when/args back into the simplest entry
// shape, including OSC args (keyed `address`) and the each_child/args mutual
// exclusion.
const path = require("path");
const esbuild = require("esbuild");

const modulePath = process.argv[2];

function load(entry) {
  const built = esbuild.buildSync({
    entryPoints: [entry],
    bundle: true,
    format: "cjs",
    platform: "node",
    loader: { ".css": "empty" },
    write: false,
    logLevel: "silent",
  });
  const code = built.outputFiles[0].text;
  const moduleObj = { exports: {} };
  const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
  fn(moduleObj.exports, require, moduleObj, entry, path.dirname(entry));
  return moduleObj.exports;
}

const H = load(modulePath);

const results = {};
function report(name, pass, detail) {
  results[name] = { pass, detail: detail === undefined ? null : detail };
}
function eq(name, got, expected) {
  const g = JSON.stringify(got);
  const e = JSON.stringify(expected);
  report(name, g === e, `got ${g}, expected ${e}`);
}

function main() {
  const { buildQueryEntry, querySend, queryWhen, queryArgs, isOscItem, isGated } = H;
  const ARG = [{ type: "i", value: "1" }];

  // --- buildQueryEntry: collapse to the simplest shape ---
  eq("bare", buildQueryEntry("/x", "", ""), "/x");
  eq("gated", buildQueryEntry("/x", "", "meters"), { send: "/x", when: "meters" });
  eq("each_child", buildQueryEntry("q{child_id}", "ch", ""), {
    each_child: "ch",
    send: "q{child_id}",
  });
  eq("each_child_when", buildQueryEntry("q{child_id}", "ch", "meters"), {
    each_child: "ch",
    send: "q{child_id}",
    when: "meters",
  });

  // --- OSC args force the {address, args} form ---
  eq("args", buildQueryEntry("/x", "", "", ARG), { address: "/x", args: ARG });
  eq("args_when", buildQueryEntry("/x", "", "meters", ARG), {
    address: "/x",
    args: ARG,
    when: "meters",
  });

  // --- each_child is address-only: args are dropped when a child is chosen ---
  eq("each_child_drops_args", buildQueryEntry("q{child_id}", "ch", "", ARG), {
    each_child: "ch",
    send: "q{child_id}",
  });

  // --- removing every arg collapses back off the {address, args} form ---
  eq("empty_args_collapses_to_bare", buildQueryEntry("/x", "", "", []), "/x");
  eq("empty_args_collapses_to_gated", buildQueryEntry("/x", "", "meters", []), {
    send: "/x",
    when: "meters",
  });

  // --- shape readers ---
  eq("querySend_reads_address", querySend({ address: "/x", args: ARG }), "/x");
  eq("queryWhen_reads_osc_when", queryWhen({ address: "/x", when: "meters" }), "meters");
  eq("queryArgs_osc", queryArgs({ address: "/x", args: ARG }), ARG);
  report("queryArgs_string_undefined", queryArgs("/x") === undefined, "a bare string has no args");
  report("queryArgs_gated_undefined", queryArgs({ send: "/x", when: "m" }) === undefined, "a gated entry has no args");
  report("isOscItem_true", isOscItem({ address: "/x", args: ARG }) === true);
  report("isOscItem_false_on_gated", isOscItem({ send: "/x", when: "m" }) === false);
  report("isGated_false_on_osc", isGated({ address: "/x", args: ARG }) === false, "an address item is not a gated {send} entry");

  process.stdout.write(JSON.stringify(results));
}

main();
