"use strict";
// Bundles the real logViewHelpers.ts (with the esbuild already in
// openavc/web/programmer/node_modules) and runs deviceFilterPredicate over filter
// scenarios, printing {scenario: boolean} JSON to stdout. The Python wrapper
// asserts the verdicts, proving the System Log Device filter matches the
// entries the server actually produces. Skips happen on the Python side when
// the Node toolchain is absent.
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
const { deviceFilterPredicate } = moduleObj.exports;

// Entries shaped like the server produces them: driver/transport lines get a
// structured device field extracted from the "[id] " prefix; device_manager
// lifecycle lines phrase the id loosely and have device = "".
const results = {};
function check(name, deviceId, entry) {
  results[name] = deviceFilterPredicate(deviceId)(entry);
}

check("structured_match", "proj1", { device: "proj1", message: "[proj1] Poll failed" });
check("structured_other_device", "proj2", { device: "proj1", message: "[proj1] Poll failed" });
check("structured_case_insensitive", "PROJ1", { device: "proj1", message: "[proj1] Poll failed" });
check("mention_quoted", "proj1", { device: "", message: "Failed to connect 'proj1': timeout" });
check("mention_bare", "proj1", { device: "", message: "Device proj1 is disabled, skipping connection" });
check("mention_state_key", "proj1", { device: "", message: "state device.proj1.power -> on" });
check("mention_end_of_message", "proj1", { device: "", message: "Added device proj1" });
check("no_mention", "proj1", { device: "", message: "Registered driver: pjlink" });
check("substring_longer_id", "proj1", { device: "", message: "Failed to connect 'proj12': timeout" });
check("substring_prefixed_id", "proj1", { device: "", message: "Failed to connect 'my-proj1': timeout" });
check("id_with_underscore", "hdmi_matrix", { device: "", message: "Removed device 'hdmi_matrix'" });

process.stdout.write(JSON.stringify(results));
