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

// --- markerFromStoredError (the server's record, which the log ring loses) ---
const stored = (over) => Object.assign({
  count: 1, handler: "handle", event: "custom.boom",
  error: "kaboom", traceback: "", at: 0,
}, over);

{
  const r = H.markerFromStoredError(stored({
    traceback:
      'Traceback (most recent call last):\n' +
      '  File "/srv/openavc/core/script_engine.py", line 759, in wrapped\n' +
      '  File "/data/projects/demo/scripts/room_logic.py", line 12, in handle\n' +
      'ValueError: kaboom\n',
  }), "room_logic.py");
  results.stored_marker_takes_the_line_in_this_file = {
    pass: r !== null && r.line === 12 && r.message === "handle: ValueError: kaboom", detail: r,
  };
}
{
  // A handler that fails inside a helper it called has two frames in the file.
  // The one worth opening the editor at is the one that actually raised.
  const r = H.markerFromStoredError(stored({
    traceback:
      '  File "/data/projects/demo/scripts/room_logic.py", line 12, in handle\n' +
      '  File "/data/projects/demo/scripts/room_logic.py", line 30, in helper\n' +
      'ValueError: kaboom\n',
  }), "room_logic.py");
  results.stored_marker_takes_the_deepest_frame = {
    pass: r !== null && r.line === 30, detail: r,
  };
}
{
  // The record is written on the server, so its paths are the server's -- a
  // Windows host reports backslashes to a browser that knows only the name.
  const r = H.markerFromStoredError(stored({
    traceback: '  File "C:\\\\ProgramData\\\\openavc\\\\scripts\\\\room_logic.py", line 7, in handle\n',
  }), "room_logic.py");
  results.stored_marker_matches_a_windows_path = {
    pass: r !== null && r.line === 7, detail: r,
  };
}
{
  const r = H.markerFromStoredError(stored({
    traceback: '  File "/data/projects/demo/scripts/other.py", line 12, in handle\n',
  }), "room_logic.py");
  results.stored_marker_ignores_another_file = { pass: r === null, detail: r };
}
{
  // A timeout has no traceback at all. Guessing a line would be worse than
  // leaving the editor unmarked -- the list row still says it failed.
  const r = H.markerFromStoredError(stored({ error: "timed out after 30s" }), "room_logic.py");
  results.stored_marker_silent_without_a_traceback = { pass: r === null, detail: r };
}
{
  const r = H.markerFromStoredError(undefined, "room_logic.py");
  results.stored_marker_silent_without_a_record = { pass: r === null, detail: r };
}

// --- describeStoredError (str(exc) is not always a sentence) ---
{
  // A KeyError stringifies to the key alone. On its own the author is told
  // `'laptop'` and learns nothing about what went wrong.
  const r = H.describeStoredError(stored({
    error: "'laptop'",
    traceback: '  File "x.py", line 7, in handle\nKeyError: \'laptop\'\n',
  }));
  results.described_error_puts_the_type_in_front = {
    pass: r === "KeyError: 'laptop'", detail: r,
  };
}
{
  // A timeout has no traceback, and its message is already a sentence.
  const r = H.describeStoredError(stored({
    error: "timed out after 30s", traceback: "",
  }));
  results.described_error_falls_back_without_a_traceback = {
    pass: r === "timed out after 30s", detail: r,
  };
}
{
  // Only the traceback's own last line qualifies, and only when it really is
  // this error with a type in front -- never some other line of a frame.
  const r = H.describeStoredError(stored({
    error: "kaboom",
    traceback: '  File "x.py", line 7, in handle\n    raise ValueError("nope")\n',
  }));
  results.described_error_refuses_an_unrelated_last_line = {
    pass: r === "kaboom", detail: r,
  };
}
{
  const r = H.markerFromStoredError(stored({
    error: "'laptop'",
    traceback:
      '  File "/data/projects/demo/scripts/room_logic.py", line 7, in select_source\n' +
      "KeyError: 'laptop'\n",
    handler: "select_source",
  }), "room_logic.py");
  results.stored_marker_carries_the_readable_error = {
    pass: r !== null && r.message === "select_source: KeyError: 'laptop'", detail: r,
  };
}

process.stdout.write(JSON.stringify(results));
