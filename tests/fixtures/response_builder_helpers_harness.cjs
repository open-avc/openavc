"use strict";
// Loads the Response Builder helpers (responseBuilderHelpers.ts — React-free
// pure logic) bundled on the fly with the esbuild already in
// web/programmer/node_modules, and checks the value-map key-rename guards and
// the set:-shorthand type fidelity that back the Driver Builder's response
// editor. Mirrors config_schema_helpers_harness.cjs. The Python wrapper skips
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

// --- value-map key renames can no longer merge/drop entries ----------------
{
  // The defect this fixes: the old per-keystroke updateEntry rebuilt the
  // Record with the new key, so renaming "off" onto an existing "on" wrote
  // next["on"] twice — one row silently vanished. Replay that legacy shape,
  // then show the rename check rejects it.
  const map = { off: "0", on: "1" };
  const legacy = {};
  for (const [k, v] of Object.entries(map)) {
    legacy[k === "off" ? "on" : k] = k === "off" ? "0" : v;
  }
  const check = H.checkValueMapKeyRename("on", "off", Object.keys(map));
  results.rename_collision_rejected = {
    pass:
      Object.keys(legacy).length === 1 && // proves the legacy shape lost a row
      check.ok === false &&
      typeof check.reason === "string",
    detail: { legacy, check },
  };
}
{
  const next = H.renameValueMapKey({ "00": "off", "01": "on" }, "01", "02");
  results.rename_preserves_order_and_values = {
    pass: eq(next, { "00": "off", "02": "on" }),
    detail: next,
  };
}
{
  // Clearing a key and blurring must not commit an empty raw key.
  const check = H.checkValueMapKeyRename("", "01", ["01"]);
  results.rename_to_empty_rejected = {
    pass: check.ok === false,
    detail: check,
  };
}
{
  // A no-op rename (blur without changes) stays ok even though the key
  // trivially "exists".
  const check = H.checkValueMapKeyRename("01", "01", ["01"]);
  results.rename_noop_ok = { pass: check.ok === true, detail: check };
}
{
  // Legacy addEntry spread {"": ""} into the record unconditionally, so
  // clicking + Add with a pending draft row reset that draft's value. The
  // helper refuses to add a second draft.
  const pending = { "": "half-typed" };
  const legacy = { ...pending, "": "" };
  const guarded = H.addValueMapEntry(pending);
  const fresh = H.addValueMapEntry({ "01": "on" });
  results.add_entry_guards_pending_draft = {
    pass:
      legacy[""] === "" && // proves the legacy shape clobbered the draft
      guarded === null &&
      eq(fresh, { "01": "on", "": "" }),
    detail: { legacy, guarded, fresh },
  };
}

// --- set: shorthand rows display the runtime's real coercion type ----------
{
  // The runtime coerces `set: {volume: "$1"}` by the state variable's
  // DECLARED type; the old getMappings hardcoded "string", misrepresenting
  // how the response is parsed.
  const got = H.getMappings(
    { match: "Vol(\\d+)", set: { volume: "$1" } },
    { volume: { type: "integer" } },
  );
  results.set_capture_shows_declared_type = {
    pass: eq(got, [{ group: 1, state: "volume", type: "integer" }]),
    detail: got,
  };
}
{
  // Static literals coerce by declared type at runtime too.
  const got = H.getMappings(
    { match: "^MUTE$", set: { mute: "true" } },
    { mute: { type: "boolean" } },
  );
  results.set_static_shows_declared_type = {
    pass: eq(got, [{ group: 0, state: "mute", value: "true", type: "boolean" }]),
    detail: got,
  };
}
{
  // Undeclared state variables fall back to "string", like the runtime.
  const got = H.getMappings({ match: "X(\\d)", set: { foo: "$1" } }, {});
  results.set_undeclared_defaults_string = {
    pass: got.length === 1 && got[0].type === "string",
    detail: got,
  };
}
{
  // Round-trip fidelity: showing the declared type must NOT rewrite the
  // author's set: form — an untouched integer-typed capture still fits the
  // shorthand because the shorthand already coerces to that type.
  const vars = { volume: { type: "integer" } };
  const original = { match: "Vol(\\d+)", set: { volume: "$1" } };
  const mappings = H.getMappings(original, vars);
  const rebuilt = H.buildResponse("Vol(\\d+)", mappings, original, vars);
  results.set_roundtrip_keeps_shorthand = {
    pass: eq(rebuilt, { match: "Vol(\\d+)", set: { volume: "$1" } }),
    detail: rebuilt,
  };
}

