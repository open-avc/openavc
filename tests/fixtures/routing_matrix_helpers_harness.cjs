"use strict";
// Loads the routing-matrix helpers (routingMatrixHelpers.ts — React-free
// pure logic behind the plugin surface configurator's crosspoint matrix)
// bundled on the fly with the esbuild in openavc/web/programmer/node_modules, and
// replays the defects: JS Boolean() coercion read string route status as
// routed (the Dante plugin writes "none" for an unsubscribed channel, so
// every unrouted crosspoint rendered routed and the first click sent
// "unroute" to hardware), and the row/column prefix derivation only
// supported a trailing '*' (mid-string patterns produced an empty matrix).
// Mirrors transport_picker_helpers_harness.cjs; the Python wrapper skips
// when the Node toolchain is absent.
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

// --- isCellRouted: string route status reads correctly ---
// The live defect: Dante writes "none" for an unsubscribed channel.
results.none_is_unrouted = H.isCellRouted("none") === false;
results.false_string_unrouted = H.isCellRouted("false") === false;
results.zero_string_unrouted = H.isCellRouted("0") === false;
results.empty_string_unrouted = H.isCellRouted("") === false;
results.off_unrouted = H.isCellRouted("off") === false;
results.case_and_space_insensitive = H.isCellRouted(" None ") === false;
results.connected_routed = H.isCellRouted("connected") === true;
results.one_string_routed = H.isCellRouted("1") === true;
results.bool_true_routed = H.isCellRouted(true) === true;
results.bool_false_unrouted = H.isCellRouted(false) === false;
results.one_number_routed = H.isCellRouted(1) === true;
results.zero_number_unrouted = H.isCellRouted(0) === false;
results.null_unrouted = H.isCellRouted(null) === false;
results.undefined_unrouted = H.isCellRouted(undefined) === false;

// --- matchStateKeys: '*' matches anywhere, not just as a suffix ---
const KEYS = [
  "plugin.dante.rx.amp-1.1",
  "plugin.dante.rx.amp-1.2",
  "plugin.dante.tx.mixer.1",
  "plugin.x.tx.alpha.name",
  "plugin.x.tx.beta.name",
  "plugin.x.tx.alpha.level",
  "unrelated.key",
];

// Trailing wildcard: parity with the old prefix derivation.
results.trailing_wildcard = eq(
  H.matchStateKeys(KEYS, "plugin.dante.rx.*").map((m) => m.name),
  ["amp-1.1", "amp-1.2"],
);
// Mid-string wildcard: the old code produced an empty matrix here.
results.mid_string_wildcard = eq(
  H.matchStateKeys(KEYS, "plugin.x.tx.*.name").map((m) => m.name),
  ["alpha", "beta"],
);
// Full keys come back alongside the short names.
results.match_keys_returned = eq(
  H.matchStateKeys(KEYS, "plugin.x.tx.*.name").map((m) => m.key),
  ["plugin.x.tx.alpha.name", "plugin.x.tx.beta.name"],
);
// Dots in the pattern are literal, not regex any-char.
results.dots_are_literal = eq(H.matchStateKeys(["pluginXdanteXrxX1"], "plugin.dante.rx.*"), []);
// No wildcard or empty pattern enumerates nothing.
results.no_wildcard_empty = eq(H.matchStateKeys(KEYS, "plugin.dante.rx.1"), []);
results.empty_pattern_empty = eq(H.matchStateKeys(KEYS, ""), []);

console.log(JSON.stringify(results));
