"use strict";
// Loads the real Updates view helpers (updatesHelpers.ts, transpiled on the
// fly with the esbuild already in openavc/web/programmer/node_modules) and runs
// pure-logic checks for the completion-outcome / toast-direction / history
// label decisions, printing JSON results to stdout. Mirrors
// ui_builder_helpers_harness.cjs; the Python wrapper skips when the Node
// toolchain is absent rather than failing the Python-only CI gate.
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
const results = {};

// --- semverLt ---
{
  const cases = [
    ["0.14.0", "0.15.0", true],
    ["0.15.0", "0.14.0", false],
    ["0.15.0", "0.15.0", false],
    ["0.15", "0.15.1", true],
    ["1.0.0", "0.99.99", false],
    ["abc", "0.1.0", false],
  ];
  const failures = cases.filter(([a, b, want]) => H.semverLt(a, b) !== want);
  results.semver_lt = { pass: failures.length === 0, detail: failures };
}

// --- updateCompletionOutcome ---
{
  const r = H.updateCompletionOutcome("0.14.0", "restarting", "0.15.0", "idle", "update");
  results.outcome_updated = { pass: r === "updated", detail: r };
}
{
  // Explicit rollback action labels the change as a rollback.
  const r = H.updateCompletionOutcome("0.15.0", "restarting", "0.14.0", "idle", "rollback");
  results.outcome_rollback_by_action = { pass: r === "rolled_back", detail: r };
}
{
  // No local action (e.g. cloud-initiated): semver direction decides.
  const r = H.updateCompletionOutcome("0.15.0", "restarting", "0.14.0", "idle", null);
  results.outcome_rollback_by_direction = { pass: r === "rolled_back", detail: r };
}
{
  // Restart finished but nothing was applied — the same-version hang case.
  const r = H.updateCompletionOutcome("0.15.0", "restarting", "0.15.0", "idle", "update");
  results.outcome_same_version_restart = { pass: r === "same_version_restart", detail: r };
}
{
  // A checking -> idle transition is not a restart completion.
  const r = H.updateCompletionOutcome("0.15.0", "checking", "0.15.0", "idle", null);
  results.outcome_null_non_restart = { pass: r === null, detail: r };
}
{
  // First mount (no previous version yet) must not produce an outcome.
  const r = H.updateCompletionOutcome("", "", "0.15.0", "idle", null);
  results.outcome_null_first_mount = { pass: r === null, detail: r };
}

// --- historyEntryDisplay ---
{
  const r = H.historyEntryDisplay({ from_version: "0.14.0", to_version: "0.15.0" });
  results.history_update_label = {
    pass: eq(r, { label: "v0.14.0 → v0.15.0", isRollback: false }),
    detail: r,
  };
}
{
  // New-style rollback entry: real target version + flag.
  const r = H.historyEntryDisplay({ from_version: "0.13.0", to_version: "0.12.0", rollback: true });
  results.history_rollback_label = {
    pass: eq(r, { label: "v0.13.0 → v0.12.0", isRollback: true }),
    detail: r,
  };
}
{
  // Legacy entry recorded the literal "rollback" as to_version.
  const r = H.historyEntryDisplay({ from_version: "0.14.0", to_version: "rollback" });
  results.history_legacy_rollback_label = {
    pass: eq(r, { label: "v0.14.0 → previous version", isRollback: true }),
    detail: r,
  };
}
{
  // Rollback whose target couldn't be resolved.
  const r = H.historyEntryDisplay({ from_version: "0.14.0", to_version: "", rollback: true });
  results.history_rollback_unknown_target = {
    pass: eq(r, { label: "v0.14.0 → previous version", isRollback: true }),
    detail: r,
  };
}

process.stdout.write(JSON.stringify(results));
