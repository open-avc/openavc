"use strict";
// Runs the Builder's stylesheet class scan (customCssHelpers.ts, bundled on the
// fly with the esbuild already in openavc/web/programmer/node_modules) over a set
// of stylesheets handed in as JSON, and prints the class names it found.
//
// The Python side runs the same stylesheets through
// openavc/core/custom_ui_review.py's stylesheet_class_names and compares. Two
// implementations exist because the Builder cannot call Python (it offers these
// names as suggestions while somebody types) and the AI cannot call TypeScript
// (it is told "you named a class that does not exist" at the write door). They
// have to mean the same thing by "a class this sheet defines", or one surface
// suggests a name the other reports as missing.
//
// argv[2] = path to customCssHelpers.ts
// argv[3] = path to a JSON file: { "<case name>": "<css>", ... }
const fs = require("fs");
const path = require("path");

const helpersPath = process.argv[2];
const casesPath = process.argv[3];

const esbuild = require("esbuild");
const built = esbuild.buildSync({
  entryPoints: [helpersPath],
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
fn(moduleObj.exports, require, moduleObj, helpersPath, path.dirname(helpersPath));
const H = moduleObj.exports;

const cases = JSON.parse(fs.readFileSync(casesPath, "utf8"));
const out = {};
for (const [name, css] of Object.entries(cases)) {
  out[name] = H.stylesheetClassNames(css);
}
process.stdout.write(JSON.stringify(out));
