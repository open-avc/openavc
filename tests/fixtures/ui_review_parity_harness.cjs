"use strict";
// Runs the Builder's page review (uiBuilderHelpers.ts, bundled on the fly with
// the esbuild already in openavc/web/programmer/node_modules) over a set of projects
// handed in as JSON, and prints its findings. The Python wrapper runs the same
// projects through openavc/ui/page_review.py and compares message for message.
//
// The point of the comparison is that a human and the AI must not reach
// different verdicts about the same file. Both surfaces read the same measured
// numbers, but two implementations of the same arithmetic drift the moment one
// of them is edited -- and the drift is silent, because each side looks right
// on its own.
//
// argv[2] = path to uiBuilderHelpers.ts
// argv[3] = path to a JSON file: { "<case name>": <project>, ... }
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

for (const [name, project] of Object.entries(cases)) {
  const theme = H.sliderThemeDefaults(project);
  const findings = [];
  // Pages in order, then masters in order -- the same walk the Python side
  // makes, so a difference in the lists is a difference in the verdicts and
  // never a difference in how they were collected.
  for (const page of project.ui.pages) {
    for (const f of H.reviewPage(page, { theme })) {
      findings.push({ element_id: f.elementId, kind: f.kind, message: f.message });
    }
  }
  for (const master of project.ui.master_elements || []) {
    for (const f of H.reviewMasterElement(master, theme)) {
      findings.push({ element_id: f.elementId, kind: f.kind, message: f.message });
    }
  }
  out[name] = findings;
}

process.stdout.write(JSON.stringify(out));
