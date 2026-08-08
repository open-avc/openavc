"use strict";
// Bundles the real driverTemplates.ts (with the esbuild already in
// openavc/web/programmer/node_modules) and generates driver source for every
// template, printing {scenario: source} JSON to stdout. The Python wrapper
// compiles each generated source and parses DRIVER_INFO from the AST, so the
// scaffolding is proven syntactically valid Python with the intended
// metadata — not just string-matched. Skips happen on the Python side when
// the Node toolchain is absent.
const path = require("path");

const templatesPath = process.argv[2];

const esbuild = require("esbuild");
const built = esbuild.buildSync({
  entryPoints: [templatesPath],
  bundle: true,
  format: "cjs",
  platform: "node",
  write: false,
  logLevel: "silent",
});
const code = built.outputFiles[0].text;
const moduleObj = { exports: {} };
const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
fn(moduleObj.exports, require, moduleObj, templatesPath, path.dirname(templatesPath));
const { DRIVER_TEMPLATES } = moduleObj.exports;

const NORMAL = {
  id: "acme_widget",
  name: "Acme Widget 3000",
  manufacturer: "Acme",
  category: "utility",
};

// Free-text fields as a hostile user could type them: quotes, backslashes,
// a triple-quote run, and a newline. Must stay inert text in the generated
// Python (kept in sync with tests/test_driver_templates_codegen.py).
const HOSTILE = {
  id: "acme_widget",
  name: 'Acme "Pro" \\ Series """ x\nLine2',
  manufacturer: 'O"Corp\\',
  category: "utility",
};

const results = {};
for (const t of DRIVER_TEMPLATES) {
  results[`${t.id}__normal`] = t.generateCode({ ...NORMAL, transport: t.transport });
  results[`${t.id}__hostile`] = t.generateCode({ ...HOSTILE, transport: t.transport });
}

// The minimal template accepts whatever transport the dialog selected; cover
// every selectable transport so each gets a config block its transport reads.
const minimal = DRIVER_TEMPLATES.find((t) => t.id === "minimal");
for (const tr of ["tcp", "serial", "http", "udp", "osc"]) {
  results[`minimal__${tr}`] = minimal.generateCode({ ...NORMAL, transport: tr });
}

process.stdout.write(JSON.stringify(results));
