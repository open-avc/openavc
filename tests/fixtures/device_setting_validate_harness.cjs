"use strict";
// Loads the real device-setting validator (views/devices/deviceUtils.ts,
// bundled on the fly with the esbuild in web/programmer/node_modules) and
// checks validateSettingValue: blank/garbage numeric input is rejected with
// an actionable error instead of being coerced to 0 and written to the
// hardware, min/max/regex from the setting definition are enforced, and
// legitimate values (including 0) pass through coerced. Prints JSON results;
// the Python wrapper skips when the Node toolchain or esbuild is absent.
const path = require("path");

const utilsPath = process.argv[2];

const esbuild = require("esbuild");
const built = esbuild.buildSync({
  entryPoints: [utilsPath],
  bundle: true,
  format: "cjs",
  platform: "node",
  write: false,
  logLevel: "silent",
});
const code = built.outputFiles[0].text;
const moduleObj = { exports: {} };
const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
fn(moduleObj.exports, require, moduleObj, utilsPath, path.dirname(utilsPath));
const V = moduleObj.exports.validateSettingValue;

const results = {};
const ok = (r, value) => r.ok === true && r.value === value;
const bad = (r, re) => r.ok === false && re.test(r.error);

{
  // The headline defect: blank or mistyped numeric input used to coerce to
  // 0 and save — both must now be rejected with a message.
  results.m156_blank_and_garbage_rejected = {
    pass:
      bad(V({ type: "integer" }, ""), /Enter a number/) &&
      bad(V({ type: "integer" }, "   "), /Enter a number/) &&
      bad(V({ type: "number" }, "abc"), /not a number/),
    detail: {
      blank: V({ type: "integer" }, ""),
      garbage: V({ type: "number" }, "abc"),
    },
  };
}
{
  // A legitimate 0 still saves (the old `|| 0` made 0 indistinguishable
  // from invalid input; the validator must treat it as a real value).
  results.m156_zero_and_negatives_valid = {
    pass:
      ok(V({ type: "integer" }, "0"), 0) &&
      ok(V({ type: "number" }, "-3.5"), -3.5),
    detail: { zero: V({ type: "integer" }, "0") },
  };
}
{
  // min/max from the setting definition are enforced with actionable text.
  const def = { type: "integer", min: 1, max: 10 };
  results.m156_min_max_enforced = {
    pass:
      bad(V(def, "0"), /at least 1/) &&
      bad(V(def, "11"), /at most 10/) &&
      ok(V(def, "5"), 5),
    detail: { low: V(def, "0"), high: V(def, "11") },
  };
}
{
  // Integers reject fractions (parseInt would have silently truncated);
  // numbers accept floats and scientific notation.
  results.m156_integer_vs_number_coercion = {
    pass:
      bad(V({ type: "integer" }, "3.7"), /whole number/) &&
      ok(V({ type: "number" }, "3.7"), 3.7) &&
      ok(V({ type: "number" }, "1e2"), 100),
    detail: { frac: V({ type: "integer" }, "3.7") },
  };
}
{
  // Strings: regex from the definition is enforced; an invalid regex from a
  // driver never blocks the save; booleans coerce.
  results.m156_string_regex_and_boolean = {
    pass:
      ok(V({ type: "string", regex: "^[A-Z]{3}$" }, "ABC"), "ABC") &&
      bad(V({ type: "string", regex: "^[A-Z]{3}$" }, "abc"), /required format/) &&
      ok(V({ type: "string", regex: "(" }, "anything"), "anything") &&
      ok(V({ type: "boolean" }, "true"), true) &&
      ok(V({ type: "boolean" }, "false"), false) &&
      ok(V(undefined, "free text"), "free text"),
    detail: {
      regexFail: V({ type: "string", regex: "^[A-Z]{3}$" }, "abc"),
    },
  };
}

process.stdout.write(JSON.stringify(results));
