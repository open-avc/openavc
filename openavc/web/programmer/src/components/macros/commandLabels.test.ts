import { describe, it, expect } from "vitest";
import type { DeviceConfig, DeviceGroup, MacroStep } from "../../api/types";
import { commandLabelsOf, stepDeviceIds } from "./commandLabels";
import { getStepType } from "./macroHelpers";

const DEVICES = [{ id: "widget_1", name: "P4 Check Widget", driver: "acme_widget", config: {} }] as DeviceConfig[];

describe("a device command step's summary", () => {
  const step: MacroStep = { action: "device.command", device: "widget_1", command: "set_level" };
  const summary = getStepType("device.command")!.summary;

  it("names the command as the device's Command list does", () => {
    const labels = { widget_1: commandLabelsOf({ set_level: { label: "Set Level" }, mute: {} }) };
    const commandLabel = (device?: string, command?: string) =>
      device && command ? labels[device as "widget_1"]?.[command] : undefined;
    expect(summary(step, DEVICES, [], { commandLabel })).toBe("P4 Check Widget → Set Level");
  });

  it("keeps the command's id when there is no label to show", () => {
    expect(summary(step, DEVICES, [], { commandLabel: () => undefined })).toBe("P4 Check Widget → set_level");
    expect(summary(step, DEVICES, [])).toBe("P4 Check Widget → set_level");
  });
});

describe("a group command step's summary", () => {
  const GROUPS: DeviceGroup[] = [
    { id: "group_1788707883504", name: "All Widgets", device_ids: ["widget_1", "widget_2"] },
    { id: "group_unnamed", name: "", device_ids: [] },
  ];
  const step: MacroStep = { action: "group.command", group: "group_1788707883504", command: "set_level" };
  const summary = getStepType("group.command")!.summary;
  const labels: Record<string, Record<string, string>> = {
    widget_2: commandLabelsOf({ set_level: { label: "Set Level" } }),
  };
  const commandLabel = (device?: string, command?: string) =>
    device && command ? labels[device]?.[command] : undefined;

  it("names the group, and the command as a member's Command list does", () => {
    expect(summary(step, DEVICES, [], { commandLabel, groups: GROUPS })).toBe("All Widgets → Set Level");
  });

  it("keeps the ids when there is nothing better to show", () => {
    expect(summary(step, DEVICES, [], { commandLabel: () => undefined, groups: GROUPS })).toBe("All Widgets → set_level");
    expect(summary(step, DEVICES, [])).toBe("group_1788707883504 → set_level");
    expect(summary({ ...step, group: "group_unnamed" }, DEVICES, [], { commandLabel, groups: GROUPS })).toBe(
      "group_unnamed → set_level",
    );
    expect(summary({ action: "group.command" }, DEVICES, [], { commandLabel, groups: GROUPS })).toBe("? → ?");
  });

  it("marks a group the project no longer has", () => {
    expect(summary({ ...step, group: "group_gone" }, DEVICES, [], { commandLabel, groups: GROUPS })).toBe(
      "group_gone (missing) → set_level",
    );
  });
});

describe("commandLabelsOf", () => {
  it("takes each command's label and skips one without", () => {
    expect(commandLabelsOf({ a: { label: " Power On " }, b: { label: "" }, c: {}, d: null })).toEqual({
      a: "Power On",
    });
  });
});

describe("stepDeviceIds", () => {
  it("finds every device a step names, inside branches too, once each", () => {
    const steps: MacroStep[] = [
      { action: "device.command", device: "b", command: "x" },
      {
        action: "conditional",
        then_steps: [{ action: "device.command", device: "a", command: "y" }],
        else_steps: [{ action: "device.command", device: "b", command: "z" }],
      },
      { action: "delay", seconds: 1 },
    ];
    expect(stepDeviceIds(steps)).toEqual(["a", "b"]);
  });

  it("finds a group command's members, so its command can be named too", () => {
    const steps: MacroStep[] = [
      { action: "group.command", group: "g", command: "x" },
      { action: "conditional", then_steps: [{ action: "group.command", group: "gone", command: "y" }] },
    ];
    const groups: DeviceGroup[] = [{ id: "g", name: "G", device_ids: ["d", "c"] }];
    expect(stepDeviceIds(steps, groups)).toEqual(["c", "d"]);
    expect(stepDeviceIds(steps)).toEqual([]);
  });
});
