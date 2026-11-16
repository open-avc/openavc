"use strict";
// The matrix's rules as the panel and the Builder run them, over a corpus the
// Python test sends on stdin.
//
// argv: the panel's modules/matrix.js and the Builder's matrixModel.ts. The
// panel's module is imported as it ships (an ES module with no build step);
// the TypeScript is bundled on the fly with the esbuild in
// openavc/web/programmer/node_modules, as the other TypeScript harnesses do.
//
// stdin: {"route": <tests/fixtures/matrix_route_cases.json>}. stdout: each
// copy's answers, in the corpus's order.
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
    // shows up as one answer differing from the other.
    matches: corpus.matches.map((c) => [copy.matches(c.a, c.b), copy.matches(c.b, c.a)]),
    reports: corpus.reports.map((c) => plain(copy.reports(c.source))),
  };
}

(async () => {
  const [panelPath, modelPath] = process.argv.slice(2);
  const panel = await import(pathToFileURL(panelPath).href);
  const builder = loadTypeScript(modelPath);
  const request = JSON.parse(fs.readFileSync(0, "utf8"));
  const copies = {
    panel: { value: panel.routeValue, matches: panel.routeMatches, reports: panel.sourceReports },
    builder: {
      value: builder.matrixRouteValue,
      matches: builder.matrixRouteMatches,
      reports: builder.matrixSourceReports,
    },
  };
  const out = {};
  for (const [name, copy] of Object.entries(copies)) {
    out[name] = { route: routeAnswers(copy, request.route) };
  }
  process.stdout.write(JSON.stringify(out));
})().catch((err) => {
  process.stderr.write(String((err && err.stack) || err));
  process.exit(1);
});
