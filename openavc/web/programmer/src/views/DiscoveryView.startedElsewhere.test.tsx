import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";

// A scan started from another Programmer tab, or by the assistant, shows on an
// open Discovery page as it would had the page started it: running, with its
// time limit and a Stop button, from the discovery.started message alone; and
// when it ends, the page reads the results again, so a host that scan dropped
// leaves the list.

const mocks = vi.hoisted(() => ({
  results: { devices: [] as unknown[], status: "complete", port_labels: {}, warnings: [] as string[] },
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
  discoveryStopScan: vi.fn(async () => ({})),
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
import { applyDiscoveryMessage, useDiscoveryStore } from "../store/discoveryStore";
import * as api from "../api/restClient";

function host(ip: string) {
  return {
    ip,
    name: "Unknown Device",
    mac: null,
    hostname: ip,
    manufacturer: null,
    model: null,
    device_name: null,
    firmware: null,
    serial_number: null,
    open_ports: [80],
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
  mocks.results = {
    devices: [host("10.0.0.7"), host("10.0.0.8")],
    status: "complete",
    port_labels: {},
    warnings: [],
  };
  vi.mocked(api.discoveryGetResults).mockClear();
});

describe("DiscoveryPanel — a scan started somewhere else", () => {
  it("shows it running with its time limit, and reads the results again when it ends", async () => {
    render(<DiscoveryPanel />);
    expect(await screen.findByText("10.0.0.8")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Scan/ })).toBeTruthy();
    const readsBefore = vi.mocked(api.discoveryGetResults).mock.calls.length;

    act(() => applyDiscoveryMessage({
      type: "discovery.started",
      scan_id: "scan_2_1700000000",
      subnets: ["10.0.0.0/24"],
      started_at: 1700000000,
      budget_seconds: 90,
    }));

    expect(useDiscoveryStore.getState().status).toBe("running");
    expect(screen.getByRole("button", { name: /Stop/ })).toBeTruthy();
    expect(screen.getByText(/up to 90s/)).toBeTruthy();

    // The scan heard from 10.0.0.7 again and not from 10.0.0.8.
    mocks.results = { devices: [host("10.0.0.7")], status: "complete", port_labels: {}, warnings: [] };
    act(() => applyDiscoveryMessage({
      type: "discovery.complete",
      scan_id: "scan_2_1700000000",
      status: "complete",
      total_devices: 1,
      duration_seconds: 30,
      warnings: [],
    }));

    await waitFor(() => expect(screen.queryByText("10.0.0.8")).toBeNull());
    expect(vi.mocked(api.discoveryGetResults).mock.calls.length).toBe(readsBefore + 1);
    expect(screen.getByText("10.0.0.7")).toBeTruthy();
  });

  it("clears the last scan's warnings and progress as it starts", () => {
    useDiscoveryStore.setState({
      status: "partial",
      phase: "finalize",
      progress: 1,
      message: "Done",
      warnings: ["Scan stopped at its 60s limit during port_scan"],
    });

    applyDiscoveryMessage({
      type: "discovery.started",
      scan_id: "scan_3_1700000100",
      subnets: ["10.0.0.0/24"],
      started_at: 1700000100,
      budget_seconds: 300,
    });

    const s = useDiscoveryStore.getState();
    expect(s.status).toBe("running");
    expect(s.scanId).toBe("scan_3_1700000100");
    expect(s.budgetSeconds).toBe(300);
    expect(s.warnings).toEqual([]);
    expect([s.phase, s.progress, s.message]).toEqual(["", 0, ""]);
  });
});
