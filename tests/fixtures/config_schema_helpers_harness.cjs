"use strict";
// Loads the config-schema editor helpers (configSchemaHelpers.ts — React-free
// pure logic) bundled on the fly with the esbuild already in
// openavc/web/programmer/node_modules, and checks the typed default coercion, the
// atomic type-change reducer, and the secret toggle's default purge that back
// the Driver Builder's config-field editor. Mirrors
// state_variable_helpers_harness.cjs. The Python wrapper skips when the Node
// toolchain or esbuild is absent rather than failing the Python-only CI gate.
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

// --- coerceConfigDefault stores the declared primitive, not a string ------
{
  // The defect this fixes: the old updateDefault wrote the raw input string,
  // so an integer field's default landed in the .avcdriver as "5".
  const got = H.coerceConfigDefault("integer", "5");
  results.integer_default_is_number = {
    pass: got === 5 && typeof got === "number",
    detail: { got, type: typeof got },
  };
}
{
  // A boolean default of "false" is truthy as a string — the worst shape of
  // the string-default bug. It must store the real primitive false.
  const got = H.coerceConfigDefault("boolean", "false");
  results.boolean_false_is_falsy = {
    pass: got === false && !got,
    detail: { got, type: typeof got },
  };
}
{
  const got = H.coerceConfigDefault("boolean", "true");
  results.boolean_true_is_boolean = {
    pass: got === true,
    detail: { got, type: typeof got },
  };
}
{
  // number and float (the runtime alias) both coerce to a float.
  const num = H.coerceConfigDefault("number", "2.5");
  const flt = H.coerceConfigDefault("float", "2.5");
  results.number_and_float_coerce = {
    pass: num === 2.5 && flt === 2.5,
    detail: { num, flt },
  };
}
{
  // Strings stay exactly as typed — number-sniffing would corrupt an
  // all-numeric device ID like "0123".
  const got = H.coerceConfigDefault("string", "0123");
  results.string_keeps_leading_zero = {
    pass: got === "0123",
    detail: { got },
  };
}
{
  // Empty input = no default; unparseable numerics can't be stored as numbers.
  results.empty_and_garbage_unset = {
    pass:
      H.coerceConfigDefault("integer", "") === undefined &&
      H.coerceConfigDefault("string", "") === undefined &&
      H.coerceConfigDefault("integer", "hdmi1") === undefined,
    detail: {
      emptyInt: H.coerceConfigDefault("integer", ""),
      emptyStr: H.coerceConfigDefault("string", ""),
      garbage: H.coerceConfigDefault("integer", "hdmi1"),
    },
  };
}

// --- applyConfigFieldTypeChange re-coerces the stored default atomically ---
{
  // string -> integer with a numeric string default: the default converts.
  const r = H.applyConfigFieldTypeChange(
    { type: "string", label: "L" },
    "5",
    "integer",
  );
  results.type_switch_converts_default = {
    pass: r.field.type === "integer" && r.defaultValue === 5,
    detail: r,
  };
}
{
  // string -> integer with a non-numeric default: the default is dropped
  // rather than stored as a wrong-typed string.
  const r = H.applyConfigFieldTypeChange(
    { type: "string", label: "L" },
    "hdmi1",
    "integer",
  );
  results.type_switch_drops_unconvertible = {
    pass: r.field.type === "integer" && r.defaultValue === undefined,
    detail: r,
  };
}
{
  // Leaving enum drops the values list (and the schema-side default follows
  // the same coercion).
  const r = H.applyConfigFieldTypeChange(
    { type: "enum", label: "L", values: ["a", "b"], default: "a" },
    "a",
    "boolean",
  );
  results.leaving_enum_drops_values = {
    pass:
      eq(r.field, { type: "boolean", label: "L" }) &&
      r.defaultValue === undefined,
    detail: r,
  };
}
{
  // boolean -> string keeps a representable default (true -> "true").
  const r = H.applyConfigFieldTypeChange(
    { type: "boolean", label: "L" },
    true,
    "string",
  );
  results.type_switch_stringifies = {
    pass: r.field.type === "string" && r.defaultValue === "true",
    detail: r,
  };
}
{
  // Replay of the OLD editor behaviour this replaces: updateDefault wrote the
  // raw input string regardless of type, so `"false"` on a boolean field was
  // truthy and `"5"` on an integer field failed numeric math. The helper's
  // output must differ from that legacy shape.
  const legacyBool = "false"; // what the old free-text input stored
  const fixedBool = H.coerceConfigDefault("boolean", "false");
  const legacyInt = "5";
  const fixedInt = H.coerceConfigDefault("integer", "5");
  results.typed_beats_legacy_strings = {
    pass:
      Boolean(legacyBool) === true && // proves the legacy shape misbehaved
      fixedBool === false &&
      legacyInt !== 5 &&
      fixedInt === 5,
    detail: { legacyBool, fixedBool, legacyInt, fixedInt },
  };
}

// --- applyConfigSecretToggle purges defaults, including imported ones ------
{
  const r = H.applyConfigSecretToggle(
    { pin: { type: "string", label: "PIN", default: "hunter2" } },
    { pin: "hunter2", other: 1 },
    "pin",
    true,
  );
  results.secret_purges_default = {
    pass:
      r.config_schema.pin.secret === true &&
      !("default" in r.config_schema.pin) &&
      !("pin" in r.default_config) &&
      r.default_config.other === 1,
    detail: r,
  };
}
{
  // Unmarking secret leaves whatever defaults exist alone.
  const r = H.applyConfigSecretToggle(
    { pin: { type: "string", label: "PIN", secret: true } },
    { other: 1 },
    "pin",
    false,
  );
  results.unsecret_keeps_maps = {
    pass:
      r.config_schema.pin.secret === false &&
      eq(r.default_config, { other: 1 }),
    detail: r,
  };
}

process.stdout.write(JSON.stringify(results));
