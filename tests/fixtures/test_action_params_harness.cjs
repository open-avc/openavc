"use strict";
// Loads the binding editor's Test-button param helpers (testActionParams.ts
// — React-free pure logic) bundled on the fly with the esbuild already in
// openavc/web/programmer/node_modules. The Test button used to send command params
// raw, so a change/submit binding's "$value" (or a "$var.volume" state ref)
// went to the device as a literal string — a malformed control command on
// real AV hardware. The helper must mirror the runtime resolver
// (openavc/core/value_resolver.py): state refs resolve from the live state
// mirror, interaction tokens have no value in the editor and block the
// send, and a state ref with no current value blocks instead of sending
// the None the runtime would.
// Mirrors trigger_helpers_harness.cjs. The Python wrapper skips when the
// Node toolchain or esbuild is absent rather than failing the Python-only
// CI gate.
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

const LIVE = {
  "var.volume": 42,
  "var.preset": "movie",
  "device.dsp.mute": false,
  "var.empty": undefined,
};

// --- Interaction tokens never go to the wire from a Test click ---
{
  const outcomes = ["$value", "$input", "$output", "$mute"].map((token) =>
    H.resolveTestParams({ level: token }, LIVE),
  );
  results.event_tokens_block_the_send = {
    pass: outcomes.every((r) => r.ok === false && r.reason === "event" && r.param === "level"),
    detail: outcomes,
  };
}

// --- State refs resolve from live state, exactly like the runtime ---
{
  const r = H.resolveTestParams(
    { volume: "$var.volume", preset: "$var.preset", mute: "$device.dsp.mute" },
    LIVE,
  );
  results.state_refs_resolve_from_live_state = {
    pass: r.ok === true && eq(r.params, { volume: 42, preset: "movie", mute: false }),
    detail: r,
  };
}

// --- A ref with no current value blocks instead of sending None ---
{
  const missing = H.resolveTestParams({ v: "$var.nonexistent" }, LIVE);
  const undef = H.resolveTestParams({ v: "$var.empty" }, LIVE);
  results.missing_state_ref_blocks_the_send = {
    pass:
      missing.ok === false && missing.reason === "no_value" && missing.token === "$var.nonexistent" &&
      undef.ok === false && undef.reason === "no_value",
    detail: { missing, undef },
  };
}

// --- Static params pass through untouched (types preserved) ---
{
  const r = H.resolveTestParams(
    { input: 3, label: "HDMI 1", enabled: true, note: "costs $5", nothing: null },
    LIVE,
  );
  results.static_params_pass_through = {
    pass:
      r.ok === true &&
      eq(r.params, { input: 3, label: "HDMI 1", enabled: true, note: "costs $5", nothing: null }),
    detail: r,
  };
}

// --- Mixed static + resolvable dynamic params send resolved values ---
{
  const r = H.resolveTestParams({ zone: 2, level: "$var.volume" }, LIVE);
  results.mixed_params_resolve_per_param = {
    pass: r.ok === true && eq(r.params, { zone: 2, level: 42 }),
    detail: r,
  };
}

// --- The refusal messages name the param and say what to do ---
{
  const event = H.testBlockedMessage({ ok: false, param: "level", token: "$value", reason: "event" });
  const noValue = H.testBlockedMessage({ ok: false, param: "v", token: "$var.gone", reason: "no_value" });
  results.blocked_messages_name_param_and_token = {
    pass:
      event.includes('"level"') && event.includes("$value") && event.includes("fixed value") &&
      noValue.includes('"v"') && noValue.includes("$var.gone") && noValue.includes("no current value"),
    detail: { event, noValue },
  };
}

process.stdout.write(JSON.stringify(results));