// --- a chosen Type always survives the save ---------------------------------
{
  // The defect this fixes: a static set: row with a user-chosen Type kept the
  // set: form on save, which has nowhere to put the type — the runtime then
  // coerced by the declared type instead. Choosing a type that differs from
  // the declared one must fall back to the explicit mappings form WITH the
  // type field (the runtime honors `type` on static `value` mappings).
  const vars = { mute: { type: "string" } };
  const original = { match: "^MUTE$", set: { mute: "1" } };
  const edited = H.getMappings(original, vars).map((m) => ({
    ...m,
    type: "boolean",
  }));
  const rebuilt = H.buildResponse("^MUTE$", edited, original, vars);
  results.static_type_choice_survives_save = {
    pass: eq(rebuilt, {
      match: "^MUTE$",
      mappings: [{ group: 0, state: "mute", value: "1", type: "boolean" }],
    }),
    detail: rebuilt,
  };
}
{
  // Same for capture rows — the old check treated a "string" Type as always
  // shorthand-safe, so choosing String for an integer-declared variable was
  // silently discarded (the shorthand kept coercing to integer).
  const vars = { volume: { type: "integer" } };
  const original = { match: "Vol(\\d+)", set: { volume: "$1" } };
  const edited = H.getMappings(original, vars).map((m) => ({
    ...m,
    type: "string",
  }));
  const rebuilt = H.buildResponse("Vol(\\d+)", edited, original, vars);
  results.capture_type_choice_survives_save = {
    pass: eq(rebuilt, {
      match: "Vol(\\d+)",
      mappings: [{ group: 1, state: "volume", type: "string" }],
    }),
    detail: rebuilt,
  };
}
{
  // Flipping the Type back to the declared one returns to the shorthand.
  const vars = { volume: { type: "integer" } };
  const original = { match: "Vol(\\d+)", set: { volume: "$1" } };
  const edited = H.getMappings(original, vars).map((m) => ({
    ...m,
    type: "integer",
  }));
  const rebuilt = H.buildResponse("Vol(\\d+)", edited, original, vars);
  results.matching_type_returns_to_shorthand = {
    pass: eq(rebuilt, { match: "Vol(\\d+)", set: { volume: "$1" } }),
    detail: rebuilt,
  };
}
{
  // A response authored in explicit mappings form never converts to set:.
  const vars = { volume: { type: "integer" } };
  const original = {
    match: "Vol(\\d+)",
    mappings: [{ group: 1, state: "volume" }],
  };
  const rebuilt = H.buildResponse(
    "Vol(\\d+)",
    H.getMappings(original, vars),
    original,
    vars,
  );
  results.mappings_form_stays_mappings = {
    pass: eq(rebuilt, original),
    detail: rebuilt,
  };
}
{
  // child_set rides along untouched through a rebuild.
  const vars = {};
  const original = {
    match: "OUT(\\d+):(\\d+)",
    set: { last_route: "$2" },
    child_set: [{ type: "output", id: "$1", state: { input: "$2" } }],
  };
  const rebuilt = H.buildResponse(
    "OUT(\\d+):(\\d+)",
    H.getMappings(original, vars),
    original,
    vars,
  );
  results.child_set_rides_along = {
    pass: eq(rebuilt.child_set, original.child_set) && !!rebuilt.set,
    detail: rebuilt,
  };
}

{
  // Wire-ID map helpers: the long form renders as its capture ref, keeps its
  // map, and rebuilds correctly from text + rows.
  const longForm = { group: 1, map: { "0": 1, "10": "ST" } };
  results.child_id_long_form_renders_ref = {
    pass: H.childIdToText(longForm) === "$1" &&
      eq(H.childIdMap(longForm), { "0": 1, "10": "ST" }) &&
      H.childIdToText("$2") === "$2" &&
      H.childIdToText(3) === "3" &&
      H.childIdMap("$2") === undefined,
    detail: H.childIdToText(longForm),
  };
}
{
  // Rebuild: ref + rows -> long form; ref alone stays a ref; a literal drops
  // the (meaningless) map; numeric text becomes a number.
  const rows = { "0": 1 };
  results.child_id_rebuild_shapes = {
    pass: eq(H.childIdFromParts("$1", rows), { group: 1, map: rows }) &&
      H.childIdFromParts("$1", undefined) === "$1" &&
      H.childIdFromParts("$1", {}) === "$1" &&
      H.childIdFromParts("2", rows) === 2 &&
      H.childIdFromParts("ST", rows) === "ST",
    detail: H.childIdFromParts("$1", rows),
  };
}

process.stdout.write(JSON.stringify(results));
