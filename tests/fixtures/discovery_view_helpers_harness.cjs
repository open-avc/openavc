"use strict";
// Loads the Discovery view helpers (discoveryViewHelpers.ts — React-free
// pure logic) bundled on the fly with the esbuild already in
// openavc/web/programmer/node_modules, and checks the port-label merge precedence
// and the SNMP-community payload rule.
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

const results = {};

// --- Port labels: driver/catalog-supplied labels must win over the generic
// fallbacks (the backend derives them from driver hints + the community
// catalog; a vendor label for 5900/9090 must not be shadowed by "VNC" /
// "HTTP alt").
{
  const merged = H.mergePortLabels({ 5900: "Acme Cam Control", 9090: "Acme Widget API" });
  results.port_label_dynamic_wins = {
    pass: merged[5900] === "Acme Cam Control" && merged[9090] === "Acme Widget API",
    detail: { 5900: merged[5900], 9090: merged[9090] },
  };
}
{
  const merged = H.mergePortLabels({ 4998: "Acme Bridge" });
  results.port_label_fallbacks_kept = {
    pass: merged[23] === "Telnet" && merged[443] === "HTTPS" && merged[4998] === "Acme Bridge",
    detail: merged,
  };
}

// --- SNMP community payload: blank input means "keep the stored value", so
// the field is omitted; a typed value is sent verbatim.
{
  results.snmp_blank_omitted = {
    pass: H.snmpCommunityField("") === undefined && H.snmpCommunityField("   ") === undefined,
    detail: { empty: H.snmpCommunityField(""), spaces: H.snmpCommunityField("   ") },
  };
}
{
  results.snmp_value_sent_verbatim = {
    pass: H.snmpCommunityField("s3cret") === "s3cret",
    detail: H.snmpCommunityField("s3cret"),
  };
}

process.stdout.write(JSON.stringify(results));
