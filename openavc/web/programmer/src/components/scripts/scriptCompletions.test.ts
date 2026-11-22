import { describe, expect, it } from "vitest";
import type { PluginScriptMethod } from "../../api/pluginClient";
import type { ProjectConfig } from "../../api/types";
import { scriptCompletions, type ScriptCompletion } from "./scriptCompletions";

const PROJECT = {
  devices: [{ id: "proj1", name: "Projector", driver: "acme_widget" }],
  macros: [{ id: "all_off", name: "All Off", steps: [] }],
  variables: [{ id: "volume", label: "Volume", type: "number" }],
  ui: {
    pages: [{
      id: "main",
      elements: [
        { id: "btn_power", type: "button", label: "Power" },
        { id: "vol", type: "slider", label: "" },
      ],
    }],
  },
} as unknown as ProjectConfig;

const LIVE_STATE = { "device.proj1.power": "on", "var.volume": 40, "system.version": "1" };

const PLUGIN_METHODS = [
  { plugin_id: "acme_player", plugin_name: "Acme Player", method: "play", sync: false, doc: "" },
] as unknown as PluginScriptMethod[];

function complete(text: string): ScriptCompletion[] {
  return scriptCompletions(text, {
    project: PROJECT,
    liveState: LIVE_STATE,
    pluginMethods: PLUGIN_METHODS,
  });
}

function labels(text: string): string[] {
  return complete(text).map((c) => c.label);
}

function member(text: string, label: string): ScriptCompletion {
  const found = complete(text).find((c) => c.label === label);
  expect(found, `${text}${label}`).toBeDefined();
  return found!;
}

describe("script editor completions", () => {
  it("offers every name a script can import", () => {
    expect(labels("from openavc import ")).toEqual([
      "Event", "PanelMessage", "after", "cancel_all_timers", "cancel_timer", "compare",
      "delay", "devices", "events", "every", "isc", "log", "macros", "on_event",
      "on_state_change", "plugins", "state",
    ]);
  });

  it("offers state.delete", () => {
    expect(labels("state.")).toContain("delete");
  });

  it("says devices.list returns the device records", () => {
    expect(member("devices.", "list").detail).toBe("() -> list[dict[str, Any]]");
  });

  it("marks what is awaited and what is not", () => {
    expect(member("devices.", "send").documentation).toContain("await devices.send(");
    expect(member("macros.", "start").documentation).not.toContain("await macros.start(");
  });

  it("offers the system events the platform sends, and no others", () => {
    const events = labels('@on_event("');
    expect(events).toEqual(expect.arrayContaining([
      "system.started", "system.stopping", "system.project.reloaded",
    ]));
    for (const gone of ["system.startup", "system.shutdown", "schedule.*"]) {
      expect(events).not.toContain(gone);
    }
  });

  it("offers each family of events, and each device's and macro's own", () => {
    const events = labels("@on_event('");
    expect(events).toEqual(expect.arrayContaining([
      "custom.*", "ui.hold.*", "device.error.*", "help.requested",
      "device.connected.proj1", "device.error.proj1", "macro.completed.all_off",
    ]));
  });

  it("gives an event its meaning", () => {
    expect(member('@on_event("', "device.error.proj1").detail).toBe(
      "Projector: A command or poll failed while the device stayed connected",
    );
  });

  it("offers an object's members after openavc. too, and not after another object's", () => {
    expect(labels("openavc.devices.")).toContain("send");
    expect(labels("self.state.")).not.toContain("get_namespace");
  });

  it("offers the project's ids and keys where a script names them", () => {
    expect(labels('devices.send("')).toEqual(["proj1"]);
    expect(labels('macros.start("')).toEqual(["all_off"]);
    expect(labels('state.get("')).toEqual([
      "var.volume",
      "ui.btn_power.label", "ui.btn_power.visible", "ui.btn_power.bg_color",
      "ui.btn_power.text_color", "ui.btn_power.opacity",
      "ui.vol.label", "ui.vol.visible", "ui.vol.bg_color", "ui.vol.text_color", "ui.vol.opacity",
      "device.proj1.power", "system.version", "device.proj1.asleep",
    ]);
  });

  it("offers a press for each button and a change for each slider", () => {
    const events = complete('@on_event("');
    expect(events.find((c) => c.label === "ui.press.btn_power")?.detail).toBe("Button press: Power");
    expect(events.find((c) => c.label === "ui.change.vol")?.detail).toBe("Value change: slider");
  });

  it("offers a running plugin's methods", () => {
    expect(labels("plugins.")).toEqual(["acme_player"]);
    expect(labels("plugins.acme_player.")).toEqual(["play"]);
  });

  it("offers each decorator with a handler the engine loads", () => {
    expect(member("@", "on_event").insertText).toContain("(event):");
    expect(member("@", "on_state_change").insertText).toContain("(key, old_value, new_value):");
  });
});
