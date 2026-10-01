import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// What the "Scanning on" line says has to be what the scan does. Unpinned, a
// scan covers the network of every adapter with a link (the server leaves an
// unplugged port out even when it still holds a static address), so the line
// names those networks rather than promising a single "default route". Pinned
// to an adapter with no link, the scan refuses, so the line says why and offers
// the connected adapters instead.

const mocks = vi.hoisted(() => ({
  subnets: [] as string[],
  controlInterface: "",
  adapters: [] as unknown[],
}));

vi.mock("../api/restClient", () => ({
  discoveryGetSubnets: vi.fn(async () => ({ subnets: mocks.subnets })),
  discoveryGetConfig: vi.fn(async () => ({
    snmp_enabled: true,
    snmp_community_set: false,
    gentle_mode: false,
  })),
  discoveryGetResults: vi.fn(async () => ({ devices: [], status: "idle", port_labels: {}, warnings: [] })),
  getSystemConfig: vi.fn(async () => ({ network: { control_interface: mocks.controlInterface } })),
  getNetworkAdapters: vi.fn(async () => ({ adapters: mocks.adapters })),
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

const WIFI = { name: "Wi-Fi", ip: "192.168.1.78", subnet: "192.168.1.0/24", mac: "", link: true };
const UNPLUGGED = { name: "Ethernet", ip: "10.20.0.5", subnet: "10.20.0.0/24", mac: "", link: false };

describe("the Scanning on line", () => {
  beforeEach(() => {
    mocks.subnets = [];
    mocks.controlInterface = "";
    mocks.adapters = [WIFI, UNPLUGGED];
  });

  it("names the networks an unpinned scan covers", async () => {
    mocks.subnets = ["192.168.1.0/24"];
    render(<DiscoveryPanel />);
    expect(
      await screen.findByText("Scanning on: every connected adapter (192.168.1.0/24)"),
    ).toBeTruthy();
  });

  it("says so when no adapter has a link", async () => {
    render(<DiscoveryPanel />);
    expect(
      await screen.findByText("Scanning on: every connected adapter (none has a link)"),
    ).toBeTruthy();
  });

  it("says a pinned adapter has no link, and offers the connected ones", async () => {
    mocks.controlInterface = "10.20.0.5";
    render(<DiscoveryPanel />);
    expect(await screen.findByText("Scanning on: Ethernet (10.20.0.5/24, no link)")).toBeTruthy();
    expect(
      screen.getByText("The pinned adapter has no link, so a scan will find nothing."),
    ).toBeTruthy();
    const button = screen.getByRole("button", { name: "Scan current adapters instead" });
    expect((button as HTMLButtonElement).disabled).toBe(false);
  });
});
