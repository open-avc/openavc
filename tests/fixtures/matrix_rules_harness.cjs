"use strict";
// The matrix's rules as the panel and the Builder run them, over a corpus the
// Python test sends on stdin.
//
// argv: the panel's modules/matrix.js and the Builder's matrixModel.ts. The
// panel's module is imported as it ships (an ES module with no build step);
// the TypeScript is bundled on the fly with the esbuild in
// openavc/web/programmer/node_modules, as the other TypeScript harnesses do.
//
// stdin: {"route": <tests/fixtures/matrix_route_cases.json>, "naming": [...]},
// either part optional. Each naming case carries its axis, the authored spec,
// the device's readings and the entries the server resolved the spec to.
// stdout: each copy's answers, in the corpus's order: the route rule from
// both; from the panel, the name it draws for each entry the server resolved;
// from the Builder, the entries it resolves the spec to itself.
const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");
const esbuild = require("esbuild");

function loadTypeScript(file) {
  const built = esbuild.buildSync({
    entryPoints: [file],
    bundle: true,
    format: "cjs",
    platform: "node",
    write: false,
    logLevel: "silent",
  });
  const moduleObj = { exports: {} };
  const fn = new Function("exports", "require", "module", "__filename", "__dirname",
    built.outputFiles[0].text);
  fn(moduleObj.exports, require, moduleObj, file, path.dirname(file));
  return moduleObj.exports;
}

// undefined has no JSON: an answer of "nothing" is null on every side.
const plain = (v) => (v === undefined ? null : v);

function routeAnswers(copy, corpus) {
  return {
    values: corpus.values.map((c) => plain(copy.value(c.reported))),
    // Asked both ways round: the rule is symmetric, and a copy that is not
    // shows up as one answer differing from the other. A case with no mode
    // is asked with none, so each copy's default is the one judged.
    matches: corpus.matches.map((c) => (c.mode === undefined
      ? [copy.matches(c.a, c.b), copy.matches(c.b, c.a)]
      : [copy.matches(c.a, c.b, c.mode), copy.matches(c.b, c.a, c.mode)])),
    modes: Object.keys(copy.modes || {}),
    reports: corpus.reports.map((c) => plain(copy.reports(c.source))),
  };
}

// The panel's own naming: the captions the matrix gives in its constructor,
// then the name it draws for each row. On a copy: the panel writes in place.
function panelNames(panel, c) {
  const entries = JSON.parse(JSON.stringify(c.entries));
  panel.captionUnnamed(entries, c.axis === "sources" ? "In" : "Out");
  const read = (key) => c.readings[key];
  return entries.map((entry) => panel.entryLabel(entry, read));
}

(async () => {
  const [panelPath, modelPath] = process.argv.slice(2);
  const panel = await import(pathToFileURL(panelPath).href);
  const builder = loadTypeScript(modelPath);
  const request = JSON.parse(fs.readFileSync(0, "utf8"));
  const copies = {
    panel: {
      value: panel.routeValue,
      matches: panel.routeMatches,
      reports: panel.sourceReports,
      modes: panel.ROUTE_MATCH_MODES,
    },
    builder: {
      value: builder.matrixRouteValue,
      matches: builder.matrixRouteMatches,
      reports: builder.matrixSourceReports,
      modes: builder.MATRIX_ROUTE_MATCH_MODES,
    },
  };
  const out = {};
  for (const [name, copy] of Object.entries(copies)) {
    out[name] = request.route ? { route: routeAnswers(copy, request.route) } : {};
  }
  if (request.naming) {
    out.panel.names = request.naming.map((c) => panelNames(panel, c));
    out.builder.resolved = request.naming.map((c) => builder.resolveMatrixAxis({ [c.axis]: c.spec }, c.axis));
  }
  process.stdout.write(JSON.stringify(out));
})().catch((err) => {
  process.stderr.write(String((err && err.stack) || err));
  process.exit(1);
});
