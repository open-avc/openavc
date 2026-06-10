"use strict";
// Loads the real Live Test panel helpers (liveTestHelpers.ts, bundled on the
// fly with the esbuild already in web/programmer/node_modules) and checks the
// wire-preview routing (presence-based, mirroring configurable.py), the
// query_params rendering, and the transport-shape mismatch messages. Prints
// JSON results to stdout; the Python wrapper skips when the Node toolchain or
// esbuild is absent.
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

// --- L-091: HTTP preview includes query_params like the runtime ----------
{
  const cmd = {
    label: "Status",
    send: "",
    method: "GET",
    path: "/api/status",
    query_params: { verbose: "1", ch: "{ch}" },
    params: {},
  };
  const out = H.previewWire(cmd, { ch: "3" });
  results.l091_preview_includes_query_params = {
    pass: out.startsWith("GET /api/status?verbose=1&ch=3"),
    detail: out,
  };
}
{
  // A path that already carries a query string gets & appended, not a
  // second ?; headers and body still render after the request line.
  const cmd = {
    label: "Set",
    send: "",
    method: "POST",
    path: "/api/set?mode=a",
    query_params: { level: "{level}" },
    headers: { "Content-Type": "text/xml" },
    body: "<Level>{level}</Level>",
    params: {},
  };
  const out = H.previewWire(cmd, { level: "7" });
  results.l091_preview_query_appends_to_existing = {
    pass:
      out.split("\n")[0] === "POST /api/set?mode=a&level=7" &&
      out.includes("Content-Type: text/xml") &&
      out.includes("<Level>7</Level>"),
    detail: out,
  };
}
{
  // No query_params -> request line unchanged (no stray "?").
  const cmd = { label: "R", send: "", method: "GET", path: "/s", params: {} };
  const out = H.previewWire(cmd, {});
  results.l091_preview_no_query_params_unchanged = {
    pass: out === "GET /s",
    detail: out,
  };
}

// --- M-154: preview routes by field PRESENCE, mirroring the runtime ------
{
  // configurable.py routes any command with an `address` key to the OSC
  // sender — even an empty one. The preview must agree, not fall through
  // to the HTTP/raw branches like the old truthiness check did.
  const cmd = {
    label: "Q",
    send: "FALLBACK",
    address: "",
    args: [{ type: "s", value: "x" }],
    params: {},
  };
  const out = H.previewWire(cmd, {});
  results.m154_preview_routes_empty_address_as_osc = {
    pass: out === " [s=x]",
    detail: out,
  };
}
{
  // OSC preview substitutes params in address and args.
  const cmd = {
    label: "Fader",
    send: "",
    address: "/ch/{ch}/fader",
    args: [{ type: "f", value: "{level}" }],
    params: {},
  };
  const out = H.previewWire(cmd, { ch: "01", level: "0.5" });
  results.m154_preview_osc_substitution = {
    pass: out === "/ch/01/fader [f=0.5]",
    detail: out,
  };
}

// --- M-154: transport-shape mismatch messages ----------------------------
{
  const oscOnTcp = H.commandShapeMismatch("tcp", {
    label: "X", send: "", address: "/x", args: [], params: {},
  });
  const httpOnSerial = H.commandShapeMismatch("serial", {
    label: "X", send: "", method: "POST", path: "/r", params: {},
  });
  const rawOnOsc = H.commandShapeMismatch("osc", {
    label: "X", send: "PWR1\\r", params: {},
  });
  const rawOnHttp = H.commandShapeMismatch("http", {
    label: "X", send: "PWR1\\r", params: {},
  });
  results.m154_mismatch_detected = {
    pass:
      !!oscOnTcp && /OSC fields/.test(oscOnTcp) && /TCP/.test(oscOnTcp) &&
      !!httpOnSerial && /HTTP fields/.test(httpOnSerial) &&
      !!rawOnOsc && /no OSC address/.test(rawOnOsc) &&
      !!rawOnHttp && /method or path/.test(rawOnHttp),
    detail: { oscOnTcp, httpOnSerial, rawOnOsc, rawOnHttp },
  };
}
{
  // Matched shapes -> null for every transport.
  const tcpOk = H.commandShapeMismatch("tcp", { label: "X", send: "PWR1\\r", params: {} });
  const udpOk = H.commandShapeMismatch("udp", { label: "X", send: "{}", params: {} });
  const oscOk = H.commandShapeMismatch("osc", {
    label: "X", send: "", address: "/x", args: [], params: {},
  });
  const httpOk = H.commandShapeMismatch("http", {
    label: "X", send: "", method: "GET", path: "/s", params: {},
  });
  results.m154_matched_shapes_pass = {
    pass: tcpOk === null && udpOk === null && oscOk === null && httpOk === null,
    detail: { tcpOk, udpOk, oscOk, httpOk },
  };
}

process.stdout.write(JSON.stringify(results));
