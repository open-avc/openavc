"use strict";
// Loads the routing-matrix helpers (routingMatrixHelpers.ts — React-free
// pure logic behind the plugin surface configurator's crosspoint matrix)
// bundled on the fly with the esbuild in openavc/web/programmer/node_modules,
// and replays what they decide: whether a crosspoint is routed (its row's
// route key read against its column's label, in the layout's mode), and
// which state keys a row or column pattern matches ('*' anywhere, not only
// as a suffix). Mirrors transport_picker_helpers_harness.cjs; the Python
// wrapper skips when the Node toolchain is absent.
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

// --- routeMatchMode: the layout's mode, else the default ---
results.default_mode_is_exact = H.SURFACE_ROUTE_MATCH_DEFAULT === "exact";
results.exact_mode_read = H.routeMatchMode("exact") === "exact";
results.port_mode_read = H.routeMatchMode("port") === "port";
results.no_mode_is_default = H.routeMatchMode(undefined) === "exact";
results.unknown_mode_is_default = H.routeMatchMode("fuzzy") === "exact";
results.inherited_name_is_no_mode = H.routeMatchMode("toString") === "exact";
results.non_text_mode_is_default = H.routeMatchMode(1) === "exact";

// --- crosspointRouted: the row's route key names the column's source ---
const ROUTES = {
  "plugin.acme_router.source.amp.1": "Stagebox / 01",
  "plugin.acme_router.source.amp.2": "",
};
const read = (key) => ROUTES[key];
const ROUTE_KEY = "plugin.acme_router.source.{row}";
results.routed_by_its_label = H.crosspointRouted(ROUTE_KEY, "exact", read, "amp.1", "stagebox / 01") === true;
results.shared_number_is_another_source = H.crosspointRouted(ROUTE_KEY, "exact", read, "amp.1", "Laptop / 01") === false;
results.shared_number_matches_in_port_mode = H.crosspointRouted(ROUTE_KEY, "port", read, "amp.1", "Laptop / 01") === true;
results.routed_to_nothing = H.crosspointRouted(ROUTE_KEY, "exact", read, "amp.2", "") === false;
results.no_route_key_never_routed = H.crosspointRouted(undefined, "exact", read, "amp.1", "Stagebox / 01") === false;
results.unpublished_row_never_routed = H.crosspointRouted(ROUTE_KEY, "exact", read, "amp.9", "Stagebox / 01") === false;

// --- axisCaption: the name the plugin publishes, else the token ---
results.caption_is_the_published_name = H.axisCaption("MIC-01 / 01", "mic_01.1") === "MIC-01 / 01";
results.caption_of_a_blank_name_is_the_token = H.axisCaption("  ", "mic_01.1") === "mic_01.1";
results.caption_of_no_name_is_the_token = H.axisCaption(undefined, "mic_01.1") === "mic_01.1";
results.caption_of_a_number_is_the_token = H.axisCaption(3, "mic_01.1") === "mic_01.1";

// --- matchStateKeys: '*' matches anywhere, not just as a suffix ---
const KEYS = [
  "plugin.acme_router.rx.amp-1.1",
  "plugin.acme_router.rx.amp-1.2",
  "plugin.acme_router.tx.mixer.1",
  "plugin.x.tx.alpha.name",
  "plugin.x.tx.beta.name",
  "plugin.x.tx.alpha.level",
  "unrelated.key",
];

// Trailing wildcard: parity with the old prefix derivation.
results.trailing_wildcard = eq(
  H.matchStateKeys(KEYS, "plugin.acme_router.rx.*").map((m) => m.name),
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
results.dots_are_literal = eq(H.matchStateKeys(["pluginXacme_routerXrxX1"], "plugin.acme_router.rx.*"), []);
// No wildcard or empty pattern enumerates nothing.
results.no_wildcard_empty = eq(H.matchStateKeys(KEYS, "plugin.acme_router.rx.1"), []);
results.empty_pattern_empty = eq(H.matchStateKeys(KEYS, ""), []);

console.log(JSON.stringify(results));
