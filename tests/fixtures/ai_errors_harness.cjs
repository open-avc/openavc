"use strict";
// Loads the AI error mapper (aiErrors.ts) bundled on the fly with the
// esbuild in web/programmer/node_modules and checks that the non-streaming
// conversation paths get the same friendly copy the streaming path maps
// inline — instead of surfacing raw 'AI API 500: {json}' strings.
// Mirrors transport_picker_helpers_harness.cjs; the Python wrapper skips
// when the Node toolchain is absent.
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
const { friendlyAIError } = moduleObj.exports;

const results = {};
const FB = "Couldn't load conversations.";

// Status-mapped copy — parity with the streaming path's inline mapping.
results.limit_429 = friendlyAIError(new Error('AI API 429: {"detail":"x"}'), FB)
  .includes("request limit");
results.subscription_402 = friendlyAIError(new Error("AI API 402: "), FB)
  .includes("subscription");
results.unavailable_503 = friendlyAIError(new Error("AI API 503: down"), FB)
  .includes("paired and connected");

// Other statuses: JSON detail is unwrapped, raw JSON never shown.
results.detail_unwrapped =
  friendlyAIError(new Error('AI API 500: {"detail":"Cloud agent restarting"}'), FB) ===
  "Cloud agent restarting";
results.non_json_falls_back =
  friendlyAIError(new Error("AI API 500: <html>boom</html>"), FB) === FB;
results.empty_detail_falls_back =
  friendlyAIError(new Error('AI API 500: {"detail":""}'), FB) === FB;

// Non-AI errors keep their own message; empty input uses the fallback.
results.other_error_kept =
  friendlyAIError(new Error("Failed to fetch"), FB) === "Failed to fetch";
results.string_error_kept = friendlyAIError("offline", FB) === "offline";
results.empty_uses_fallback = friendlyAIError(new Error(""), FB) === FB;

console.log(JSON.stringify(results));
