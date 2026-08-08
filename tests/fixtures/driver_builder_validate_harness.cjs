"use strict";
// Loads the real Driver Builder validator (validateDriver.ts, bundled on the
// fly with the esbuild already in openavc/web/programmer/node_modules) and exercises
// what is left in it now that the driver contract is enforced in exactly one
// place, on the platform:
//
//   * editor mechanics that carry no contract knowledge — which sender the
//     runtime routes a command to, what a transport switch has to strip,
//     whether an OSC argument box holds a number;
//   * `issuesFromServer`, the path -> tab mapping that decides where each of
//     the platform's issues is rendered;
//   * the duplicate-id check, which the endpoint cannot make (it validates
//     one definition, with no view of the library);
//   * housekeeping advice that never blocks a save.
//
// The rules themselves are covered by tests/test_driver_definition_validate.py
// (verdict and message parity with the save path over the whole rejection
// corpus) and tests/test_driver_builder_reverse_corpus.py (every shipped
// driver opens clean). Prints JSON results to stdout; the Python wrapper skips
// when the Node toolchain or esbuild is absent.
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
const strayIssues = (issues) => issues.filter((i) => /sender ignores/.test(i.message));
const configIssues = (issues) => issues.filter((i) => /^Config field/.test(i.message));
const bridgeIssues = (issues) => issues.filter((i) => i.field === "bridge");
const derivedIssues = (issues) =>
  issues.filter((i) => String(i.field || "").startsWith("config_derived"));
const irIssues = (issues) => issues.filter((i) => i.field === "ir_codes");
const errorsOf = (issues) => issues.filter((i) => i.severity === "error");

// --- The platform's issues land on the right tab -------------------------
// The only mapping the editor performs. `path` is what the validation walk
// stamped; the first segment picks the tab. Two of these are not guessable
// from the field name: "Connect Sequence" and command framing live on the
// Connection tab, not Behavior.
{
  const asked = {
    "": "general",
    id: "general",
    ir_codes: "general",
    transport: "connection",
    command_prefix: "connection",
    on_connect: "connection",
    auth: "connection",
    liveness: "connection",
    push: "connection",
    frame_parser: "connection",
    send_frame: "connection",
    "config_schema.host": "connection",
    config_derived: "connection",
    bridge: "connection",
    commands: "behavior",
    "commands.power_on": "behavior",
    "state_variables.volume": "behavior",
    "child_entity_types.zone": "behavior",
    "responses[2]": "behavior",
    "actions[0]": "behavior",
    "polling.queries[1]": "behavior",
    "device_settings.brightness": "behavior",
    web_ui: "behavior",
    discovery: "discovery",
    simulator: "simulation",
  };
  const wrong = {};
  for (const [p, want] of Object.entries(asked)) {
    const got = V.issuesFromServer([
      { severity: "error", message: "x", path: p },
    ])[0].section;
    if (got !== want) wrong[p] = { want, got };
  }
  results.server_issue_paths_land_on_the_right_tab = {
    pass: Object.keys(wrong).length === 0,
    detail: wrong,
  };
}
{
  // Severity and message pass through untouched — the platform's wording is
  // the contract, and rewording it here would be a second contract again.
  // An unrecognised root (a field added to the contract before this table
  // learns about it) lands on General rather than vanishing.
  const out = V.issuesFromServer([
    { severity: "warning", message: "Careful.", path: "config_schema.password" },
    { severity: "error", message: "Nope.", path: "brand_new_block.thing" },
  ]);
  results.server_issues_keep_their_severity_and_wording = {
    pass:
      out.length === 2 &&
      out[0].severity === "warning" &&
      out[0].message === "Careful." &&
      out[0].section === "connection" &&
      out[0].field === "config_schema.password" &&
      out[1].severity === "error" &&
      out[1].message === "Nope." &&
      out[1].section === "general",
    detail: out,
  };
}

