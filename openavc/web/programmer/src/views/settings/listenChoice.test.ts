import { describe, it, expect } from "vitest";
import { cutOffFacts, listenBody, listenStatus, runningAsPreview, shownListenChoice } from "./listenChoice";
import type { ListenBlock, ListenPreview, SystemConfig } from "../../api/restClient";

const ETHERNET = { name: "Ethernet", mac: "50-EB-F6-D2-FC-29" };

function network(over: Partial<SystemConfig["network"]> = {}): SystemConfig["network"] {
  return {
    http_port: 8080, accept_connections_from: "any", accept_connections_adapter: {},
    bind_address: "0.0.0.0", reach_devices_through: {}, control_interface: "", port80_redirect: false,
    ...over,
  } as SystemConfig["network"];
}

function running(over: Partial<ListenBlock> = {}): ListenBlock {
  return {
    mode: "any", adapter: {}, adapter_name: "", label: "Any network", addresses: ["0.0.0.0"],
    network_addresses: [], local_only: false, held_by: null, problem: null,
    restart_needed: false, safety_net: null,
    ...over,
  };
}

function preview(over: Partial<ListenPreview> = {}): ListenPreview {
  return {
    mode: "adapter", adapter_name: "Ethernet", label: "Ethernet only", panel_addresses: ["10.20.30.5"],
    connected_through: "192.168.1.40", serves_this_address: false, keeps_this_connection: false,
    safety_net: true, problem: null, consequences: [], held_by: null, cloud_remote_access: false,
    ...over,
  };
}

describe("listenBody", () => {
  it("carries the adapter only for a chosen adapter", () => {
    expect(listenBody("adapter", ETHERNET)).toEqual({ accept_connections_from: "adapter", accept_connections_adapter: ETHERNET });
    expect(listenBody("any", ETHERNET)).toEqual({ accept_connections_from: "any" });
    expect(listenBody("this_computer", {})).toEqual({ accept_connections_from: "this_computer" });
  });
});

describe("shownListenChoice", () => {
  it("is the saved choice with the unsaved change over it", () => {
    expect(shownListenChoice(network(), undefined)).toEqual({ mode: "any", adapter: {} });
    expect(shownListenChoice(network(), { accept_connections_from: "adapter", accept_connections_adapter: ETHERNET }))
      .toEqual({ mode: "adapter", adapter: ETHERNET });
  });

  it("drops an adapter left over from a choice that is not an adapter", () => {
    expect(shownListenChoice(network({ accept_connections_adapter: ETHERNET }), undefined)).toEqual({ mode: "any", adapter: {} });
  });

  it("reads an empty or absent choice as this computer only", () => {
    expect(shownListenChoice(undefined, undefined)).toEqual({ mode: "this_computer", adapter: {} });
    expect(shownListenChoice(network({ accept_connections_from: "" as never }), undefined))
      .toEqual({ mode: "this_computer", adapter: {} });
  });

  it("gives a chosen adapter with none saved an empty identity", () => {
    expect(shownListenChoice(network({ accept_connections_from: "adapter", accept_connections_adapter: undefined as never }), undefined))
      .toEqual({ mode: "adapter", adapter: {} });
  });
});

describe("runningAsPreview", () => {
  it("stands in with the running choice's addresses while the saved choice is on screen", () => {
    const p = runningAsPreview(running({ network_addresses: ["10.0.0.9"], held_by: "OPENAVC_BIND" }), false);
    expect(p).toEqual({
      mode: "any", adapter_name: "", label: "Any network", panel_addresses: ["10.0.0.9"], connected_through: "",
      serves_this_address: true, keeps_this_connection: true, safety_net: false, problem: null,
      consequences: [], held_by: "OPENAVC_BIND", cloud_remote_access: false,
    });
  });

  it("stands in for nothing once the choice changed, with no addresses, or with nothing running", () => {
    expect(runningAsPreview(running({ network_addresses: ["10.0.0.9"] }), true)).toBeNull();
    expect(runningAsPreview(running(), false)).toBeNull();
    expect(runningAsPreview(undefined, false)).toBeNull();
  });
});

