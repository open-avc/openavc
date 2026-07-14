"use strict";
// Loads the Device Settings editor/setup helpers (deviceSettingsHelpers.ts —
// React-free pure logic) bundled on the fly with the esbuild already in
// web/programmer/node_modules, and checks the write-transport normalization
// (H-120), the OSC empty-value detection (H-119), and the min/max/regex value
// validation the setup dialog now enforces (M-169). Mirrors
// driver_builder_store_harness.cjs. The Python wrapper skips when the Node
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

// --- H-120: normalizeWriteForTransport strips stale cross-transport fields ---
{
  const r = H.normalizeWriteForTransport(
    { address: "/x", args: [{ type: "f", value: "{value}" }], send: "X", method: "POST" },
    "osc",
  );
  results.h120_osc_keeps_only_osc = {
    pass: eq(r, { address: "/x", args: [{ type: "f", value: "{value}" }] }),
    detail: r,
  };
}
{
  const r = H.normalizeWriteForTransport(
    { address: "/x", method: "PUT", path: "/p", body: "b", headers: { A: "1" }, send: "S" },
    "http",
  );
  results.h120_http_keeps_only_http = {
    pass: eq(r, { method: "PUT", path: "/p", body: "b", headers: { A: "1" } }),
    detail: r,
  };
}
{
  // A stale OSC address must be dropped when the transport is TCP/serial, else
  // the runtime dispatches on it (address-first) and mis-routes to OSC.
  const r = H.normalizeWriteForTransport(
    { address: "/x", method: "POST", send: "SET {value}" },
    "serial",
  );
  results.h120_tcp_drops_foreign_keeps_send = {
    pass: eq(r, { send: "SET {value}" }),
    detail: r,
  };
}
{
  results.h120_foreign_keys_detected = {
    pass:
      H.writeHasForeignKeys({ address: "/x" }, "http") === true &&
      H.writeHasForeignKeys({ method: "POST", path: "/p" }, "http") === false,
    detail: {
      stale: H.writeHasForeignKeys({ address: "/x" }, "http"),
      clean: H.writeHasForeignKeys({ method: "POST", path: "/p" }, "http"),
    },
  };
}

// --- H-119: oscWriteOmitsValue flags an OSC write that never sends the value ---
{
  results.h119_osc_address_only_omits_value = {
    pass: H.oscWriteOmitsValue({ address: "/x" }) === true,
    detail: H.oscWriteOmitsValue({ address: "/x" }),
  };
}
{
  results.h119_osc_value_arg_sends_value = {
    pass: H.oscWriteOmitsValue({ address: "/x", args: [{ type: "f", value: "{value}" }] }) === false,
    detail: H.oscWriteOmitsValue({ address: "/x", args: [{ type: "f", value: "{value}" }] }),
  };
}
{
  // A literal-only arg (no {value}) still omits the value.
  results.h119_osc_literal_arg_omits_value = {
    pass: H.oscWriteOmitsValue({ address: "/x", args: [{ type: "f", value: "3" }] }) === true,
    detail: H.oscWriteOmitsValue({ address: "/x", args: [{ type: "f", value: "3" }] }),
  };
}
{
  // {value} embedded in the address counts as sending the value.
  results.h119_osc_value_in_address_ok = {
    pass: H.oscWriteOmitsValue({ address: "/x/{value}" }) === false,
    detail: H.oscWriteOmitsValue({ address: "/x/{value}" }),
  };
}

// --- setting key generation + rename ---
{
  const existing = ["setting_1", "setting_3"];
  const got = H.nextSettingKey(existing);
  results.next_setting_key_skips_existing = {
    pass: !existing.includes(got) && got === "setting_4",
    detail: { got, existing },
  };
}
{
  results.sanitize_setting_key = {
    pass: H.sanitizeSettingKey("My Key!") === "mykey",
    detail: H.sanitizeSettingKey("My Key!"),
  };
}
{
  const empty = H.checkSettingRename("", "ndi_name", ["ndi_name"]);
  const collide = H.checkSettingRename("b", "a", ["a", "b"]);
  const ok = H.checkSettingRename("c", "a", ["a", "b"]);
  results.check_setting_rename = {
    pass: empty.ok === false && collide.ok === false && ok.ok === true,
    detail: { empty, collide, ok },
  };
}

// --- M-169: validateSettingValue enforces min/max (numeric) + regex (string) ---
{
  const def = { type: "integer", min: 1, max: 10 };
  results.m169_int_in_range_ok = { pass: H.validateSettingValue("5", def).ok === true, detail: H.validateSettingValue("5", def) };
  results.m169_int_below_min = { pass: H.validateSettingValue("0", def).ok === false, detail: H.validateSettingValue("0", def) };
  results.m169_int_above_max = { pass: H.validateSettingValue("11", def).ok === false, detail: H.validateSettingValue("11", def) };
  results.m169_int_not_a_number = { pass: H.validateSettingValue("abc", def).ok === false, detail: H.validateSettingValue("abc", def) };
}
{
  const def = { type: "string", regex: "^[a-z0-9]+$" };
  results.m169_regex_match_ok = { pass: H.validateSettingValue("abc123", def).ok === true, detail: H.validateSettingValue("abc123", def) };
  results.m169_regex_mismatch = { pass: H.validateSettingValue("ABC!", def).ok === false, detail: H.validateSettingValue("ABC!", def) };
}
{
  // L-169: an empty NUMERIC setting is rejected (it would silently coerce to 0
  // on push); an empty string/enum or a def-less value is still allowed blank.
  results.l169_empty_integer_rejected = {
    pass: H.validateSettingValue("", { type: "integer", min: 1 }).ok === false,
    detail: H.validateSettingValue("", { type: "integer", min: 1 }),
  };
  results.l169_empty_number_rejected = {
    pass: H.validateSettingValue("", { type: "number" }).ok === false,
    detail: H.validateSettingValue("", { type: "number" }),
  };
  results.l169_empty_string_allowed = {
    pass: H.validateSettingValue("", { type: "string" }).ok === true,
    detail: H.validateSettingValue("", { type: "string" }),
  };
  results.l169_empty_no_def_allowed = {
    pass: H.validateSettingValue("", undefined).ok === true,
    detail: H.validateSettingValue("", undefined),
  };
  results.m169_malformed_regex_does_not_block = {
    pass: H.validateSettingValue("x", { type: "string", regex: "[" }).ok === true,
    detail: H.validateSettingValue("x", { type: "string", regex: "[" }),
  };
}

process.stdout.write(JSON.stringify(results));