// --- The Builder raises no contract errors of its own --------------------
{
  // The whole point of the move. A draft the platform refuses for a dozen
  // reasons (no id, no name, a command with nothing to send, an unknown
  // state-variable type, a device setting with no write block, an
  // uncompilable response pattern) raises NO error here — those all come
  // back from the endpoint now. A contract rule creeping back into
  // TypeScript fails this.
  const broken = {
    transport: "tcp",
    commands: { dead: { label: "Dead" } },
    state_variables: { volume: { type: "zzz" } },
    device_settings: { brightness: { type: "integer", label: "Brightness" } },
    responses: [{ match: "(" }],
    child_entity_types: { zone: { id_format: { type: "roman" } } },
  };
  const errors = errorsOf(V.validateDriverSafely(broken, [], null));
  results.builder_raises_no_contract_errors = {
    pass: errors.length === 0,
    detail: errors,
  };
}
{
  // The one error the editor still raises, and the reason it must: the
  // endpoint validates a single definition and cannot see the library, so
  // nothing else can tell you the id is taken before the save 409s.
  const draft = baseDraft("tcp", {});
  const siblings = [{ id: "acme_x", name: "Other" }];
  const clash = errorsOf(V.validateDriver(draft, siblings, null));
  const editingInPlace = errorsOf(V.validateDriver(draft, siblings, "acme_x"));
  results.duplicate_id_is_the_editors_own_check = {
    pass:
      clash.length === 1 &&
      /already exists/.test(clash[0].message) &&
      clash[0].section === "general" &&
      editingInPlace.length === 0,
    detail: { clash, editingInPlace },
  };
}

