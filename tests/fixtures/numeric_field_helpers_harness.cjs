"use strict";
// Loads the UI Builder numeric property-field parsers (numericField.ts —
// React-free pure logic) bundled on the fly with the esbuild already in
// openavc/web/programmer/node_modules and checks the clear-means-unset parsing that
// backs the BasicProperties numeric inputs. Mirrors
// config_schema_helpers_harness.cjs. The Python wrapper skips when the Node
// toolchain or esbuild is absent rather than failing the Python-only CI gate.
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
const scenario = (name, fnBody) => {
  try {
    results[name] = fnBody();
  } catch (e) {
    results[name] = { pass: false, detail: String(e) };
  }
};

scenario("empty_unsets_not_zero", () => {
  // The defect this fixes: the editors parsed with Number(v) — and
  // Number("") is 0 — so briefly clearing Min/Max/Step/Digits to retype
  // committed a literal 0 (digits=0 keypad, step=0 slider).
  const legacy = Number("");
  const fixed = H.numOrUndefined("");
  return {
    pass: legacy === 0 && fixed === undefined,
    detail: { legacy, fixed },
  };
});
scenario("whitespace_unsets", () => {
  // Number(" ") is also 0.
  return {
    pass: H.numOrUndefined("  ") === undefined,
    detail: H.numOrUndefined("  "),
  };
});
scenario("zero_stays_zero", () => {
  // An explicit 0 is a real value (e.g. meter max 0 dB) — unset ≠ 0.
  const got = H.numOrUndefined("0");
  return { pass: got === 0, detail: got };
});
scenario("garbage_unsets", () => {
  return {
    pass: H.numOrUndefined("abc") === undefined,
    detail: H.numOrUndefined("abc"),
  };
});
scenario("numbers_parse", () => {
  const checks = {
    negative: H.numOrUndefined("-12") === -12,
    float: H.numOrUndefined("2.5") === 2.5,
    integer: H.numOrUndefined("60") === 60,
  };
  return { pass: Object.values(checks).every(Boolean), detail: checks };
});
scenario("int_truncates_and_unsets", () => {
  // Integer-typed plugin config fields: same unset semantics as the float
  // path, value truncated like the old parseInt read "2.7" — but "" no
  // longer becomes 0 (parseInt("") || 0 did).
  const checks = {
    truncates: H.intOrUndefined("2.7") === 2,
    negativeTruncates: H.intOrUndefined("-2.7") === -2,
    emptyUnsets: H.intOrUndefined("") === undefined,
    zeroKept: H.intOrUndefined("0") === 0,
  };
  return { pass: Object.values(checks).every(Boolean), detail: checks };
});

scenario("commit_clamps_once_empty_stays_empty", () => {
  // The NumericInput commit path: the clamp runs HERE, on a finished edit —
  // never per keystroke. The defect this replaces: clearing a Width field ran
  // Math.max(0.1, Number("") || 0) on the change event, committing a live
  // 0.1%-wide element before you could type the value you meant.
  const checks = {
    emptyIsUndefined: H.commitNumeric("", { min: 0.1 }) === undefined,
    belowFloorClamps: H.commitNumeric("0.05", { min: 0.1 }) === 0.1,
    aboveCeilingClamps: H.commitNumeric("60", { min: 1, max: 48 }) === 48,
    inRangeUntouched: H.commitNumeric("25", { min: 0.1 }) === 25,
    integerTruncates: H.commitNumeric("2.7", { integer: true, min: 1 }) === 2,
    garbageIsUndefined: H.commitNumeric("abc", { min: 0.1 }) === undefined,
    unboundedPasses: H.commitNumeric("-3.5", {}) === -3.5,
  };
  return { pass: Object.values(checks).every(Boolean), detail: checks };
});

scenario("live_commit_only_when_no_correction_needed", () => {
  // The NumericInput keystroke path: a value previews live only when it needs
  // no clamp, so mid-edit states ("0" on the way to "0.5" with min 0.1, "60"
  // on the way past a max) are tolerated instead of fought.
  const checks = {
    emptySkipped: H.liveNumeric("", { min: 0.1 }) === undefined,
    belowFloorSkipped: H.liveNumeric("0.05", { min: 0.1 }) === undefined,
    aboveCeilingSkipped: H.liveNumeric("60", { min: 1, max: 48 }) === undefined,
    inRangeCommits: H.liveNumeric("25", { min: 0.1 }) === 25,
    boundaryCommits: H.liveNumeric("0.1", { min: 0.1 }) === 0.1,
    integerTruncates: H.liveNumeric("6.9", { integer: true, min: 1, max: 48 }) === 6,
  };
  return { pass: Object.values(checks).every(Boolean), detail: checks };
});

process.stdout.write(JSON.stringify(results));
