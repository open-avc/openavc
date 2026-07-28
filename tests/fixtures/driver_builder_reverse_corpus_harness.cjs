"use strict";
// Runs the real Driver Builder validator (validateDriver.ts, bundled on the
// fly with the esbuild already in web/programmer/node_modules) over every
// shipped driver definition and reports what it flagged.
//
// The other direction — every definition the loader refuses must be an error
// in the Builder — is driver_builder_validate_harness.cjs. This one is the
// reverse: a driver the platform loads and ships must raise nothing. The
// Python wrapper parses the YAML and hands the definitions over as JSON, so
// the driver library is resolved in one place (tests/gates.py's corpus rules)
// rather than reimplemented here.
const path = require("path");
const fs = require("fs");

const validatorPath = process.argv[2];
// {driver name: definition}, written by the Python wrapper.
const driversPath = process.argv[3];

const esbuild = require("esbuild");
const built = esbuild.buildSync({
  entryPoints: [validatorPath],
  bundle: true,
  format: "cjs",
  platform: "node",
  write: false,
  logLevel: "silent",
});
const code = built.outputFiles[0].text;
const moduleObj = { exports: {} };
const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
fn(moduleObj.exports, require, moduleObj, validatorPath, path.dirname(validatorPath));
const V = moduleObj.exports;

const drivers = JSON.parse(fs.readFileSync(driversPath, "utf8"));
const results = {};

// Every shipped driver is a sibling of every other, which is what the
// Builder sees with a full library loaded. Passing them lets the duplicate-id
// rule see the real corpus instead of an empty one.
const siblings = Object.values(drivers);

for (const [name, definition] of Object.entries(drivers)) {
  try {
    const issues = V.validateDriver(definition, siblings, definition.id) || [];
    results[name] = {
      errors: issues
        .filter((i) => i.severity === "error")
        .map((i) => `${i.field || i.section || "?"}: ${i.message}`),
      warnings: issues
        .filter((i) => i.severity !== "error")
        .map((i) => `${i.field || i.section || "?"}: ${i.message}`),
      threw: null,
    };
  } catch (e) {
    results[name] = {
      errors: [],
      warnings: [],
      threw: String(e && e.message ? e.message : e),
    };
  }
}

process.stdout.write(JSON.stringify(results));
