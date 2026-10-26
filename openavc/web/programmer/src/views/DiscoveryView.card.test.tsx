import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// A scanned device's card, opened. Three things the card used to get wrong:
// it offered "+ Add to Project" again after a reload for a device already in
// the project (only the card that was clicked remembered), it named a port
// shared by sibling models after whichever driver was read first, and it
// showed a Protocols row that nothing fills in ("None identified" on every
// device).

const mocks = vi.hoisted(() => ({
  results: {} as Record<string, unknown>,
  project: { devices: [] as unknown[], connections: {} as Record<string, unknown> },
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
  listDrivers: vi.fn(async () => [
    { id: "acme_widget_pro", name: "Acme Widget Pro", manufacturer: "Acme", category: "audio", commands: {}, config_schema: {} },
  ]),
  fetchCommunityDrivers: vi.fn(async () => []),
  discoveryStartScan: vi.fn(async () => ({})),
  discoveryCancelScan: vi.fn(async () => ({})),
  discoverySetConfig: vi.fn(async () => ({})),
}));

vi.mock("../store/projectStore", () => ({
  useProjectStore: (selector: (s: unknown) => unknown) => selector({ project: mocks.project }),
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

const SCANNED = {
  ip: "10.0.0.5",
  mac: null,
  hostname: null,
  manufacturer: "Acme",
  model: "Widget Pro",
  device_name: null,
  firmware: null,
  serial_number: null,
  open_ports: [17300],
  banners: {},
  protocols: [],
  category: "audio",
  alive: true,
  identification: {
    state: "identified",
    driver_id: "acme_widget_pro",
    candidates: ["acme_widget_pro"],
    source: "ssdp",
    reason: "SSDP model",
    evidence: [],
  },
  evidence_log: [],
  mdns_services: [],
  ssdp_info: null,
  snmp_info: null,
};

beforeEach(() => {
  useDiscoveryStore.getState().clear();
  mocks.results = {
    devices: [SCANNED],
    status: "complete",
    port_labels: { "17300": "Acme" },
    port_drivers: { "17300": ["acme_widget", "acme_widget_pro"] },
    warnings: [],
  };
  mocks.project = { devices: [], connections: {} };
});

async function openCard() {
  render(<DiscoveryPanel />);
  const title = await screen.findByText("Acme Widget Pro");
  fireEvent.click(title);
  await screen.findByText(/Open Ports:/);
}

describe("a scanned device's card", () => {
  it("knows a device already in the project after a reload", async () => {
    mocks.project = {
      devices: [{ id: "acme_widget_pro_10_0_0_5", driver: "acme_widget_pro", name: "Acme Widget Pro", config: {} }],
      connections: { acme_widget_pro_10_0_0_5: { host: "10.0.0.5" } },
    };
    await openCard();
    expect(screen.getByText('In the project as "Acme Widget Pro".')).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Add to Project/ })).toBeNull();
  });

  it("offers Add to Project for a device the project does not have", async () => {
    await openCard();
    await waitFor(() => expect(screen.getByRole("button", { name: /Add to Project/ })).toBeTruthy());
  });

  it("names its own driver on a port sibling models share", async () => {
    await openCard();
    expect(screen.getByText(/17300 \(Acme Widget Pro\)/)).toBeTruthy();
  });

  it("has no Protocols row", async () => {
    await openCard();
    expect(screen.queryByText("Protocols")).toBeNull();
    expect(screen.queryByText("None identified")).toBeNull();
  });

  it("is headed by the name Add to Project gives the device", async () => {
    // Maker and model, as the server names the device it adds, not the name
    // of the driver it was identified as.
    mocks.results = { ...mocks.results, devices: [{ ...SCANNED, model: "WP-3000" }] };
    render(<DiscoveryPanel />);
    expect(await screen.findByText("Acme WP-3000")).toBeTruthy();
  });
});

describe("a filtered list while a scan runs", () => {
  it("says the scan is still identifying devices", async () => {
    mocks.results = {
      devices: [{ ...SCANNED, manufacturer: null, model: null, identification: null }],
      status: "running",
      port_labels: {},
      port_drivers: {},
      warnings: [],
    };
    render(<DiscoveryPanel />);
    fireEvent.click(await screen.findByLabelText("AV only"));
    expect(
      await screen.findByText(
        "No devices match the current filters yet. The scan is still identifying devices.",
      ),
    ).toBeTruthy();
  });
});
