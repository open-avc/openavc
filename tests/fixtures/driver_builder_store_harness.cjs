"use strict";
// Loads the real Driver Builder store helpers (driverBuilderStore.helpers.ts,
// bundled on the fly with the esbuild already in web/programmer/node_modules)
// and runs pure-logic checks for the save-reconcile, latest-wins refresh guard,
// and import-validation helpers, printing JSON results to stdout. Mirrors
// ui_builder_helpers_harness.cjs, but uses buildSync(bundle) instead of
// transformSync because importBlockers pulls in the real validateDriver.ts — so
// this exercises the actual validator the form editor uses, not a stub. The
// Python wrapper skips when the Node toolchain or esbuild is absent rather than
// failing the Python-only CI gate.
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

// --- H-072 / M-126: reconcileAfterSave keeps edits + selects the saved id ---
{
  // Draft untouched during the await -> mark clean, select the saved record.
  const r = H.reconcileAfterSave({ savedId: "acme_x", draftUnchanged: true, selectionUnchanged: true });
  results.h072_reconcile_clean = {
    pass: eq(r, { saving: false, dirty: false, selectedId: "acme_x" }),
    detail: r,
  };
}
{
  // Edited in place during the await -> keep dirty (don't discard the edits)
  // but still point selection at the id we persisted.
  const r = H.reconcileAfterSave({ savedId: "acme_x", draftUnchanged: false, selectionUnchanged: true });
  results.h072_reconcile_edited_keeps_dirty = {
    pass: eq(r, { saving: false, dirty: true, selectedId: "acme_x" }),
    detail: r,
  };
}
{
  // Navigated to a different driver mid-save -> only clear the saving flag,
  // never clobber the user's new selection or its dirty state.
  const r = H.reconcileAfterSave({ savedId: "acme_x", draftUnchanged: false, selectionUnchanged: false });
  results.m126_reconcile_navigated_away_untouched = {
    pass: eq(r, { saving: false }) && !("selectedId" in r) && !("dirty" in r),
    detail: r,
  };
}

// --- M-127: makeLatestWins makes the newest-started refresh win ---
{
  const g = H.makeLatestWins();
  const t1 = g.next();
  results.m127_latest_single = { pass: t1 === 1 && g.isCurrent(t1) === true, detail: { t1 } };
}
{
  // A later refresh supersedes an earlier in-flight one regardless of which
  // resolves last: the stale token is no longer current, the newest is.
  const g = H.makeLatestWins();
  const t1 = g.next();
  const t2 = g.next();
  results.m127_latest_superseded = {
    pass: g.isCurrent(t1) === false && g.isCurrent(t2) === true,
    detail: { t1, t2 },
  };
}
{
  // Independent guards (registered vs installed lists) don't cross-talk.
  const a = H.makeLatestWins();
  const b = H.makeLatestWins();
  const ta = a.next();
  b.next();
  results.m127_latest_independent = {
    pass: a.isCurrent(ta) === true && b.isCurrent(ta) === true,
    detail: { ta },
  };
}

// --- M-128: importBlockers routes imports through the real validateDriver ---
{
  // A complete, well-formed driver has no blockers -> import proceeds.
  const def = { id: "acme_x", name: "Acme X", transport: "tcp", version: "1.0.0", author: "Acme" };
  const r = H.importBlockers(def, []);
  results.m128_import_valid_no_blockers = { pass: eq(r, []), detail: r };
}
{
  // Missing transport is caught here (validateDriver doesn't, since the editor
  // always defaults one).
  const def = { id: "acme_x", name: "Acme X" };
  const r = H.importBlockers(def, []);
  results.m128_import_missing_transport = {
    pass: r.length === 1 && /Transport is required/.test(r[0]),
    detail: r,
  };
}
{
  // Missing id -> the same clean message the form editor shows, not a 422.
  const def = { name: "Acme X", transport: "tcp" };
  const r = H.importBlockers(def, []);
  results.m128_import_missing_id = {
    pass: r.some((m) => /Driver ID is required/.test(m)),
    detail: r,
  };
}
{
  // An ID with illegal characters -> blocked with the validator's message.
  const def = { id: "Acme X", name: "X", transport: "tcp" };
  const r = H.importBlockers(def, []);
  results.m128_import_bad_id = {
    pass: r.some((m) => /lowercase/.test(m)),
    detail: r,
  };
}
{
  // A deep structural error the OLD import path skipped (it only checked
  // id/name/transport presence): a child_id param with no child type. This is
  // the whole point of routing imports through validateDriver.
  const def = {
    id: "acme_x",
    name: "X",
    transport: "tcp",
    commands: { set_ch: { params: { ch: { type: "child_id" } } } },
  };
  const r = H.importBlockers(def, []);
  results.m128_import_deep_structural_error = {
    pass: r.some((m) => /Child ID but no child type/.test(m)),
    detail: r,
  };
}
{
  // Warnings alone (an undeclared {placeholder} is a warning) must NOT block —
  // import semantics match the editor, where warnings don't gate save.
  const def = {
    id: "acme_x",
    name: "X",
    transport: "tcp",
    commands: { power_on: { send: "PWR {foo}\\r" } },
  };
  const r = H.importBlockers(def, []);
  results.m128_import_warning_does_not_block = { pass: eq(r, []), detail: r };
}

