"use strict";
// Loads the real Driver Builder validator (validateDriver.ts, bundled on the
// fly with the esbuild already in web/programmer/node_modules) and exercises
// the transport-shape rules: stale wire-format fields after a transport
// switch (commands and device-setting writes), the transport-switch scrub,
// and OSC argument value checks. Prints JSON results to stdout; the Python
// wrapper skips when the Node toolchain or esbuild is absent.
const path = require("path");

const validatorPath = process.argv[2];

const esbuild = require("esbuild");
const built = esbuild.buildSync({
  entryPoints: [validatorPath],
  bundle: true,
  format: "cjs",
  platform: "node",
  write: false,
  logLevel: "silent",
});
const code = built.outputFiles[0].text;
const moduleObj = { exports: {} };
const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
fn(moduleObj.exports, require, moduleObj, validatorPath, path.dirname(validatorPath));
const V = moduleObj.exports;

const results = {};

// Complete draft so publish-quality warnings (description, version, ...)
// don't muddy the assertions.
const baseDraft = (transport, commands, extra = {}) => ({
  id: "acme_x",
  name: "Acme X",
  manufacturer: "Acme",
  category: "other",
  version: "1.0.0",
  author: "T",
  description: "Test driver.",
  transport,
  delimiter: "\\r\\n",
  default_config: {},
  config_schema: {},
  state_variables: {},
  commands,
  responses: [],
  polling: {},
  help: { overview: "Test." },
  ...extra,
});

const validate = (draft) => V.validateDriver(draft, [], null);
const shapeRe = /OSC fields|HTTP fields|no OSC address|empty OSC address|method or path|sender ignores/;
const shapeIssues = (issues) => issues.filter((i) => shapeRe.test(i.message));
const argIssues = (issues) => issues.filter((i) => /OSC argument/.test(i.message));
const errorsOf = (issues) => issues.filter((i) => i.severity === "error");

// --- H-097: stale wire-format fields are flagged, not silently dead ------
{
  // OSC fields left behind on a TCP driver: the runtime routes the command
  // to the OSC sender, which refuses the transport — dead command.
  const issues = validate(
    baseDraft("tcp", {
      power_on: {
        label: "On",
        send: "",
        address: "/main/power",
        args: [{ type: "i", value: "1" }],
        params: {},
      },
    }),
  );
  const hits = shapeIssues(issues);
  results.h097_tcp_with_osc_fields_error = {
    pass:
      hits.length === 1 &&
      hits[0].severity === "error" &&
      hits[0].command === "power_on" &&
      /OSC fields/.test(hits[0].message),
    detail: hits,
  };
}
{
  // HTTP fields on a serial driver — same class, HTTP sender refuses.
  const issues = validate(
    baseDraft("serial", {
      reboot: { label: "Reboot", send: "", method: "POST", path: "/reboot", params: {} },
    }),
  );
  const hits = shapeIssues(issues);
  results.h097_serial_with_http_fields_error = {
    pass: hits.length === 1 && hits[0].severity === "error" && /HTTP fields/.test(hits[0].message),
    detail: hits,
  };
}
{
  // The reverse direction: a raw send-string command left on an OSC driver.
  const issues = validate(
    baseDraft("osc", {
      legacy: { label: "Legacy", send: "PWR ON\\r", params: {} },
    }),
  );
  const hits = errorsOf(shapeIssues(issues));
  results.h097_osc_without_address_error = {
    pass: hits.length === 1 && /no OSC address/.test(hits[0].message),
    detail: hits,
  };
}
{
  // Raw command on an HTTP driver: nothing to send as a request.
  const issues = validate(
    baseDraft("http", {
      legacy: { label: "Legacy", send: "PWR ON\\r", params: {} },
    }),
  );
  const hits = errorsOf(shapeIssues(issues));
  results.h097_http_without_method_path_error = {
    pass: hits.length === 1 && /method or path/.test(hits[0].message),
    detail: hits,
  };
}
{
  // Matching shape with a non-empty leftover send: ignored by the OSC
  // sender -> warning, not error (the command itself works).
  const issues = validate(
    baseDraft("osc", {
      fader: {
        label: "Fader",
        send: "OLD\\r",
        address: "/ch/01/fader",
        args: [{ type: "f", value: "{level}" }],
        params: { level: { type: "number" } },
      },
    }),
  );
  const hits = shapeIssues(issues);
  results.h097_osc_leftover_send_warning = {
    pass: hits.length === 1 && hits[0].severity === "warning" && /sender ignores/.test(hits[0].message),
    detail: hits,
  };
}
{
  // Empty OSC address on an OSC driver (cleared field / hand-edited YAML).
  const issues = validate(
    baseDraft("osc", {
      q: { label: "Q", send: "", address: "   ", args: [], params: {} },
    }),
  );
  const hits = errorsOf(shapeIssues(issues));
  results.h097_osc_empty_address_error = {
    pass: hits.length === 1 && /empty OSC address/.test(hits[0].message),
    detail: hits,
  };
}
{
  // Device-setting writes route exactly like commands at runtime — a stale
  // OSC write on a TCP driver is flagged too.
  const issues = validate(
    baseDraft(
      "tcp",
      {},
      {
        device_settings: {
          volume: {
            label: "Volume",
            type: "number",
            write: { address: "/vol", args: [{ type: "f", value: "{value}" }] },
          },
        },
      },
    ),
  );
  const hits = errorsOf(shapeIssues(issues));
  results.h097_setting_write_mismatch_error = {
    pass: hits.length === 1 && /Device setting "volume"/.test(hits[0].message),
    detail: hits,
  };
}
{
  // Clean drivers stay clean: matching shapes produce no shape issues.
  const tcp = validate(
    baseDraft("tcp", { on: { label: "On", send: "PWR1\\r", params: {} } }),
  );
  const osc = validate(
    baseDraft("osc", {
      f: {
        label: "F",
        send: "",
        address: "/x",
        args: [{ type: "f", value: "0.5" }],
        params: {},
      },
    }),
  );
  const http = validate(
    baseDraft("http", {
      r: { label: "R", send: "", method: "GET", path: "/status", params: {} },
    }),
  );
  results.h097_clean_drivers_no_shape_issues = {
    pass:
      shapeIssues(tcp).length === 0 &&
      shapeIssues(osc).length === 0 &&
      shapeIssues(http).length === 0,
    detail: { tcp: shapeIssues(tcp), osc: shapeIssues(osc), http: shapeIssues(http) },
  };
}

