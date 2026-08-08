"use strict";
// Loads the macro step editor's typed-value helpers (macroValueHelpers.ts —
// React-free pure logic behind the state.set value input and the event.emit
// payload editor) bundled on the fly with the esbuild already in
// openavc/web/programmer/node_modules, and replays the defects the helpers exist to
// fix: the state.set value input guessed a type from how the text looked
// (a literal string '0' or 'true' could never be authored), and numeric
// step fields snapped blank/invalid input to 0.
// Mirrors transport_picker_helpers_harness.cjs. The Python wrapper skips
// when the Node toolchain or esbuild is absent rather than failing the
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

// --- valueKind: the stored value's own type drives the editor ---
results.kind_number = H.valueKind(50) === "number";
results.kind_boolean = H.valueKind(false) === "boolean";
results.kind_text = H.valueKind("50") === "text";
results.kind_null_is_text = H.valueKind(null) === "text";

// --- convertValue: explicit type switches convert sanely ---
results.convert_text_to_number = H.convertValue("50", "number") === 50;
results.convert_junk_to_number = H.convertValue("abc", "number") === 0;
results.convert_text_to_boolean = H.convertValue("true", "boolean") === true;
results.convert_number_to_boolean = H.convertValue(50, "boolean") === false;
results.convert_number_to_text = H.convertValue(50, "text") === "50";
results.convert_null_to_text = H.convertValue(null, "text") === "";

// --- parseTypedInput: text stays verbatim; numbers never snap to 0 ---
// The headline defect: a text value of "true" / "0" must stay a string.
results.text_true_stays_string = H.parseTypedInput("true", "text") === "true";
results.text_zero_stays_string = H.parseTypedInput("0", "text") === "0";
results.text_numeric_stays_string = H.parseTypedInput("123", "text") === "123";
// Number mode: blank and junk are "not usable yet" (undefined = keep prior),
// zero is a real value.
results.number_blank_undefined = H.parseTypedInput("", "number") === undefined;
results.number_junk_undefined = H.parseTypedInput("abc", "number") === undefined;
results.number_zero_kept = H.parseTypedInput("0", "number") === 0;
results.number_float_parses = H.parseTypedInput("12.5", "number") === 12.5;
results.boolean_parses = H.parseTypedInput("true", "boolean") === true;

// --- payload row editing: insertion order, typed values, empty cleanup ---
const p = { level: 75, source: "hdmi_1" };
results.payload_update_value = eq(
  H.updatePayloadRow(p, 0, "level", 80),
  { level: 80, source: "hdmi_1" },
);
results.payload_rename_key_keeps_order = eq(
  H.updatePayloadRow(p, 0, "volume", 75),
  { volume: 75, source: "hdmi_1" },
);
results.payload_typed_value_survives = eq(
  H.updatePayloadRow(p, 1, "source", true),
  { level: 75, source: true },
);
results.payload_empty_key_drops_row = eq(
  H.updatePayloadRow(p, 0, "", 75),
  { source: "hdmi_1" },
);
results.payload_remove_row = eq(H.removePayloadRow(p, 1), { level: 75 });
results.payload_remove_last_returns_undefined =
  H.removePayloadRow({ level: 75 }, 0) === undefined;
results.payload_add_row = eq(H.addPayloadRow({}), { field1: "" });
results.payload_add_row_avoids_collision = eq(
  H.addPayloadRow({ field1: "x" }),
  { field1: "x", field2: "" },
);

const failed = Object.entries(results).filter(([, ok]) => ok !== true);
if (failed.length) {
  process.stderr.write(`failed: ${failed.map(([k]) => k).join(", ")}\n`);
}
process.stdout.write(JSON.stringify(results));