describe("cutOffFacts", () => {
  const context = { pageHost: "localhost", deploymentType: "linux_package", runningLabel: "Any network" };

  it("says the server's answer, with the addresses given", () => {
    const facts = cutOffFacts(
      preview({ consequences: [{ code: "approved_panels", count: 3, message: "3 approved touch panels will stop connecting." }], cloud_remote_access: true }),
      { programmerUrls: ["http://10.20.30.5:8080/programmer"], localUrl: "http://localhost:8080/programmer" },
      context,
    );
    expect(facts).toEqual({
      connectedThrough: "192.168.1.40", mode: "adapter", adapterName: "Ethernet",
      programmerUrls: ["http://10.20.30.5:8080/programmer"], localUrl: "http://localhost:8080/programmer",
      ownScreenOpensProgrammer: true, previousLabel: "Any network", minutes: 10, cloudPaired: true,
      consequences: ["3 approved touch panels will stop connecting."],
    });
  });

  it("names no network address outside a chosen adapter", () => {
    const facts = cutOffFacts(preview({ mode: "this_computer" }), { programmerUrls: ["x"], localUrl: "l" }, context);
    expect(facts.programmerUrls).toEqual([]);
  });

  it("falls back to this page's host, and to the previous choice, when nothing names them", () => {
    const facts = cutOffFacts(preview({ connected_through: "" }), { programmerUrls: [], localUrl: "l" },
      { ...context, runningLabel: undefined });
    expect(facts.connectedThrough).toBe("localhost");
    expect(facts.previousLabel).toBe("the previous choice");
  });

  it("never offers the appliance's own screen", () => {
    const facts = cutOffFacts(preview(), { programmerUrls: [], localUrl: "l" }, { ...context, deploymentType: "android_appliance" });
    expect(facts.ownScreenOpensProgrammer).toBe(false);
  });
});

describe("listenStatus", () => {
  function config(saved: Partial<SystemConfig["network"]>, run: Partial<ListenBlock> | null): SystemConfig {
    return { network: network(saved), ...(run ? { _listen: running(run) } : {}) } as unknown as SystemConfig;
  }
  const any = { mode: "any" as const, adapter: {} };
  const ethernet = { mode: "adapter" as const, adapter: ETHERNET };

  it("is unchanged, not saving and needing no restart on the saved, running choice", () => {
    expect(listenStatus(any, config({}, {}), undefined)).toEqual({ changed: false, saving: false, needsRestart: false });
  });

  it("is being saved once a field of a changed choice was edited", () => {
    const c = config({}, {});
    expect(listenStatus(ethernet, c, { accept_connections_from: "adapter" })).toEqual({ changed: true, saving: true, needsRestart: true });
    expect(listenStatus(ethernet, c, { accept_connections_adapter: ETHERNET })).toEqual({ changed: true, saving: true, needsRestart: true });
    expect(listenStatus(ethernet, c, { http_port: 9090 })).toEqual({ changed: true, saving: false, needsRestart: true });
    expect(listenStatus(ethernet, c, undefined).saving).toBe(false);
  });

  it("is not being saved when the edit chose the saved choice again", () => {
    expect(listenStatus(any, config({}, {}), { accept_connections_from: "any" }).saving).toBe(false);
  });

  it("needs a restart against what the server runs, not what is saved", () => {
    const savedEthernet = config({ accept_connections_from: "adapter", accept_connections_adapter: ETHERNET }, {});
    expect(listenStatus(ethernet, savedEthernet, undefined)).toEqual({ changed: false, saving: false, needsRestart: true });
    expect(listenStatus(any, savedEthernet, { accept_connections_from: "any" })).toEqual({ changed: true, saving: true, needsRestart: false });
  });

  it("compares adapters by identity", () => {
    const runningEthernet = config({ accept_connections_from: "adapter", accept_connections_adapter: ETHERNET }, { mode: "adapter", adapter: { name: "Ethernet", mac: "50:eb:f6:d2:fc:29" } });
    expect(listenStatus(ethernet, runningEthernet, undefined).needsRestart).toBe(false);
  });

  it("knows nothing before the config is read, and needs no restart when the server does not say what it runs", () => {
    expect(listenStatus(any, null, { accept_connections_from: "adapter" })).toEqual({ changed: false, saving: false, needsRestart: false });
    expect(listenStatus(ethernet, config({}, null), undefined)).toEqual({ changed: true, saving: false, needsRestart: false });
  });
});
