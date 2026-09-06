import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "@testing-library/react";

// Devices arrive over the WebSocket one at a time, but the engine's final pass
// can REMOVE records: a host that has left the network, or an address the ARP
// table named that then answered nothing. Nothing on the socket says "forget
// this one", so the view reconciles against the server when a scan finishes.
//
// That reconcile is gated on a ref that dies with the component. Start a scan,
// navigate away while it runs, come back after it finished, and the gate is
// false on the fresh mount, so the reconcile never happens. The mount's own
// fetch was the last line of defence and it only took the server's list when
// that list was non-empty, which is exactly the case that matters: a scan of a
// network that has gone quiet returns nothing, and every host from the last
// scan stayed on screen until the page was reloaded.
//
// The list lives in a store that outlives the view, which is why it survives at
// all, so this drives the real store and mocks only what it talks to.

const mocks = vi.hoisted(() => ({
  results: { devices: [] as unknown[], status: "complete", port_labels: {}, warnings: [] },
}));

vi.mock("../api/restClient", () => ({
  discoveryGetSubnets: vi.fn(async () => ({ subnets: [] })),
  discoveryGetConfig: vi.fn(async () => ({
    snmp_enabled: true,
    snmp_community_set: false,
    gentle_mode: false,
  })),
  discoveryGetResults: vi.fn(async () => mocks.results),
  getSystemConfig: vi.fn(async () => ({ network: {} })),
  getNetworkAdapters: vi.fn(async () => ({ adapters: [] })),
  listDrivers: vi.fn(async () => []),
  fetchCommunityDrivers: vi.fn(async () => []),
  discoveryStartScan: vi.fn(async () => ({})),
  discoveryCancelScan: vi.fn(async () => ({})),
  discoverySetConfig: vi.fn(async () => ({})),
}));

vi.mock("../store/projectStore", () => ({
  useProjectStore: (selector: (s: unknown) => unknown) =>
    selector({ project: { devices: [], connections: {} } }),
}));

vi.mock("../store/navigationStore", () => ({
  useNavigationStore: (selector: (s: unknown) => unknown) =>
    selector({ navigate: vi.fn(), setPendingDeviceId: vi.fn() }),
}));

vi.mock("../store/toastStore", () => ({ showError: vi.fn() }));

vi.mock("../components/shared/DeviceSettingsSetupDialog", () => ({
  DeviceSettingsSetupDialog: () => null,
  hasDriverSetupSettings: () => false,
}));

import { DiscoveryPanel } from "./DiscoveryView";
import { useDiscoveryStore } from "../store/discoveryStore";

function ghost(ip: string) {
  return {
    ip,
    mac: null,
    hostname: ip,
    manufacturer: null,
    model: null,
    device_name: null,
    firmware: null,
    serial_number: null,
    open_ports: [],
    banners: {},
    protocols: [],
    category: null,
    alive: true,
    identification: null,
    evidence_log: [],
    mdns_services: [],
    ssdp_info: null,
    snmp_info: null,
  };
}

/** What a previous scan left in the store, as if the view had just remounted. */
function seedStore(status: "running" | "complete") {
  useDiscoveryStore.setState({
    devices: { "10.0.0.7": ghost("10.0.0.7"), "10.0.0.9": ghost("10.0.0.9") },
    status,
  });
}

function storeIps() {
  return Object.keys(useDiscoveryStore.getState().devices).sort();
}

beforeEach(() => {
  useDiscoveryStore.getState().clear();
  mocks.results = { devices: [], status: "complete", port_labels: {}, warnings: [] };
});

describe("DiscoveryPanel — hosts a finished scan dropped", () => {
  it("clears the list when the finished scan found nothing", async () => {
    seedStore("complete");
    expect(storeIps()).toEqual(["10.0.0.7", "10.0.0.9"]);

    render(<DiscoveryPanel />);

    await waitFor(() => expect(storeIps()).toEqual([]));
  });

  it("takes the finished scan's shorter list too", async () => {
    seedStore("complete");
    mocks.results = {
      devices: [ghost("10.0.0.7")],
      status: "complete",
      port_labels: {},
      warnings: [],
    };

    render(<DiscoveryPanel />);

    await waitFor(() => expect(storeIps()).toEqual(["10.0.0.7"]));
  });

  it("keeps what the socket delivered while a scan is still running", async () => {
    // Mid-scan the server's stored results can legitimately lag the socket, so
    // an empty answer here is not an authority on anything and must not wipe
    // devices that are already on screen.
    seedStore("running");
    mocks.results = { devices: [], status: "running", port_labels: {}, warnings: [] };

    render(<DiscoveryPanel />);

    await waitFor(() => expect(useDiscoveryStore.getState().status).toBe("running"));
    expect(storeIps()).toEqual(["10.0.0.7", "10.0.0.9"]);
  });
});
