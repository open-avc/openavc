"use strict";
// Loads the real Driver Builder validator (validateDriver.ts, bundled on the
// fly with the esbuild already in web/programmer/node_modules) and exercises
// the transport-shape rules: stale wire-format fields after a transport
// switch (commands and device-setting writes), the transport-switch scrub,
// OSC argument value checks, and the declared command semantics rules
// (sets / query_for). Prints JSON results to stdout; the Python wrapper
// skips when the Node toolchain or esbuild is absent.
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

// --- State variable labels / types (M-172) -------------------------------
// driver_loader.py hard-requires a label on every top-level state variable
// and rejects an unknown type; the builder's free-text inputs let either go
// bad, surfacing only as an unanchored save-time 422.
const stateVarIssues = (issues) =>
  issues.filter((i) => i.field && i.field.startsWith("state_variables"));

{
  const issues = stateVarIssues(
    validate(baseDraft("tcp", {}, { state_variables: { volume: { type: "number", label: "" } } })),
  );
  results.m172_state_var_no_label_error = {
    pass:
      issues.length === 1 &&
      issues[0].severity === "error" &&
      issues[0].section === "behavior" &&
      issues[0].field === "state_variables.volume" &&
      /needs a label/.test(issues[0].message),
    detail: issues,
  };
}
{
  const issues = stateVarIssues(
    validate(baseDraft("tcp", {}, { state_variables: { volume: { type: "number", label: "Volume" } } })),
  );
  results.m172_state_var_with_label_ok = { pass: issues.length === 0, detail: issues };
}
{
  const issues = stateVarIssues(
    validate(baseDraft("tcp", {}, { state_variables: { mode: { type: "widget", label: "Mode" } } })),
  );
  results.m172_state_var_unknown_type_error = {
    pass: issues.length === 1 && /unknown type/.test(issues[0].message),
    detail: issues,
  };
}

// --- Command wire format + response structure (M-173 / H-124) ------------
// driver_loader.py rejects a command with no send/path-method/address and a
// response that is neither a valid OSC address (leading '/') nor a text
// pattern. The raw-transport blank-send case slips past the shape check.
const wireIssues = (issues) => issues.filter((i) => /nothing to send/.test(i.message));
const responseIssues = (issues) => issues.filter((i) => /^Response \d/.test(i.message));

{
  // A tcp command whose send was left blank (the builder's seed) does nothing
  // and is rejected at load — flag it, anchored to the command.
  const hits = wireIssues(
    validate(baseDraft("tcp", { ping: { label: "Ping", send: "", params: {} } })),
  );
  results.m173_command_no_wire_format_error = {
    pass: hits.length === 1 && hits[0].severity === "error" && hits[0].command === "ping",
    detail: hits,
  };
}
{
  // A tcp command with a real send string is clean.
  const hits = wireIssues(
    validate(baseDraft("tcp", { ping: { label: "Ping", send: "PING\\r", params: {} } })),
  );
  results.m173_command_with_send_ok = { pass: hits.length === 0, detail: hits };
}
{
  // A response with neither address nor pattern/match has nothing to match.
  const issues = responseIssues(
    validate(baseDraft("tcp", {}, { responses: [{ mappings: [] }] })),
  );
  results.m173_response_no_pattern_error = {
    pass: issues.length === 1 && issues[0].severity === "error" && /no pattern to match/.test(issues[0].message),
    detail: issues,
  };
}
{
  // OSC response address missing the leading '/' (runtime hard rule).
  const issues = responseIssues(
    validate(baseDraft("osc", {}, { responses: [{ address: "main/vol" }] })),
  );
  results.m173_response_osc_address_no_slash_error = {
    pass: issues.length === 1 && /must start with/.test(issues[0].message),
    detail: issues,
  };
}
{
  // H-124: an OSC-address response left on a non-OSC transport never matches.
  const issues = responseIssues(
    validate(baseDraft("tcp", {}, { responses: [{ address: "/main/vol" }] })),
  );
  results.h124_response_osc_address_on_tcp_error = {
    pass: issues.length === 1 && /transport is TCP/.test(issues[0].message),
    detail: issues,
  };
}
{
  // A normal text response with a pattern is clean.
  const issues = responseIssues(
    validate(baseDraft("tcp", {}, {
      responses: [{ pattern: "PWR=(\\d)", mappings: [{ group: 1, state: "power" }] }],
    })),
  );
  results.m173_response_with_pattern_ok = { pass: issues.length === 0, detail: issues };
}

