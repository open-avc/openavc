"use strict";
// Runs the Programmer's serial line reading (serialLine.ts, bundled on the fly
// with the esbuild already in openavc/web/programmer/node_modules) over the
// cases in serial_line_cases.json and prints each answer. The Python wrapper
// runs the same cases through openavc/drivers/serial_line.py read_line_setting and
// compares.
//
// argv[2] = path to serialLine.ts
// argv[3] = path to the cases JSON: { "cases": [{name, key, value}, ...] }
const fs = require("fs");
const path = require("path");

const sourcePath = process.argv[2];
const casesPath = process.argv[3];

const esbuild = require("esbuild");
const built = esbuild.buildSync({
  entryPoints: [sourcePath],
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
fn(moduleObj.exports, require, moduleObj, sourcePath, path.dirname(sourcePath));
const S = moduleObj.exports;

const { cases } = JSON.parse(fs.readFileSync(casesPath, "utf8"));
const out = { answers: {} };
for (const c of cases) {
  out.answers[c.name] = S.readLineSetting(c.key, c.value);
}

process.stdout.write(JSON.stringify(out));
