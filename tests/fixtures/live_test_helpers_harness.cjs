"use strict";
// Loads the real Live Test panel helpers (liveTestHelpers.ts, bundled on the
// fly with the esbuild already in openavc/web/programmer/node_modules) and checks the
// transport-shape mismatch messages. Prints JSON results to stdout; the
// Python wrapper skips when the Node toolchain or esbuild is absent.
//
// There is no wire-preview check here any more, because the panel no longer
// builds the wire. It asks the server for a dry run, which builds the command
// with the real driver runtime; the tests for that live beside the runtime,
// in test_driver_command_dry_run.py and its corpus sweep.
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

// The Builder holds no protocol interpretation. Routing a command by its
// declared fields is fine; exporting something that builds a wire out of
// templates is what must not come back.
results.no_wire_builder_exported = {
  pass: typeof H.previewWire === "undefined",
  detail: Object.keys(H),
};

process.stdout.write(JSON.stringify(results));