// --- Editor mechanics: routing, scrub, OSC argument values ---------------
{
  // Routing precedence mirrors the runtime: configurable.py checks address
  // first, then path/method, then raw — a command with both address and
  // path routes to OSC.
  results.route_precedence_matches_runtime = {
    pass:
      V.commandRoute({ address: "/x", path: "/y" }) === "osc" &&
      V.commandRoute({ path: "/y" }) === "http" &&
      V.commandRoute({ method: "POST" }) === "http" &&
      V.commandRoute({}) === "raw",
    detail: null,
  };
}
{
  // A leftover send on a command the OSC sender handles: the command works,
  // the field is dead weight -> advice, never a refusal.
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
  const hits = strayIssues(issues);
  results.osc_leftover_send_warning = {
    pass:
      hits.length === 1 &&
      hits[0].severity === "warning" &&
      /sender ignores/.test(hits[0].message),
    detail: hits,
  };
}
{
  // Clean drivers stay clean: matching shapes produce no stray-field advice.
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
  results.clean_drivers_no_stray_field_issues = {
    pass:
      strayIssues(tcp).length === 0 &&
      strayIssues(osc).length === 0 &&
      strayIssues(http).length === 0,
    detail: { tcp: strayIssues(tcp), osc: strayIssues(osc), http: strayIssues(http) },
  };
}
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
  results.scrub_to_tcp_removes_osc_fields = {
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
  results.scrub_to_osc_clears_send = {
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
  results.scrub_setting_write_dropped = {
    pass:
      r.device_settings !== undefined &&
      !("write" in r.device_settings.volume) &&
      removalNames.includes("volume (setting)") &&
      // the seeded command's "/" address IS authored content; args [] is not
      r.removals.find((x) => x.name === "seeded").fields.join(",") === "address",
    detail: r,
  };
}
{
  // Whitespace IS a wire value. Some devices document a bare line feed as
  // their keepalive, and the platform accepts it (a plain truthiness test on
  // `send`). The scrub is the surviving half of that fix: switching such a
  // driver to OSC must report the keepalive as authored content it is about
  // to throw away, rather than dropping it silently as if the field were
  // empty.
  const draft = baseDraft("tcp", {
    heart_beat: { label: "Keepalive", send: "\n", params: {} },
    seeded: { label: "Seeded", send: "", params: {} },
  });
  const r = V.scrubForTransport(draft, "osc");
  results.whitespace_send_is_authored_content = {
    pass:
      r.removals.length === 1 &&
      r.removals[0].name === "heart_beat" &&
      r.removals[0].fields.join(",") === "send",
    detail: r,
  };
}
{
  // Direct helper checks, including the T/F/N no-value tags. The OSC
  // argument editor shows these inline as you type.
  const o = V.oscArgValueIssue;
  results.osc_arg_helper_matrix = {
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

// --- Housekeeping advice (warnings only — none of it blocks a save) ------
{
  // A string default on an integer field (a legacy Builder draft, or a
  // hand-edited file) exports wrong-typed YAML.
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
  // are clean — and a masked field with a default says nothing HERE: that
  // advice comes from the platform now, and saying it twice was exactly the
  // duplication this move removes.
  const issues = configIssues(
    validate(baseDraft("tcp", {}, {
      config_schema: {
        display_id: { type: "integer", label: "Display ID" },
        gain: { type: "float", label: "Gain" },
        enabled: { type: "boolean", label: "Enabled" },
        zone: { type: "string", label: "Zone" },
        password: { type: "string", label: "Password", secret: true, default: "admin" },
      },
      default_config: { display_id: 5, gain: 0.5, enabled: false, zone: "A" },
    })),
  );
  results.config_typed_defaults_ok = { pass: issues.length === 0, detail: issues };
}
{
  // Two bridge ports sharing an id: the runtime's id-keyed dict silently
  // keeps the last one.
  const issues = validate(
    baseDraft("tcp", {}, {
      bridge: {
        ports: [
          { id: "ir:1", kind: "ir" },
          { id: "ir:1", kind: "ir" },
        ],
      },
    }),
  );
  const hits = bridgeIssues(issues);
  results.bridge_duplicate_port_id_warning = {
    pass:
      hits.length === 1 &&
      hits[0].severity === "warning" &&
      /duplicates another port/.test(hits[0].message),
    detail: hits,
  };
}
{
  // A well-formed bridge block produces no bridge issues.
  const issues = validate(
    baseDraft("tcp", {}, {
      bridge: {
        ports: [
          { id: "serial:1", kind: "serial", passthrough_port: 4999, label: "Serial 1" },
          { id: "ir:1", kind: "ir", label: "IR Emitter 1" },
          { id: "relay:1", kind: "relay" },
        ],
      },
    }),
  );
  results.bridge_valid_ok = {
    pass: bridgeIssues(issues).length === 0,
    detail: bridgeIssues(issues),
  };
}
{
  // An unknown {token} makes the computed value silently always "" at
  // runtime (derive_config treats missing fields as empty).
  const issues = validate(
    baseDraft("tcp", {}, {
      config_schema: { workspace_id: { type: "string", label: "Workspace" } },
      config_derived: { ws: "/workspace/{workspace_idd}" },
    }),
  );
  const hits = derivedIssues(issues);
  results.config_derived_unknown_placeholder_warning = {
    pass:
      hits.length === 1 &&
      hits[0].severity === "warning" &&
      /\{workspace_idd\}/.test(hits[0].message),
    detail: hits,
  };
}
{
  // A computed name colliding with a declared config field silently
  // overwrites the configured value when the device connects.
  const issues = validate(
    baseDraft("tcp", {}, {
      config_schema: { zone: { type: "string", label: "Zone" } },
      config_derived: { zone: "Z{host}" },
    }),
  );
  const hits = derivedIssues(issues);
  results.config_derived_name_collision_warning = {
    pass:
      hits.length === 1 &&
      hits[0].severity === "warning" &&
      /same name as a config field/.test(hits[0].message),
    detail: hits,
  };
}
{
  // Valid templates: config_schema keys, default_config keys, baseline
  // injected keys, earlier computed names, and {name:format} specs all count.
  const issues = validate(
    baseDraft("tcp", {}, {
      config_schema: { workspace_id: { type: "string", label: "Workspace" } },
      default_config: { unit: 1 },
      config_derived: {
        ws: "/workspace/{workspace_id}",
        target: "{ws}@{host}:{unit:02d}",
      },
    }),
  );
  results.config_derived_valid_ok = {
    pass: derivedIssues(issues).length === 0,
    detail: derivedIssues(issues),
  };
}
{
  // The field doc says use transport "bridge" with ir_codes; the platform
  // doesn't enforce it, so a mismatch is advice.
  const issues = validate(baseDraft("tcp", {}, { ir_codes: true }));
  const hits = irIssues(issues);
  results.ir_codes_non_bridge_warning = {
    pass:
      hits.length === 1 &&
      hits[0].severity === "warning" &&
      /bridge/i.test(hits[0].message),
    detail: hits,
  };
}
{
  // The generic_ir shape: transport bridge + ir_codes true is clean.
  const issues = validate(baseDraft("bridge", {}, { ir_codes: true }));
  results.ir_bridge_driver_ok = {
    pass: irIssues(issues).length === 0,
    detail: irIssues(issues),
  };
}
{
  // An undeclared {token} in a command leaves a literal "{token}" on the
  // wire; a declared param or config field (including a computed one) is
  // fine.
  const issues = validate(
    baseDraft(
      "tcp",
      {
        set_vol: {
          label: "Vol",
          send: "VOL {level} {ws} {typo}\\r",
          params: { level: { type: "number" } },
        },
      },
      {
        config_schema: { workspace_id: { type: "string", label: "Workspace" } },
        config_derived: { ws: "/w/{workspace_id}" },
      },
    ),
  );
  const hits = issues.filter((i) => /references \{/.test(i.message));
  results.command_unknown_placeholder_warning = {
    pass:
      hits.length === 1 &&
      hits[0].severity === "warning" &&
      /\{typo\}/.test(hits[0].message),
    detail: hits,
  };
}
{
  // A link action's URL substitutes {host}, {port} and declared config
  // fields; anything else is left verbatim in the opened URL.
  const issues = validate(
    baseDraft("tcp", {}, {
      web_ui: "https://{host}/admin",
      actions: [
        { id: "open_ui", kind: "link", label: "Open", url: "https://{hostt}/" },
      ],
    }),
  );
  const hits = issues.filter((i) => /left in the opened URL/.test(i.message));
  results.link_url_unknown_placeholder_warning = {
    pass:
      hits.length === 1 &&
      hits[0].severity === "warning" &&
      /\{hostt\}/.test(hits[0].message),
    detail: hits,
  };
}
{
  // Child-entity advice: no label, no state fields, and a summary/name field
  // that isn't declared. All three describe a driver the platform runs.
  const issues = validate(
    baseDraft("tcp", {}, {
      child_entity_types: {
        zone: { state_variables: {} },
        out: {
          label: "Output",
          state_variables: { input: { type: "integer", label: "Input" } },
          summary_fields: ["input", "nope"],
          label_field: "also_nope",
        },
      },
    }),
  );
  const hits = issues.filter((i) => /^Child type/.test(i.message));
  results.child_entity_advice_warnings = {
    pass:
      hits.length === 4 &&
      hits.every((i) => i.severity === "warning") &&
      hits.some((i) => /has no label/.test(i.message)) &&
      hits.some((i) => /declares no state fields/.test(i.message)) &&
      hits.some((i) => /summary field "nope"/.test(i.message)) &&
      hits.some((i) => /name field "also_nope"/.test(i.message)),
    detail: hits,
  };
}
{
  // A driver with everything filled in raises nothing at all.
  const issues = validate(
    baseDraft(
      "tcp",
      {
        power_on: { label: "On", send: "PWR1 {ws}\\r", params: {} },
      },
      {
        transports: ["tcp", "serial"],
        bridge: {
          ports: [
            { id: "serial:1", kind: "serial", passthrough_port: 4999 },
            { id: "ir:1", kind: "ir" },
          ],
        },
        config_schema: { workspace_id: { type: "string", label: "Workspace" } },
        config_derived: { ws: "/workspace/{workspace_id}" },
        help: {
          overview: "Test.",
          setup: "Plug it in.",
          connection: "Enable remote control in the device menu first.",
        },
        discovery: { requires: "0.23.0", mdns: ["_acme._tcp.local."] },
        simulator: {
          state_machines: { warmup: { steps: [] } },
          notifications: { power: "PWR {value}\\r" },
        },
        ir_codes: false,
      },
    ),
  );
  results.full_featured_driver_no_issues = {
    pass: issues.length === 0,
    detail: issues,
  };
}

// --- A validator failure is an issue, never a blank screen ---------------
// A driver file is arbitrary YAML, so a field can hold a type no rule
// expected. The editor validates inside a render, so a throw there takes the
// whole screen down and leaves no way to fix the value that caused it. Both
// of these load on the platform without complaint and throw here. (Two more
// used to: `auth.username_prompt: 5` and a null param definition. Nothing
// reads those fields any more — the rules that did belong to the platform
// now — which is the class shrinking, not the guard weakening.)
{
  const throwers = {
    unquoted_year_author: { author: 2026 },
    numeric_child_label: { child_entity_types: { zone: { label: 7 } } },
  };
  for (const [name, extra] of Object.entries(throwers)) {
    const draft = { id: "acme_x", name: "Acme X", transport: "tcp", ...extra };
    let raw = "did not throw";
    try {
      V.validateDriver(draft, [], null);
    } catch (e) {
      raw = String(e && e.message ? e.message : e);
    }
    let issues = null;
    let threw = null;
    try {
      issues = V.validateDriverSafely(draft, [], null);
    } catch (e) {
      threw = String(e && e.message ? e.message : e);
    }
    results[`safe_${name}`] = {
      pass:
        raw !== "did not throw" &&
        threw === null &&
        Array.isArray(issues) &&
        issues.length >= 1 &&
        issues.some((i) => i.severity === "error"),
      detail: { raw, threw, issues },
    };
  }
  // The wrapper must not invent an issue for a draft the rules can read.
  const clean = V.validateDriverSafely(
    baseDraft("tcp", { ping: { label: "Ping", send: "PING\\r", params: {} } }),
    [],
    null,
  );
  results.safe_wrapper_adds_nothing_to_a_readable_draft = {
    pass: clean.filter((i) => i.severity === "error").length === 0,
    detail: clean,
  };
}

process.stdout.write(JSON.stringify(results));
