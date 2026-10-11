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

const DRIVER = "openavc.drivers.configurable";
const MANAGER = "openavc.core.device_manager";
check("structured_match", "proj1", { source: DRIVER, device: "proj1", message: "[proj1] Poll failed" });
check("structured_other_device", "proj2", { source: DRIVER, device: "proj1", message: "[proj1] Poll failed" });
check("structured_case_insensitive", "PROJ1", { source: DRIVER, device: "proj1", message: "[proj1] Poll failed" });
check("mention_quoted", "proj1", { source: MANAGER, device: "", message: "Failed to connect 'proj1': timeout" });
check("mention_bare", "proj1", { source: MANAGER, device: "", message: "Device proj1 is disabled, skipping connection" });
check("mention_state_key", "proj1", { source: "openavc.core.engine", device: "", message: "state device.proj1.power -> on" });
check("mention_end_of_message", "proj1", { source: MANAGER, device: "", message: "Added device proj1" });
check("mention_in_user_script", "proj1", { source: "openavc_user_scripts.room", device: "", message: "Turning proj1 off" });
check("no_mention", "proj1", { source: "openavc.drivers.driver_loader", device: "", message: "Registered driver: pjlink" });
check("substring_longer_id", "proj1", { source: MANAGER, device: "", message: "Failed to connect 'proj12': timeout" });
check("substring_prefixed_id", "proj1", { source: MANAGER, device: "", message: "Failed to connect 'my-proj1': timeout" });
check("id_with_underscore", "hdmi_matrix", { source: MANAGER, device: "", message: "Removed device 'hdmi_matrix'" });
// A library's line can name the device in a URL; it was never about it.
check("library_url_mention", "audio_1", {
  source: "httpx",
  device: "",
  message: 'HTTP Request: GET http://127.0.0.1:19500/api/devices/audio_1 "HTTP/1.1 200 OK"',
});

process.stdout.write(JSON.stringify(results));