// --- Device settings: the runtime requires a write block ------------------
// driver_loader.py hard-errors on a device setting without a write block
// ("a device setting must be writable") — a draft missing one (or with a
// write emptied by the transport scrub) must be flagged before save.
const missingWriteIssues = (issues) =>
  issues.filter((i) => /missing write block/.test(i.message));
{
  const issues = missingWriteIssues(
    validate(
      baseDraft("tcp", {}, {
        device_settings: { volume: { label: "Volume", type: "number" } },
      }),
    ),
  );
  results.setting_missing_write_error = {
    pass:
      issues.length === 1 &&
      issues[0].severity === "error" &&
      /Device setting "volume"/.test(issues[0].message),
    detail: issues,
  };
}
{
  // An emptied write object is the same as none — no wire format left.
  const issues = missingWriteIssues(
    validate(
      baseDraft("tcp", {}, {
        device_settings: {
          volume: { label: "Volume", type: "number", write: {} },
        },
      }),
    ),
  );
  results.setting_empty_write_error = {
    pass: issues.length === 1 && issues[0].severity === "error",
    detail: issues,
  };
}
{
  // A setting with a real write block raises no missing-write error.
  const issues = missingWriteIssues(
    validate(
      baseDraft("tcp", {}, {
        device_settings: {
          volume: {
            label: "Volume",
            type: "number",
            write: { send: "VOL {value}\\r" },
          },
        },
      }),
    ),
  );
  results.setting_with_write_ok = { pass: issues.length === 0, detail: issues };
}

// --- Config fields: secret defaults are errors, wrong-typed defaults warn ---
const configIssues = (issues) =>
  issues.filter((i) => /^Config field/.test(i.message));
{
  // A secret field carrying a default exports the credential in plain text
  // inside the shareable .avcdriver — the import/hand-edit path the Config
  // editor itself can't produce. Must be a save-blocking error.
  const issues = configIssues(
    validate(baseDraft("tcp", {}, {
      config_schema: { pin: { type: "string", label: "PIN", secret: true } },
      default_config: { pin: "hunter2" },
    })),
  );
  results.secret_field_default_error = {
    pass:
      issues.length === 1 &&
      issues[0].severity === "error" &&
      issues[0].section === "connection" &&
      /secret/.test(issues[0].message),
    detail: issues,
  };
}
{
  // The schema entry's own `default` is exported too — same error.
  const issues = configIssues(
    validate(baseDraft("tcp", {}, {
      config_schema: {
        pin: { type: "string", label: "PIN", secret: true, default: "hunter2" },
      },
    })),
  );
  results.secret_schema_default_error = {
    pass: issues.length === 1 && issues[0].severity === "error",
    detail: issues,
  };
}
{
  // A secret field with no default (the legacy empty-string seed included)
  // is clean.
  const issues = configIssues(
    validate(baseDraft("tcp", {}, {
      config_schema: { pin: { type: "string", label: "PIN", secret: true } },
      default_config: { pin: "" },
    })),
  );
  results.secret_field_no_default_ok = { pass: issues.length === 0, detail: issues };
}
{
  // A string default on an integer field (a legacy Builder draft, or a
  // hand-edited file) exports wrong-typed YAML — flag it, but only as a
  // warning since the runtime re-coerces most paths.
  const issues = configIssues(
    validate(baseDraft("tcp", {}, {
      config_schema: { display_id: { type: "integer", label: "Display ID" } },
      default_config: { display_id: "5" },
    })),
  );
  results.config_default_type_mismatch_warning = {
    pass:
      issues.length === 1 &&
      issues[0].severity === "warning" &&
      /integer/.test(issues[0].message),
    detail: issues,
  };
}
{
  // A string default on a boolean field is the worst shape ("false" is
  // truthy) — flagged the same way.
  const issues = configIssues(
    validate(baseDraft("tcp", {}, {
      config_schema: { enabled: { type: "boolean", label: "Enabled" } },
      default_config: { enabled: "false" },
    })),
  );
  results.config_boolean_string_default_warning = {
    pass: issues.length === 1 && issues[0].severity === "warning",
    detail: issues,
  };
}
{
  // Correctly typed defaults (including float, the runtime's number alias)
  // are clean.
  const issues = configIssues(
    validate(baseDraft("tcp", {}, {
      config_schema: {
        display_id: { type: "integer", label: "Display ID" },
        gain: { type: "float", label: "Gain" },
        enabled: { type: "boolean", label: "Enabled" },
        zone: { type: "string", label: "Zone" },
      },
      default_config: { display_id: 5, gain: 0.5, enabled: false, zone: "A" },
    })),
  );
  results.config_typed_defaults_ok = { pass: issues.length === 0, detail: issues };
}

