"use strict";
// Loads the state-key picker helpers (variableKeyPickerHelpers.ts — React-free
// pure logic) bundled on the fly with the esbuild already in
// openavc/web/programmer/node_modules, and checks the group-header labelling. The old
// inline switch relabelled only device:/system/ui: groups, so plugin.* keys
// and orphan ui.* keys (element not in the project) fell through to the default
// "Project Variables" header — a plugin's live state shown under the wrong
// source category. Mirrors trigger_helpers_harness.cjs. The Python wrapper
// skips when the Node toolchain or esbuild is absent rather than failing the
// Python-only CI gate.
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

// The OLD inline switch (pre-fix) had no plugin:/bare-ui cases, so both fell
// through to "Project Variables". Replayed here to prove the new helper diverges.
const oldLabel = (group, name) => {
  if (group === "control") return "This control";
  if (group.startsWith("device:")) return `Device: ${name}`;
  if (group === "system") return "System";
  if (group.startsWith("ui:")) return `UI: ${name}`;
  if (group === "trigger") return "Trigger event";
  return "Project Variables";
};

const labelCase = (key, group, name, expected) => {
  const got = H.groupLabel(group, name);
  results[key] = { pass: got === expected, detail: { group, name, got, expected } };
};

// Unchanged groups keep their labels.
labelCase("label_control", "control", undefined, "This control");
labelCase("label_variables_default", "variables", undefined, "Project Variables");
labelCase("label_device", "device:proj", "Projector", "Device: Projector");
labelCase("label_system", "system", undefined, "System");
labelCase("label_ui_element", "ui:btn1", "Main Page", "UI: Main Page");
labelCase("label_trigger", "trigger", undefined, "Trigger event");

// THE fix: plugin and orphan-ui groups get their own headers, not "Project Variables".
labelCase("label_plugin", "plugin:myplug", "myplug", "Plugin: myplug");
labelCase("label_orphan_ui", "ui", undefined, "UI");

// Contrast against the pre-fix inline logic — proves the finding is closed.
results.plugin_was_mislabeled_before = {
  pass:
    oldLabel("plugin:myplug", "myplug") === "Project Variables" &&
    H.groupLabel("plugin:myplug", "myplug") === "Plugin: myplug",
  detail: {
    old: oldLabel("plugin:myplug", "myplug"),
    now: H.groupLabel("plugin:myplug", "myplug"),
  },
};
results.orphan_ui_was_mislabeled_before = {
  pass:
    oldLabel("ui", undefined) === "Project Variables" &&
    H.groupLabel("ui", undefined) === "UI",
  detail: { old: oldLabel("ui", undefined), now: H.groupLabel("ui", undefined) },
};

process.stdout.write(JSON.stringify(results));
