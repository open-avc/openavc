"use strict";
// Loads the AI error mapper (aiErrors.ts) bundled on the fly with the
// esbuild in openavc/web/programmer/node_modules and checks that the non-streaming
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

// Status copy — parity with the streaming path, which maps through the same
// helper. A refusal that carried no sentence gets the fixed copy.
results.limit_429 = friendlyAIError(new Error("AI API 429: rate limited"), FB)
  .includes("request limit");
results.subscription_402 = friendlyAIError(new Error("AI API 402: "), FB)
  .includes("subscription");
results.unavailable_503 = friendlyAIError(new Error("AI API 503: down"), FB)
  .includes("paired and connected");

// ...and a refusal that DID carry one keeps it. An account past its allowance
// and a cloud that is briefly down used to read as the same fixed sentence.
results.relays_429_sentence =
  friendlyAIError(
    new Error('AI API 429: {"detail":"Rate limit exceeded (60 requests/minute). Please wait."}'),
    FB
  ) === "Rate limit exceeded (60 requests/minute). Please wait.";
results.relays_402_sentence =
  friendlyAIError(
    new Error('AI API 402: {"detail":"Adding a paid space lifts it."}'),
    FB
  ) === "Adding a paid space lifts it.";
results.relays_503_sentence =
  friendlyAIError(
    new Error('AI API 503: {"detail":"The AI assistant is paused on this account."}'),
    FB
  ) === "The AI assistant is paused on this account.";

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
