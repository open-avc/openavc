"use strict";
// Loads the button binding press helpers (buttonBindingHelpers.ts —
// React-free pure logic shared by the UI Builder and the Surface
// Configurator) bundled on the fly with the esbuild already in
// openavc/web/programmer/node_modules, and replays the Remove flows that used to
// lose data: removing the primary press action nulled the whole binding in
// tap mode (every additional action discarded — the runtimes fire the full
// press list in order), and in toggle/tap-hold modes left the extras
// stranded behind an action-less primary where the editor can't show them
// but the runtimes still fire them.
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

const MACRO_1 = { action: "macro", macro: "room_on" };
const MACRO_2 = { action: "macro", macro: "lights_dim" };
const STATE_SET = { action: "state.set", key: "var.scene", value: "movie" };

// --- Removing the primary press action keeps the additional actions ---
{
  // Tap button: primary + two extras. Remove the primary: the first extra
  // becomes the new primary and the rest shift up. The old logic returned
  // null here, deleting all three actions from the element.
  const out = H.pressAfterActionEdit(MACRO_1, [MACRO_2, STATE_SET], null);
  results.remove_primary_promotes_next_action = {
    pass: eq(out, [MACRO_2, STATE_SET]),
    detail: out,
  };
}

// --- Non-tap mode: promoted action joins the mode config on press[0] ---
{
  // A toggle whose press[0] carries config + On Action, with a stray extra
  // (hand-edited projects can hold this shape). Removing the On Action
  // used to leave [config-only, extra] — the extra invisible in the editor
  // (extras UI is tap-only) but still fired by the runtimes. Now the extra
  // becomes the On Action, config intact.
  const press = {
    mode: "toggle",
    toggle_key: "device.proj.power",
    toggle_value: true,
    off_action: { action: "device.command", device: "proj", command: "power_off" },
    ...MACRO_1,
  };
  const out = H.pressAfterActionEdit(press, [MACRO_2], null);
  results.remove_promoted_action_keeps_mode_config = {
    pass: eq(out, [
      {
        mode: "toggle",
        off_action: { action: "device.command", device: "proj", command: "power_off" },
        toggle_key: "device.proj.power",
        toggle_value: true,
        ...MACRO_2,
      },
    ]),
    detail: out,
  };
}

// --- Removing the only action of a bare tap button clears the binding ---
{
  const out = H.pressAfterActionEdit(MACRO_1, [], null);
  results.remove_last_action_clears_binding = {
    pass: out === null,
    detail: out,
  };
}

// --- Removing a toggle's On Action keeps the config-only entry ---
{
  // Off Action still fires via toggle_off, so the config must survive.
  const press = {
    mode: "toggle",
    toggle_key: "var.audio_on",
    toggle_value: false,
    off_action: { action: "macro", macro: "audio_off" },
    ...MACRO_1,
  };
  const out = H.pressAfterActionEdit(press, [], null);
  results.remove_on_action_keeps_toggle_config = {
    pass:
      eq(out, [
        {
          mode: "toggle",
          off_action: { action: "macro", macro: "audio_off" },
          toggle_key: "var.audio_on",
          toggle_value: false,
        },
      ]),
    detail: out,
  };
}

// --- Editing (not removing) merges over config and keeps extras ---
{
  const press = { mode: "tap_hold", hold_threshold_ms: 700, hold_action: MACRO_2, ...MACRO_1 };
  const out = H.pressAfterActionEdit(press, [STATE_SET], { action: "macro", macro: "projector_on" });
  results.edit_action_keeps_config_and_extras = {
    pass: eq(out, [
      { mode: "tap_hold", hold_action: MACRO_2, hold_threshold_ms: 700, action: "macro", macro: "projector_on" },
      STATE_SET,
    ]),
    detail: out,
  };
}

// --- press[0] splits cleanly into config vs. action fields ---
{
  const press = {
    mode: "toggle",
    toggle_key: "var.on",
    toggle_value: false, // "on when false" is valid and must survive
    on_label: "Turn Off",
    ...MACRO_1,
  };
  const action = H.pressActionFields(press);
  const config = H.pressConfigFields(press);
  const configOnly = H.pressActionFields({ mode: "toggle", toggle_key: "var.on" });
  results.press_entry_splits_config_from_action = {
    pass:
      eq(action, MACRO_1) &&
      eq(config, { mode: "toggle", toggle_key: "var.on", toggle_value: false, on_label: "Turn Off" }) &&
      configOnly === null,
    detail: { action, config, configOnly },
  };
}

process.stdout.write(JSON.stringify(results));
