import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen } from "@testing-library/react";

// A scan someone stopped ends cancelled in every open Discovery view, from the
// discovery.complete event alone. The devices it found before the stop stay
// listed with the time they are from, as they do after a finished scan.

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

beforeEach(() => {
  useDiscoveryStore.getState().clear();
  mocks.results = { devices: [], status: "running", port_labels: {}, warnings: [] };
});

describe("DiscoveryPanel — a stopped scan", () => {
  it("keeps the devices it found, with the time they are from", async () => {
    useDiscoveryStore.setState({ devices: { "10.0.0.7": ghost("10.0.0.7") }, status: "running" });
    render(<DiscoveryPanel />);
    expect(screen.queryByText(/Results from/)).toBeNull();

    act(() => useDiscoveryStore.getState().setStatus("cancelled"));

    expect(await screen.findByText(/Results from .* \(1 device\)/)).toBeTruthy();
    expect(Object.keys(useDiscoveryStore.getState().devices)).toEqual(["10.0.0.7"]);
  });
});
