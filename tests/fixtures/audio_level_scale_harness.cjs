"use strict";
// Loads the simulator AudioPanel's level-scale helpers (audioLevelScale.ts —
// zero-import pure math) and checks the fraction / dB / percent classification
// and the meter + write-back conversions. The old inline test read any level in
// [0,1] as a 0..1 fraction, so a dB value at 0 dB (nominal, in [0,1]) rendered a
// silent 0% meter and the slider wrote back the wrong scale. Mirrors
// project_import_harness.cjs (esbuild is only needed to strip TS syntax). The
// Python wrapper skips when the Node toolchain or esbuild is absent.
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

const approx = (a, b) => Math.abs(a - b) < 0.01;
const results = {};

// --- scale classification ---
results.m310_scale_db_when_hasdb = { pass: H.audioLevelScale(0, true) === "db", detail: H.audioLevelScale(0, true) };
results.m310_scale_fraction = { pass: H.audioLevelScale(0.5, false) === "fraction", detail: H.audioLevelScale(0.5, false) };
results.m310_scale_db_when_negative = { pass: H.audioLevelScale(-5, false) === "db", detail: H.audioLevelScale(-5, false) };
results.m310_scale_percent = { pass: H.audioLevelScale(50, false) === "percent", detail: H.audioLevelScale(50, false) };

// --- normalize (meter) ---
// THE fix: 0 dB is nominal, not silent. Old code rendered it 0% (fraction).
results.m310_zero_db_not_silent = {
  pass: H.normalizeAudioLevel(0, true) > 0 && Math.round(H.normalizeAudioLevel(0, true)) === 89,
  detail: H.normalizeAudioLevel(0, true),
};
// And 1 dB is ~90%, not the full 100% the old fraction path gave.
results.m310_one_db_not_full = {
  pass: Math.round(H.normalizeAudioLevel(1, true)) === 90,
  detail: H.normalizeAudioLevel(1, true),
};
results.m310_fraction_half = { pass: H.normalizeAudioLevel(0.5, false) === 50, detail: H.normalizeAudioLevel(0.5, false) };
results.m310_fraction_bounds = {
  pass: H.normalizeAudioLevel(0, false) === 0 && H.normalizeAudioLevel(1, false) === 100,
  detail: [H.normalizeAudioLevel(0, false), H.normalizeAudioLevel(1, false)],
};
results.m310_db_bounds = {
  pass: H.normalizeAudioLevel(-100, true) === 0 && H.normalizeAudioLevel(12, true) === 100,
  detail: [H.normalizeAudioLevel(-100, true), H.normalizeAudioLevel(12, true)],
};
// A 0..100 device passes through (old code mapped 50 -> 100 via level+100).
results.m310_percent_passthrough = { pass: H.normalizeAudioLevel(50, false) === 50, detail: H.normalizeAudioLevel(50, false) };

// --- denormalize (slider write-back) matches the same scale ---
results.m310_denorm_fraction = { pass: H.denormalizeAudioLevel(75, 0.5, false) === 0.75, detail: H.denormalizeAudioLevel(75, 0.5, false) };
results.m310_denorm_percent = { pass: H.denormalizeAudioLevel(50, 50, false) === 50, detail: H.denormalizeAudioLevel(50, 50, false) };
// 50% on a dB scale -> -100 + 0.5*112 = -44 dB (old code wrote the raw 50).
results.m310_denorm_db = { pass: approx(H.denormalizeAudioLevel(50, 0, true), -44), detail: H.denormalizeAudioLevel(50, 0, true) };

process.stdout.write(JSON.stringify(results));
