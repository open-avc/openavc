"use strict";
// Bundles the real macroHelpers.ts (with the esbuild already in
// openavc/web/programmer/node_modules) and runs macroToScript over a set of scenario
// macros, printing {scenario: {script, meta}} JSON to stdout. The Python
// wrapper compiles and EXECUTES the generated scripts against a stubbed
// `openavc` module (whose compare() is the real server-side evaluator), so
// these scenarios prove the generated code matches macro-engine semantics.
// Skips happen on the Python side when the Node toolchain is absent.
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

const results = {};
function gen(name, macro, groups, meta) {
  results[name] = { script: H.macroToScript(macro, groups), meta: meta || {} };
}

// --- Coercion: conditional / wait_until comparisons must behave like the
// macro engine (string-stored numerics match; mismatches don't raise) ---
gen("coercion_conditional", {
  id: "m1",
  name: "Coercion Conditional",
  steps: [
    {
      action: "conditional",
      condition: { key: "var.level", operator: "gte", value: 50 },
      then_steps: [{ action: "device.command", device: "ok_dev", command: "go" }],
      else_steps: [{ action: "device.command", device: "else_dev", command: "halt" }],
    },
  ],
});

gen("coercion_wait_until", {
  id: "m2",
  name: "Coercion Wait",
  steps: [
    {
      action: "wait_until",
      condition: { key: "var.temp", operator: "lte", value: 70 },
      timeout: 5,
      on_timeout: "continue",
    },
    { action: "device.command", device: "after_dev", command: "done" },
  ],
});

// --- Injection: hostile name/id/state_key/event_pattern/cron must stay
// inert text, and the decorator patterns must round-trip exactly ---
const EVIL_KEY = 'lab.sensor") or True]\nimport os  # x';
const EVIL_PATTERN = 'evil") and None\nimport os  # y';
const EVIL_CRON = '0 0 * * *\nimport os';
const EVIL_NAME = 'Room "On" \\ <"""> break\nout';
const EVIL_ID = 'mac"id\n2';
gen("injection", {
  id: EVIL_ID,
  name: EVIL_NAME,
  steps: [],
  triggers: [
    { type: "state_change", enabled: true, state_key: EVIL_KEY },
    { type: "event", enabled: true, event_pattern: EVIL_PATTERN },
    { type: "schedule", enabled: true, cron: EVIL_CRON },
  ],
}, undefined, {
  state_key: EVIL_KEY,
  event_pattern: EVIL_PATTERN,
  name: EVIL_NAME,
});

// --- $-references: params, state.set values and event payloads resolve like
// the macro engine (top level only) ---
gen("dollar_params", {
  id: "m3",
  name: "Dollar Params",
  steps: [
    { action: "device.command", device: "proj", command: "set_preset",
      params: { level: "$var.preset", static: 5 } },
    { action: "state.set", key: "var.active_source", value: "$var.src" },
    { action: "event.emit", event: "source.changed",
      payload: { ref: "$var.src", n: 1 } },
    { action: "group.command", group: "g1", command: "gcmd",
      params: { p: "$var.preset" } },
  ],
}, [{ id: "g1", name: "G1", device_ids: ["d_on", "d_off"] }]);

// --- Python literal validity: true/false/null in params must become
// True/False/None, not JSON tokens ---
gen("python_literals", {
  id: "m4",
  name: "Literals",
  steps: [
    { action: "device.command", device: "amp", command: "cfg",
      params: { mute: true, label: null, nested: { a: [true, null, "x"] }, f: 1.5 } },
  ],
});

// --- skip_if_offline: the generated script must honor the offline guard ---
gen("skip_if_offline", {
  id: "m5",
  name: "Offline Guard",
  steps: [
    { action: "device.command", device: "dev_off", command: "cmd1", skip_if_offline: true },
    { action: "device.command", device: "dev_off", command: "cmd2" },
  ],
});

// --- group.command skips offline members (the macro engine always does) ---
gen("group_offline", {
  id: "m6",
  name: "Group Offline",
  steps: [
    { action: "group.command", group: "g1", command: "power_on" },
  ],
}, [{ id: "g1", name: "G1", device_ids: ["d_on", "d_off"] }]);

// --- Operator aliases (">=", "==") must evaluate, not silently turn into eq ---
gen("skip_if_alias", {
  id: "m7",
  name: "Alias Skip",
  steps: [
    { action: "device.command", device: "guarded", command: "cmd",
      skip_if: { key: "var.mode", operator: ">=", value: 2 } },
  ],
});

// --- Trigger operator check with delay re-check uses coercion too ---
gen("trigger_gte", {
  id: "m8",
  name: "Trigger GTE",
  steps: [{ action: "device.command", device: "trig_dev", command: "fire" }],
  triggers: [
    { type: "state_change", enabled: true, state_key: "var.x",
      state_operator: "gte", state_value: 50 },
  ],
});

