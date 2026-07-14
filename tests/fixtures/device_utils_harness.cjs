"use strict";
// Loads DeviceDetail/DeviceView's device-reference helper (deviceUtils.ts —
// findDeviceReferences; a type-only import that esbuild erases, so effectively
// zero-import) and checks what it reports before deleting a device. The old
// version under-reported (only step.device, trigger state_key/conditions) and
// over-reported (substring match on the stringified bindings), so the delete
// warning both missed real dependencies and flagged sibling device ids. The
// fix walks macro params/group/nested steps + event-trigger patterns + device
// groups and anchors the UI-binding match on segment boundaries. Mirrors
// device_view_helpers_harness.cjs. The Python wrapper skips when the Node
// toolchain or esbuild is absent.
const fs = require("fs");
const path = require("path");

const helpersPath = process.argv[2];
const src = fs.readFileSync(helpersPath, "utf8");

const esbuild = require("esbuild");
const { code } = esbuild.transformSync(src, { loader: "ts", format: "cjs" });
const moduleObj = { exports: {} };
const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
fn(moduleObj.exports, require, moduleObj, helpersPath, path.dirname(helpersPath));
const H = moduleObj.exports;

const find = (project, id) => H.findDeviceReferences(project, id);
const some = (refs, sub) => refs.some((r) => r.includes(sub));
const results = {};
const record = (name, pass, detail) => { results[name] = { pass, detail }; };

// L-175a: a $device param reference with no step.device must be found.
{
  const p = { device_groups: [], macros: [{ name: "M", steps: [
    { action: "state.set", key: "var.x", value: "$device.d1.volume" },
  ] }], ui: { pages: [] } };
  const refs = find(p, "d1");
  record("l175_param_device_ref", some(refs, 'Macro "M"'), refs);
}

// L-175b: a group.command step whose group contains the device, plus the
// device-group membership line.
{
  const p = { device_groups: [{ id: "displays", name: "Displays", device_ids: ["d1", "d2"] }],
    macros: [{ name: "AllOff", steps: [{ action: "group.command", group: "displays", command: "off" }] }],
    ui: { pages: [] } };
  const refs = find(p, "d1");
  record("l175_group_command_step", some(refs, 'Macro "AllOff": 1 step(s)') && some(refs, 'Device group "Displays"'), refs);
}

// L-175c: a device.* event-trigger pattern.
{
  const p = { device_groups: [], macros: [{ name: "M", steps: [],
    triggers: [{ type: "event", event_pattern: "device.disconnected.d1" }] }], ui: { pages: [] } };
  const refs = find(p, "d1");
  record("l175_event_pattern", some(refs, "device.disconnected.d1"), refs);
}

// L-175d: a device.command buried in a conditional's then_steps.
{
  const p = { device_groups: [], macros: [{ name: "M", steps: [
    { action: "conditional", condition: { key: "var.mode", operator: "eq", value: "on" },
      then_steps: [{ action: "device.command", device: "d1", command: "power_on" }] },
  ] }], ui: { pages: [] } };
  const refs = find(p, "d1");
  record("l175_nested_conditional", some(refs, 'Macro "M"'), refs);
}

// L-176: substring false positive — searching d1 must NOT match a binding that
// only references sibling device d10.
{
  const p = { device_groups: [], macros: [], ui: { pages: [{ name: "Home", elements: [
    { id: "sl1", label: "Vol", bindings: { show: { value: { source: "state", key: "device.d10.volume" } } } },
  ] }] } };
  record("l176_no_substring_false_positive", find(p, "d1").length === 0, find(p, "d1"));
  record("l176_sibling_still_found", find(p, "d10").length === 1, find(p, "d10"));
}

// L-176 no-regression: a do-action targeting the device by bare id is still caught.
{
  const p = { device_groups: [], macros: [], ui: { pages: [{ name: "Home", elements: [
    { id: "b1", label: "On", bindings: { do: { press: [{ action: "device.command", device: "d1", command: "power_on" }] } } },
  ] }] } };
  const refs = find(p, "d1");
  record("l176_do_action_target_kept", refs.length === 1 && some(refs, 'element "On"'), refs);
}

// True-positive show key still reported.
{
  const p = { device_groups: [], macros: [], ui: { pages: [{ name: "Home", elements: [
    { id: "sl1", label: "Vol", bindings: { show: { value: { key: "device.d1.volume" } } } },
  ] }] } };
  record("l176_true_positive_show_key", find(p, "d1").length === 1, find(p, "d1"));
}

process.stdout.write(JSON.stringify(results));