// --- Declared command semantics: sets / query_for -------------------------
// Mirror avcdriver_semantic.py's per-command checks: every `sets` key and the
// `query_for` name must be a declared state variable (device-level, or of the
// addressed child type when the command has exactly ONE child_id param), a
// "{param}" value must name a declared parameter, and `sets` must be a
// mapping. A dangling name silently declares nothing to the auto-generated
// simulator, so all of these are save-blocking errors.
const semIssues = (issues) => issues.filter((i) => /\bsets\b|\bquery_for\b/.test(i.message));
const semVars = {
  power: { type: "boolean", label: "Power" },
  volume: { type: "integer", label: "Volume" },
};
const zoneChild = {
  zone: {
    label: "Zone",
    id_format: { type: "integer", min: 1, max: 4 },
    state_variables: { mute: { type: "boolean", label: "Mute" } },
  },
};

{
  // Device-level: literal + {param} sets and a query_for naming declared
  // variables are clean.
  const issues = semIssues(
    validate(baseDraft("tcp", {
      set_volume: {
        label: "Set Volume",
        send: "VOL {level}\\r",
        params: { level: { type: "integer" } },
        sets: { volume: "{level}", power: true },
      },
      get_power: { label: "Get Power", send: "PWR?\\r", params: {}, query_for: "power" },
    }, { state_variables: semVars })),
  );
  results.sem_device_sets_query_for_ok = { pass: issues.length === 0, detail: issues };
}
{
  // Child variant: a command with exactly one child_id param may name the
  // child type's own variables — both sets and query_for.
  const issues = semIssues(
    validate(baseDraft("tcp", {
      mute_zone: {
        label: "Mute Zone",
        send: "MUTE {zone} {state}\\r",
        params: {
          zone: { type: "child_id", child_type: "zone" },
          state: { type: "boolean" },
        },
        sets: { mute: "{state}" },
      },
      query_zone_mute: {
        label: "Query Zone Mute",
        send: "MUTE? {zone}\\r",
        params: { zone: { type: "child_id", child_type: "zone" } },
        query_for: "mute",
      },
    }, { state_variables: semVars, child_entity_types: zoneChild })),
  );
  results.sem_child_sets_query_for_ok = { pass: issues.length === 0, detail: issues };
}
{
  // A sets key that names no declared state variable is an error, anchored
  // to the command.
  const issues = semIssues(
    validate(baseDraft("tcp", {
      set_bright: {
        label: "Brightness",
        send: "BRT 1\\r",
        params: {},
        sets: { brightness: 100 },
      },
    }, { state_variables: semVars })),
  );
  results.sem_sets_unknown_var_error = {
    pass:
      issues.length === 1 &&
      issues[0].severity === "error" &&
      issues[0].command === "set_bright" &&
      /sets "brightness" which is not a declared state variable/.test(issues[0].message),
    detail: issues,
  };
}
{
  // A "{param}" value must reference a declared parameter of the command.
  const issues = semIssues(
    validate(baseDraft("tcp", {
      set_power: {
        label: "Set Power",
        send: "PWR {state}\\r",
        params: { state: { type: "boolean" } },
        sets: { power: "{level}" },
      },
    }, { state_variables: semVars })),
  );
  results.sem_sets_unknown_param_ref_error = {
    pass:
      issues.length === 1 &&
      issues[0].severity === "error" &&
      /must be a literal or a bare \{param\} reference to a declared parameter/.test(issues[0].message),
    detail: issues,
  };
}
{
  // The child variant needs EXACTLY one child_id param — with two, child
  // variables are out of scope and the message doesn't offer them.
  const issues = semIssues(
    validate(baseDraft("tcp", {
      route: {
        label: "Route",
        send: "ROUTE {src} {dst}\\r",
        params: {
          src: { type: "child_id", child_type: "zone" },
          dst: { type: "child_id", child_type: "zone" },
        },
        sets: { mute: true },
      },
    }, { state_variables: semVars, child_entity_types: zoneChild })),
  );
  results.sem_sets_two_child_id_params_error = {
    pass:
      issues.length === 1 &&
      issues[0].severity === "error" &&
      /sets "mute" which is not a declared state variable\.$/.test(issues[0].message) &&
      !/addressed child/.test(issues[0].message),
    detail: issues,
  };
}
{
  // query_for naming an unknown variable is an error.
  const issues = semIssues(
    validate(baseDraft("tcp", {
      get_watts: {
        label: "Get Watts",
        send: "WATT?\\r",
        params: {},
        query_for: "wattage",
      },
    }, { state_variables: semVars })),
  );
  results.sem_query_for_unknown_var_error = {
    pass:
      issues.length === 1 &&
      issues[0].severity === "error" &&
      issues[0].command === "get_watts" &&
      /query_for "wattage" is not a declared state variable/.test(issues[0].message),
    detail: issues,
  };
}
{
  // sets must be a mapping (imported / hand-edited YAML could carry a list).
  const issues = semIssues(
    validate(baseDraft("tcp", {
      set_power: {
        label: "Set Power",
        send: "PWR1\\r",
        params: {},
        sets: ["power"],
      },
    }, { state_variables: semVars })),
  );
  results.sem_sets_not_mapping_error = {
    pass:
      issues.length === 1 &&
      issues[0].severity === "error" &&
      /"sets" must be a mapping/.test(issues[0].message),
    detail: issues,
  };
}