// --- H-097: transport-switch scrub --------------------------------------
{
  // OSC -> TCP: address/args are dropped from the command (they're
  // invisible and uneditable in the TCP form), send stays as a key, and
  // the authored fields are reported for the confirm prompt.
  const draft = baseDraft("osc", {
    fader: {
      label: "Fader",
      send: "",
      address: "/ch/01/fader",
      args: [{ type: "f", value: "{level}" }],
      params: { level: { type: "number" } },
    },
  });
  const r = V.scrubForTransport(draft, "tcp");
  const cmd = r.commands.fader;
  results.h097_scrub_to_tcp_removes_osc_fields = {
    pass:
      !("address" in cmd) &&
      !("args" in cmd) &&
      cmd.send === "" &&
      cmd.label === "Fader" &&
      r.removals.length === 1 &&
      r.removals[0].name === "fader" &&
      r.removals[0].fields.includes("address") &&
      r.removals[0].fields.includes("args"),
    detail: r,
  };
}
{
  // TCP -> OSC: the send string is cleared (key kept — every command seed
  // carries it) and reported; switching among raw transports scrubs nothing.
  const draft = baseDraft("tcp", {
    on: { label: "On", send: "PWR1\\r", params: {} },
  });
  const toOsc = V.scrubForTransport(draft, "osc");
  const toUdp = V.scrubForTransport(draft, "udp");
  results.h097_scrub_to_osc_clears_send = {
    pass:
      toOsc.commands.on.send === "" &&
      toOsc.removals.length === 1 &&
      toOsc.removals[0].fields.includes("send") &&
      toUdp.commands.on.send === "PWR1\\r" &&
      toUdp.removals.length === 0,
    detail: { toOsc, toUdp },
  };
}
{
  // A setting write emptied by the scrub is dropped entirely (read-only is
  // the honest state once its wire format is gone); empty seeds (send: "")
  // are scrubbed silently with no confirm noise.
  const draft = baseDraft(
    "osc",
    {
      seeded: { label: "Seeded", send: "", address: "/x", args: [], params: {} },
    },
    {
      device_settings: {
        volume: {
          label: "Volume",
          type: "number",
          write: { address: "/vol", args: [{ type: "f", value: "{value}" }] },
        },
      },
    },
  );
  const r = V.scrubForTransport(draft, "tcp");
  const removalNames = r.removals.map((x) => x.name);
  results.h097_scrub_setting_write_dropped = {
    pass:
      r.device_settings !== undefined &&
      !("write" in r.device_settings.volume) &&
      removalNames.includes("volume (setting)") &&
      // the seeded command's "/" address IS authored content; args [] is not
      r.removals.find((x) => x.name === "seeded").fields.join(",") === "address",
    detail: r,
  };
}

