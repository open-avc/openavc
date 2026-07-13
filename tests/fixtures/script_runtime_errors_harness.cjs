"use strict";
// Loads the script editor's runtime-error helpers (scriptRuntimeErrors.ts —
// React-free pure logic, only `import type` deps which esbuild strips) and
// checks the marker extraction and the narrow-subscription selector that make
// the ScriptView error markers update when new log entries arrive. Mirrors
// project_import_harness.cjs. The Python wrapper skips when the Node toolchain
// or esbuild is absent rather than failing the Python-only CI gate.
const fs = require("fs");
const path = require("path");

const helpersPath = process.argv[2];
const src = fs.readFileSync(helpersPath, "utf8");

const esbuild = require("esbuild");
const { code } = esbuild.transformSync(src, { loader: "ts", format: "cjs" });
const moduleObj = { exports: {} };
const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
fn(moduleObj.exports, require, moduleObj, helpersPath, path.dirname(helpersPath));
const H = moduleObj.exports;

const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const mk = (id, level, category, message) => ({
  id, timestamp: 0, level, source: "", category, message, device: "",
});
const results = {};

// --- extractScriptRuntimeErrors ---
{
  const entries = [mk(1, "ERROR", "script", "myscript: SyntaxError at line 12")];
  const r = H.extractScriptRuntimeErrors(entries, "myscript", "myscript.py");
  results.m309_extract_matches_by_id = {
    pass: eq(r, [{ line: 12, message: "myscript: SyntaxError at line 12" }]), detail: r,
  };
}
{
  const entries = [mk(1, "ERROR", "script", "error in scripts/foo.py, line 3: boom")];
  const r = H.extractScriptRuntimeErrors(entries, "foo", "scripts/foo.py");
  results.m309_extract_matches_by_file = { pass: r.length === 1 && r[0].line === 3, detail: r };
}
{
  const entries = [
    mk(1, "INFO", "script", "myscript ran, line 5"),   // not ERROR
    mk(2, "ERROR", "device", "device x error line 9"), // not script category
  ];
  const r = H.extractScriptRuntimeErrors(entries, "myscript", "myscript.py");
  results.m309_extract_filters_non_error = { pass: r.length === 0, detail: r };
}
{
  const entries = [mk(1, "ERROR", "script", "otherscript failed at line 7")];
  const r = H.extractScriptRuntimeErrors(entries, "myscript", "myscript.py");
  results.m309_extract_filters_other_script = { pass: r.length === 0, detail: r };
}
{
  const entries = [mk(1, "ERROR", "script", "myscript blew up (no location)")];
  const r = H.extractScriptRuntimeErrors(entries, "myscript", "myscript.py");
  results.m309_extract_needs_line_number = { pass: r.length === 0, detail: r };
}
{
  const entries = [mk(1, "ERROR", "script", "myscript error at line 4\nTraceback...\n  more")];
  const r = H.extractScriptRuntimeErrors(entries, "myscript", "myscript.py");
  results.m309_extract_message_first_line = {
    pass: r.length === 1 && r[0].message === "myscript error at line 4", detail: r,
  };
}

// --- latestScriptErrorId (the narrow reactive trigger) ---
{
  const entries = [
    mk(10, "ERROR", "script", "s line 1"),
    mk(11, "ERROR", "script", "s line 2"),
    mk(12, "INFO", "script", "s ok"),   // later non-error must not change the id
  ];
  results.m309_latest_id_returns_last_script_error = {
    pass: H.latestScriptErrorId(entries) === 11, detail: H.latestScriptErrorId(entries),
  };
}
{
  const entries = [mk(1, "INFO", "script", "ok"), mk(2, "ERROR", "device", "dev")];
  results.m309_latest_id_zero_when_none = {
    pass: H.latestScriptErrorId(entries) === 0, detail: H.latestScriptErrorId(entries),
  };
}

process.stdout.write(JSON.stringify(results));