// --- JSON body response rules (mirror avcdriver_semantic.py + runtime) ----
// A json: true rule parses the whole reply body as JSON; set values are JSON
// field paths (string) or {key, type, map} specs. `require:` scopes the rule
// to bodies carrying the named key(s) and is json-only.
const jsonVars = {
  power: { type: "boolean", label: "Power" },
  volume: { type: "integer", label: "Volume" },
};

{
  // String-path set form (+ require as a single string) is clean.
  const issues = responseIssues(
    validate(baseDraft("http", {}, {
      state_variables: jsonVars,
      responses: [
        { json: true, set: { power: "status.power" }, require: "status" },
      ],
    })),
  );
  results.json_set_string_path_ok = { pass: issues.length === 0, detail: issues };
}
{
  // {key, type, map} object form (+ require as a list) is clean; throttle
  // stays available on json rules.
  const issues = responseIssues(
    validate(baseDraft("http", {}, {
      state_variables: jsonVars,
      responses: [
        {
          json: true,
          throttle: 0.5,
          require: ["status", "serialNumber"],
          set: {
            power: { key: "status.power", type: "string", map: { "1": "on" } },
            volume: { path: "status.vol" },
          },
        },
      ],
    })),
  );
  results.json_set_object_form_ok = { pass: issues.length === 0, detail: issues };
}
{
  // Explicit mappings-list form ({state, key, type}) is clean too.
  const issues = responseIssues(
    validate(baseDraft("tcp", {}, {
      state_variables: jsonVars,
      responses: [
        { json: true, mappings: [{ state: "volume", key: "vol", type: "integer" }] },
      ],
    })),
  );
  results.json_mappings_form_ok = { pass: issues.length === 0, detail: issues };
}
{
  // require on a non-json (regex) rule is rejected — it only scopes
  // JSON body rules.
  const issues = responseIssues(
    validate(baseDraft("tcp", {}, {
      state_variables: jsonVars,
      responses: [
        { match: "VOL=(\\d+)", set: { volume: "$1" }, require: "status" },
      ],
    })),
  );
  results.json_require_without_json_error = {
    pass:
      issues.length === 1 &&
      issues[0].severity === "error" &&
      /only applies to JSON body rules/.test(issues[0].message),
    detail: issues,
  };
}
{
  // Blank require entries would silently disable the rule: a blank string
  // and a list with a blank entry are both rejected.
  const blankStr = responseIssues(
    validate(baseDraft("http", {}, {
      state_variables: jsonVars,
      responses: [{ json: true, set: { power: "p" }, require: "   " }],
    })),
  );
  const blankList = responseIssues(
    validate(baseDraft("http", {}, {
      state_variables: jsonVars,
      responses: [{ json: true, set: { power: "p" }, require: ["status", ""] }],
    })),
  );
  results.json_require_blank_error = {
    pass:
      blankStr.length === 1 &&
      /must name a JSON key/.test(blankStr[0].message) &&
      blankList.length === 1 &&
      /non-empty JSON key names/.test(blankList[0].message),
    detail: { blankStr, blankList },
  };
}
{
  // A non-string, non-list require (hand-edited YAML) is rejected.
  const issues = responseIssues(
    validate(baseDraft("http", {}, {
      state_variables: jsonVars,
      responses: [{ json: true, set: { power: "p" }, require: 5 }],
    })),
  );
  results.json_require_bad_type_error = {
    pass:
      issues.length === 1 &&
      /JSON key name or a list of them/.test(issues[0].message),
    detail: issues,
  };
}
{
  // A row targeting an undeclared state variable silently does nothing.
  const issues = responseIssues(
    validate(baseDraft("http", {}, {
      state_variables: jsonVars,
      responses: [{ json: true, set: { brightness: "status.bright" } }],
    })),
  );
  results.json_unknown_state_var_error = {
    pass:
      issues.length === 1 &&
      issues[0].severity === "error" &&
      /"brightness", which isn't a declared state variable/.test(issues[0].message),
    detail: issues,
  };
}
{
  // An unknown coercion type falls back to plain string at runtime —
  // flag it instead of silently ignoring the author's choice.
  const issues = responseIssues(
    validate(baseDraft("http", {}, {
      state_variables: jsonVars,
      responses: [
        { json: true, set: { power: { key: "status.power", type: "widget" } } },
      ],
    })),
  );
  results.json_unknown_row_type_error = {
    pass: issues.length === 1 && /unknown type "widget"/.test(issues[0].message),
    detail: issues,
  };
}
{
  // child_set is rejected on json rules (no capture groups to route by).
  const issues = responseIssues(
    validate(baseDraft("http", {}, {
      state_variables: jsonVars,
      responses: [
        {
          json: true,
          set: { power: "status.power" },
          child_set: [{ type: "zone", id: "$1", state: { mute: "$2" } }],
        },
      ],
    })),
  );
  results.json_child_set_error = {
    pass:
      issues.length === 1 &&
      /child entity routing isn't supported on JSON responses/.test(issues[0].message),
    detail: issues,
  };
}
{
  // A json rule with no set/mappings — and the empty-set seed — map no
  // fields, so they do nothing at runtime.
  const noFields = responseIssues(
    validate(baseDraft("http", {}, {
      state_variables: jsonVars,
      responses: [{ json: true }],
    })),
  );
  const emptySet = responseIssues(
    validate(baseDraft("http", {}, {
      state_variables: jsonVars,
      responses: [{ json: true, set: {} }],
    })),
  );
  results.json_no_fields_error = {
    pass:
      noFields.length === 1 &&
      /maps no fields/.test(noFields[0].message) &&
      emptySet.length === 1 &&
      /maps no fields/.test(emptySet[0].message),
    detail: { noFields, emptySet },
  };
}
{
  // A blank field path makes the row silently do nothing.
  const issues = responseIssues(
    validate(baseDraft("http", {}, {
      state_variables: jsonVars,
      responses: [{ json: true, set: { power: "" } }],
    })),
  );
  results.json_empty_path_error = {
    pass: issues.length === 1 && /reads no JSON field/.test(issues[0].message),
    detail: issues,
  };
}
{
  // The runtime dispatches OSC by address before json rules are consulted,
  // so a json rule on an OSC driver never fires.
  const issues = responseIssues(
    validate(baseDraft("osc", {}, {
      state_variables: jsonVars,
      responses: [{ json: true, set: { power: "status.power" } }],
    })),
  );
  results.json_on_osc_transport_error = {
    pass:
      issues.length === 1 &&
      /transport is OSC/.test(issues[0].message),
    detail: issues,
  };
}

process.stdout.write(JSON.stringify(results));
