import { describe, it, expect } from "vitest";
import type { DeviceGroup, MacroConfig, MacroStep } from "../../api/types";
import { loadWorksOffline, macroToScript } from "./macroHelpers";

// An invented widget whose "wake" works while it is offline (a Wake-on-LAN
// Power On) and whose "beep" needs it connected.
const COMMANDS = { wake: { label: "Wake", available_offline: true }, beep: { label: "Beep" } };
const GROUPS = [{ id: "wall", name: "Wall", device_ids: ["widget_1", "widget_2"] }] as DeviceGroup[];

function macro(steps: MacroStep[]): MacroConfig {
  return { id: "macro_on", name: "On", steps } as MacroConfig;
}

async function convert(m: MacroConfig, offlineDevices = ["widget_1", "widget_2"]): Promise<string> {
  const getDevice = async (id: string) => {
    if (!offlineDevices.includes(id)) throw new Error("not found");
    return { commands: COMMANDS };
  };
  return macroToScript(m, GROUPS, await loadWorksOffline(m, GROUPS, getDevice));
}

describe("a converted macro's 'skip if offline'", () => {
  it("never guards a command that works while the device is offline", async () => {
    const src = await convert(
      macro([{ action: "device.command", device: "widget_1", command: "wake", skip_if_offline: true }]),
    );
    expect(src).not.toContain('if state.get("device.widget_1.connected")');
    expect(src).toContain('await devices.send("widget_1", "wake")');
  });

  it("still guards a command that needs the device", async () => {
    const src = await convert(
      macro([{ action: "device.command", device: "widget_1", command: "beep", skip_if_offline: true }]),
    );
    expect(src).toContain('if state.get("device.widget_1.connected"):  # skip_if_offline');
  });

  it("guards as before when a device's driver cannot be read", async () => {
    const src = await convert(
      macro([{ action: "device.command", device: "widget_1", command: "wake", skip_if_offline: true }]),
      [],
    );
    expect(src).toContain('if state.get("device.widget_1.connected"):  # skip_if_offline');
  });
});

describe("a converted group command", () => {
  it("sends a command that works offline to every member", async () => {
    const src = await convert(macro([{ action: "group.command", group: "wall", command: "wake" }]));
    expect(src).not.toContain("continue  # offline devices are skipped");
  });

  it("skips only the members whose command needs them connected", async () => {
    const src = await convert(macro([{ action: "group.command", group: "wall", command: "wake" }]), ["widget_2"]);
    expect(src).toContain(
      `if not state.get(f"device.{device_id}.connected") and device_id not in ["widget_2"]:`,
    );
  });

  it("skips every offline member for a command that needs the device", async () => {
    const src = await convert(macro([{ action: "group.command", group: "wall", command: "beep" }]));
    expect(src).toContain('    if not state.get(f"device.{device_id}.connected"):');
  });
});
