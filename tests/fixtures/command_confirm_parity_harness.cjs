"use strict";
// Runs the Programmer's confirm rule (commandConfirm.ts, bundled on the fly
// with the esbuild already in openavc/web/programmer/node_modules) over the
// cases in command_confirm_cases.json and prints each answer. The Python
// wrapper runs the same cases through openavc/drivers/command_model.py and
// compares.
//
// argv[2] = path to commandConfirm.ts
// argv[3] = path to the cases JSON: { "cases": [{name, confirm, label}, ...] }
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
const C = moduleObj.exports;

const { cases } = JSON.parse(fs.readFileSync(casesPath, "utf8"));
const out = { question: C.CONFIRM_QUESTION, answers: {} };
for (const c of cases) {
  out.answers[c.name] = {
    sentence: C.confirmSentence(c.confirm, c.label),
    asks: C.asksFirst(c.confirm),
    sets_its_own: C.setsItsOwnConfirm(c.confirm),
    live_test: C.commandConfirmMessage({ confirm: c.confirm }, c.label),
  };
}

process.stdout.write(JSON.stringify(out));
