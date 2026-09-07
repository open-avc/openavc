"use strict";
// Runs the Builder's device-reference walk (deviceUtils.ts, bundled on the fly
// with the esbuild already in openavc/web/programmer/node_modules) over a set of
// projects handed in as JSON, and prints what it found. The Python wrapper runs
// the same projects through openavc/core/device_references.py and compares the
// sentences one for one.
//
// The point of the comparison is that a human clicking Delete and an AI asking
// check_references must not be told different things about the same device.
// Two implementations exist on purpose -- the Builder's dialog runs on project
// state the server has never been sent -- and two implementations of one rule
// drift the moment either is edited, silently, because each looks right alone.
//
// argv[2] = path to deviceUtils.ts
// argv[3] = path to a JSON file: { "<case name>": {project, device_id}, ... }
const fs = require("fs");
const path = require("path");

const utilsPath = process.argv[2];
const casesPath = process.argv[3];

const esbuild = require("esbuild");
const built = esbuild.buildSync({
  entryPoints: [utilsPath],
  bundle: true,
  format: "cjs",
  platform: "node",
  write: false,
  logLevel: "silent",
});
const moduleObj = { exports: {} };
const fn = new Function(
  "exports", "require", "module", "__filename", "__dirname",
  built.outputFiles[0].text,
);
fn(moduleObj.exports, require, moduleObj, utilsPath, path.dirname(utilsPath));
const U = moduleObj.exports;

const cases = JSON.parse(fs.readFileSync(casesPath, "utf8"));
const out = {};
for (const [name, { project, device_id }] of Object.entries(cases)) {
  out[name] = U.findDeviceReferences(project, device_id);
}

process.stdout.write(JSON.stringify(out));
