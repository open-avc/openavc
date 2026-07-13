"use strict";
// Loads the trigger editor helpers (triggerHelpers.ts — React-free pure
// logic) bundled on the fly with the esbuild already in
// web/programmer/node_modules, and checks the cron-safe field parsing, the
// verbatim day-of-week rebuild, and the saved-event category detection that
// back the schedule and event trigger editors.
// Mirrors state_variable_helpers_harness.cjs. The Python wrapper skips when
// the Node toolchain or esbuild is absent rather than failing the
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
const setEq = (s, arr) => s.size === arr.length && arr.every((v) => s.has(v));
const results = {};

// --- cronFieldInt: plain integers only, everything else falls back ---
{
  results.field_plain_int_parses = {
    pass: H.cronFieldInt("30", 0) === 30,
    detail: H.cronFieldInt("30", 0),
  };
}
{
  // parseInt('*/15') is NaN — the value that used to corrupt rebuilt crons.
  results.field_step_falls_back = {
    pass: H.cronFieldInt("*/15", 0) === 0,
    detail: H.cronFieldInt("*/15", 0),
  };
}
{
  // parseInt('8-17') silently truncates to 8; the helper must not.
  results.field_range_falls_back = {
    pass: H.cronFieldInt("8-17", 18) === 18,
    detail: H.cronFieldInt("8-17", 18),
  };
}
{
  results.field_star_falls_back = {
    pass: H.cronFieldInt("*", 5) === 5,
    detail: H.cronFieldInt("*", 5),
  };
}

// --- getCronActiveDays ---
{
  const s = H.getCronActiveDays("0 18 * * 1-5");
  results.days_weekday_range = { pass: setEq(s, [1, 2, 3, 4, 5]), detail: [...s] };
}
{
  const s = H.getCronActiveDays("0 18 * * *");
  results.days_star_is_all = { pass: setEq(s, [0, 1, 2, 3, 4, 5, 6]), detail: [...s] };
}
{
  const s = H.getCronActiveDays("0 12 * * 0,6");
  results.days_weekend_list = { pass: setEq(s, [0, 6]), detail: [...s] };
}
{
  const s = H.getCronActiveDays("");
  results.days_malformed_empty = { pass: s.size === 0, detail: [...s] };
}

// --- cronWithDays preserves non-dow fields verbatim ---
{
  // THE corruption case: a stepped/range schedule plus a weekday toggle.
  // The OLD toggleDay rebuilt the cron from parseInt'd minute/hour:
  // parseInt('*/15') -> NaN, parseInt('8-17') -> 8, writing an invalid cron
  // ("NaN 8 * * ...") that croniter can't parse — silently dead trigger.
  const cron = "*/15 8-17 * * 1-5";
  const days = [1, 2, 3, 4, 5, 6];
  const oldResult = `${parseInt("*/15")} ${parseInt("8-17")} * * ${days.join(",")}`;
  const newResult = H.cronWithDays(cron, days);
  results.rebuild_preserves_stepped_fields = {
    pass:
      !H.isValidCron(oldResult) && // proves the old formula corrupted it
      newResult === "*/15 8-17 * * 1,2,3,4,5,6" &&
      H.isValidCron(newResult),
    detail: { oldResult, newResult },
  };
}
{
  // Day-of-month and month survive too (the old rebuild hardcoded '* *').
  const r = H.cronWithDays("0 9 1 * *", [1]);
  results.rebuild_preserves_day_of_month = {
    pass: r === "0 9 1 * 1",
    detail: r,
  };
}
{
  const r = H.cronWithDays("0 18 * * *", [5, 1, 3]);
  results.rebuild_sorts_days = { pass: r === "0 18 * * 1,3,5", detail: r };
}
{
  const r = H.cronWithDays("garbage", [1, 2]);
  results.rebuild_malformed_falls_back = {
    pass: r === "0 18 * * 1,2" && H.isValidCron(r),
    detail: r,
  };
}

// --- Preset switch on a stepped schedule stays valid ---
{
  // OLD: handlePresetChange fed parseInt'd fields into CRON_PRESETS.make,
  // so "Every day at..." on a stepped cron produced "NaN 8 * * *".
  const daily = H.CRON_PRESETS[0];
  const oldResult = daily.make(parseInt("8-17"), parseInt("*/15"));
  const newResult = daily.make(H.cronFieldInt("8-17", 18), H.cronFieldInt("*/15", 0));
  results.preset_switch_on_stepped_valid = {
    pass: !H.isValidCron(oldResult) && newResult === "0 18 * * *" && H.isValidCron(newResult),
    detail: { oldResult, newResult },
  };
}

// --- detectEventCategory: editor opens on the saved event's category ---
const DEVICES = [{ id: "proj", name: "Projector" }];
const MACROS = [{ id: "m1", name: "Startup" }];
{
  const r = H.detectEventCategory("device.connected.proj", DEVICES, MACROS);
  results.category_device_event = { pass: r === 0, detail: r };
}
{
  const r = H.detectEventCategory("macro.completed.m1", DEVICES, MACROS);
  results.category_macro_event = { pass: r === 1, detail: r };
}
{
  const r = H.detectEventCategory("system.started", DEVICES, MACROS);
  results.category_system_event = { pass: r === 2, detail: r };
}
{
  // Script events and wildcards aren't in any list — land on Custom, which
  // shows the raw pattern instead of a mismatched Device Events dropdown.
  const r = H.detectEventCategory("script.myscript", DEVICES, MACROS);
  results.category_unknown_is_custom = { pass: r === 3, detail: r };
}
{
  // Saved event of a since-deleted device: no match -> Custom (raw pattern
  // stays visible and editable, nothing overwritten).
  const r = H.detectEventCategory("device.connected.gone", DEVICES, MACROS);
  results.category_deleted_device_is_custom = { pass: r === 3, detail: r };
}
{
  const r = H.detectEventCategory(undefined, DEVICES, MACROS);
  results.category_no_pattern_defaults_device = { pass: r === 0, detail: r };
}

// --- isValidCron accepts everything the runtime (croniter) does ---
// The old validator required 5 numeric fields, so @-aliases and day/month
// names — all valid at runtime — were falsely flagged "Invalid", tempting an
// integrator to delete a working schedule.
const cronCase = (key, cron, expected) => {
  const got = H.isValidCron(cron);
  results[key] = { pass: got === expected, detail: { cron, got, expected } };
};
cronCase("cron_alias_daily", "@daily", true);
cronCase("cron_alias_hourly", "@hourly", true);
cronCase("cron_dow_name_range", "0 8 * * mon-fri", true);
cronCase("cron_dow_name_single", "30 6 * * sun", true);
cronCase("cron_dow_name_list", "0 9 * * mon,wed,fri", true);
cronCase("cron_month_name", "0 0 1 jan *", true);
// Still-strict where the runtime is strict:
cronCase("cron_reboot_rejected", "@reboot", false); // croniter can't schedule it
cronCase("cron_name_wrong_field_rejected", "mon 8 * * *", false); // name only in month/dow
cronCase("cron_bad_name_rejected", "0 8 * * xyz", false);
cronCase("cron_out_of_range_rejected", "99 8 * * *", false);
cronCase("cron_numeric_range_still_valid", "0 8 * * 1-5", true);

process.stdout.write(JSON.stringify(results));
