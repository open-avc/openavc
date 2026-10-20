import { describe, it, expect } from "vitest";
import type { DeviceConfig, MacroStep } from "../../api/types";
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
    expect(summary(step, DEVICES, [], commandLabel)).toBe("P4 Check Widget → Set Level");
  });

  it("keeps the command's id when there is no label to show", () => {
    expect(summary(step, DEVICES, [], () => undefined)).toBe("P4 Check Widget → set_level");
    expect(summary(step, DEVICES, [])).toBe("P4 Check Widget → set_level");
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
});
