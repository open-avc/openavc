"use strict";
// Bundles pluginsView.helpers.ts (with the esbuild already in web/programmer/
// node_modules) and exercises isPluginIncompatible — the M-174 fix that reads
// the backend's truthful `compatible` flag instead of gating only on
// status === "incompatible". Prints JSON results to stdout; the Python wrapper
// skips when the Node toolchain or esbuild is absent.
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
const V = moduleObj.exports;
const f = V.isPluginIncompatible;

const results = {};

// The truthful `compatible` flag is authoritative.
results.m174_compatible_false_is_incompatible = {
  pass: f({ compatible: false, status: "stopped" }) === true,
  detail: f({ compatible: false, status: "stopped" }),
};
results.m174_compatible_true_is_compatible = {
  pass: f({ compatible: true, status: "stopped" }) === false,
  detail: f({ compatible: true, status: "stopped" }),
};

// The core bug: a plugin discovered but not started has compatible:false yet a
// status that isn't "incompatible" — status-only gating let it through.
results.m174_unstarted_incompatible_caught = {
  pass:
    f({ compatible: false, status: "stopped" }) === true &&
    f({ status: "stopped" }) === false,
  detail: null,
};

// Fall back to the status string only when the flag is absent (older payloads).
results.m174_status_fallback_incompatible = {
  pass: f({ status: "incompatible" }) === true,
  detail: null,
};
results.m174_status_fallback_compatible = {
  pass: f({ status: "running" }) === false,
  detail: null,
};

// `compatible` wins over a stale/contradictory status string.
results.m174_compatible_true_overrides_status = {
  pass: f({ compatible: true, status: "incompatible" }) === false,
  detail: null,
};

process.stdout.write(JSON.stringify(results));