// --- wait_until timeout=fail raises TimeoutError at the deadline ---
gen("wait_until_timeout_fail", {
  id: "m9",
  name: "Wait Fail",
  steps: [
    {
      action: "wait_until",
      condition: { key: "var.never", operator: "eq", value: "yes" },
      timeout: 0,
      on_timeout: "fail",
    },
  ],
});

// --- A plain macro stays plain (no regression on the simple path) ---
gen("legacy_plain", {
  id: "m10",
  name: "Plain",
  steps: [
    { action: "device.command", device: "disp", command: "input_hdmi1" },
    { action: "delay", seconds: 0 },
    { action: "state.set", key: "var.src", value: "hdmi1" },
    { action: "macro", macro: "sub_macro" },
  ],
});

// --- Ask for help and Clear help request have no script API ---
gen("help_steps", {
  id: "m11",
  name: "Help",
  steps: [
    { action: "help.request", message: "Hall 2 'projector'\nwill not start" },
    { action: "help.clear" },
    { action: "state.set", key: "var.after", value: "ran" },
  ],
});

// --- A trigger. key and a $trigger. reference read the run's firing context,
// as the macro engine reads them: Skip If, a Conditional and a step's values
// read it; a Wait Until reads none; a trigger's guard reads the state ---
gen("trigger_context", {
  id: "m12",
  name: "Trigger Context",
  steps: [
    { action: "state.set", key: "var.got_new", value: "$trigger.new_value" },
    {
      action: "device.command", device: "amp", command: "set",
      params: { level: "$trigger.new_value", src: "$trigger.source" },
    },
    {
      action: "device.command", device: "unless_off", command: "go",
      skip_if: { key: "trigger.new_value", operator: "eq", value: "off" },
    },
    {
      action: "conditional",
      condition: { key: "trigger.key", operator: "eq", value: "var.power" },
      then_steps: [{ action: "state.set", key: "var.branch", value: "then" }],
      else_steps: [{ action: "state.set", key: "var.branch", value: "else" }],
    },
    { action: "event.emit", event: "custom.seen", payload: { v: "$trigger.new_value" } },
  ],
  triggers: [
    { type: "state_change", enabled: true, state_key: "var.power" },
    { type: "event", enabled: true, event_pattern: "custom.go" },
    { type: "startup", enabled: true },
  ],
});

gen("trigger_context_wait_until", {
  id: "m13",
  name: "Wait On A Trigger Field",
  steps: [
    {
      action: "wait_until",
      condition: { key: "trigger.new_value", operator: "falsy" },
      timeout: 0,
      on_timeout: "fail",
    },
    { action: "state.set", key: "var.after", value: "ran" },
  ],
  triggers: [{ type: "state_change", enabled: true, state_key: "var.power" }],
});

gen("trigger_context_guard", {
  id: "m14",
  name: "Guard On A Trigger Field",
  steps: [{ action: "state.set", key: "var.after", value: "ran" }],
  triggers: [{
    type: "state_change", enabled: true, state_key: "var.power",
    conditions: [{ key: "trigger.new_value", operator: "eq", value: "on" }],
  }],
});

// --- A condition's value that names a key reads that key where the engine
// reads it: a step from the state or the run's firing context, a Wait Until
// with no context, a trigger's own test from the change, a guard from the
// state only ---
gen("reference_steps", {
  id: "m15",
  name: "Values That Name A Key",
  steps: [
    { action: "state.set", key: "var.ran", value: true,
      skip_if: { key: "var.power", operator: "eq", value: "$var.wanted" } },
    { action: "conditional", condition: { key: "var.power", operator: "eq", value: "$var.wanted" },
      then_steps: [{ action: "state.set", key: "var.then", value: true }],
      else_steps: [{ action: "state.set", key: "var.else", value: true }] },
    { action: "wait_until", condition: { key: "var.power", operator: "eq", value: "$var.wanted" },
      timeout: 0, on_timeout: "continue" },
  ],
  triggers: [{
    type: "state_change", enabled: true, state_key: "var.level",
    state_operator: "gte", state_value: "$var.limit",
    conditions: [{ key: "var.power", operator: "eq", value: "$var.wanted" }],
  }],
});

gen("reference_trigger_value", {
  id: "m16",
  name: "A Value Naming A Trigger Field",
  steps: [{
    action: "conditional", condition: { key: "var.power", operator: "eq", value: "$trigger.new_value" },
    then_steps: [{ action: "state.set", key: "var.then", value: true }],
    else_steps: [{ action: "state.set", key: "var.else", value: true }],
  }],
  triggers: [{
    type: "state_change", enabled: true, state_key: "var.wanted",
    conditions: [{ key: "var.power", operator: "ne", value: "$trigger.new_value" }],
  }],
});

process.stdout.write(JSON.stringify(results));