// --- M-152: OSC argument values checked at author time -------------------
{
  // The builder seeds new args with value "" — firing that command crashes
  // the send at runtime (float("")), so it must be an author-time error.
  const issues = validate(
    baseDraft("osc", {
      f: {
        label: "F",
        send: "",
        address: "/x",
        args: [{ type: "f", value: "" }],
        params: {},
      },
    }),
  );
  const hits = argIssues(issues);
  results.m152_empty_numeric_arg_error = {
    pass: hits.length === 1 && hits[0].severity === "error" && /needs a numeric value/.test(hits[0].message),
    detail: hits,
  };
}
{
  const issues = validate(
    baseDraft("osc", {
      f: {
        label: "F",
        send: "",
        address: "/x",
        args: [{ type: "i", value: "fast" }],
        params: {},
      },
    }),
  );
  const hits = argIssues(issues);
  results.m152_non_numeric_arg_error = {
    pass: hits.length === 1 && /not a number/.test(hits[0].message),
    detail: hits,
  };
}
{
  // Placeholders resolve at send time — not statically checkable, no error.
  // String args may be empty (a valid OSC string). Int64 rejects fractions
  // (the runtime's int(str) does too), and device-setting args are checked.
  const ok = validate(
    baseDraft("osc", {
      f: {
        label: "F",
        send: "",
        address: "/x",
        args: [
          { type: "f", value: "{level}" },
          { type: "s", value: "" },
          { type: "h", value: "42" },
          { type: "d", value: "1e3" },
        ],
        params: { level: { type: "number" } },
      },
    }),
  );
  const frac = validate(
    baseDraft("osc", {
      f: {
        label: "F",
        send: "",
        address: "/x",
        args: [{ type: "h", value: "1.5" }],
        params: {},
      },
    }),
  );
  const setting = validate(
    baseDraft(
      "osc",
      {},
      {
        device_settings: {
          gain: {
            label: "Gain",
            type: "number",
            write: { address: "/gain", args: [{ type: "f", value: "" }] },
          },
        },
      },
    ),
  );
  results.m152_placeholder_string_ok_int64_fraction_error = {
    pass:
      argIssues(ok).length === 0 &&
      argIssues(frac).length === 1 &&
      /whole number/.test(argIssues(frac)[0].message) &&
      argIssues(setting).length === 1 &&
      /Device setting "gain"/.test(argIssues(setting)[0].message),
    detail: { ok: argIssues(ok), frac: argIssues(frac), setting: argIssues(setting) },
  };
}
{
  // Direct helper checks, including the T/F/N no-value tags.
  const o = V.oscArgValueIssue;
  results.m152_helper_matrix = {
    pass:
      o("f", "") !== null &&
      o("f", "0.5") === null &&
      o("i", "3") === null &&
      o("h", "7") === null &&
      o("h", "7.5") !== null &&
      o("d", "abc") !== null &&
      o("s", "") === null &&
      o("T", "") === null &&
      o("F", "") === null &&
      o("N", "") === null &&
      o("f", "{level}") === null,
    detail: {
      f_empty: o("f", ""),
      h_frac: o("h", "7.5"),
      d_abc: o("d", "abc"),
    },
  };
}

// --- Routing precedence mirrors the runtime ------------------------------
{
  // configurable.py checks address first, then path/method, then raw —
  // a command with both address and path routes to OSC.
  results.route_precedence_matches_runtime = {
    pass:
      V.commandRoute({ address: "/x", path: "/y" }) === "osc" &&
      V.commandRoute({ path: "/y" }) === "http" &&
      V.commandRoute({ method: "POST" }) === "http" &&
      V.commandRoute({}) === "raw",
    detail: null,
  };
}

// --- Discovery hints validation (H-121 / H-122 / M-170) ------------------
const discIssues = (issues) => issues.filter((i) => i.section === "discovery");

