"use strict";
// Loads the Simulation tab editor helpers (simulatorEditorHelpers.ts —
// React-free pure logic) bundled on the fly with the esbuild already in
// openavc/web/programmer/node_modules, and checks the response-delay parsing and the
// error-mode behavior/set_state editing that back the Driver Builder's
// Simulation tab. Mirrors config_schema_helpers_harness.cjs. The Python
// wrapper skips when the Node toolchain or esbuild is absent rather than
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
// A scenario that throws (e.g. a helper missing from a pre-fix build) counts
// as a failure with the error as detail, not a harness crash.
const scenario = (name, fnBody) => {
  try {
    results[name] = fnBody();
  } catch (e) {
    results[name] = { pass: false, detail: String(e) };
  }
};

// --- response delay: 0 is a valid authored value ---------------------------
scenario("delay_zero_is_stored", () => {
  // The defect this fixes: the old onChange used `parseFloat(v) || 0.05`, so
  // an authored 0 (instant response) silently snapped back to 0.05.
  const r = H.setCommandResponseDelay(undefined, "0");
  return { pass: eq(r, { command_response: 0 }), detail: r };
});
scenario("delay_value_stored", () => {
  const r = H.setCommandResponseDelay({ command_response: 2 }, "1.5");
  return { pass: eq(r, { command_response: 1.5 }), detail: r };
});
scenario("delay_empty_unsets", () => {
  // Clearing the input means "use the simulator default", not 0.05: the key
  // is removed and an empty delays map disappears from the YAML entirely.
  const r = H.setCommandResponseDelay({ command_response: 2 }, "");
  return { pass: r === undefined, detail: r };
});
scenario("delay_unset_keeps_other_delays", () => {
  const r = H.setCommandResponseDelay(
    { command_response: 2, power_on_warmup: 3 },
    "",
  );
  return { pass: eq(r, { power_on_warmup: 3 }), detail: r };
});
scenario("delay_negative_unsets", () => {
  // min=0 on the input blocks the spinner; typed negatives are dropped
  // rather than stored as a nonsense delay.
  const r = H.setCommandResponseDelay(undefined, "-1");
  return { pass: r === undefined, detail: r };
});

// --- error-mode behaviors match what the runtime reads ---------------------
scenario("behavior_options_match_runtime", () => {
  // Only no_response / corrupt_response are wired into the simulator
  // transports, plus "" for a set_state-only mode. The old dropdown offered
  // disconnect and custom_state, which no transport reads — dead ends.
  const values = H.SIM_ERROR_BEHAVIORS.map((b) => b.value);
  return {
    pass:
      eq(values, ["", "no_response", "corrupt_response"]) &&
      !values.includes("custom_state") &&
      !values.includes("disconnect"),
    detail: values,
  };
});
scenario("behavior_state_only_removes_key", () => {
  // "" (State Change Only) must remove the behavior key — the runtime treats
  // a behavior-less mode as set_state-only; behavior: "" would export junk.
  const r = H.applyErrorModeBehavior(
    { behavior: "no_response", description: "d" },
    "",
  );
  return {
    pass: !("behavior" in r) && r.description === "d",
    detail: r,
  };
});
scenario("behavior_set_normally", () => {
  const r = H.applyErrorModeBehavior({ description: "d" }, "corrupt_response");
  return { pass: r.behavior === "corrupt_response", detail: r };
});

// --- set_state editing writes the key the runtime actually reads -----------
scenario("set_state_uses_runtime_key", () => {
  // The defect this fixes: the editor had no way to author state changes at
  // all, and the type modeled the field as `state` while the runtime's
  // inject_error reads `set_state` — a dead-end contract.
  const r = H.addErrorModeStateEntry({ behavior: "no_response" }, ["power", "lamp_hours"]);
  return {
    pass: eq(r.set_state, { power: "" }) && !("state" in r),
    detail: r,
  };
});
scenario("add_entry_picks_first_unused", () => {
  const r = H.addErrorModeStateEntry(
    { set_state: { power: "off" } },
    ["power", "lamp_hours"],
  );
  return { pass: eq(r.set_state, { power: "off", lamp_hours: "" }), detail: r };
});
scenario("add_entry_noop_when_all_used", () => {
  const mode = { set_state: { power: "off" } };
  const r = H.addErrorModeStateEntry(mode, ["power"]);
  return { pass: eq(r, mode), detail: r };
});
scenario("rename_keeps_value_and_order", () => {
  const r = H.renameErrorModeStateVar(
    { set_state: { power: "off", lamp_hours: 19500 } },
    "power",
    "signal",
  );
  return {
    pass: eq(r.set_state, { signal: "off", lamp_hours: 19500 }),
    detail: r,
  };
});
scenario("remove_last_entry_drops_set_state", () => {
  const r = H.removeErrorModeStateEntry(
    { behavior: "no_response", set_state: { power: "off" } },
    "power",
  );
  return {
    pass: !("set_state" in r) && r.behavior === "no_response",
    detail: r,
  };
});

// --- set_state values coerce by the variable's declared type ---------------
scenario("coerce_matches_declared_types", () => {
  const checks = {
    boolTrue: H.coerceSimStateValue("boolean", "true") === true,
    boolFalse: H.coerceSimStateValue("boolean", "false") === false,
    integer: H.coerceSimStateValue("integer", "19500") === 19500,
    number: H.coerceSimStateValue("number", "2.5") === 2.5,
    float: H.coerceSimStateValue("float", "2.5") === 2.5,
    stringKeepsZeros: H.coerceSimStateValue("string", "0123") === "0123",
    undeclared: H.coerceSimStateValue(undefined, "on") === "on",
  };
  return { pass: Object.values(checks).every(Boolean), detail: checks };
});

process.stdout.write(JSON.stringify(results));
