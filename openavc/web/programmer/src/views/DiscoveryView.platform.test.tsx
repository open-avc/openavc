import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// A discovered device whose matching community driver needs a newer OpenAVC
// is marked "Needs OpenAVC v..." on its card, and Install & Add is not offered,
// as Browse Drivers marks and greys Install. Before, the card offered the
// install and the server refused it after the click.

const mocks = vi.hoisted(() => ({
  results: {} as Record<string, unknown>,
  minPlatform: "99.0.0",
  installAndMatch: vi.fn(async () => ({ status: "ok", device_id: "acme_amp_10_0_0_9", name: "Acme Amp" })),
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
  fetchCommunityDrivers: vi.fn(async () => [
    {
      id: "acme_amp",
      name: "Acme Amp",
      manufacturer: "Acme",
      category: "audio",
      version: "1.0.0",
      file: "audio/acme_amp.avcdriver",
      min_platform_version: mocks.minPlatform,
    },
  ]),
  discoveryStartScan: vi.fn(async () => ({})),
  discoveryCancelScan: vi.fn(async () => ({})),
  discoverySetConfig: vi.fn(async () => ({})),
  discoveryInstallAndMatch: mocks.installAndMatch,
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
import { useConnectionStore } from "../store/connectionStore";

const SCANNED = {
  ip: "10.0.0.9",
  mac: null,
  hostname: null,
  manufacturer: "Acme",
  model: "Amp",
  device_name: null,
  firmware: null,
  serial_number: null,
  open_ports: [9443],
  banners: {},
  protocols: [],
  category: "audio",
  alive: true,
  identification: {
    state: "identified",
    driver_id: "acme_amp",
    candidates: ["acme_amp"],
    source: "mdns",
    reason: "mDNS announcement",
    evidence: [],
  },
  evidence_log: [],
  mdns_services: [],
  ssdp_info: null,
  snmp_info: null,
};

beforeEach(() => {
  useDiscoveryStore.getState().clear();
  useConnectionStore.setState({ liveState: { "system.version": "0.37.0" } });
  mocks.results = { devices: [SCANNED], status: "complete", port_labels: {}, port_drivers: {}, warnings: [] };
  mocks.installAndMatch.mockClear();
});

async function openCard() {
  render(<DiscoveryPanel />);
  fireEvent.click(await screen.findByText("Acme Amp"));
  return screen.findByRole("button", { name: /Install & Add/ });
}

describe("a discovered device whose community driver needs a newer OpenAVC", () => {
  it("is marked and not offered for install", async () => {
    mocks.minPlatform = "99.0.0";
    const button = await openCard();
    expect(screen.getByText("Needs OpenAVC v99.0.0")).toBeTruthy();
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(mocks.installAndMatch).not.toHaveBeenCalled();
  });

  it("is offered when this OpenAVC is new enough", async () => {
    mocks.minPlatform = "0.36.0";
    const button = await openCard();
    expect(screen.queryByText(/Needs OpenAVC/)).toBeNull();
    expect(button).not.toBeDisabled();
  });
});
