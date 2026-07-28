"use strict";
// Loads the real Driver Builder validator (validateDriver.ts) and the Actions
// editor's pure helpers (actionsEditorHelpers.ts), bundled on the fly with the
// esbuild already in web/programmer/node_modules, and exercises the
// actions/quick_actions/web_ui rules: id and kind legality, command
// resolution, visible_when conditions, URL placeholder coverage, and the
// legacy quick_actions conversion. Prints JSON results to stdout; the Python
// wrapper skips when the Node toolchain or esbuild is absent.
const path = require("path");

const validatorPath = process.argv[2];
const helpersPath = process.argv[3];

const esbuild = require("esbuild");
const loadTs = (entry) => {
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
};

const V = loadTs(validatorPath);
const H = loadTs(helpersPath);

const results = {};

// Complete draft so publish-quality warnings (description, version, ...)
// don't muddy the assertions.
const baseDraft = (extra = {}) => ({
  id: "acme_x",
  name: "Acme X",
  manufacturer: "Acme",
  category: "other",
  version: "1.0.0",
  author: "T",
  description: "Test driver.",
  transport: "tcp",
  delimiter: "\\r\\n",
  default_config: {},
  config_schema: {},
  state_variables: {},
  commands: {
    power_on: { label: "Power On", send: "PWR1\\r", params: {} },
    reboot: { label: "Reboot", send: "BOOT\\r", params: {} },
  },
  responses: [],
  polling: {},
  help: { overview: "Test." },
  ...extra,
});

const validate = (draft) => V.validateDriver(draft, [], null);
// Everything this feature emits is anchored to one of these fields.
const actionIssues = (issues) =>
  issues.filter(
    (i) => i.field === "actions" || i.field === "quick_actions" || i.field === "web_ui",
  );
const errorsOf = (issues) => issues.filter((i) => i.severity === "error");

// --- actions: clean full-featured declarations produce no issues ---------
{
  const issues = actionIssues(
    validate(
      baseDraft({
        web_ui: true,
        actions: [
          { id: "power_on" },
          {
            id: "restart",
            kind: "command",
            command: "reboot",
            label: "Restart",
            icon: "rotate-cw",
            confirm: "Restart the device?",
            availability: "offline",
            visible_when: { key: "power", operator: "eq", value: true },
          },
          { id: "open_admin", kind: "link", url: "http://{host}/admin" },
        ],
      }),
    ),
  );
  results.actions_clean_ok = { pass: issues.length === 0, detail: issues };
}

// --- url rules ------------------------------------------------------------
{
  // A link with no url at all is fine — the runtime opens https://{host}.
  const issues = actionIssues(
    validate(baseDraft({ actions: [{ id: "web", kind: "link" }] })),
  );
  results.action_link_no_url_ok = { pass: issues.length === 0, detail: issues };
}

// --- quick_actions --------------------------------------------------------
{
  const issues = actionIssues(
    validate(baseDraft({ quick_actions: ["power_on", "reboot"] })),
  );
  results.quick_actions_ok = { pass: issues.length === 0, detail: issues };
}

// --- URL placeholder coverage (web_ui + link url) -------------------------
{
  const issues = actionIssues(
    validate(baseDraft({ web_ui: "http://{hostt}/setup" })),
  );
  results.web_ui_unknown_placeholder_warning = {
    pass:
      issues.length === 1 &&
      issues[0].severity === "warning" &&
      issues[0].field === "web_ui" &&
      /\{hostt\}/.test(issues[0].message),
    detail: issues,
  };
}
{
  // {host}/{port} are baseline; declared config fields count too.
  const issues = actionIssues(
    validate(
      baseDraft({
        web_ui: "http://{host}:{port}/{zone}",
        config_schema: { zone: { type: "string", label: "Zone" } },
      }),
    ),
  );
  results.web_ui_known_placeholders_ok = { pass: issues.length === 0, detail: issues };
}
{
  const issues = actionIssues(
    validate(
      baseDraft({
        actions: [{ id: "web", kind: "link", url: "http://{host}/{workspace}" }],
      }),
    ),
  );
  results.link_url_unknown_placeholder_warning = {
    pass:
      issues.length === 1 &&
      issues[0].severity === "warning" &&
      /\{workspace\}/.test(issues[0].message),
    detail: issues,
  };
}

// --- helpers: quick_actions conversion ------------------------------------
{
  // Appends after existing actions, skips ids already declared, drops blanks.
  const converted = H.convertQuickActionsToActions(
    [{ id: "power_on", kind: "command", label: "On" }],
    ["power_on", "reboot", "", "reboot"],
  );
  results.convert_quick_appends_and_skips = {
    pass:
      converted.length === 2 &&
      converted[0].label === "On" &&
      converted[1].id === "reboot" &&
      converted[1].kind === "command" &&
      Object.keys(converted[1]).join(",") === "id,kind",
    detail: converted,
  };
}
{
  const converted = H.convertQuickActionsToActions(undefined, ["a", "b"]);
  const noQuick = H.convertQuickActionsToActions([{ id: "x" }], undefined);
  results.convert_quick_edge_inputs = {
    pass:
      converted.length === 2 &&
      converted[0].id === "a" &&
      noQuick.length === 1 &&
      noQuick[0].id === "x",
    detail: { converted, noQuick },
  };
}

// --- helpers: visible_when mode + conditions ------------------------------
{
  const m = H.visibleWhenMode;
  const c = H.visibleWhenConditions;
  results.visible_when_mode_matrix = {
    pass:
      m(undefined) === "always" &&
      m({ key: "power" }) === "single" &&
      m({ any: [{ key: "a" }] }) === "any" &&
      m({ all: [{ key: "a" }] }) === "all" &&
      c(undefined).length === 0 &&
      c({ key: "power" }).length === 1 &&
      c({ any: [{ key: "a" }, { key: "b" }] }).length === 2,
    detail: null,
  };
}

// --- helpers: condition value coercion ------------------------------------
{
  const v = H.coerceConditionValue;
  results.coerce_condition_value_matrix = {
    pass:
      v("true") === true &&
      v("false") === false &&
      v("5") === 5 &&
      v("-2.5") === -2.5 &&
      v("hdmi1") === "hdmi1" &&
      v("1.2.3") === "1.2.3",
    detail: {
      five: v("5"),
      neg: v("-2.5"),
      text: v("hdmi1"),
    },
  };
}

// --- helpers: extra-key preservation --------------------------------------
{
  const extras = H.extraKeys(
    { key: "power", operator: "eq", value: 1, note: "hand-authored" },
    ["key", "operator", "value"],
  );
  results.extra_keys_preserved = {
    pass: Object.keys(extras).join(",") === "note" && extras.note === "hand-authored",
    detail: extras,
  };
}

process.stdout.write(JSON.stringify(results));
