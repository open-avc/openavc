"use strict";
// Loads the transport panel helpers (transportPickerHelpers.ts — React-free
// pure logic behind the Driver Builder's transport config form) bundled on
// the fly with the esbuild already in openavc/web/programmer/node_modules, and
// replays the defects the helpers exist to fix: the delimiter dropdown
// compared real control characters against escaped-text option values (an
// installed driver's CR delimiter matched nothing and a re-pick silently
// swapped the stored representation), numeric config inputs snapped blank
// and 0 to a magic default mid-edit, and credential defaults exported with
// no way to tell which fields carried secrets.
// Mirrors button_binding_helpers_harness.cjs. The Python wrapper skips when
// the Node toolchain or esbuild is absent rather than failing the
// Python-only CI gate.
const path = require("path");

const helpersPath = process.argv[2];

const esbuild = require("esbuild");
const built = esbuild.buildSync({
  entryPoints: [helpersPath],
  bundle: true,
  format: "cjs",
  platform: "node",
  write: false,
  logLevel: "silent",
});
const code = built.outputFiles[0].text;
const moduleObj = { exports: {} };
const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
fn(moduleObj.exports, require, moduleObj, helpersPath, path.dirname(helpersPath));
const H = moduleObj.exports;

const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const results = {};

// --- normalizeDelimiter: legacy escaped text forms become real chars ---
results.normalize_escaped_cr = H.normalizeDelimiter("\\r") === "\r";
results.normalize_escaped_crlf = H.normalizeDelimiter("\\r\\n") === "\r\n";
// Already-canonical values pass through untouched.
results.normalize_real_cr_untouched = H.normalizeDelimiter("\r") === "\r";
results.normalize_real_crlf_untouched = H.normalizeDelimiter("\r\n") === "\r\n";
// Non-delimiter text is left alone.
results.normalize_plain_text = H.normalizeDelimiter(";") === ";";

// --- displayDelimiter: control chars render as visible escapes ---
results.display_cr = H.displayDelimiter("\r") === "\\r";
results.display_crlf = H.displayDelimiter("\r\n") === "\\r\\n";
results.display_stx = H.displayDelimiter("\x02") === "\\x02";
results.display_printable = H.displayDelimiter(";") === ";";

// --- parseNumericField: blank/0 hold instead of snapping to a default ---
// Blank clears the field (null = unset the key).
results.blank_clears = H.parseNumericField("") === null;
// Zero is a value, not falsy-to-default.
results.zero_is_kept = H.parseNumericField("0") === 0;
results.int_parses = H.parseNumericField("8080") === 8080;
// Unparseable keystroke is ignored (undefined), not coerced.
results.garbage_ignored = H.parseNumericField("abc") === undefined;
results.float_parses = H.parseNumericField("0.5", true) === 0.5;
results.int_mode_truncates = H.parseNumericField("12.7") === 12;

// --- secretFieldsInConfig: which credential defaults are non-empty ---
results.no_config = eq(H.secretFieldsInConfig(undefined), []);
results.empty_config = eq(H.secretFieldsInConfig({}), []);
results.token_detected = eq(H.secretFieldsInConfig({ token: "abc123" }), ["token"]);
results.blank_token_ignored = eq(H.secretFieldsInConfig({ token: "" }), []);
results.both_detected = eq(
  H.secretFieldsInConfig({ token: "t", api_key: "k", port: 80 }),
  ["token", "api_key"],
);
// A field the driver's own config_schema flags `secret: true` counts too —
// the export warning must catch imported drivers whose secret fields aren't
// named token/api_key.
results.schema_flagged_secret_detected = eq(
  H.secretFieldsInConfig(
    { pin: "hunter2", zone: "A" },
    { pin: { type: "string", secret: true }, zone: { type: "string" } },
  ),
  ["pin"],
);
results.schema_flagged_blank_ignored = eq(
  H.secretFieldsInConfig({ pin: "" }, { pin: { secret: true } }),
  [],
);

console.log(JSON.stringify(results));
