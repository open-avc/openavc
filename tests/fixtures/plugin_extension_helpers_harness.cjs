"use strict";
// Loads the plugin extension renderer helpers (pluginExtensionHelpers.ts —
// React-free pure logic behind PluginExtensions.tsx) bundled on the fly with
// the esbuild already in openavc/web/programmer/node_modules, and replays the
// defects the helpers exist to fix: the driver-id glob handled only a single
// trailing '*' (a '*_pro' or 'a*b*c' pattern silently never matched, so a
// plugin's device panel or context action never appeared), and a boolean
// status-card metric rendered plugin-published string 'false'/'0' as 'Yes'.
// Mirrors transport_picker_helpers_harness.cjs. The Python wrapper skips
// when the Node toolchain or esbuild is absent rather than failing the
// Python-only CI gate.
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

// --- matchesDriverGlob: documented glob syntax, '*' anywhere ---
results.glob_trailing = H.matchesDriverGlob("dante_controller", "dante_*") === true;
results.glob_trailing_no_match = H.matchesDriverGlob("qsc_qrc", "dante_*") === false;
results.glob_exact = H.matchesDriverGlob("qsc_qrc", "qsc_qrc") === true;
results.glob_exact_no_match = H.matchesDriverGlob("qsc_qrc", "qsc") === false;
// The headline defect: leading and multi-wildcard patterns never matched.
results.glob_leading = H.matchesDriverGlob("mixer_pro", "*_pro") === true;
results.glob_leading_no_match = H.matchesDriverGlob("mixer_lite", "*_pro") === false;
results.glob_multi = H.matchesDriverGlob("acme_video_wall", "acme_*_wall") === true;
results.glob_multi_no_match = H.matchesDriverGlob("acme_video_panel", "acme_*_wall") === false;
// Anchored: a prefix pattern without '*' is not a startsWith match.
results.glob_anchored = H.matchesDriverGlob("dante_controller", "dante") === false;
results.glob_blank_no_match = H.matchesDriverGlob("dante_controller", "") === false;

// --- formatMetric: boolean format coerces string state values ---
results.metric_bool_true = H.formatMetric(true, "boolean") === "Yes";
results.metric_bool_false = H.formatMetric(false, "boolean") === "No";
// The headline defect: plugin-published 'false'/'0' strings are truthy in JS.
results.metric_string_false = H.formatMetric("false", "boolean") === "No";
results.metric_string_zero = H.formatMetric("0", "boolean") === "No";
results.metric_string_true = H.formatMetric("true", "boolean") === "Yes";
results.metric_number_zero = H.formatMetric(0, "boolean") === "No";
results.metric_null_dash = H.formatMetric(null, "boolean") === "—";
results.metric_plain_passthrough = H.formatMetric(42, "number") === "42";

// --- filterPluginLog: plugin scoping + recency cap ---
const mkEntry = (i, msg, source) => ({
  timestamp: 1000 + i,
  level: "INFO",
  source: source || "openavc.core.engine",
  message: msg,
});
const entries = [];
for (let i = 0; i < 200; i++) entries.push(mkEntry(i, `[Plugin:foo] line ${i}`));
entries.push(mkEntry(500, "unrelated line"));
entries.push(mkEntry(501, "loader line", "openavc.core.plugin_loader"));
const filtered = H.filterPluginLog(entries, "foo");
results.log_filter_caps_50 = filtered.length === 50;
results.log_filter_excludes_unrelated =
  filtered.every((e) => e.message.includes("[Plugin:foo]") || e.source === "openavc.core.plugin_loader");
results.log_filter_keeps_loader = filtered.some((e) => e.source === "openavc.core.plugin_loader");
results.log_filter_other_plugin =
  H.filterPluginLog([mkEntry(0, "[Plugin:bar] x")], "foo").length === 0;

// --- sameLogTail: cheap change detection for the interval refresh ---
const a = [mkEntry(1, "[Plugin:foo] a"), mkEntry(2, "[Plugin:foo] b")];
results.tail_same = H.sameLogTail(a, [...a]) === true;
results.tail_diff_len = H.sameLogTail(a, a.slice(0, 1)) === false;
results.tail_diff_last = H.sameLogTail(a, [a[0], mkEntry(3, "[Plugin:foo] c")]) === false;
results.tail_empty_same = H.sameLogTail([], []) === true;

const failed = Object.entries(results).filter(([, ok]) => ok !== true);
if (failed.length) {
  process.stderr.write(`failed: ${failed.map(([k]) => k).join(", ")}\n`);
}
process.stdout.write(JSON.stringify(results));
