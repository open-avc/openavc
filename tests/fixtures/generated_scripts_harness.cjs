"use strict";
// Bundles the real scriptTemplates.ts and macroHelpers.ts (with the esbuild
// already in openavc/web/programmer/node_modules) and prints every script the
// Programmer writes for an integrator as JSON on stdout:
//
//   templates   [{name, code}] -- every entry in SCRIPT_TEMPLATES
//   steps       {label: script} -- Convert to Script of one macro per entry in
//               STEP_TYPES, alone, behind a skip_if, and inside each branch of
//               a conditional
//   step_actions  the STEP_TYPES actions, for the Python side to hold to the
//               engine's list
//   triggers    {type: script} -- Convert to Script of one macro per entry in
//               TRIGGER_TYPES, with a state.set step the test can see
//
// tests/test_generated_scripts_load.py loads each through the real
// ScriptEngine. Skips happen on the Python side when Node is absent.
const path = require("path");

const [templatesPath, helpersPath, triggersPath] = process.argv.slice(2);

const esbuild = require("esbuild");
function load(entry) {
  const built = esbuild.buildSync({
    entryPoints: [entry],
    bundle: true,
    format: "cjs",
    platform: "node",
    write: false,
    logLevel: "silent",
  });
  const moduleObj = { exports: {} };
  const fn = new Function("exports", "require", "module", "__filename", "__dirname", built.outputFiles[0].text);
  fn(moduleObj.exports, require, moduleObj, entry, path.dirname(entry));
  return moduleObj.exports;
}

const { SCRIPT_TEMPLATES } = load(templatesPath);
const { STEP_TYPES, macroToScript } = load(helpersPath);
const { TRIGGER_TYPES } = load(triggersPath);

const results = {
  templates: SCRIPT_TEMPLATES.map((t) => ({ name: t.name, code: t.code })),
  steps: {},
  step_actions: STEP_TYPES.map((t) => t.action),
  triggers: {},
};

// --- Every step type, as the editor first adds it. A step with no script
// equivalent comes out as comments, so each is also generated where it is the
// only thing in a block: behind a skip_if and inside a conditional's branches.
const CONDITION = { key: "var.acme_ready", operator: "eq", value: true };
function stepMacro(label, steps) {
  return { id: "macro_acme", name: `Acme ${label}`, steps };
}
const stepSamples = [
  ...STEP_TYPES.map((t) => ({ label: t.action, step: { ...t.defaults(), action: t.action } })),
  // A plugin's macro action and one nothing knows take the fallback branches.
  { label: "acme.flash (plugin)", step: { action: "acme.flash", params: { times: 2 } } },
  { label: "acme_unknown", step: { action: "acme_unknown" } },
];
for (const { label, step } of stepSamples) {
  results.steps[label] = macroToScript(stepMacro(label, [step]));
  results.steps[`${label} behind skip_if`] = macroToScript(
    stepMacro(label, [{ ...step, skip_if: CONDITION }]),
  );
  results.steps[`${label} in then`] = macroToScript(
    stepMacro(label, [{ action: "conditional", condition: CONDITION, then_steps: [step], else_steps: [] }]),
  );
  results.steps[`${label} in else`] = macroToScript(
    stepMacro(label, [{
      action: "conditional",
      condition: CONDITION,
      then_steps: [{ action: "state.set", key: "var.acme_then", value: 1 }],
      else_steps: [step],
    }]),
  );
}

// --- Every trigger type, filled in the way an integrator would. A type with
// no fill keeps the editor's defaults; the Python side names what is missing.
const TRIGGER_FILL = {
  schedule: { cron: "0 22 * * 1-5" },
  state_change: { state_key: "var.acme_level", state_operator: "gte", state_value: 50 },
  event: { event_pattern: "custom.acme_go" },
  startup: { delay_seconds: 0 },
};
for (const t of TRIGGER_TYPES) {
  const trigger = { id: `trigger_${t.type}`, ...t.defaults(), ...(TRIGGER_FILL[t.type] ?? {}) };
  results.triggers[t.type] = macroToScript({
    id: "macro_acme",
    name: `Acme ${t.label}`,
    steps: [{ action: "state.set", key: "var.acme_fired", value: t.type }],
    triggers: [trigger],
  });
}

process.stdout.write(JSON.stringify(results));
