"use strict";
// Loads the state-variable editor helpers (stateVariableHelpers.ts —
// React-free pure logic) bundled on the fly with the esbuild already in
// openavc/web/programmer/node_modules, and checks the collision-safe name generation
// and the atomic type-change reducer that back the editor.
// Mirrors child_entity_types_helpers_harness.cjs. The Python wrapper skips
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

// --- nextStateVariableName never collides with an existing variable ---
{
  results.add_first_variable = {
    pass: H.nextStateVariableName([]) === "variable_1",
    detail: H.nextStateVariableName([]),
  };
}
{
  results.add_sequential = {
    pass: H.nextStateVariableName(["variable_1", "variable_2"]) === "variable_3",
    detail: H.nextStateVariableName(["variable_1", "variable_2"]),
  };
}
{
  // The add-three / delete-one / add-another path: length is 2, so the old
  // `variable_${len+1}` regenerated "variable_3" and spreading it into the
  // map silently overwrote the configured variable. The helper must skip
  // past every existing name.
  const existing = ["variable_1", "variable_3"];
  const oldName = `variable_${existing.length + 1}`;
  const got = H.nextStateVariableName(existing);
  results.add_after_delete_no_overwrite = {
    pass:
      existing.includes(oldName) && // proves the old scheme collided here
      !existing.includes(got) &&
      got === "variable_4",
    detail: { oldName, got, existing },
  };
}
{
  // Custom-named variables don't confuse the generator.
  const existing = ["power", "input", "variable_3"];
  const got = H.nextStateVariableName(existing);
  results.add_alongside_custom_names = {
    pass: !existing.includes(got) && got === "variable_4",
    detail: { got, existing },
  };
}

// --- applyStateVarTypeChange is a single atomic object (no stale clobber) ---
{
  const r = H.applyStateVarTypeChange({ type: "string", label: "L" }, "integer");
  results.type_change_applies = {
    pass: eq(r, { type: "integer", label: "L" }),
    detail: r,
  };
}
{
  // Leaving integer/number drops numeric bounds (unit rides with them, the
  // control flag survives — it isn't type-specific).
  const r = H.applyStateVarTypeChange(
    { type: "integer", label: "L", min: 0, max: 100, step: 5, unit: "dB", control: true },
    "string",
  );
  results.leaving_numeric_drops_bounds = {
    pass: eq(r, { type: "string", label: "L", control: true }),
    detail: r,
  };
}
{
  // Leaving enum drops values.
  const r = H.applyStateVarTypeChange(
    { type: "enum", label: "L", values: ["off", "on"] },
    "boolean",
  );
  results.leaving_enum_drops_values = {
    pass: eq(r, { type: "boolean", label: "L" }),
    detail: r,
  };
}
{
  // integer -> number keeps the numeric bounds (both are numeric).
  const r = H.applyStateVarTypeChange(
    { type: "integer", label: "L", min: 1, max: 9 },
    "number",
  );
  results.numeric_to_numeric_keeps_bounds = {
    pass: eq(r, { type: "number", label: "L", min: 1, max: 9 }),
    detail: r,
  };
}
{
  // integer -> float keeps the numeric bounds: float is the runtime's alias
  // for number (driver_loader.py accepts both), so it is numeric here too.
  const r = H.applyStateVarTypeChange(
    { type: "integer", label: "L", min: 1, max: 9, step: 0.5 },
    "float",
  );
  results.numeric_to_float_keeps_bounds = {
    pass: eq(r, { type: "float", label: "L", min: 1, max: 9, step: 0.5 }),
    detail: r,
  };
}
{
  // Help text is unrelated to the type and must survive any switch.
  const r = H.applyStateVarTypeChange(
    { type: "integer", label: "L", help: "H", min: 1 },
    "enum",
  );
  results.type_change_keeps_help = {
    pass: eq(r, { type: "enum", label: "L", help: "H" }),
    detail: r,
  };
}
{
  // The clobber the fix removes: the OLD onChange ran several updateVariable()
  // calls that each read the SAME stale `vars` snapshot, so the last write
  // (clearing enum values) reverted the type back to its previous value and
  // left the numeric bounds in place. Replay that here to prove the atomic
  // reducer behaves differently.
  const stale = { type: "integer", label: "L", min: 0, max: 10, step: 1 };
  const NAME = "v";
  const seq = (snapshot, field, value) => {
    const merged = { ...snapshot[NAME], [field]: value };
    if (value === undefined) delete merged[field];
    return { ...snapshot, [NAME]: merged }; // writes whole map, reads stale snapshot
  };
  const snapshot = { [NAME]: stale };
  let written = seq(snapshot, "type", "string"); // reads snapshot
  written = seq(snapshot, "min", undefined); // reads STALE snapshot again
  written = seq(snapshot, "max", undefined);
  written = seq(snapshot, "step", undefined);
  written = seq(snapshot, "values", undefined); // last write wins in the store
  const oldResult = written[NAME];
  const newResult = H.applyStateVarTypeChange(stale, "string");
  results.atomic_beats_stale_sequential = {
    pass:
      oldResult.type === "integer" && // the old path lost the type change
      eq(newResult, { type: "string", label: "L" }),
    detail: { oldResult, newResult },
  };
}

process.stdout.write(JSON.stringify(results));
