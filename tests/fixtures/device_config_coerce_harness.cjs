"use strict";
// Loads the real deviceConfigCoerce.ts (transpiled on the fly with the esbuild
// already in openavc/web/programmer/node_modules) and exercises coerceConfigValue,
// printing JSON results to stdout. Mirrors color_utils_harness.cjs: no build
// step, and the Python wrapper skips when the toolchain is absent.
const fs = require("fs");
const path = require("path");

const srcPath = process.argv[2];
const src = fs.readFileSync(srcPath, "utf8");

const esbuild = require("esbuild");
const { code } = esbuild.transformSync(src, { loader: "ts", format: "cjs" });
const moduleObj = { exports: {} };
const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
fn(moduleObj.exports, require, moduleObj, srcPath, path.dirname(srcPath));
const C = moduleObj.exports;

const coerce = C.coerceConfigValue;
const results = {};

// H-061: an object-typed field with a valid JSON object → parsed object. This
// is the case the Add dialog used to store as a raw string, breaking command
// sending at runtime.
{
  const r = coerce('{"on": "PWR ON", "off": "PWR OFF"}', "object");
  results.object_valid = {
    pass: r.ok === true && r.value && r.value.on === "PWR ON" && r.value.off === "PWR OFF",
    detail: r,
  };
}
// Object field with invalid JSON → error, not silently stored as a string.
{
  const r = coerce("not json {", "object");
  results.object_invalid_json = { pass: r.ok === false && /JSON/.test(r.error), detail: r };
}
// Object field that parses to a non-object (array / number) → error.
{
  const r = coerce("[1, 2, 3]", "object");
  results.object_array_rejected = { pass: r.ok === false, detail: r };
}
{
  const r = coerce("42", "object");
  results.object_number_rejected = { pass: r.ok === false, detail: r };
}
// boolean
{
  const t = coerce("true", "boolean");
  const f = coerce("false", "boolean");
  results.boolean = { pass: t.ok && t.value === true && f.ok && f.value === false, detail: [t, f] };
}
// integer / number
{
  const i = coerce("23", "integer");
  const n = coerce("1.5", "number");
  results.numbers = { pass: i.ok && i.value === 23 && n.ok && n.value === 1.5, detail: [i, n] };
}
// a non-simple "number" string (IP-like) stays a string, not NaN
{
  const r = coerce("192.168.1.5", "integer");
  results.numberish_string_kept = { pass: r.ok && r.value === "192.168.1.5", detail: r };
}
// text → raw string preserved (no coercion)
{
  const r = coerce("line1\nline2", "text");
  results.text_raw = { pass: r.ok && r.value === "line1\nline2", detail: r };
}
// plain string field → string passthrough
{
  const r = coerce("PWR ON", "string");
  results.string_passthrough = { pass: r.ok && r.value === "PWR ON", detail: r };
}
// untyped field with a JSON object value still parses (Edit-dialog back-compat)
{
  const r = coerce('{"a": 1}', "");
  results.untyped_json_object = { pass: r.ok && r.value && r.value.a === 1, detail: r };
}

process.stdout.write(JSON.stringify(results));
