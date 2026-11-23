import { describe, it, expect } from "vitest";
import type { PluginExtension } from "../../api/restClient";
import {
  contextActionConfirm,
  contextActionName,
  contextActionPayload,
  contextActionsFor,
} from "./pluginExtensionHelpers";

function action(over: Partial<PluginExtension>): PluginExtension {
  return {
    id: "scan", label: "Scan", plugin_id: "acme_widget", plugin_name: "Acme Widget",
    event: "action.scan", ...over,
  };
}

describe("the action name a context action's event is sent as", () => {
  it("takes one leading action. off", () => {
    expect(contextActionName("action.refresh_domains")).toBe("refresh_domains");
  });

  it("leaves an action. that is not at the start", () => {
    expect(contextActionName("reaction.scan")).toBe("reaction.scan");
    expect(contextActionName("my_action.scan")).toBe("my_action.scan");
  });

  it("takes only the first action. off", () => {
    expect(contextActionName("action.action.scan")).toBe("action.scan");
  });

  it("sends an event with no action. as written", () => {
    expect(contextActionName("scan")).toBe("scan");
  });

  it("has no name for an action with no event", () => {
    expect(contextActionName(undefined)).toBeNull();
    expect(contextActionName("")).toBeNull();
    expect(contextActionName("action.")).toBeNull();
  });
});

describe("which context actions a page draws", () => {
  const all = [
    action({ id: "everywhere", context: "global" }),
    action({ id: "own", context: "plugin" }),
    action({ id: "on_device", context: "device", match: { driver_id: "acme_*" } }),
    action({ id: "other_global", context: "global", plugin_id: "acme_gadget" }),
    action({ id: "nowhere", context: "toolbar" }),
  ];
  const ids = (list: PluginExtension[]) => list.map((a) => `${a.plugin_id}.${a.id}`);

  it("draws a plugin's global and plugin actions on its own page", () => {
    expect(ids(contextActionsFor(all, { page: "plugin", pluginId: "acme_widget" }))).toEqual([
      "acme_widget.everywhere", "acme_widget.own",
    ]);
  });

  it("draws a device action on a device its match names", () => {
    expect(ids(contextActionsFor(all, { page: "device", deviceId: "w1", driverId: "acme_projector" })))
      .toEqual(["acme_widget.on_device"]);
    expect(contextActionsFor(all, { page: "device", deviceId: "g1", driverId: "other_projector" }))
      .toEqual([]);
  });

  it("sends a device action the device's id and a plugin page's action nothing", () => {
    expect(contextActionPayload({ page: "device", deviceId: "w1" })).toEqual({ device_id: "w1" });
    expect(contextActionPayload({ page: "plugin", pluginId: "acme_widget" })).toEqual({});
  });
});

describe("the sentence an action asks before it runs", () => {
  it("is the action's confirm", () => {
    expect(contextActionConfirm(action({ confirm: "Wipe every preset?" }))).toBe("Wipe every preset?");
  });

  it("is none for an action with no confirm, a blank one, or one that is not words", () => {
    expect(contextActionConfirm(action({}))).toBeNull();
    expect(contextActionConfirm(action({ confirm: "  " }))).toBeNull();
    expect(contextActionConfirm(action({ confirm: true as unknown as string }))).toBeNull();
  });
});
