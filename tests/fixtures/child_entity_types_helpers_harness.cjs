"use strict";
// Loads the Child Entity Types editor helpers (childEntityTypesHelpers.ts —
// React-free pure logic) bundled on the fly with the esbuild already in
// openavc/web/programmer/node_modules, and checks the collision-safe id generation, the
// atomic type-change reducer, and the rename validation that back the editor.
// Mirrors driver_builder_store_harness.cjs. The Python wrapper skips when the
// Node toolchain or esbuild is absent rather than failing the Python-only CI gate.
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

// --- H-118: nextChildFieldId never collides with an existing field ---
{
  results.h118_first_field = { pass: H.nextChildFieldId([]) === "field_1", detail: H.nextChildFieldId([]) };
}
{
  results.h118_sequential = {
    pass: H.nextChildFieldId(["field_1", "field_2"]) === "field_3",
    detail: H.nextChildFieldId(["field_1", "field_2"]),
  };
}
{
  // The add-three / remove-one / add-another path: length is 2, so the old
  // `field_${len+1}` would regenerate "field_3" and overwrite it. The guard
  // must skip past every existing id.
  const existing = ["field_1", "field_3"];
  const got = H.nextChildFieldId(existing);
  results.h118_skips_existing_no_overwrite = {
    pass: !existing.includes(got) && got === "field_4",
    detail: { got, existing },
  };
}
{
  const existing = ["child_type_1", "child_type_3"];
  const got = H.nextChildTypeId(existing);
  results.h118_type_skips_existing = {
    pass: !existing.includes(got) && got === "child_type_4",
    detail: { got, existing },
  };
}

// --- H-117: applyChildVarTypeChange is a single atomic object (no stale clobber) ---
{
  const r = H.applyChildVarTypeChange({ type: "string", label: "L" }, "integer");
  results.h117_string_to_integer_keeps_type = {
    pass: eq(r, { type: "integer", label: "L" }),
    detail: r,
  };
}
{
  // Leaving integer/number drops numeric bounds (unit rides with them, the
  // control flag survives — it isn't type-specific).
  const r = H.applyChildVarTypeChange(
    { type: "integer", label: "L", min: 1, max: 10, step: 2, unit: "dB", control: true },
    "string",
  );
  results.h117_leaving_numeric_drops_bounds = {
    pass: eq(r, { type: "string", label: "L", control: true }),
    detail: r,
  };
}
{
  // Leaving enum drops values.
  const r = H.applyChildVarTypeChange(
    { type: "enum", label: "L", values: ["a", "b"] },
    "boolean",
  );
  results.h117_leaving_enum_drops_values = {
    pass: eq(r, { type: "boolean", label: "L" }),
    detail: r,
  };
}
{
  // integer -> number keeps the numeric bounds (both are numeric).
  const r = H.applyChildVarTypeChange(
    { type: "integer", label: "L", min: 1, max: 9 },
    "number",
  );
  results.h117_numeric_to_numeric_keeps_bounds = {
    pass: eq(r, { type: "number", label: "L", min: 1, max: 9 }),
    detail: r,
  };
}
{
  // integer -> float keeps the numeric bounds: float is the runtime's alias
  // for number (driver_loader.py accepts both), so it is numeric here too.
  const r = H.applyChildVarTypeChange(
    { type: "integer", label: "L", min: 1, max: 9 },
    "float",
  );
  results.numeric_to_float_keeps_bounds = {
    pass: eq(r, { type: "float", label: "L", min: 1, max: 9 }),
    detail: r,
  };
}
{
  // The clobber the fix removes: the OLD onChange did several updateVar() calls
  // that each read the SAME stale `vars` snapshot, so the last write (clearing
  // values for a non-enum type) reverted the type back to its previous value.
  // Replay that here to prove the atomic reducer behaves differently.
  const stale = { type: "string", label: "L" };
  const NAME = "f";
  const seq = (snapshot, field, value) => {
    const merged = { ...snapshot[NAME], [field]: value };
    if (value === undefined) delete merged[field];
    return { ...snapshot, [NAME]: merged }; // writes whole map, reads stale snapshot
  };
  const snapshot = { [NAME]: stale };
  let written = seq(snapshot, "type", "integer"); // reads snapshot
  // integer is numeric -> the min/max/step branch is skipped; values branch runs:
  written = seq(snapshot, "values", undefined); // reads STALE snapshot again
  const oldResult = written[NAME];
  const newResult = H.applyChildVarTypeChange(stale, "integer");
  results.h117_atomic_beats_stale_sequential = {
    pass: oldResult.type === "string" && newResult.type === "integer",
    detail: { oldResult, newResult },
  };
}

// --- M-168: sanitize + checkRename back the commit-on-blur rename ---
{
  results.m168_sanitize_field = {
    pass: H.sanitizeFieldId("My Field!") === "myfield",
    detail: H.sanitizeFieldId("My Field!"),
  };
}
{
  results.m168_sanitize_type = {
    pass: H.sanitizeTypeId("Enc-1") === "enc1",
    detail: H.sanitizeTypeId("Enc-1"),
  };
}
{
  const r = H.checkRename("", "enc", ["enc"]);
  results.m168_rename_empty_rejected = {
    pass: r.ok === false && /empty/i.test(r.reason || ""),
    detail: r,
  };
}
{
  const r = H.checkRename("enc", "enc", ["enc"]);
  results.m168_rename_noop_is_ok = { pass: r.ok === true, detail: r };
}
{
  const r = H.checkRename("dec", "enc", ["enc", "dec"]);
  results.m168_rename_collision_rejected = {
    pass: r.ok === false && /exists/i.test(r.reason || ""),
    detail: r,
  };
}
{
  const r = H.checkRename("zone", "enc", ["enc", "dec"]);
  results.m168_rename_valid_accepted = { pass: r.ok === true, detail: r };
}

process.stdout.write(JSON.stringify(results));