// --- M-229: cloneDraft fills in state_variables the editors index blindly ---
{
  // The runtime loader tolerates a driver that omits state_variables, so a
  // definition can arrive without it; cloning it verbatim crashed the
  // State Variables / Behavior / Simulation tabs on Object.keys(undefined).
  const def = { id: "acme_min", name: "Acme Minimal", transport: "tcp" };
  let keys = null;
  let threw = false;
  try {
    keys = Object.keys(H.cloneDraft(def).state_variables);
  } catch {
    threw = true;
  }
  results.m229_clone_fills_missing_state_variables = {
    pass: !threw && eq(keys, []) && !("state_variables" in def),
    detail: { keys, threw },
  };
}
{
  // A well-formed definition round-trips byte-identically: same content AND
  // same key order (the fill must append only when absent, not re-order).
  const def = {
    id: "acme_full",
    name: "Acme Full",
    state_variables: { power: { type: "boolean", label: "Power" } },
    transport: "tcp",
  };
  try {
    const clone = H.cloneDraft(def);
    clone.state_variables.power.label = "Changed";
    results.m229_clone_preserves_shape_and_is_deep = {
      pass:
        JSON.stringify(Object.keys(H.cloneDraft(def))) === JSON.stringify(Object.keys(def)) &&
        eq(H.cloneDraft(def), def) &&
        def.state_variables.power.label === "Power",
      detail: { clone },
    };
  } catch (e) {
    results.m229_clone_preserves_shape_and_is_deep = { pass: false, detail: String(e) };
  }
}

// --- L-150: parseDriverDefinition gates on a mapping, not any non-null object ---
const parseOutcome = (text) => {
  try {
    return { value: H.parseDriverDefinition(text), threw: false };
  } catch (e) {
    return { threw: true, isSyntax: e instanceof SyntaxError, message: String(e && e.message) };
  }
};
{
  // A JSON object round-trips to a definition.
  const r = parseOutcome('{"id":"acme_x","name":"Acme X","transport":"tcp"}');
  results.l150_json_object_ok = {
    pass: r.threw === false && r.value.id === "acme_x" && r.value.transport === "tcp",
    detail: r,
  };
}
{
  // A YAML mapping (community driver form) round-trips too.
  const r = parseOutcome("id: acme_x\nname: Acme X\ntransport: tcp\n");
  results.l150_yaml_mapping_ok = {
    pass: r.threw === false && r.value.id === "acme_x" && r.value.name === "Acme X",
    detail: r,
  };
}
{
  // A JSON array was cast straight to DriverDefinition by the old code, reached
  // the API and 422'd on a missing id. Now rejected up front with a shape msg.
  const r = parseOutcome("[1, 2, 3]");
  results.l150_json_array_rejected = {
    pass: r.threw === true && r.isSyntax === true && /mapping/.test(r.message),
    detail: r,
  };
}
{
  // A YAML sequence slipped through the old `parsed && typeof === 'object'`
  // guard (typeof [] === "object"); the mapping gate rejects it.
  const r = parseOutcome("- one\n- two\n");
  results.l150_yaml_sequence_rejected = {
    pass: r.threw === true && r.isSyntax === true && /mapping/.test(r.message),
    detail: r,
  };
}
{
  // A bare scalar (JSON number) is rejected — the old code returned it verbatim.
  const r = parseOutcome("42");
  results.l150_scalar_rejected = {
    pass: r.threw === true && r.isSyntax === true && /mapping/.test(r.message),
    detail: r,
  };
}
{
  // JSON null (the one shape the old guard already caught) stays rejected — the
  // mapping gate is a superset of the old null check.
  const r = parseOutcome("null");
  results.l150_null_rejected = {
    pass: r.threw === true && r.isSyntax === true,
    detail: r,
  };
}
{
  // Genuinely unparseable input reports the distinct "not JSON or YAML" message,
  // not the wrong-shape one — the two failures stay separable for the caller.
  const r = parseOutcome("{ this: is: not: valid");
  results.l150_unparseable_distinct_message = {
    pass: r.threw === true && r.isSyntax === true && /not valid JSON or YAML/.test(r.message),
    detail: r,
  };
}

process.stdout.write(JSON.stringify(results));
