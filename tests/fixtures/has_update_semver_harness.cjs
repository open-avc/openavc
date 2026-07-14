"use strict";
// Bundles the Programmer SPA's api/types.ts with the esbuild in
// web/programmer/node_modules and exercises hasUpdate(installed, available)
// across semver cases with pre-release and build suffixes. The old
// `installed.split('.').map(Number)` turned '1.0.1-beta' into [1,0,NaN] and
// `b[i] || 0` coerced NaN to 0, so updates to/from suffixed versions were
// silently mis-detected (hidden, or spuriously shown for +build metadata).
// Prints JSON results to stdout; the Python wrapper skips when Node/esbuild is
// absent.
const path = require("path");

const typesPath = process.argv[2];

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
  const code = built.outputFiles[0].text;
  const moduleObj = { exports: {} };
  const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
  fn(moduleObj.exports, require, moduleObj, entry, path.dirname(entry));
  return moduleObj.exports;
}

const T = load(typesPath);

const results = {};
function report(name, pass, detail) {
  results[name] = { pass, detail: detail === undefined ? null : detail };
}

// name -> [installed, available, expected]
const CASES = {
  // --- Clean x.y.z (behavior must be unchanged) ---
  clean_patch_newer: ["1.0.0", "1.0.1", true],
  clean_none_newer: ["1.0.1", "1.0.0", false],
  clean_equal: ["1.0.0", "1.0.0", false],
  clean_minor_numeric_order: ["1.2.0", "1.10.0", true], // 10 > 2, not string "10" < "2"
  clean_missing_patch: ["1.0", "1.0.1", true],

  // --- Pre-release / build suffixes (the bug) ---
  // available is a suffixed newer version — was hidden (NaN->0).
  prerelease_available_newer: ["1.0.0", "1.0.1-beta", true],
  // release supersedes the installed pre-release — was hidden (NaN->0 equal).
  release_over_installed_prerelease: ["2.0.0-rc.1", "2.0.0", true],
  // both pre-release, higher pre-release number — was hidden (both NaN->0).
  prerelease_bump: ["1.0.0-beta.1", "1.0.0-beta.2", true],
  // build metadata has no precedence — must NOT report an update.
  build_metadata_not_an_update: ["1.0.0", "1.0.0+build.5", false],
  // installing a pre-release of the same release is a downgrade, not an update.
  installed_release_available_prerelease: ["1.0.0", "1.0.0-beta", false],

  // --- Guards ---
  empty_installed: ["", "1.0.0", false],
  empty_available: ["1.0.0", "", false],
};

function main() {
  if (typeof T.hasUpdate !== "function") {
    report("hasUpdate_exported", false, "hasUpdate is not exported from types.ts");
    process.stdout.write(JSON.stringify(results));
    return;
  }
  for (const [name, [installed, available, expected]] of Object.entries(CASES)) {
    let got;
    try {
      got = T.hasUpdate(installed, available);
    } catch (e) {
      report(name, false, `threw: ${e}`);
      continue;
    }
    report(name, got === expected, `hasUpdate(${JSON.stringify(installed)}, ${JSON.stringify(available)}) => ${got}, expected ${expected}`);
  }
  process.stdout.write(JSON.stringify(results));
}

main();