{
  // H-121: a disallowed open port (8080 et al) is flagged as an error, not
  // silently saved to fail at load.
  const issues = discIssues(validate(baseDraft("tcp", {}, { discovery: { port_open: [8080] } })));
  results.h121_disallowed_port_8080_error = {
    pass: issues.length === 1 && issues[0].severity === "error" &&
      issues[0].field === "port_open" && /8080/.test(issues[0].message),
    detail: issues,
  };
}
{
  // A vendor-specific port is fine.
  const issues = discIssues(validate(baseDraft("tcp", {}, { discovery: { port_open: [4352] } })));
  results.h121_vendor_port_ok = { pass: issues.length === 0, detail: issues };
}
{
  // H-122: a probe declaring two matchers is an error (runtime allows one).
  const issues = discIssues(
    validate(baseDraft("tcp", {}, {
      discovery: { tcp_probe: { port: 1234, expect: "A", expect_hex: "AA55" } },
    })),
  );
  results.h122_probe_two_matchers_error = {
    pass: issues.length === 1 && issues[0].severity === "error" &&
      issues[0].field === "tcp_probe" && /only one matcher/.test(issues[0].message),
    detail: issues,
  };
}
{
  // A probe with exactly one matcher is fine.
  const issues = discIssues(
    validate(baseDraft("tcp", {}, {
      discovery: { tcp_probe: { port: 1234, expect: "NovaStar" } },
    })),
  );
  results.h122_probe_one_matcher_ok = { pass: issues.length === 0, detail: issues };
}
{
  // M-170: a blank string row (OUI here) is flagged with a field anchor.
  const issues = discIssues(validate(baseDraft("tcp", {}, { discovery: { oui: ["00:11:22", ""] } })));
  results.m170_blank_oui_error = {
    pass: issues.length === 1 && issues[0].severity === "error" &&
      issues[0].field === "oui" && /blank/.test(issues[0].message),
    detail: issues,
  };
}
{
  // M-170: a blank mDNS fingerprint (object form, empty service) is flagged.
  const issues = discIssues(
    validate(baseDraft("tcp", {}, { discovery: { mdns: [{ service: "" }] } })),
  );
  results.m170_blank_mdns_service_error = {
    pass: issues.length === 1 && issues[0].field === "mdns" && /blank/.test(issues[0].message),
    detail: issues,
  };
}
{
  // Fully-populated discovery hints produce no discovery issues.
  const issues = discIssues(
    validate(baseDraft("tcp", {}, {
      discovery: {
        oui: ["00:11:22"],
        hostname: ["acme-*"],
        mdns: [{ service: "_acme._tcp.local." }],
        tcp_probe: { port: 4352, expect: "ACME" },
        port_open: [4352],
      },
    })),
  );
  results.discovery_clean_no_issues = { pass: issues.length === 0, detail: issues };
}

// --- Frame parser validation (H-123 / L-102) -----------------------------
// Mirrors server/drivers/driver_loader.py: header_size must be 1/2/4 and a
// fixed length must be a positive integer, else the parser raises at connect.
const fpIssues = (issues) =>
  issues.filter((i) => i.field && i.field.startsWith("frame_parser"));

{
  // H-123: a length-prefix header_size the runtime rejects (only 1/2/4) — an
  // imported/hand-edited driver could carry 3, which raises at connect.
  const issues = fpIssues(
    validate(baseDraft("tcp", {}, { frame_parser: { type: "length_prefix", header_size: 3 } })),
  );
  results.h123_header_size_3_error = {
    pass:
      issues.length === 1 &&
      issues[0].severity === "error" &&
      issues[0].section === "connection" &&
      issues[0].field === "frame_parser.header_size" &&
      /1, 2, or 4/.test(issues[0].message),
    detail: issues,
  };
}
{
  // A supported header_size (4) is clean.
  const issues = fpIssues(
    validate(baseDraft("tcp", {}, { frame_parser: { type: "length_prefix", header_size: 4, header_offset: -4 } })),
  );
  results.h123_header_size_4_negoffset_ok = { pass: issues.length === 0, detail: issues };
}
{
  // L-102: a non-positive fixed length saves silently in older builders and
  // raises ValueError at connect — flag it as a Connection error.
  const issues = fpIssues(
    validate(baseDraft("tcp", {}, { frame_parser: { type: "fixed_length", length: -5 } })),
  );
  results.l102_fixed_negative_length_error = {
    pass:
      issues.length === 1 &&
      issues[0].severity === "error" &&
      issues[0].field === "frame_parser.length" &&
      /positive whole number/.test(issues[0].message),
    detail: issues,
  };
}
{
  // A positive fixed length is clean.
  const issues = fpIssues(
    validate(baseDraft("tcp", {}, { frame_parser: { type: "fixed_length", length: 8 } })),
  );
  results.l102_fixed_length_ok = { pass: issues.length === 0, detail: issues };
}
{
  // An unknown frame_parser type is rejected (matches the loader's message).
  const issues = fpIssues(
    validate(baseDraft("tcp", {}, { frame_parser: { type: "sliding_window" } })),
  );
  results.frame_parser_unknown_type_error = {
    pass:
      issues.length === 1 &&
      issues[0].field === "frame_parser.type" &&
      /isn't supported/.test(issues[0].message),
    detail: issues,
  };
}
{
  // No frame_parser block (the common case) produces no frame_parser issues.
  const issues = fpIssues(validate(baseDraft("tcp", {})));
  results.frame_parser_absent_no_issues = { pass: issues.length === 0, detail: issues };
}

process.stdout.write(JSON.stringify(results));
